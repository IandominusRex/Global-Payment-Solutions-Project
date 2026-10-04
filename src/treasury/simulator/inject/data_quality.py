"""Data-quality defects (v4): dirty data in the raw files only.

Applied to the Parquet files in data/raw/ (and stream micro-batches), never to
the ledger or the clean database, so balances still reconcile and the Part 1 cleaning
pipeline has a known right answer: data/answer_key/dq_defects.parquet.

Selection is a pure function of (seed, defect, payment number), so a payment gets the
same defect whether it lands in a monthly backfill file or a live micro-batch.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from treasury.simulator.config import SimulationConfig

# Each bad currency code is a misspelling of the true code (like the country variants), never a random other
# currency, so a lookup map can always undo it. None of these are real ISO codes (unlike e.g. SDG).
CURRENCY_VARIANTS = {"USD": ["usd", "US$"], "SGD": ["sgd", "SGP"], "EUR": ["eur", "EUR "],
                     "GBP": ["gbp", "GBP "], "CNY": ["cny", "RMB"], "INR": ["inr", "INR "]}
COUNTRY_VARIANTS = {"SG": ["Singapore", "sg", "S'pore"], "CN": ["China", "PRC", "cn"], "US": ["USA", "U.S.", "us"],
                    "DE": ["Germany", "de", "Deutschland"], "GB": ["UK", "United Kingdom", "gb"],
                    "IN": ["India", "in"], "NL": ["Netherlands", "Holland", "nl"]}
DEFECTS = ["null_purpose_code", "bad_currency_code", "negative_amount", "time_order_violation",
           "resent_file_duplicate", "inconsistent_country_name"]


def _draws(cfg: SimulationConfig, payments: pd.DataFrame, defect: str) -> tuple[np.ndarray, np.ndarray]:
    """(selected mask, auxiliary uniform) - stable per payment regardless of batching."""
    num = payments["payment_id"].str[1:].astype(np.int64).to_numpy()
    rng = np.random.default_rng([cfg.seed, 7919, DEFECTS.index(defect)])
    u = rng.random((int(num.max(initial=0)) + 1, 2))
    rate = getattr(cfg.data_quality, f"{defect}_rate")
    return u[num, 0] < rate, u[num, 1]


def label(cfg: SimulationConfig, payments: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d in DEFECTS:
        sel, _ = _draws(cfg, payments, d)
        if d == "time_order_violation":
            sel &= payments["settled_ts"].notna().to_numpy()
        if d == "inconsistent_country_name":
            sel &= payments["sender_country"].isin(COUNTRY_VARIANTS).to_numpy()
        if d == "bad_currency_code":
            sel &= payments["currency_code"].isin(CURRENCY_VARIANTS).to_numpy()
        rows.append(pd.DataFrame({"payment_id": payments.loc[sel, "payment_id"], "defect_type": d}))
    return pd.concat(rows, ignore_index=True)


def apply(cfg: SimulationConfig, payments: pd.DataFrame) -> pd.DataFrame:
    """Return a dirty copy of the payments batch as it would arrive in a raw file."""
    df = payments.copy()
    df["amount"] = df["amount"].astype(float)
    df["currency_code"] = df["currency_code"].astype(object)
    df["sender_country"] = df["sender_country"].astype(object)
    sel, u = _draws(cfg, df, "null_purpose_code")
    df.loc[sel, "purpose_code"] = None
    sel, u = _draws(cfg, df, "bad_currency_code")
    sel &= df["currency_code"].isin(CURRENCY_VARIANTS).to_numpy()
    df.loc[sel, "currency_code"] = [CURRENCY_VARIANTS[c][int(x * len(CURRENCY_VARIANTS[c]))]
                                    for c, x in zip(df.loc[sel, "currency_code"], u[sel], strict=True)]
    sel, _ = _draws(cfg, df, "negative_amount")
    df.loc[sel, "amount"] = -df.loc[sel, "amount"]
    sel, u = _draws(cfg, df, "time_order_violation")
    sel &= df["settled_ts"].notna().to_numpy()
    earlier = df.loc[sel, "initiated_ts"] - pd.to_timedelta(np.round(3600 + u[sel] * 86_400), unit="s")
    df.loc[sel, "settled_ts"] = earlier.astype(df["settled_ts"].dtype)
    sel, u = _draws(cfg, df, "inconsistent_country_name")
    sel &= df["sender_country"].isin(COUNTRY_VARIANTS).to_numpy()
    df.loc[sel, "sender_country"] = [COUNTRY_VARIANTS[c][int(x * len(COUNTRY_VARIANTS[c]))]
                                     for c, x in zip(df.loc[sel, "sender_country"], u[sel], strict=True)]
    sel, _ = _draws(cfg, df, "resent_file_duplicate")
    return pd.concat([df, df[sel]], ignore_index=True)  # a re-sent file repeats rows verbatim
