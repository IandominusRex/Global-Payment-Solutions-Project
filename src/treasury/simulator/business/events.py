"""Business Event Engine (v1): what causes money to move.

Invoices first, payments second. Each flow produces *payment intents*: who pays whom,
how much, in which currency, and on which date the payer initiates. The lifecycle
engine then decides how each intent travels (rail, timing, outcome).

Flows (see docs/architecture.md):
  ar_receipt       customers pay AR invoices around the due date (+ their lateness)
  ap_payment       supplier invoices paid in the entity's Tue/Thu payment run before due
  urgent_supplier  supplier invoices paid the same day on an instant rail
  card_spend       many small corporate card payments
  payroll          fixed pay day per country, rolled back over holidays
  tax              monthly, with larger quarterly instalments
  ic_funding       HQ funds subsidiaries monthly (round amounts)
  ic_repatriation  subsidiaries send surplus cash back to HQ quarterly

Intercompany funding here is *scheduled*, not triggered by a forecast (see
docs/dataset-design-review.md, B4). v2 adds the balance-driven top-up rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from treasury.simulator.config import SimulationConfig
from treasury.simulator.rng import stream
from treasury.simulator.world.builder import World
from treasury.simulator.world.calendar import BusinessCalendar

# (median, sigma) of the log-normal amount in SGD, before growth.
AMOUNT_SGD = {
    "ar_receipt": (22_000, 1.25),
    "ap_payment": (16_000, 1.35),
    "urgent_supplier": (35_000, 0.9),
    "card_spend": (180, 1.0),
}
PAYROLL_DAY = {"SG": 25, "CN": 10, "IN": 31, "DE": 25, "NL": 25, "GB": 31, "US": 31}  # 31 = month end
PAYMENT_RUN_WEEKDAYS = (1, 3)  # Tuesday, Thursday
DOMESTIC_WEIGHT = 12.0
REF_QUALITY = ["exact", "truncated", "typo", "missing"]
REF_QUALITY_P_FREE_TEXT = [0.55, 0.20, 0.10, 0.15]

INTENT_COLUMNS = [
    "flow", "direction", "entity_id", "counterparty_id", "amount", "currency_code",
    "intended_date", "invoice_id", "is_intercompany", "is_urgent", "is_batch",
    "purpose_code", "remittance_ref",
]


@dataclass
class Fx:
    """Daily rate_to_sgd lookup by (currency, date) for the simulated window."""

    start: np.datetime64
    rates: dict[str, np.ndarray]

    @classmethod
    def from_frame(cls, fx: pd.DataFrame) -> Fx:
        days = pd.to_datetime(fx["date_id"].astype(str), format="%Y%m%d").values.astype("datetime64[D]")
        start = days.min()
        rates = {}
        for ccy, g in fx.assign(day=days).groupby("currency_code"):
            g = g.sort_values("day")
            rates[ccy] = g["rate_to_sgd"].to_numpy()
        return cls(start=start, rates=rates)

    def to_sgd(self, ccy: np.ndarray, days: np.ndarray) -> np.ndarray:
        ccy = np.asarray(ccy)
        idx = (np.asarray(days, dtype="datetime64[D]") - self.start).astype(int)
        out = np.empty(len(ccy))
        for c in np.unique(ccy):
            m = ccy == c
            r = self.rates[c]
            out[m] = r[np.clip(idx[m], 0, len(r) - 1)]
        return out


def generate(cfg: SimulationConfig, world: World, cal: BusinessCalendar, fx: Fx,
             end: np.datetime64 | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (invoices, payment intents, allocation) for data_start..end (default end_date).

    allocation maps intent_id -> invoice_id(s) with the amount each payment settles;
    it goes into the answer key for reconciliation (analysis 8).
    """
    gen = _Generator(cfg, world, cal, fx, end)
    ar_inv, ar_pay = gen.ar_receipts()
    ap_inv, ap_pay = gen.ap_payments()
    invoices = pd.concat([ar_inv, ap_inv], ignore_index=True)
    intents = pd.concat(
        [ar_pay, ap_pay, gen.card_spend(), gen.payroll(), gen.tax(), gen.intercompany()],
        ignore_index=True,
    )[INTENT_COLUMNS]
    intents = intents[intents["intended_date"] <= gen.end].reset_index(drop=True)
    intents["intent_id"] = np.arange(len(intents))
    has_inv = intents["invoice_id"].notna()
    allocation = pd.DataFrame({"intent_id": intents.loc[has_inv, "intent_id"],
                               "invoice_id": intents.loc[has_inv, "invoice_id"],
                               "allocated_amount": intents.loc[has_inv, "amount"]}).reset_index(drop=True)
    intents, allocation = _customer_payment_habits(cfg, world, intents, allocation)
    return invoices, intents, allocation


SHORT_PAY_RATE = 0.03        # free-text payers who deduct something (disputes, bank charges)
CONSOLIDATE_RATE = 0.12      # free-text payers who pay several invoices in one go


def _customer_payment_habits(cfg: SimulationConfig, world: World, intents: pd.DataFrame,
                             allocation: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Short payments and one-payment-many-invoices from customers without virtual accounts.

    These are what make real reconciliation hard (analysis 8). Virtual-account payers pay
    each invoice exactly, which is the point of virtual accounts.
    """
    rng = stream(cfg.seed, "events.ar_habits")
    va = world.dim_counterparty.set_index("counterparty_id")["uses_virtual_account"]
    ar = (intents["flow"] == "ar_receipt").to_numpy() & ~va.reindex(intents["counterparty_id"]).fillna(False).to_numpy()

    short = ar & (rng.random(len(intents)) < SHORT_PAY_RATE)
    intents.loc[short, "amount"] = np.round(intents.loc[short, "amount"] * rng.uniform(0.90, 0.99, short.sum()), 2)
    alloc_amt = allocation.set_index("intent_id")["allocated_amount"]
    alloc_amt.loc[intents.loc[short, "intent_id"]] = intents.loc[short, "amount"].to_numpy()
    allocation["allocated_amount"] = alloc_amt.to_numpy()

    pick = ar & ~short & (rng.random(len(intents)) < CONSOLIDATE_RATE)
    cand = intents[pick]
    week = pd.to_datetime(cand["intended_date"]).dt.to_period("W").astype(str)
    groups = cand.groupby([cand["entity_id"], cand["counterparty_id"], cand["currency_code"], week])["intent_id"]
    carrier = groups.transform("min")
    size = groups.transform("size")
    merged = cand[size > 1].assign(carrier=carrier[size > 1])
    if len(merged):
        def join_refs(refs: pd.Series) -> str:
            return "; ".join(r for r in refs if isinstance(r, str))

        agg = merged.groupby("carrier").agg(amount=("amount", "sum"), intended_date=("intended_date", "max"),
                                            refs=("remittance_ref", join_refs))
        idx = intents.set_index("intent_id")
        idx.loc[agg.index, "amount"] = agg["amount"].round(2)
        idx.loc[agg.index, "intended_date"] = agg["intended_date"]
        idx.loc[agg.index, "remittance_ref"] = agg["refs"].str.slice(0, 140).replace("", None)
        idx.loc[agg.index, "invoice_id"] = None
        dropped = merged.loc[merged["intent_id"] != merged["carrier"], "intent_id"]
        intents = idx.drop(index=dropped).reset_index()
        remap = merged.set_index("intent_id")["carrier"]
        allocation["intent_id"] = allocation["intent_id"].map(remap).fillna(allocation["intent_id"]).astype(int)
    return intents[INTENT_COLUMNS + ["intent_id"]], allocation


class _Generator:
    def __init__(self, cfg: SimulationConfig, world: World, cal: BusinessCalendar, fx: Fx,
                 end: np.datetime64 | None = None):
        self.cfg, self.world, self.cal, self.fx = cfg, world, cal, fx
        self.ent = world.dim_entity.set_index("entity_id")
        self.cp = world.dim_counterparty.set_index("counterparty_id")
        self.start = np.datetime64(cfg.data_start)
        self.end = np.datetime64(end if end is not None else cfg.end_date, "D")
        self.origin = np.datetime64(cfg.start_date)
        # Fixed "size" per counterparty so a few customers/suppliers dominate volume.
        self.cp_size = pd.Series(stream(cfg.seed, "events.cp_size").lognormal(0, 1.0, len(self.cp)),
                                 index=self.cp.index)
        total_size = sum(e.size for e in cfg.entities)
        self.entity_share = {e.entity_id: e.size / total_size for e in cfg.entities}

    # ---------- shared helpers ----------

    def _growth(self, days: np.ndarray) -> np.ndarray:
        years = (np.asarray(days, dtype="datetime64[D]") - self.origin).astype(int) / 365.25
        return (1 + self.cfg.volumes.annual_growth) ** years

    def _season(self, country: str, days: np.ndarray) -> np.ndarray:
        d = pd.DatetimeIndex(days)
        m = np.array([1.15, 1.05, 1.0, 1.0, 0.9, 0.0, 0.0])[d.dayofweek]
        days_left = (d + pd.offsets.MonthEnd(0) - d).days
        month_end = days_left < 3
        quarter_month = np.asarray(d.month % 3 == 0)
        m = m * np.where(month_end & quarter_month, 1.4, np.where(month_end, 1.25, 1.0))
        m = m * np.where((d.month == 12) & (d.day >= 20), 0.8, 1.0)
        if country == "CN":
            m = m * np.where(self._near_spring_festival(d), 0.3, 1.0)
        return m

    def _near_spring_festival(self, d: pd.DatetimeIndex) -> np.ndarray:
        import holidays
        years = sorted(set(d.year))
        cn = holidays.country_holidays("CN", years=years)
        festival = [pd.Timestamp(k) for k, v in cn.items() if "Spring Festival" in v or "Chinese New Year" in v]
        out = np.zeros(len(d), dtype=bool)
        for f in festival:
            out |= (d >= f - pd.Timedelta(days=2)) & (d <= f + pd.Timedelta(days=10))
        return out

    def _daily_counts(self, rng: np.random.Generator, share: float, bias_sign: int = 0) -> pd.DataFrame:
        """Poisson counts per (entity, business day) for a flow with the given volume share.

        bias_sign +1 (receipts) / -1 (payments) applies each entity's net_bias.
        """
        base = self.cfg.volumes.payments_per_business_day * share
        frames = []
        for e in self.cfg.entities:
            days = self.cal.business_days(e.country)
            days = days[(days >= self.start) & (days <= self.end)]
            lam = base * self.entity_share[e.entity_id] * self._growth(days) * self._season(e.country, days)
            lam = lam * (1 + bias_sign * e.net_bias)
            n = rng.poisson(lam)
            frames.append(pd.DataFrame({"entity_id": e.entity_id, "day": np.repeat(days, n)}))
        return pd.concat(frames, ignore_index=True)

    def _pick_counterparties(self, rng: np.random.Generator, entity_ids: np.ndarray, cp_type: str) -> np.ndarray:
        pool = self.cp[self.cp["counterparty_type"] == cp_type]
        out = np.empty(len(entity_ids), dtype=object)
        for eid in np.unique(entity_ids):
            e_country = self.ent.loc[eid, "country"]
            w = self.cp_size[pool.index].to_numpy() * np.where(pool["home_entity_id"] == eid, 6.0, 1.0)
            # Most trade is domestic; cross-border is the minority that matters (analysis 2).
            w = w * np.where(pool["country"] == e_country, DOMESTIC_WEIGHT, 1.0)
            if cp_type == "customer":
                # Planted story: European customers pay SG and US entities in EUR (long EUR, analysis 7).
                w = w * np.where(np.isin(e_country, ["SG", "US", "GB"]) & pool["country"].isin(["DE", "NL"]),
                                 4.0, 1.0)
                w = w * np.where((e_country == "CN") & (pool["country"] == "SG"), 4.0, 1.0)
            if cp_type == "supplier":
                # Planted stories: SG sources heavily from CN (dominant corridor, short CNY),
                # US buys services from IN (feeds the high-failure corridor).
                w = w * np.where((e_country == "SG") & (pool["country"] == "CN"), 10.0, 1.0)
                w = w * np.where((e_country == "US") & (pool["country"] == "IN"), 3.0, 1.0)
                w = w * np.where((e_country == "SG") & (pool["country"] == "VN"), 3.0, 1.0)
            m = entity_ids == eid
            out[m] = rng.choice(pool.index.to_numpy(), size=m.sum(), p=w / w.sum())
        return out

    def _invoice_currency(self, entity_ids: np.ndarray, cp_ids: np.ndarray, is_ap: bool) -> np.ndarray:
        e_country = self.ent.loc[entity_ids, "country"].to_numpy()
        e_ccy = self.ent.loc[entity_ids, "functional_currency"].to_numpy()
        c_country = self.cp.loc[cp_ids, "country"].to_numpy()
        c_ccy = np.array([self.cfg.countries[c].settle_ccy for c in c_country])
        restricted = np.array([self.cfg.currencies[c].is_restricted for c in c_ccy])
        # Foreign customers in restricted-currency countries pay in USD. CN suppliers still
        # invoice CNY cross-border (CNH), which gives the group its short-CNY position.
        foreign_ccy = np.where(restricted & ~(is_ap & (c_ccy == "CNY")), "USD", c_ccy)
        return np.where(e_country == c_country, e_ccy, foreign_ccy)

    def _amounts(self, rng: np.random.Generator, flow: str, days: np.ndarray, ccy: np.ndarray) -> np.ndarray:
        median, sigma = AMOUNT_SGD[flow]
        sgd = rng.lognormal(np.log(median), sigma, len(days)) * self._growth(days)
        return np.round(sgd / self.fx.to_sgd(ccy, days), 2)

    # ---------- flows ----------

    def ar_receipts(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        rng = stream(self.cfg.seed, "events.ar")
        inv = self._daily_counts(rng, self.cfg.volumes.flow_mix.ar_receipt, bias_sign=+1)
        inv["counterparty_id"] = self._pick_counterparties(rng, inv["entity_id"].to_numpy(), "customer")
        inv["currency_code"] = self._invoice_currency(inv["entity_id"].to_numpy(), inv["counterparty_id"].to_numpy(),
                                                      is_ap=False)
        days = inv["day"].to_numpy()
        inv["amount"] = self._amounts(rng, "ar_receipt", days, inv["currency_code"].to_numpy())
        terms = rng.choice([30, 45, 60], size=len(inv), p=[0.5, 0.3, 0.2])
        inv["due"] = days + terms.astype("timedelta64[D]")
        inv = inv.sort_values(["day", "entity_id"], kind="stable").reset_index(drop=True)
        days = inv["day"].to_numpy()
        inv["invoice_id"] = [f"AR{i:07d}" for i in range(1, len(inv) + 1)]
        inv["invoice_ref"] = inv["entity_id"].str.cat(inv["invoice_id"].str[2:], sep="-").radd("INV-")

        # Payment date: due date + the customer's habitual lateness, on the payer's business day.
        late = self.cp.loc[inv["counterparty_id"], "avg_days_late"].to_numpy()
        delay = np.where(late > 0, rng.normal(late, 4), rng.normal(-1, 2)).round().astype(int)
        pay_day = inv["due"].to_numpy() + delay.astype("timedelta64[D]")
        pay_day = np.maximum(pay_day, days + np.timedelta64(1, "D"))
        payer_country = self.cp.loc[inv["counterparty_id"], "country"].to_numpy()
        pay_day = self.cal.roll_forward(payer_country, pay_day)
        never_paid = rng.random(len(inv)) < 0.015
        paid = ~never_paid & (pay_day <= self.end)

        va = self.cp.loc[inv["counterparty_id"], "uses_virtual_account"].to_numpy()
        quality = np.where(va, "exact", rng.choice(REF_QUALITY, size=len(inv), p=REF_QUALITY_P_FREE_TEXT))
        refs = _degrade_refs(inv["invoice_ref"].to_numpy(), quality, rng)

        pay = pd.DataFrame({
            "flow": "ar_receipt", "direction": "IN",
            "entity_id": inv["entity_id"], "counterparty_id": inv["counterparty_id"],
            "amount": inv["amount"], "currency_code": inv["currency_code"],
            "intended_date": pay_day, "invoice_id": inv["invoice_id"],
            "is_intercompany": False, "is_urgent": False, "is_batch": False,
            "purpose_code": rng.choice(["GDDS", "SCVE"], size=len(inv), p=[0.7, 0.3]),
            "remittance_ref": refs,
        })[paid]
        return _invoice_frame(inv, "AR"), pay

    def ap_payments(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        rng = stream(self.cfg.seed, "events.ap")
        mix = self.cfg.volumes.flow_mix
        inv = self._daily_counts(rng, mix.ap_payment + mix.urgent_supplier, bias_sign=-1)
        inv["is_urgent"] = rng.random(len(inv)) < mix.urgent_supplier / (mix.ap_payment + mix.urgent_supplier)
        inv["counterparty_id"] = self._pick_counterparties(rng, inv["entity_id"].to_numpy(), "supplier")
        inv["currency_code"] = self._invoice_currency(inv["entity_id"].to_numpy(), inv["counterparty_id"].to_numpy(),
                                                      is_ap=True)
        days = inv["day"].to_numpy()
        amt_regular = self._amounts(rng, "ap_payment", days, inv["currency_code"].to_numpy())
        amt_urgent = self._amounts(rng, "urgent_supplier", days, inv["currency_code"].to_numpy())
        inv["amount"] = np.where(inv["is_urgent"], amt_urgent, amt_regular)
        terms = np.where(inv["is_urgent"], 0, rng.choice([30, 60], size=len(inv), p=[0.6, 0.4]))
        inv["due"] = days + terms.astype("timedelta64[D]")
        inv = inv.sort_values(["day", "entity_id"], kind="stable").reset_index(drop=True)
        days = inv["day"].to_numpy()
        inv["invoice_id"] = [f"AP{i:07d}" for i in range(1, len(inv) + 1)]
        inv["invoice_ref"] = inv["counterparty_id"] + "/" + pd.Series(
            rng.integers(10_000, 999_999, len(inv))).astype(str)

        # Regular invoices go into the last Tue/Thu payment run before the due date.
        e_country = self.ent.loc[inv["entity_id"], "country"].to_numpy()
        pay_day = np.empty(len(inv), dtype="datetime64[D]")
        for cc in np.unique(e_country):
            runs = self.cal.business_days(cc)
            runs = runs[np.isin(pd.DatetimeIndex(runs).dayofweek, PAYMENT_RUN_WEEKDAYS)]
            m = e_country == cc
            target = inv.loc[m, "due"].to_numpy() - np.timedelta64(1, "D")
            idx = np.searchsorted(runs, target, side="right") - 1
            chosen = runs[np.clip(idx, 0, len(runs) - 1)]
            # Due too soon for a run before it: take the first run after the invoice arrives.
            first_after = runs[np.clip(np.searchsorted(runs, days[m], side="left"), 0, len(runs) - 1)]
            pay_day[m] = np.where(chosen < days[m], first_after, chosen)
        urgent = inv["is_urgent"].to_numpy()
        pay_day[urgent] = self.cal.roll_forward(e_country[urgent], days[urgent])

        pay = pd.DataFrame({
            "flow": np.where(urgent, "urgent_supplier", "ap_payment"), "direction": "OUT",
            "entity_id": inv["entity_id"], "counterparty_id": inv["counterparty_id"],
            "amount": inv["amount"], "currency_code": inv["currency_code"],
            "intended_date": pay_day, "invoice_id": inv["invoice_id"],
            "is_intercompany": False, "is_urgent": urgent, "is_batch": ~urgent,
            "purpose_code": "SUPP", "remittance_ref": inv["invoice_ref"],
        })
        return _invoice_frame(inv, "AP"), pay

    def card_spend(self) -> pd.DataFrame:
        rng = stream(self.cfg.seed, "events.card")
        c = self._daily_counts(rng, self.cfg.volumes.flow_mix.card_spend)
        ccy = self.ent.loc[c["entity_id"], "functional_currency"].to_numpy()
        return pd.DataFrame({
            "flow": "card_spend", "direction": "OUT",
            "entity_id": c["entity_id"],
            "counterparty_id": self._pick_counterparties(rng, c["entity_id"].to_numpy(), "supplier"),
            "amount": self._amounts(rng, "card_spend", c["day"].to_numpy(), ccy),
            "currency_code": ccy, "intended_date": c["day"], "invoice_id": None,
            "is_intercompany": False, "is_urgent": False, "is_batch": False,
            "purpose_code": "GDDS", "remittance_ref": None,
        })

    def _month_days(self, day_of_month: int) -> np.ndarray:
        months = pd.date_range(self.cfg.data_start, pd.Timestamp(self.end), freq="MS")
        return np.array([(m + pd.offsets.MonthEnd(0)) if day_of_month >= 28 else m + pd.Timedelta(days=day_of_month - 1)
                         for m in months], dtype="datetime64[D]")

    def payroll(self) -> pd.DataFrame:
        rng = stream(self.cfg.seed, "events.payroll")
        rows = []
        groups = self.cp[self.cp["counterparty_type"] == "employee_group"]
        for e in self.cfg.entities:
            e_groups = groups.index[groups["home_entity_id"] == e.entity_id].to_numpy()
            split = rng.dirichlet(np.ones(len(e_groups)) * 4)
            monthly_sgd = 450_000 * e.size * self.cfg.volumes.scheduled_scale
            runs = [(PAYROLL_DAY[e.country], 1.0)] if e.country != "US" else [(15, 0.5), (31, 0.5)]
            for dom, frac in runs:
                targets = self._month_days(dom)
                days = self.cal.roll_backward(np.repeat(e.country, len(targets)), targets)
                months = pd.DatetimeIndex(days).month
                bonus = np.where(months == (1 if e.country == "CN" else 12), 1.8, 1.0)
                for g, s in zip(e_groups, split, strict=True):
                    sgd = monthly_sgd * frac * s * bonus * self._growth(days) * rng.normal(1, 0.01, len(days))
                    rows.append(pd.DataFrame({
                        "entity_id": e.entity_id, "counterparty_id": g, "intended_date": days,
                        "amount": np.round(sgd / self.fx.to_sgd(np.repeat(e.functional_ccy, len(days)), days), 2),
                        "currency_code": e.functional_ccy,
                        "remittance_ref": ["SALARY " + str(d)[:7] for d in days],
                    }))
        df = pd.concat(rows, ignore_index=True)
        return df.assign(flow="payroll", direction="OUT", invoice_id=None, is_intercompany=False,
                         is_urgent=False, is_batch=True, purpose_code="SALA")

    def tax(self) -> pd.DataFrame:
        rng = stream(self.cfg.seed, "events.tax")
        rows = []
        authorities = self.cp[self.cp["counterparty_type"] == "tax_authority"]
        for e in self.cfg.entities:
            auth = authorities.index[authorities["home_entity_id"] == e.entity_id][0]
            days = self.cal.roll_forward(np.repeat(e.country, len(self._month_days(15))), self._month_days(15))
            quarterly = np.isin(pd.DatetimeIndex(days).month, [1, 4, 7, 10])
            base = 90_000 * e.size * self.cfg.volumes.scheduled_scale
            sgd = rng.lognormal(np.log(base), 0.25, len(days)) * np.where(quarterly, 3.0, 1.0)
            sgd = sgd * self._growth(days)
            rows.append(pd.DataFrame({
                "entity_id": e.entity_id, "counterparty_id": auth, "intended_date": days,
                "amount": np.round(sgd / self.fx.to_sgd(np.repeat(e.functional_ccy, len(days)), days), 2),
                "currency_code": e.functional_ccy,
                "remittance_ref": ["TAX " + str(d)[:7] for d in days],
            }))
        df = pd.concat(rows, ignore_index=True)
        return df.assign(flow="tax", direction="OUT", invoice_id=None, is_intercompany=False,
                         is_urgent=False, is_batch=False, purpose_code="TAXS")

    def intercompany(self) -> pd.DataFrame:
        """OUT legs only; the lifecycle engine mirrors settled legs into the receiving entity."""
        rng = stream(self.cfg.seed, "events.ic")
        hq = next(e for e in self.cfg.entities if e.is_in_house_bank)
        ic = self.cp[self.cp["counterparty_type"] == "intercompany"]
        ic_for = {eid: g.index[0] for eid, g in ic.groupby("home_entity_id")}
        rows = []
        for e in self.cfg.entities:
            if e.entity_id == hq.entity_id:
                continue
            ccy = "USD" if self.cfg.currencies[e.functional_ccy].is_restricted else e.functional_ccy
            k = self.cfg.volumes.scheduled_scale
            digits = -5 if k >= 0.5 else -3
            # Monthly funding HQ -> subsidiary on the 5th business day.
            first = self._month_days(1)
            fund_days = self.cal.add_business_days(np.repeat(hq.country, len(first)), first, 4)
            fund = rng.random(len(fund_days)) < 0.6
            sgd = np.round(rng.lognormal(np.log(1_200_000 * e.size * k), 0.5, len(fund_days)), digits)
            rows.append(pd.DataFrame({
                "flow": "ic_funding", "entity_id": hq.entity_id, "counterparty_id": ic_for[e.entity_id],
                "intended_date": fund_days[fund], "currency_code": ccy,
                "amount": _round_amount(
                    sgd[fund] / self.fx.to_sgd(np.repeat(ccy, fund.sum()), fund_days[fund]), digits + 1),
                "remittance_ref": "IC FUNDING " + e.entity_id,
            }))
            # Quarterly repatriation subsidiary -> HQ, in the subsidiary's own country calendar.
            q = self._month_days(1)[pd.DatetimeIndex(self._month_days(1)).month % 3 == 1]
            rep_days = self.cal.add_business_days(np.repeat(e.country, len(q)), q, 9)
            sgd = np.round(rng.lognormal(np.log(1_500_000 * e.size * k), 0.4, len(rep_days)), digits)
            rows.append(pd.DataFrame({
                "flow": "ic_repatriation", "entity_id": e.entity_id, "counterparty_id": ic_for[hq.entity_id],
                "intended_date": rep_days, "currency_code": ccy,
                "amount": _round_amount(sgd / self.fx.to_sgd(np.repeat(ccy, len(rep_days)), rep_days), digits + 1),
                "remittance_ref": "IC REPATRIATION " + e.entity_id,
            }))
        df = pd.concat(rows, ignore_index=True)
        return df.assign(direction="OUT", invoice_id=None, is_intercompany=True, is_urgent=False,
                         is_batch=False, purpose_code="INTC")


def _round_amount(x: np.ndarray, decimals: int = -4) -> np.ndarray:
    return np.round(x, decimals)


def _degrade_refs(refs: np.ndarray, quality: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = refs.astype(object).copy()
    trunc = quality == "truncated"
    out[trunc] = [r[: max(6, len(r) - 4)] for r in refs[trunc]]
    typo = quality == "typo"
    swapped = []
    for r in refs[typo]:
        i = int(rng.integers(4, len(r) - 1))
        swapped.append(r[:i] + r[i + 1] + r[i] + r[i + 2:])
    out[typo] = swapped
    out[quality == "missing"] = None
    return out


def _invoice_frame(inv: pd.DataFrame, direction: str) -> pd.DataFrame:
    return pd.DataFrame({
        "invoice_id": inv["invoice_id"], "direction": direction,
        "entity_id": inv["entity_id"], "counterparty_id": inv["counterparty_id"],
        "invoice_ref": inv["invoice_ref"], "currency_code": inv["currency_code"],
        "amount": inv["amount"], "issue_date": inv["day"].to_numpy(), "due_date": inv["due"].to_numpy(),
    })
