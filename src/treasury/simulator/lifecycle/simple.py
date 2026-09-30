"""Payment lifecycle, v1: one vectorised pass from intent to final state.

For every intent this decides: our account, rail, channel, timestamps (initiated,
submitted, settled), cut-off misses, cross-border hops and sanctions holds, repairs,
failures with ISO 20022-style reasons, charges, and FX conversion.

v2 replaces this with the event-by-event state machine (state_machine.py) that also
emits fact_payment_event rows and posts to the ledger. The output columns stay the
same, so SQL and dashboards built on v1 keep working.

Status meanings:
  completed  settled without a delay event
  delayed    settled, but after a cut-off miss, a sanctions hold or extra routing hops
  rejected   never settled (pain.002 RJCT)
  returned   settled, then sent back (pacs.004)
  pending    not yet settled when the data ends
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd

from treasury.simulator.business.events import Fx
from treasury.simulator.config import SimulationConfig
from treasury.simulator.market.fx import spread_bps
from treasury.simulator.rng import stream
from treasury.simulator.world.builder import World
from treasury.simulator.world.calendar import BusinessCalendar

INSTANT_RAILS = {"FAST", "SEPA_INST", "BOOK_TRANSFER"}
FEE_SGD = {"FAST": 0.5, "GIRO": 0.2, "MEPS_RTGS": 15, "SEPA_CT": 0.2, "SEPA_INST": 0.5, "ACH": 0.3,
           "FEDWIRE": 15, "BACS": 0.2, "NEFT": 2, "CNAPS": 5, "CARD": 0, "BOOK_TRANSFER": 0,
           "SWIFT_XBORDER": 25}
# Approval delay (median minutes) from ERP creation to bank submission, by channel.
APPROVAL_MIN = {"API": 15, "H2H_FILE": 60, "PORTAL": 90, "LEGACY_FILE": 180, "INBOUND": 3}
CHANNEL_FAIL_MULT = {"API": 1.0, "H2H_FILE": 1.1, "PORTAL": 1.2, "LEGACY_FILE": 1.8, "INBOUND": 0.4}
CHANNEL_REPAIR_P = {"API": 0.01, "H2H_FILE": 0.03, "PORTAL": 0.04, "LEGACY_FILE": 0.01, "INBOUND": 0.005}
# Rejection reasons: base weights shaped so ~3 reasons drive most failures (analysis 4 Pareto).
REJECT_REASONS = ["AC01", "RR03", "AM04", "BE04", "RC01", "FF01", "AG01", "RR04", "MS03"]
REJECT_WEIGHTS = np.array([45, 20, 13, 5, 5, 4, 3, 2, 3], dtype=float)
RETURN_REASONS = ["AC01", "AC04", "CUST"]
RETURN_WEIGHTS = np.array([0.5, 0.35, 0.15])

PAYMENT_COLUMNS = [
    "payment_id", "end_to_end_id", "uetr", "batch_id", "direction", "account_id", "counterparty_id",
    "type_id", "channel", "sender_country", "receiver_country", "amount", "currency_code", "amount_sgd",
    "fx_rate_applied", "charge_bearer", "fees", "amount_received", "purpose_code", "remittance_ref",
    "is_intercompany", "initiated_ts", "submitted_ts", "settled_ts", "value_date_id", "status",
    "failure_reason", "is_stp", "repair_count", "missed_cutoff",
]


def run(cfg: SimulationConfig, world: World, cal: BusinessCalendar, fx: Fx,
        intents: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (fact_payment, truth_payment_invoice)."""
    rng = stream(cfg.seed, "lifecycle.v1")
    p = intents.copy().reset_index(drop=True)
    n = len(p)
    ent = world.dim_entity.set_index("entity_id")
    cp = world.dim_counterparty.set_index("counterparty_id")
    country_tz = {k: c.timezone for k, c in cfg.countries.items()}

    e_country = ent.loc[p["entity_id"], "country"].to_numpy()
    c_country = cp.loc[p["counterparty_id"], "country"].to_numpy()
    # An intercompany counterparty "lives" in the country of the entity it represents.
    out = p["direction"].to_numpy() == "OUT"
    p["sender_country"] = np.where(out, e_country, c_country)
    p["receiver_country"] = np.where(out, c_country, e_country)
    dq = cp.loc[p["counterparty_id"], "data_quality_score"].to_numpy()
    high_risk = np.isin(c_country, cfg.high_risk_countries)
    amount_sgd_est = p["amount"].to_numpy() * fx.to_sgd(p["currency_code"], p["intended_date"])

    # ---- channel and account ----
    primary = ent.loc[p["entity_id"], "primary_channel"].to_numpy()
    others = np.array(["API", "H2H_FILE", "PORTAL"])
    channel = np.where(rng.random(n) < 0.85, primary, others[rng.integers(0, 3, n)])
    p["channel"] = np.where(out, channel, "INBOUND")
    p["account_id"], acct_ccy = _pick_accounts(world, p)

    # ---- rail ----
    rail = _pick_rail(p, amount_sgd_est, rng)
    types = world.dim_payment_type.set_index("rail")
    p["type_id"] = types.loc[rail, "type_id"].to_numpy()
    xb = rail == "SWIFT_XBORDER"

    # ---- initiated / submitted (UTC) ----
    initiator_tz = np.where(out, np.array([country_tz[c] for c in e_country]),
                            np.array([country_tz[c] for c in c_country]))
    local_minutes = np.where(
        p["is_batch"], rng.uniform(9 * 60, 11 * 60, n),
        np.where(p["flow"] == "card_spend", rng.normal(14 * 60, 180, n).clip(0, 1439),
                 rng.uniform(8 * 60, 18 * 60, n)))
    initiated_local = p["intended_date"].to_numpy().astype("datetime64[m]") + local_minutes.astype("timedelta64[m]")
    initiated = _local_to_utc(initiated_local, initiator_tz)
    approval_med = np.array([APPROVAL_MIN[c] for c in p["channel"]])
    submitted = initiated + (rng.lognormal(np.log(approval_med), 0.6) * 60).astype("timedelta64[s]")

    # Manual repair (breaks STP): legacy files and poor master data need fixing by hand.
    p_repair = np.array([CHANNEL_REPAIR_P[c] for c in p["channel"]]) + np.where(dq < 0.8, 0.05, 0.0)
    p_repair = p_repair + np.where(p["channel"] == "LEGACY_FILE", cfg.scenarios.legacy_file_channel_stp_penalty, 0)
    repaired = rng.random(n) < p_repair
    submitted = submitted + np.where(repaired, rng.lognormal(np.log(3 * 3600), 0.7, n), 0).astype("timedelta64[s]")

    # ---- cut-off: submitted after the rail's local cut-off (or on a non-business day) rolls ----
    sender_tz = np.array([country_tz[c] for c in p["sender_country"]])
    sub_local = _utc_to_local(submitted, sender_tz)
    sub_day = sub_local.astype("datetime64[D]")
    cutoff_min = np.array([_cutoff_minutes(cfg, r) for r in rail])
    after_cutoff = (sub_local - sub_day).astype("timedelta64[m]").astype(int) > cutoff_min
    is_bd = cal.is_business_day(p["sender_country"].to_numpy(), sub_day)
    instant = np.isin(rail, list(INSTANT_RAILS))
    missed_cutoff = ~instant & is_bd & after_cutoff
    process_day = np.where(missed_cutoff, cal.add_business_days(p["sender_country"].to_numpy(), sub_day, 1),
                           cal.roll_forward(p["sender_country"].to_numpy(), sub_day))
    rolled = process_day != sub_day

    # ---- settlement ----
    receiver_tz = np.array([country_tz[c] for c in p["receiver_country"]])
    settle_days = np.array([cfg.rails[r].settle_days or 0 for r in rail])
    value_day = cal.add_business_days(p["sender_country"].to_numpy(), process_day, settle_days)
    same_day_local = value_day.astype("datetime64[m]") + rng.uniform(9 * 60, 12 * 60, n).astype("timedelta64[m]")
    settled = _local_to_utc(same_day_local, sender_tz)
    # Same-day RTGS / domestic real-time-ish rails settle shortly after submission.
    quick = (settle_days == 0) & ~rolled & ~instant & ~xb
    settled = np.where(quick, submitted + (rng.lognormal(np.log(25 * 60), 0.8, n)).astype("timedelta64[s]"), settled)
    # Instant rails: seconds.
    lat = np.array([cfg.rails[r].latency_s or (0, 1) for r in rail])
    settled = np.where(instant, submitted + rng.uniform(lat[:, 0], lat[:, 1]).astype("timedelta64[s]"), settled)

    # Cross-border: hops through correspondents, optional sanctions hold, credit on receiver business day.
    hops_lo, hops_hi = cfg.rails["SWIFT_XBORDER"].hops or (1, 4)
    hops = rng.integers(hops_lo, hops_hi + 1, n)
    slow = (p["sender_country"].to_numpy() == cfg.scenarios.slow_corridor[0]) & \
           (p["receiver_country"].to_numpy() == cfg.scenarios.slow_corridor[1])
    hops = hops + np.where(slow, 2, 0)
    hop_hours = rng.lognormal(np.log(0.5), 1.0, (n, hops_hi + 2))  # gpi: most hops take minutes
    hop_hours = np.where(np.arange(hops_hi + 2)[None, :] < hops[:, None], hop_hours, 0)
    # The slow corridor's last intermediary only processes once per business day.
    slow_extra_hours = np.where(slow, rng.lognormal(np.log(20), 0.4, n), 0)
    risk_mult = np.where(high_risk, 6.0, np.where(cp.loc[p["counterparty_id"], "risk_rating"] == "medium", 1.5, 1.0))
    held = xb & (rng.random(n) < cfg.rails["SWIFT_XBORDER"].sanctions_hold_rate * risk_mult)
    hold_hours = np.where(held, rng.lognormal(np.log(10), 0.9, n), 0)
    depart_local = np.where(rolled, process_day.astype("datetime64[m]") + np.timedelta64(9 * 60, "m"),
                            sub_local.astype("datetime64[m]"))
    transit_hours = hop_hours.sum(axis=1) + hold_hours + slow_extra_hours
    arrive = _local_to_utc(depart_local, sender_tz) + (transit_hours * 3600).astype("timedelta64[s]")
    arrive_local = _utc_to_local(arrive, receiver_tz)
    arrive_day = arrive_local.astype("datetime64[D]")
    late_in_day = (arrive_local - arrive_day).astype("timedelta64[m]").astype(int) > 17 * 60
    credit_day = np.where(late_in_day, cal.add_business_days(p["receiver_country"].to_numpy(), arrive_day, 1),
                          cal.roll_forward(p["receiver_country"].to_numpy(), arrive_day))
    moved = credit_day != arrive_day
    credit_local = np.where(moved, credit_day.astype("datetime64[m]") + rng.uniform(9 * 60, 10 * 60, n).astype(
        "timedelta64[m]"), arrive_local.astype("datetime64[m]"))
    settled = np.where(xb, _local_to_utc(credit_local, receiver_tz), settled)
    settled = np.maximum(settled, submitted + np.timedelta64(1, "s"))
    value_local_day = _utc_to_local(settled, receiver_tz).astype("datetime64[D]")

    # ---- failures ----
    corridor_mult = np.where(
        (p["sender_country"].to_numpy() == cfg.scenarios.high_failure_corridor[0])
        & (p["receiver_country"].to_numpy() == cfg.scenarios.high_failure_corridor[1]), 4.0, 1.0)
    base_fail = np.array([cfg.rails[r].fail_rate for r in rail])
    dq_mult = np.clip((1 - dq) / 0.1, 0.3, 8.0)
    ch_mult = np.array([CHANNEL_FAIL_MULT[c] for c in p["channel"]])
    p_fail = base_fail * corridor_mult * dq_mult * ch_mult * np.where(high_risk, 3.0, 1.0)
    failed = rng.random(n) < np.clip(p_fail, 0, 0.5)
    returned = failed & (rng.random(n) < 0.15) & (rail != "BOOK_TRANSFER")
    rejected = failed & ~returned

    reason = np.full(n, None, dtype=object)
    reason[rejected] = _draw_reasons(rng, rejected, p["channel"].to_numpy(), out, high_risk, dq)
    reason[returned] = rng.choice(RETURN_REASONS, size=returned.sum(), p=RETURN_WEIGHTS)

    # ---- status ----
    end_ts = np.datetime64(cfg.end_date) + np.timedelta64(1, "D")
    status = np.where(missed_cutoff | held | slow, "delayed", "completed").astype(object)
    status[returned] = "returned"
    status[rejected] = "rejected"
    pending = ~rejected & (settled >= end_ts)
    status[pending] = "pending"
    reason[pending] = None
    unsettled = rejected | pending
    p["status"] = status
    p["failure_reason"] = reason
    p["missed_cutoff"] = missed_cutoff
    p["repair_count"] = repaired.astype(int)
    p["is_stp"] = ~repaired & ~held & ~rejected
    p["initiated_ts"] = initiated
    p["submitted_ts"] = submitted
    p["settled_ts"] = pd.Series(settled).where(~unsettled)
    p["value_date_id"] = pd.Series(_date_id(value_local_day)).where(~unsettled).astype("Int64")

    # ---- charges and amounts ----
    ccy_to_sgd = fx.to_sgd(p["currency_code"], np.where(unsettled, p["intended_date"], value_local_day))
    p["amount_sgd"] = np.round(p["amount"] * ccy_to_sgd, 2)
    bearer = np.where(xb, rng.choice(["SHA", "OUR", "BEN"], size=n, p=[0.7, 0.2, 0.1]), None)
    p["charge_bearer"] = bearer
    fee_sgd = np.array([FEE_SGD[r] for r in rail]) + np.where(bearer == "OUR", hops * 12, 0)
    fee_sgd = np.where(bearer == "BEN", 0, fee_sgd)
    p["fees"] = np.where(out, np.round(fee_sgd / ccy_to_sgd, 2), 0.0)
    deduction_sgd = np.where(np.isin(bearer, ["SHA", "BEN"]), hops * rng.uniform(10, 20, n), 0) + \
        np.where(bearer == "BEN", FEE_SGD["SWIFT_XBORDER"], 0)
    received = np.round(p["amount"] - deduction_sgd / ccy_to_sgd, 2)
    p["amount_received"] = pd.Series(received).where(~unsettled)

    # FX: account currency units per unit of payment currency, including the bank's spread.
    conv = acct_ccy != p["currency_code"].to_numpy()
    mid = ccy_to_sgd / fx.to_sgd(acct_ccy, np.where(unsettled, p["intended_date"], value_local_day))
    spread = np.array([max(spread_bps(cfg, a), spread_bps(cfg, c)) for a, c in zip(acct_ccy, p["currency_code"],
                                                                                  strict=True)]) / 1e4
    p["fx_rate_applied"] = pd.Series(np.round(mid * np.where(out, 1 + spread, 1 - spread), 6)).where(conv)

    # ---- identifiers ----
    p = p.sort_values(["initiated_ts", "entity_id"], kind="stable").reset_index(drop=True)
    p["payment_id"] = [f"P{i:08d}" for i in range(1, len(p) + 1)]
    p["end_to_end_id"] = "E2E" + p["payment_id"].str[1:]
    xb_sorted = p["type_id"].to_numpy() == types.loc["SWIFT_XBORDER", "type_id"]
    uetr_bits = stream(cfg.seed, "lifecycle.uetr").integers(0, 2**63, size=(len(p), 2), dtype=np.int64)
    p["uetr"] = [str(uuid.UUID(int=(int(a) << 64) | int(b), version=4)) if x else None
                 for (a, b), x in zip(uetr_bits, xb_sorted, strict=True)]
    batch_rail = world.dim_payment_type.set_index("type_id").loc[p["type_id"], "is_batch"].to_numpy()
    p["batch_id"] = np.where(
        p["is_batch"].to_numpy() & batch_rail & (p["direction"] == "OUT").to_numpy(),
        p["entity_id"] + "-" + p["type_id"].astype(str) + "-" + p["intended_date"].dt.strftime("%Y%m%d"),
        None)

    p = _mirror_intercompany(p, world, fx)
    truth = p.loc[p["invoice_id"].notna(), ["payment_id", "invoice_id", "amount"]].rename(
        columns={"amount": "allocated_amount"})
    return p[PAYMENT_COLUMNS + ["entity_id", "flow", "invoice_id", "intended_date"]], truth


# ---------------------------------------------------------------- helpers

def _pick_accounts(world: World, p: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    acc = world.dim_account
    ent = world.dim_entity.set_index("entity_id")
    cache: dict[tuple[str, str, str], tuple[str, str]] = {}

    def choose(eid: str, ccy: str, want: str) -> tuple[str, str]:
        a = acc[(acc["entity_id"] == eid) & (acc["account_type"] != "pool_header")]
        if a.empty:  # the in-house bank transacts on its header accounts
            a = acc[acc["entity_id"] == eid]
        for cand in (a[(a["currency_code"] == ccy) & (a["account_type"] == want)], a[a["currency_code"] == ccy],
                     a[(a["account_type"] == "operating")],
                     a[a["currency_code"] == ent.loc[eid, "functional_currency"]], a):
            if not cand.empty:
                row = cand.iloc[0]
                return row["account_id"], row["currency_code"]
        raise ValueError(f"no account for {eid}")

    keys = list(zip(p["entity_id"], p["currency_code"],
                    np.where(p["flow"] == "payroll", "payroll",
                             np.where(p["direction"] == "IN", "collection", "disbursement")), strict=True))
    ids, ccys = [], []
    for k in keys:
        if k not in cache:
            cache[k] = choose(*k)
        ids.append(cache[k][0])
        ccys.append(cache[k][1])
    return np.array(ids), np.array(ccys)


def _pick_rail(p: pd.DataFrame, amount_sgd: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    n = len(p)
    local = np.where(p["direction"] == "OUT", p["sender_country"], p["receiver_country"])
    domestic = (p["sender_country"] == p["receiver_country"]).to_numpy()
    urgent = p["is_urgent"].to_numpy() | ((p["direction"] == "IN").to_numpy() & (rng.random(n) < 0.3))
    big = amount_sgd >= 1_000_000
    tax = (p["flow"] == "tax").to_numpy()
    rail = np.select(
        [local == "SG", np.isin(local, ["DE", "NL"]), local == "GB", local == "US", local == "CN", local == "IN"],
        [np.where(tax | big, "MEPS_RTGS", np.where(urgent & (amount_sgd < 200_000), "FAST", "GIRO")),
         np.where(urgent & (amount_sgd < 150_000), "SEPA_INST", "SEPA_CT"),
         np.full(n, "BACS"),
         np.where(urgent | big | tax, "FEDWIRE", "ACH"),
         np.full(n, "CNAPS"),
         np.full(n, "NEFT")],
        default="SWIFT_XBORDER")
    rail = np.where(domestic, rail, "SWIFT_XBORDER")
    rail = np.where(p["is_intercompany"].to_numpy() & domestic, "BOOK_TRANSFER", rail)
    return np.where(p["flow"] == "card_spend", "CARD", rail)


def _cutoff_minutes(cfg: SimulationConfig, rail: str) -> int:
    c = cfg.rails[rail].cut_off_local
    if not c:
        return 24 * 60
    h, m = c.split(":")
    return int(h) * 60 + int(m)


def _draw_reasons(rng: np.random.Generator, mask: np.ndarray, channel: np.ndarray, out: np.ndarray,
                  high_risk: np.ndarray, dq: np.ndarray) -> np.ndarray:
    idx = np.flatnonzero(mask)
    w = np.tile(REJECT_WEIGHTS, (len(idx), 1))
    col = {r: i for i, r in enumerate(REJECT_REASONS)}
    file_ch = np.isin(channel[idx], ["LEGACY_FILE", "H2H_FILE"])
    w[:, col["FF01"]] *= np.where(file_ch, 3.0, 0.3)
    w[:, col["AM04"]] *= np.where(out[idx], 1.0, 0.0)
    w[:, col["AG01"]] *= np.where(high_risk[idx], 15.0, 1.0)
    w[:, col["RR04"]] *= np.where(high_risk[idx], 10.0, 1.0)
    for r in ("AC01", "RR03", "BE04", "RC01"):
        w[:, col[r]] *= np.where(dq[idx] < 0.8, 1.5, 1.0)
    w /= w.sum(axis=1, keepdims=True)
    u = rng.random(len(idx))[:, None]
    return np.array(REJECT_REASONS)[(w.cumsum(axis=1) < u).sum(axis=1)]


def _local_to_utc(local: np.ndarray, tz: np.ndarray) -> np.ndarray:
    local = np.asarray(local, dtype="datetime64[s]")
    out = np.empty(len(local), dtype="datetime64[s]")
    for z in np.unique(tz):
        m = tz == z
        s = pd.DatetimeIndex(local[m]).tz_localize(z, ambiguous="NaT", nonexistent="shift_forward")
        s = s.tz_convert("UTC").tz_localize(None)
        # Ambiguous DST hour: fall back to naive + standard offset of the first valid neighbour.
        vals = s.values.astype("datetime64[s]")
        bad = np.isnat(vals)
        if bad.any():
            vals[bad] = pd.DatetimeIndex(local[m][bad] + np.timedelta64(1, "h")).tz_localize(
                z, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC").tz_localize(None).values
        out[m] = vals
    return out


def _utc_to_local(utc: np.ndarray, tz: np.ndarray) -> np.ndarray:
    utc = np.asarray(utc, dtype="datetime64[s]")
    out = np.empty(len(utc), dtype="datetime64[s]")
    for z in np.unique(tz):
        m = tz == z
        out[m] = pd.DatetimeIndex(utc[m]).tz_localize("UTC").tz_convert(z).tz_localize(None).values
    return out


def _date_id(days: np.ndarray) -> np.ndarray:
    return pd.DatetimeIndex(days).strftime("%Y%m%d").astype(int).to_numpy()


def _mirror_intercompany(p: pd.DataFrame, world: World, fx: Fx) -> pd.DataFrame:
    """Add the receiving entity's IN leg for every intercompany payment that settled.

    Both legs share the end_to_end_id. Group-level money-movement totals must therefore
    filter is_intercompany (or direction) to avoid double counting - a real treasury gotcha.
    """
    cp = world.dim_counterparty.set_index("counterparty_id")
    ic_for = (world.dim_counterparty[world.dim_counterparty["counterparty_type"] == "intercompany"]
              .groupby("home_entity_id")["counterparty_id"].first())
    src = p[p["is_intercompany"] & p["status"].isin(["completed", "delayed", "returned"])]
    mirror = src.copy()
    mirror["direction"] = "IN"
    mirror["entity_id"] = cp.loc[src["counterparty_id"], "home_entity_id"].to_numpy()
    mirror["counterparty_id"] = ic_for.loc[src["entity_id"]].to_numpy()
    mirror["channel"] = "INBOUND"
    mirror["fees"] = 0.0
    mirror["batch_id"] = None
    mirror["account_id"], acct_ccy = _pick_accounts(world, mirror)
    value_day = pd.to_datetime(mirror["value_date_id"].astype(str), format="%Y%m%d").to_numpy()
    mid = fx.to_sgd(mirror["currency_code"], value_day) / fx.to_sgd(acct_ccy, value_day)
    mirror["fx_rate_applied"] = pd.Series(np.round(mid, 6), index=mirror.index).where(
        acct_ccy != mirror["currency_code"].to_numpy())  # in-house conversion at mid, no spread
    mirror["amount_sgd"] = np.round(mirror["amount_sgd"] * mirror["amount_received"] / mirror["amount"], 2)
    mirror["amount"] = mirror["amount_received"]
    mirror["payment_id"] = [f"P{i:08d}" for i in range(len(p) + 1, len(p) + len(mirror) + 1)]
    return pd.concat([p, mirror], ignore_index=True)
