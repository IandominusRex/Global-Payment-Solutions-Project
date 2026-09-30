"""Ledger / Balance Engine (v2): money moves only when it settles, in time order.

Postings (account currency):
  OUT  debit  -(amount + fees) x fx     at execution (cross-border: when routed; domestic: at settlement)
  IN   credit +amount_received x fx     at settlement
  returned payments reverse the principal when the pacs.004 comes back (fees are not refunded)

Rules applied while replaying events:
  - Insufficient funds: an OUT debit that would take a non-pooled account past its
    overdraft limit is rejected with AM04. Pooled accounts draw on their pool header.
  - Funding (07:00 local, each business day): projected balance = balance - scheduled
    (ERP-known) outflows over the next N business days. Below the buffer, the account is
    funded first from same-entity, same-currency accounts with surplus (internal transfer),
    then by an intercompany top-up from HQ. Card spend and urgent payments are not
    "scheduled", which is exactly why some of them still bounce.
  - Cash concentration (weekly): surplus far above target goes back to HQ as an
    intercompany payment. Restricted currencies (CNY, INR) and entities kept out of the
    pool are exempt - the "trapped cash" that analysis 6 should find.
  - End of day (23:59:59 entity-local): pooled accounts are zero-balanced into their
    header (fact_sweep), then closing balances are snapshotted (fact_balance).

Invariant (tested): for every account, opening + postings + sweeps == closing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
import pandas as pd

from treasury.simulator.business.events import Fx
from treasury.simulator.config import SimulationConfig
from treasury.simulator.engine import EventLoop
from treasury.simulator.rng import stream
from treasury.simulator.world.builder import World
from treasury.simulator.world.calendar import BusinessCalendar

SCHEDULED_FLOWS = {"ap_payment", "payroll", "tax", "ic_funding", "ic_repatriation"}
# Event kinds and their priority at equal timestamps.
FUNDING, DEBIT, CREDIT, REVERSAL, EOD = 0, 1, 2, 3, 4
PRIORITY = {FUNDING: 0, DEBIT: 1, CREDIT: 2, REVERSAL: 3, EOD: 9}


@dataclass
class LedgerResult:
    payments: pd.DataFrame
    balances: pd.DataFrame
    sweeps: pd.DataFrame
    postings: pd.DataFrame   # every movement, used by bank statements (v4) and the invariant test
    opening: pd.Series       # opening balance per account at data_start


def run(cfg: SimulationConfig, world: World, cal: BusinessCalendar, fx: Fx, payments: pd.DataFrame,
        horizon_end: np.datetime64) -> LedgerResult:
    return _Ledger(cfg, world, cal, fx, payments, horizon_end).run()


def _to_s(x) -> np.ndarray:
    return pd.to_datetime(pd.Series(x)).to_numpy().astype("datetime64[s]").astype(np.int64)


class _Ledger:
    def __init__(self, cfg, world: World, cal: BusinessCalendar, fx: Fx, p: pd.DataFrame, horizon_end):
        self.cfg, self.world, self.cal, self.fx = cfg, world, cal, fx
        self.p = p.reset_index(drop=True).copy()
        self.horizon_s = int(np.datetime64(horizon_end, "s").astype(np.int64)) + 86_400
        self.rng = stream(cfg.seed, "ledger")
        acc = world.dim_account.reset_index(drop=True)
        self.acc = acc
        self.aidx = {a: i for i, a in enumerate(acc["account_id"])}
        self.ent = world.dim_entity.set_index("entity_id")
        self.hq = world.dim_entity.loc[world.dim_entity["is_in_house_bank"], "entity_id"].iloc[0]
        self.header_of = [self.aidx.get(h) if isinstance(h, str) else None for h in acc["pool_header_account_id"]]
        self.limit = acc["overdraft_limit"].astype(float).tolist()
        self.target = acc["target_balance"].astype(float).tolist()
        self.ccy = acc["currency_code"].tolist()
        self.restricted = [cfg.currencies[c].is_restricted for c in self.ccy]
        self.unpooled_entities = set(cfg.accounts.force_unpooled_entities)
        self.pooled = acc["is_pooled"].astype(bool).tolist()
        # HQ funds top-ups from its pool header in the account's currency.
        headers = set(acc["pool_header_account_id"].dropna())
        hq_acc = acc[acc["entity_id"] == self.hq]
        self.hq_header = {}
        for a, c in zip(hq_acc["account_id"], hq_acc["currency_code"], strict=True):
            if c not in self.hq_header or a in headers:
                self.hq_header[c] = self.aidx[a]

    # ------------------------------------------------------------------ setup
    def _postings(self):
        p = self.p
        conv = p["fx_rate_applied"].fillna(1.0).to_numpy()
        out = (p["direction"] == "OUT").to_numpy()
        live = (p["status"] != "rejected").to_numpy()
        xb = (p["_rail"] == "SWIFT_XBORDER").to_numpy()
        debit_ts = np.where(xb, _to_s(p["_routed_ts"]), _to_s(p["settled_ts"].fillna(p["_routed_ts"])))
        credit_ts = _to_s(p["settled_ts"].fillna(p["_routed_ts"]))
        amt = p["amount"].to_numpy()
        recv = p["amount_received"].fillna(0).to_numpy()
        fees = p["fees"].to_numpy()
        self.p_acct = np.array([self.aidx[a] for a in p["account_id"]])
        self.p_delta = np.where(out, -(amt + fees) * conv, recv * conv)
        self.p_reversal = np.where(out, amt * conv, -recv * conv)
        self.p_ts = np.where(out, debit_ts, credit_ts)

        ev_ts = [self.p_ts[live]]
        ev_kind = [np.where(out[live], DEBIT, CREDIT)]
        ev_ref = [np.flatnonzero(live)]
        ret = live & p["_returned_ts"].notna().to_numpy()
        ev_ts.append(_to_s(p.loc[ret, "_returned_ts"]))
        ev_kind.append(np.full(ret.sum(), REVERSAL))
        ev_ref.append(np.flatnonzero(ret))
        return ev_ts, ev_kind, ev_ref

    def _calendar_events(self):
        """Funding checks (07:00 local, business days) and end of day (23:59:59 local) per entity."""
        days = pd.date_range(self.cfg.data_start, pd.Timestamp(self.horizon_s, unit="s"), freq="D")
        ts, kind, ref = [], [], []
        self.entities = list(self.ent.index)
        for ei, eid in enumerate(self.entities):
            tz = self.ent.loc[eid, "timezone"]
            eod = (days + pd.Timedelta(hours=23, minutes=59, seconds=59)).tz_localize(tz).tz_convert("UTC")
            ts.append(eod.tz_localize(None).values.astype("datetime64[s]").astype(np.int64))
            kind.append(np.full(len(days), EOD))
            ref.append(np.full(len(days), ei))
            if eid == self.hq:
                continue
            bdays = self.cal.business_days(self.ent.loc[eid, "country"])
            bdays = bdays[(bdays >= np.datetime64(self.cfg.data_start)) & (bdays <= days[-1].to_datetime64())]
            f = (pd.DatetimeIndex(bdays) + pd.Timedelta(hours=7)).tz_localize(tz).tz_convert("UTC")
            ts.append(f.tz_localize(None).values.astype("datetime64[s]").astype(np.int64))
            kind.append(np.full(len(bdays), FUNDING))
            ref.append(np.full(len(bdays), ei))
        return ts, kind, ref

    def _opening_balances(self) -> list[float]:
        acc = self.acc
        out = self.p_delta < 0
        first_days = 60
        window = self.p_ts < self.p_ts.min() + first_days * 86_400 if len(self.p_ts) else np.zeros(0, bool)
        daily_out = np.bincount(self.p_acct[out & window], weights=-self.p_delta[out & window],
                                minlength=len(acc)) / (first_days * 5 / 7)
        opening = acc["target_balance"].to_numpy(float) + self.cfg.ledger.opening_days_of_outflow * daily_out
        return [0.0 if pooled else float(o) for o, pooled in zip(opening, self.pooled, strict=True)]

    def _scheduled_outflow_index(self):
        """Per account: sorted debit times and cumulative scheduled outflow, for the funding rule."""
        sched = (self.p_delta < 0) & self.p["flow"].isin(SCHEDULED_FLOWS).to_numpy() & \
            (self.p["status"] != "rejected").to_numpy()
        self.sched = {}
        for a in np.unique(self.p_acct[sched]):
            m = sched & (self.p_acct == a)
            order = np.argsort(self.p_ts[m], kind="stable")
            self.sched[a] = (self.p_ts[m][order], np.concatenate([[0.0], np.cumsum(-self.p_delta[m][order])]))

    def _upcoming_outflow(self, a: int, now: int, until: int) -> float:
        if a not in self.sched:
            return 0.0
        t, cum = self.sched[a]
        return float(cum[np.searchsorted(t, until, "right")] - cum[np.searchsorted(t, now, "left")])

    # ------------------------------------------------------------------ run
    def run(self) -> LedgerResult:
        ev_ts, ev_kind, ev_ref = self._postings()
        c_ts, c_kind, c_ref = self._calendar_events()
        ts = np.concatenate(ev_ts + c_ts)
        kind = np.concatenate(ev_kind + c_kind)
        ref = np.concatenate(ev_ref + c_ref)
        keep = ts < self.horizon_s
        ts, kind, ref = ts[keep], kind[keep], ref[keep]
        prio = np.vectorize(PRIORITY.get)(kind) if len(kind) else kind
        loop = EventLoop(ts, prio, kind, ref)
        self._scheduled_outflow_index()

        bal = self._opening_balances()
        self.opening = pd.Series(bal, index=self.acc["account_id"])
        acct_by_entity = {eid: [self.aidx[a] for a in self.acc.loc[self.acc["entity_id"] == eid, "account_id"]]
                          for eid in self.entities}
        acct, delta, rev = self.p_acct.tolist(), self.p_delta.tolist(), self.p_reversal.tolist()
        am04: dict[int, int] = {}          # payment row -> attempted debit time
        self.bal = bal
        topups: list[dict] = []
        topup_legs: list[tuple[int, float]] = []  # dynamic postings: (account, delta)
        postings: list[tuple[int, int, float, str, int]] = []  # (ts, account, delta, kind, ref)
        balances, sweeps = [], []
        lookahead = self.cfg.ledger.funding_lookahead_business_days
        mirror_of = self.p["_mirror_of"].to_numpy()
        pid_row = {pid: i for i, pid in enumerate(self.p["payment_id"])}
        dropped_mirrors = set()

        for t, k, r in loop:
            if k == DEBIT:
                if r < 0:  # dynamic top-up leg
                    a, d = topup_legs[-r - 1]
                    bal[a] += d
                    postings.append((t, a, d, "topup", r))
                    continue
                a = acct[r]
                h = self.header_of[a]
                avail = bal[a] + self.limit[a] + ((bal[h] + self.limit[h]) if self.pooled[a] and h is not None else 0)
                if bal[a] + delta[r] < -self.limit[a] and avail + delta[r] < 0:
                    am04[r] = t
                    continue
                bal[a] += delta[r]
                postings.append((t, a, delta[r], "payment", r))
            elif k == CREDIT:
                if r < 0:
                    a, d = topup_legs[-r - 1]
                    bal[a] += d
                    postings.append((t, a, d, "topup", r))
                    continue
                src = mirror_of[r]
                if src is not None and pid_row[src] in am04:
                    dropped_mirrors.add(r)
                    continue
                a = acct[r]
                bal[a] += delta[r]
                postings.append((t, a, delta[r], "payment", r))
            elif k == REVERSAL:
                if r in am04 or r in dropped_mirrors:
                    continue
                a = acct[r]
                bal[a] += rev[r]
                postings.append((t, a, rev[r], "return", r))
            elif k == FUNDING:
                eid = self.entities[r]
                local = pd.Timestamp(t, unit="s").tz_localize("UTC").tz_convert(self.ent.loc[eid, "timezone"])
                day = np.datetime64(local.date())
                until_day = self.cal.add_business_days(np.array([self.ent.loc[eid, "country"]]),
                                                       np.array([day]), lookahead)[0]
                until = int((until_day + np.timedelta64(1, "D")).astype("datetime64[s]").astype(np.int64))
                proj = {a: bal[a] - self._upcoming_outflow(a, t, until) for a in acct_by_entity[eid]
                        if not self.pooled[a]}
                for a in proj:
                    need = self._funding_need(a, proj[a])
                    for s in proj:  # 1) same entity, same currency surplus
                        if need <= 0:
                            break
                        if s == a or self.ccy[s] != self.ccy[a]:
                            continue
                        take = min(need, proj[s] - self.cfg.ledger.concentration_keep * self.target[s])
                        if take > 0:
                            bal[s] -= take
                            bal[a] += take
                            proj[s] -= take
                            proj[a] += take
                            need -= take
                            sweeps.append((t, local.date(), s, a, take, "internal"))
                            postings.append((t, s, -take, "sweep", len(sweeps) - 1))
                            postings.append((t, a, take, "sweep", len(sweeps) - 1))
                    if need > 0:  # 2) intercompany top-up from HQ
                        need = self._round_up(a, need)
                        hq_src = self.hq_header.get(self.ccy[a], self.hq_header[self.cfg.reporting_currency])
                        self._ic_transfer(loop, t, hq_src, a, need, "ic_topup", topups, topup_legs)
                if local.dayofweek == self.cfg.ledger.concentration_weekday and eid not in self.unpooled_entities:
                    for a, pr in proj.items():  # 3) weekly concentration of surplus to HQ
                        if self.restricted[a] or pr <= self.cfg.ledger.concentration_trigger * self.target[a]:
                            continue
                        amount = self._round_down(a, pr - self.cfg.ledger.concentration_keep * self.target[a])
                        if amount > 0:
                            self._ic_transfer(loop, t, a, self.hq_header[self.ccy[a]], amount, "ic_concentration",
                                              topups, topup_legs)
            elif k == EOD:
                eid = self.entities[r]
                local_day = pd.Timestamp(t, unit="s").tz_localize("UTC").tz_convert(
                    self.ent.loc[eid, "timezone"]).date()
                for a in acct_by_entity[eid]:
                    h = self.header_of[a]
                    if self.pooled[a] and h is not None and abs(bal[a]) > 0.005:
                        amt = bal[a]
                        bal[h] += amt
                        bal[a] = 0.0
                        src, dst = (a, h) if amt > 0 else (h, a)
                        sweeps.append((t, local_day, src, dst, abs(amt), "zba"))
                        postings.append((t, a, -amt, "sweep", len(sweeps) - 1))
                        postings.append((t, h, amt, "sweep", len(sweeps) - 1))
                for a in acct_by_entity[eid]:
                    balances.append((local_day, a, bal[a], t))

        return self._results(am04, dropped_mirrors, topups, balances, sweeps, postings)

    def _funding_need(self, a: int, projected: float) -> float:
        lc = self.cfg.ledger
        limit = self.limit[a]
        if limit > 0:
            if projected >= -lc.overdraft_fund_trigger * limit:
                return 0.0
            return -lc.overdraft_fund_to * limit - projected
        if projected >= lc.funding_buffer_share * self.target[a]:
            return 0.0
        return self.target[a] - projected

    def _step(self, a: int) -> float:
        return self.cfg.ledger.topup_rounding_sgd / self.cfg.currencies[self.ccy[a]].start_rate_to_sgd

    def _round_up(self, a: int, x: float) -> float:
        return float(np.ceil(x / self._step(a)) * self._step(a))

    def _round_down(self, a: int, x: float) -> float:
        return float(np.floor(x / self._step(a)) * self._step(a))

    def _ic_transfer(self, loop: EventLoop, t: int, src: int, dst: int, amount: float, flow: str,
                     records: list, legs: list) -> None:
        """Intercompany transfer between two of our accounts; `amount` is in the destination currency."""
        src_e, dst_e = self.acc.loc[src, "entity_id"], self.acc.loc[dst, "entity_id"]
        same_country = self.ent.loc[src_e, "country"] == self.ent.loc[dst_e, "country"]
        routed = t + 600 + int(self.rng.uniform(0, 600))
        transit = self.rng.uniform(5, 60) if same_country else self.rng.lognormal(np.log(7200), 0.4)
        settled = routed + int(transit)
        rate = 1.0 if self.ccy[src] == self.ccy[dst] else self._cross_rate(self.ccy[dst], self.ccy[src], settled)
        legs.append((src, -amount * rate))
        loop.schedule(routed, PRIORITY[DEBIT], DEBIT, -len(legs))
        legs.append((dst, amount))
        loop.schedule(settled, PRIORITY[CREDIT], CREDIT, -len(legs))
        records.append({"t": t, "routed": routed, "settled": settled, "flow": flow, "src": src, "dst": dst,
                        "src_entity": src_e, "dst_entity": dst_e, "amount": amount, "ccy": self.ccy[dst],
                        "rate": None if rate == 1.0 else rate, "same_country": same_country})

    def _cross_rate(self, ccy: str, acct_ccy: str, t: int) -> float:
        day = np.array([np.datetime64(t, "s").astype("datetime64[D]")])
        return float(self.fx.to_sgd(np.array([ccy]), day)[0] / self.fx.to_sgd(np.array([acct_ccy]), day)[0])

    # ------------------------------------------------------------------ outputs
    def _results(self, am04, dropped_mirrors, topups, balances, sweeps, postings) -> LedgerResult:
        p = self.p
        rows = np.array(sorted(am04), dtype=int)
        if len(rows):
            att = pd.to_datetime(pd.Series([am04[r] for r in rows]), unit="s").to_numpy()
            p.loc[rows, "status"] = "rejected"
            p.loc[rows, "failure_reason"] = "AM04"
            p.loc[rows, "_rejected_ts"] = att
            p.loc[rows, "is_stp"] = False
            for c in ["settled_ts", "_returned_ts", "amount_received", "fx_rate_applied", "value_date_id"] + \
                    [f"_hop{i}_ts" for i in range(1, 7)]:
                p.loc[rows, c] = None
            p.loc[rows, "_hops"] = 0
        p = p.drop(index=list(dropped_mirrors))
        p = pd.concat([p, self._topup_rows(topups, len(self.p) + 1)], ignore_index=True)

        acc_ids = self.acc["account_id"].to_numpy()
        acc_ccy = self.acc["currency_code"].to_numpy()
        bal = pd.DataFrame(balances, columns=["date", "a", "closing_balance", "t"])
        bal["_eod_ts"] = pd.to_datetime(bal["t"], unit="s")
        bal["account_id"] = acc_ids[bal["a"]]
        bal["currency_code"] = acc_ccy[bal["a"]]
        bal["date_id"] = pd.to_datetime(bal["date"]).dt.strftime("%Y%m%d").astype(int)
        bal["closing_balance"] = bal["closing_balance"].round(2)
        bal["closing_balance_sgd"] = np.round(
            bal["closing_balance"] * self.fx.to_sgd(bal["currency_code"], pd.to_datetime(bal["date"])), 2)
        # An entity's EOD can fall on the same local date twice around DST changes; keep the last.
        bal = bal.drop_duplicates(["date_id", "account_id"], keep="last")

        sw = pd.DataFrame(sweeps, columns=["t", "date", "src", "dst", "amount", "sweep_type"])
        sweep_df = pd.DataFrame({
            "sweep_id": [f"S{i:07d}" for i in range(1, len(sw) + 1)],
            "date_id": pd.to_datetime(sw["date"]).dt.strftime("%Y%m%d").astype(int),
            "from_account_id": acc_ids[sw["src"]],
            "to_account_id": acc_ids[sw["dst"]],
            "amount": sw["amount"].round(2),
            "currency_code": acc_ccy[sw["src"]],
            "sweep_ts": pd.to_datetime(sw["t"], unit="s"),
            "sweep_type": sw["sweep_type"],
        })
        post = pd.DataFrame(postings, columns=["t", "a", "delta", "kind", "ref"])
        post_df = pd.DataFrame({"posting_ts": pd.to_datetime(post["t"], unit="s"),
                                "account_id": acc_ids[post["a"]], "delta": post["delta"],
                                "kind": post["kind"], "ref": post["ref"]})
        # Map payment postings and top-up legs to payment ids.
        pay_ids = self.p["payment_id"].to_numpy()
        is_pay = post_df["kind"].isin(["payment", "return"]).to_numpy()
        post_df["payment_id"] = None
        post_df.loc[is_pay, "payment_id"] = pay_ids[post_df.loc[is_pay, "ref"].to_numpy()]
        is_top = (post_df["kind"] == "topup").to_numpy()
        leg_ids = self._topup_leg_ids
        post_df.loc[is_top, "payment_id"] = [leg_ids[-r - 1] for r in post_df.loc[is_top, "ref"]]
        bal = bal[["date_id", "account_id", "closing_balance", "currency_code", "closing_balance_sgd", "_eod_ts"]]
        return LedgerResult(payments=p.reset_index(drop=True), balances=bal.reset_index(drop=True),
                            sweeps=sweep_df, postings=post_df.drop(columns="ref"), opening=self.opening)

    def _topup_rows(self, records: list[dict], first_number: int) -> pd.DataFrame:
        """Two payment rows (OUT at the source entity, IN at the destination) per ledger transfer."""
        acc = self.acc
        ic = self.world.dim_counterparty[self.world.dim_counterparty["counterparty_type"] == "intercompany"]
        ic_for = ic.groupby("home_entity_id")["counterparty_id"].first()
        types = self.world.dim_payment_type.set_index("rail")["type_id"]
        uetr_rng = stream(self.cfg.seed, "ledger.uetr")
        rows, self._topup_leg_ids = [], []
        n = first_number
        for tp in records:
            routed = pd.Timestamp(tp["routed"], unit="s")
            settled = pd.Timestamp(tp["settled"], unit="s")
            created = pd.Timestamp(tp["t"], unit="s")
            rail = "BOOK_TRANSFER" if tp["same_country"] else "SWIFT_XBORDER"
            src_e, dst_e = tp["src_entity"], tp["dst_entity"]
            tz = self.ent.loc[dst_e, "timezone"]
            value_id = int(settled.tz_localize("UTC").tz_convert(tz).strftime("%Y%m%d"))
            sgd = round(tp["amount"] * float(self.fx.to_sgd(np.array([tp["ccy"]]),
                                                            np.array([settled.to_datetime64()]))[0]), 2)
            bits = uetr_rng.integers(0, 2**63, size=2)
            uetr = str(uuid.UUID(int=(int(bits[0]) << 64) | int(bits[1]), version=4)) if rail == "SWIFT_XBORDER" \
                else None
            label = "TOPUP" if tp["flow"] == "ic_topup" else "CONCENTRATION"
            common = {
                "end_to_end_id": f"E2EIC{n:08d}", "uetr": uetr, "batch_id": None, "type_id": int(types[rail]),
                "sender_country": self.ent.loc[src_e, "country"], "receiver_country": self.ent.loc[dst_e, "country"],
                "amount": round(tp["amount"], 2), "currency_code": tp["ccy"], "amount_sgd": sgd,
                "charge_bearer": "OUR" if uetr else None, "fees": 0.0, "amount_received": round(tp["amount"], 2),
                "purpose_code": "INTC", "remittance_ref": f"IC {label} {dst_e if label == 'TOPUP' else src_e}",
                "is_intercompany": True, "initiated_ts": created, "submitted_ts": created + pd.Timedelta(minutes=5),
                "settled_ts": settled, "value_date_id": value_id, "status": "completed",
                "_settled_status": "completed", "_rejected_ts": pd.NaT, "_returned_ts": pd.NaT,
                "_repaired_ts": pd.NaT, "failure_reason": None, "is_stp": True, "repair_count": 0,
                "missed_cutoff": False, "flow": tp["flow"], "intent_id": -1, "intended_date": created.normalize(),
                "anomaly_type": None, "_approved_ts": created + pd.Timedelta(minutes=2),
                "_screened_ts": created + pd.Timedelta(minutes=6), "_routed_ts": routed, "_rail": rail,
                "_held": False, "_hops": 1 if uetr else 0,
                "_hop1_ts": settled - pd.Timedelta(seconds=30) if uetr else pd.NaT, "_amount_sgd_initiated": sgd,
            }
            out_id, in_id = f"P{n:08d}", f"P{n + 1:08d}"
            rows.append({**common, "payment_id": out_id, "direction": "OUT", "entity_id": src_e,
                         "account_id": acc.loc[tp["src"], "account_id"], "_account_ccy": self.ccy[tp["src"]],
                         "counterparty_id": ic_for[dst_e], "channel": "API",
                         "fx_rate_applied": tp["rate"], "_mirror_of": None})
            rows.append({**common, "payment_id": in_id, "direction": "IN", "entity_id": dst_e,
                         "account_id": acc.loc[tp["dst"], "account_id"], "_account_ccy": tp["ccy"],
                         "counterparty_id": ic_for[src_e], "channel": "INBOUND",
                         "fx_rate_applied": None, "_mirror_of": out_id})
            self._topup_leg_ids += [out_id, in_id]
            n += 2
        return pd.DataFrame(rows)
