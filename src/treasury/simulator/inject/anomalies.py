"""Business anomalies (v4): suspicious but valid payments, injected BEFORE the lifecycle.

They are real money movements, so they go through the same lifecycle and ledger as
everything else. Each injected payment carries `anomaly_type`, which becomes the hidden
label table data/answer_key/business_anomalies.parquet (never loaded into the clean database).

Types (rates in config.anomalies, relative to the number of payment intents):
  duplicate_payment        same beneficiary and amount again, minutes later
  structuring              a series just below the reporting threshold
  round_amount             suspiciously round amounts
  burst                    many payments to one beneficiary within an hour
  high_risk_country        payments to counterparties in high-risk countries
  new_beneficiary_high_value  a brand-new beneficiary receives a large payment
  off_hours                initiated at night or on a weekend
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from faker import Faker

from treasury.simulator.business.events import Fx
from treasury.simulator.config import SimulationConfig
from treasury.simulator.rng import stream
from treasury.simulator.world.builder import World

ANOMALY_TYPES = ["duplicate_payment", "structuring", "round_amount", "burst", "high_risk_country",
                 "new_beneficiary_high_value", "off_hours"]


def inject(cfg: SimulationConfig, world: World, fx: Fx, intents: pd.DataFrame) -> tuple[pd.DataFrame, World]:
    rng = stream(cfg.seed, "inject.anomalies")
    a = cfg.anomalies
    n = len(intents)
    intents = intents.assign(anomaly_type=None, local_minute=np.nan)
    ent = world.dim_entity.set_index("entity_id")
    cp = world.dim_counterparty
    suppliers = cp.loc[cp["counterparty_type"] == "supplier", "counterparty_id"].to_numpy()
    base = intents[(intents["direction"] == "OUT") & intents["flow"].isin(["ap_payment", "urgent_supplier"])]
    days = intents.loc[intents["flow"] == "ap_payment", "intended_date"].to_numpy()
    entities = ent.index[~ent["is_in_house_bank"]].to_numpy()
    new_rows = []

    def new_payment(entity, counterparty, amount_sgd, day, kind, minute=np.nan, ref=None):
        ccy = ent.loc[entity, "functional_currency"]
        amount = amount_sgd / fx.to_sgd(np.array([ccy]), np.array([day]))[0]
        new_rows.append({"flow": "ap_payment", "direction": "OUT", "entity_id": entity,
                         "counterparty_id": counterparty, "amount": round(float(amount), 2), "currency_code": ccy,
                         "intended_date": day, "invoice_id": None, "is_intercompany": False, "is_urgent": False,
                         "is_batch": False, "purpose_code": "SUPP", "remittance_ref": ref, "anomaly_type": kind,
                         "local_minute": minute})

    # duplicate_payment: resend an existing supplier payment minutes after the original.
    dup = base.sample(int(n * a.duplicate_payment_rate), random_state=rng.integers(2**31))
    minute = rng.uniform(9 * 60, 16 * 60, len(dup))
    intents.loc[dup.index, "local_minute"] = minute
    copies = dup.assign(anomaly_type="duplicate_payment", invoice_id=None,
                        local_minute=minute + rng.uniform(2, 30, len(dup)), is_batch=False)
    new_rows.extend(copies.drop(columns="intent_id").to_dict("records"))

    # structuring: series of 3 payments just below the threshold to one beneficiary.
    thr = a.structuring_threshold_sgd
    for _ in range(max(1, int(n * a.structuring_rate / 3))):
        e, c, d = rng.choice(entities), rng.choice(suppliers), rng.choice(days)
        for k in range(3):
            new_payment(e, c, thr * rng.uniform(0.90, 0.995), d + np.timedelta64(int(k * rng.integers(1, 3)), "D"),
                        "structuring", ref=f"PAYMENT {int(rng.integers(1000, 9999))}")

    # round_amount
    for _ in range(int(n * a.round_amount_rate)):
        amt = float(rng.choice([5_000, 10_000, 20_000, 25_000, 50_000]))
        e, c, d = rng.choice(entities), rng.choice(suppliers), rng.choice(days)
        ccy = ent.loc[e, "functional_currency"]
        new_payment(e, c, amt * fx.to_sgd(np.array([ccy]), np.array([d]))[0], d, "round_amount",
                    ref="ADVANCE PAYMENT")

    # burst: 5-12 payments to one beneficiary inside an hour.
    for _ in range(int(n * a.burst_rate)):
        e, c, d = rng.choice(entities), rng.choice(suppliers), rng.choice(days)
        start = rng.uniform(9 * 60, 16 * 60)
        for _ in range(int(rng.integers(5, 13))):
            new_payment(e, c, rng.lognormal(np.log(8_000), 0.5), d, "burst", minute=start + rng.uniform(0, 60))

    # high_risk_country
    risky = cp.loc[cp["country"].isin(cfg.high_risk_countries), "counterparty_id"].to_numpy()
    for _ in range(int(n * a.high_risk_country_share) if len(risky) else 0):
        new_payment(rng.choice(entities), rng.choice(risky), rng.lognormal(np.log(15_000), 0.8), rng.choice(days),
                    "high_risk_country")

    # new_beneficiary_high_value: create the beneficiary, then pay it a large amount at once.
    n_new = int(n * a.new_beneficiary_high_value_rate)
    fake = Faker("en_US")
    fake.seed_instance(cfg.seed)
    countries = [c for c, v in cfg.countries.items() if v.risk_rating != "high"]
    new_cps = []
    for i in range(n_new):
        cid = f"C9{i + 1:03d}"
        country = str(rng.choice(countries))
        new_cps.append({"counterparty_id": cid, "name": fake.company(), "counterparty_type": "supplier",
                        "country": country, "risk_rating": cfg.countries[country].risk_rating,
                        "avg_days_late": 0.0, "data_quality_score": float(rng.uniform(0.85, 1.0)),
                        "uses_virtual_account": False, "home_entity_id": str(rng.choice(entities)),
                        "first_seen_date": pd.NaT})
        new_payment(new_cps[-1]["home_entity_id"], cid, rng.lognormal(np.log(120_000), 0.5), rng.choice(days),
                    "new_beneficiary_high_value")

    # off_hours: move existing payments to the night or a weekend.
    off = base.drop(index=dup.index).sample(int(n * a.off_hours_rate), random_state=rng.integers(2**31))
    night = rng.random(len(off)) < 0.5
    intents.loc[off.index, "local_minute"] = np.where(night, rng.uniform(60, 300, len(off)),
                                                      rng.uniform(9 * 60, 17 * 60, len(off)))
    weekend = pd.to_datetime(off["intended_date"]) + pd.to_timedelta(
        (5 - pd.to_datetime(off["intended_date"]).dt.dayofweek) % 7, unit="D")
    intents.loc[off.index[~night], "intended_date"] = weekend[~night].to_numpy()
    intents.loc[off.index, ["anomaly_type", "is_batch"]] = ["off_hours", False]  # invoices still get paid

    injected = pd.DataFrame(new_rows)
    start = int(intents["intent_id"].max()) + 1
    injected["intent_id"] = np.arange(start, start + len(injected))
    injected["intended_date"] = pd.to_datetime(injected["intended_date"])
    out = pd.concat([intents, injected[intents.columns]], ignore_index=True)

    if new_cps:
        world.dim_counterparty = pd.concat([world.dim_counterparty, pd.DataFrame(new_cps)], ignore_index=True)
    return out, world
