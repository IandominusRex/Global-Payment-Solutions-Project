"""Stage 2 - data-quality checks run on raw files before loading.

Each check returns the offending rows so the pipeline can quarantine them and
the report can be compared against data/answer_key/dq_defects.parquet.

The first five checks are written. Read them, understand them, then fill in the stubs
below them. `run_all_checks` combines everything into one "flags" table:

    payment_id | check_name
    P00008598  | currency_code
    P00008598  | duplicate
    ...

One row per (payment, failed check). A payment can fail several checks.
"""

from __future__ import annotations

import pandas as pd

# Check names used in the flags table. score.py maps these to the answer-key labels.
CHECK_NAMES = (
    "duplicate",
    "currency_code",
    "country_code",
    "negative_amount",
    "time_order",
    "null_purpose_code",
)


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


# --------------------------------------------------------------------------------------
# Your turn
# --------------------------------------------------------------------------------------


def check_country_codes(df: pd.DataFrame, allowed: set[str]) -> pd.DataFrame:
    """Rows where sender_country or receiver_country is not a valid 2-letter code.

    Tutorial:
      1. `allowed` is the set of codes in dim_country (SG, CN, DE, ... plus XA, XB).
      2. Build two boolean masks: `~df["sender_country"].isin(allowed)` and the same for
         receiver_country.
      3. Return `df[mask_sender | mask_receiver]`.

    In the notebook, look at `df["sender_country"].value_counts()` first. You'll see
    "China", "PRC", "cn", "UK"... This check only *detects* them; clean.py maps them back.
    """
    raise NotImplementedError("dq_checks.check_country_codes")


def run_all_checks(df: pd.DataFrame, currencies: set[str], countries: set[str]) -> pd.DataFrame:
    """Run every check and return the long "flags" table: columns payment_id, check_name.

    Tutorial:
      1. Call each check and pair it with its name from CHECK_NAMES, e.g.
             results = {
                 "duplicate": check_exact_duplicates(df),
                 "currency_code": check_currency_codes(df, currencies),
                 ...
             }
         For "null_purpose_code" use check_nulls(df, ["purpose_code"]).
      2. For each (name, bad_rows) build a small frame:
             pd.DataFrame({"payment_id": bad_rows["payment_id"], "check_name": name})
      3. pd.concat them (ignore_index=True) and return.
      4. Tip: keep the row index too (`row_idx = bad_rows.index`). Duplicates share a
         payment_id, so the index is the only way to know *which copy* was flagged.

    Think about: check_time_order only compares against initiated_ts. Is a payment that
    settled *before it was submitted* also wrong? Should the check catch that too?
    """
    raise NotImplementedError("dq_checks.run_all_checks")
