"""World Builder: creates every static dimension once, deterministically from the seed."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from faker import Faker

from treasury.simulator.config import SimulationConfig
from treasury.simulator.rng import stream
from treasury.simulator.world.reference import FAILURE_REASONS, PURPOSE_CODES

# Which domestic rails an entity country can use, before cross-border.
DOMESTIC_RAILS = {
    "SG": ["FAST", "GIRO", "MEPS_RTGS"],
    "DE": ["SEPA_CT", "SEPA_INST"],
    "NL": ["SEPA_CT", "SEPA_INST"],
    "GB": ["BACS"],
    "US": ["ACH", "FEDWIRE"],
    "CN": ["CNAPS"],
    "IN": ["NEFT"],
}
FAKER_LOCALE = {"SG": "en_US", "CN": "zh_CN", "IN": "en_IN", "DE": "de_DE", "NL": "nl_NL",
                "GB": "en_GB", "US": "en_US", "JP": "ja_JP", "AU": "en_AU"}


@dataclass
class World:
    dim_entity: pd.DataFrame
    dim_bank: pd.DataFrame
    dim_country: pd.DataFrame
    dim_currency: pd.DataFrame
    dim_account: pd.DataFrame
    dim_counterparty: pd.DataFrame
    dim_payment_type: pd.DataFrame
    dim_failure_reason: pd.DataFrame
    dim_purpose_code: pd.DataFrame

    def tables(self) -> dict[str, pd.DataFrame]:
        return dict(vars(self))


def build_world(cfg: SimulationConfig) -> World:
    rng = stream(cfg.seed, "world")
    dim_entity = pd.DataFrame([e.model_dump() for e in cfg.entities]).rename(
        columns={"functional_ccy": "functional_currency"}
    )
    dim_entity["timezone"] = dim_entity["country"].map(lambda c: cfg.countries[c].timezone)

    dim_bank = pd.DataFrame([b.model_dump() for b in cfg.banks])
    dim_country = pd.DataFrame(
        [{"country_code": k, **v.model_dump()} for k, v in cfg.countries.items()]
    ).rename(columns={"settle_ccy": "settlement_currency"})
    dim_currency = pd.DataFrame(
        [{"currency_code": k, "name": v.name, "decimals": v.decimals,
          "is_restricted": v.is_restricted} for k, v in cfg.currencies.items()]
    )

    return World(
        dim_entity=dim_entity,
        dim_bank=dim_bank,
        dim_country=dim_country,
        dim_currency=dim_currency,
        dim_account=_build_accounts(cfg, rng),
        dim_counterparty=_build_counterparties(cfg, rng),
        dim_payment_type=_build_payment_types(cfg),
        dim_failure_reason=pd.DataFrame(FAILURE_REASONS),
        dim_purpose_code=pd.DataFrame(PURPOSE_CODES),
    )


def _build_accounts(cfg: SimulationConfig, rng: np.random.Generator) -> pd.DataFrame:
    banks_by_country: dict[str, list[str]] = {}
    for b in cfg.banks:
        banks_by_country.setdefault(b.country, []).append(b.bank_id)
    global_banks = [b.bank_id for b in cfg.banks if b.is_correspondent]
    header_entity = next(e for e in cfg.entities if e.is_in_house_bank)

    rows, n = [], 0
    for e in cfg.entities:
        k = int(rng.integers(cfg.accounts.per_entity[0], cfg.accounts.per_entity[1] + 1))
        local_banks = banks_by_country.get(e.country, global_banks)
        for i in range(k):
            n += 1
            # First account is the operating account in functional currency;
            # others are a mix of functional and USD/EUR collection accounts.
            if i == 0:
                ccy, acc_type = e.functional_ccy, "operating"
            else:
                ccy = rng.choice([e.functional_ccy, "USD", "EUR"], p=[0.5, 0.3, 0.2])
                acc_type = rng.choice(["collection", "disbursement", "payroll"], p=[0.5, 0.35, 0.15])
            rows.append({
                "account_id": f"A{n:03d}",
                "entity_id": e.entity_id,
                "bank_id": rng.choice(local_banks if i == 0 else local_banks + global_banks),
                "currency_code": str(ccy),
                "account_type": str(acc_type),
                "overdraft_limit": 0.0,
                "target_balance": float(np.round(rng.lognormal(12.5, 0.6), -3)),
                "is_pooled": False,
                "pool_header_account_id": None,
            })
    acc = pd.DataFrame(rows)

    # Pool headers: one per currency, held by the in-house bank.
    headers = {}
    for ccy in acc["currency_code"].unique():
        mask = (acc["entity_id"] == header_entity.entity_id) & (acc["currency_code"] == ccy)
        if mask.any():
            headers[ccy] = acc.loc[mask, "account_id"].iloc[0]
        else:
            n += 1
            aid = f"A{n:03d}"
            acc.loc[len(acc)] = {
                "account_id": aid, "entity_id": header_entity.entity_id,
                "bank_id": global_banks[0], "currency_code": ccy, "account_type": "pool_header",
                "overdraft_limit": 0.0, "target_balance": 0.0, "is_pooled": False,
                "pool_header_account_id": None,
            }
            headers[ccy] = aid

    eligible = (
        ~acc["entity_id"].isin(cfg.accounts.force_unpooled_entities + [header_entity.entity_id])
        & ~acc["currency_code"].map(lambda c: cfg.currencies[c].is_restricted)
    )
    pooled = eligible & (rng.random(len(acc)) < cfg.accounts.pooled_share)
    acc.loc[pooled, "is_pooled"] = True
    acc.loc[pooled, "pool_header_account_id"] = acc.loc[pooled, "currency_code"].map(headers)

    od = acc["entity_id"].isin(cfg.accounts.overdraft_entities) & (acc["account_type"] == "operating")
    acc.loc[od, "overdraft_limit"] = 2_000_000.0

    acc["credit_rate"] = acc["currency_code"].map(cfg.accounts.interest.credit_rate)
    acc["debit_rate"] = acc["currency_code"].map(cfg.accounts.interest.debit_rate)
    # Pool headers first so the self-referencing foreign key is satisfied on insert.
    return acc.sort_values(["is_pooled", "account_id"], kind="stable").reset_index(drop=True)


def _build_counterparties(cfg: SimulationConfig, rng: np.random.Generator) -> pd.DataFrame:
    cp = cfg.counterparties
    types = rng.choice(list(cp.mix), size=cp.count, p=list(cp.mix.values()))

    # Counterparty countries: concentrated where the group trades, plus a high-risk tail
    # that takes exactly `high_risk_counterparty_share` of the probability mass.
    countries = list(cfg.countries)
    boost = {"CN": 4, "SG": 3, "DE": 3, "US": 3, "IN": 2, "GB": 2, "VN": 1.5}
    is_high = np.array([cfg.countries[c].risk_rating == "high" for c in countries])
    weights = np.array([boost.get(c, 1.0) for c in countries])
    weights[~is_high] *= (1 - cp.high_risk_counterparty_share) / weights[~is_high].sum()
    weights[is_high] *= cp.high_risk_counterparty_share / weights[is_high].sum()
    entity_ids = [e.entity_id for e in cfg.entities]

    fakers: dict[str, Faker] = {}
    rows = []
    for i, t in enumerate(types):
        if t == "intercompany":
            ent = cfg.entities[i % len(cfg.entities)]
            country, name = ent.country, f"IC {ent.name}"
        else:
            country = str(rng.choice(countries, p=weights))
            loc = FAKER_LOCALE.get(country, "en_US")
            fk = fakers.setdefault(loc, Faker(loc))
            fk.seed_instance(cfg.seed * 100_003 + i)
            name = {
                "employee_group": f"Payroll {country} {i:03d}",
                "tax_authority": f"Tax Authority {country} {i:03d}",
            }.get(str(t), fk.company())
        rows.append({
            "counterparty_id": f"C{i + 1:04d}",
            "name": name,
            "counterparty_type": str(t),
            "country": country,
            "risk_rating": cfg.countries[country].risk_rating,
            "avg_days_late": float(max(0.0, rng.normal(12, 6)))
            if t == "customer" and rng.random() < cfg.scenarios.late_payer_share else 0.0,
            "data_quality_score": float(np.clip(rng.beta(9, 1), 0, 1)),
            "uses_virtual_account": bool(t == "customer" and rng.random() < cp.virtual_account_share),
            "home_entity_id": str(rng.choice(entity_ids)),
        })
    df = pd.DataFrame(rows)

    # Repeat offenders: a handful of suppliers with bad master data (analysis 4).
    sup = df.index[df["counterparty_type"] == "supplier"]
    bad = rng.choice(sup, size=min(cp.repeat_offender_suppliers, len(sup)), replace=False)
    df.loc[bad, "data_quality_score"] = rng.uniform(0.55, 0.75, size=len(bad))

    # first_seen_date is assigned by the event engine when the first payment occurs.
    df["first_seen_date"] = pd.NaT
    return df


def _build_payment_types(cfg: SimulationConfig) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "type_id": i + 1,
            "rail": name,
            "is_instant": r.is_instant,
            "is_batch": r.is_batch,
            "is_cross_border": name == "SWIFT_XBORDER",
            "cut_off_local": r.cut_off_local,
            "typical_settlement_days": r.settle_days,
            "base_fail_rate": r.fail_rate,
        }
        for i, (name, r) in enumerate(cfg.rails.items())
    ])
