"""Data-quality checks run on raw landing batches before loading.

Each check returns the offending rows so the pipeline can quarantine them and
the report can be compared against data/truth/label_dq.parquet.
"""

from __future__ import annotations

import pandas as pd


def check_nulls(df: pd.DataFrame, required: list[str]) -> pd.DataFrame:
    return df[df[required].isna().any(axis=1)]


def check_currency_codes(df: pd.DataFrame, allowed: set[str], col: str = "currency_code") -> pd.DataFrame:
    # Validate against the MODELLED set, not all ISO 4217 codes: "SDG" is a real code
    # (Sudanese pound) but is still wrong here.
    return df[~df[col].isin(allowed)]


def check_negative_amounts(df: pd.DataFrame, col: str = "amount") -> pd.DataFrame:
    return df[df[col] < 0]


def check_time_order(df: pd.DataFrame) -> pd.DataFrame:
    s = df["submitted_ts"].notna() & (df["submitted_ts"] < df["initiated_ts"])
    t = df["settled_ts"].notna() & (df["settled_ts"] < df["initiated_ts"])
    return df[s | t]


def check_exact_duplicates(df: pd.DataFrame, key: str = "payment_id") -> pd.DataFrame:
    return df[df.duplicated(subset=[key], keep="first")]
