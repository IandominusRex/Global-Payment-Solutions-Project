"""Stage 3 - clean: fix what can be fixed, quarantine what can't.

The golden rule: **only repair a value when you can prove the right answer.** If you guess,
the clean database looks clean but is silently wrong, which is worse than a quarantined row that
someone can chase.

Decision table (fill in the last column yourself as you go; it becomes your write-up):

| Defect            | Repair?     | How                                                 | Why / evidence |
|-------------------|-------------|-----------------------------------------------------|----------------|
| duplicate         | drop copy   | keep the first row per payment_id                   |                |
| country_code      | yes         | lookup map "China"/"PRC"/"cn" -> "CN"               |                |
| negative_amount   | yes         | abs(amount) - direction already says OUT or IN      |                |
| time_order        | yes         | settled_ts from the SETTLED event in payment_events |                |
| currency_code     | yes         | lookup map "usd"/"US$" -> "USD", "EUR " -> "EUR"    |                |
| null_purpose_code | no          | keep the row, leave it null, keep the flag          |                |

Every function below takes the raw DataFrame and returns a NEW DataFrame (never modify the
input in place - it makes notebook re-runs confusing). `df = df.copy()` at the top.
"""

from __future__ import annotations

import pandas as pd

# Fill this in from what you find in the notebook (value_counts of sender_country).
# Keys are the messy values, values are the dim_country code.
COUNTRY_MAP: dict[str, str] = {
    # "China": "CN",
    # "PRC": "CN",
    # "UK": "GB",
}

# Same idea for currency codes (value_counts of currency_code in the notebook).
CURRENCY_MAP: dict[str, str] = {
    # "usd": "USD",
    # "US$": "USD",
}


def drop_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the first row per payment_id.

    Tutorial:
      1. The re-sent files repeat a payment_id that already arrived. The first copy is the
         real one; the rest are the same payment sent twice by the source system.
      2. `df.drop_duplicates(subset=["payment_id"], keep="first")`.
      3. Question for your write-up: why is dropping on payment_id safe here, but dropping on
         (counterparty, amount, date) would be dangerous? (Hint: analysis 8 has a
         "duplicate_payment" anomaly - a *real* second payment that did post to the ledger.)
    """
    raise NotImplementedError("clean.drop_duplicates")


def standardise_countries(df: pd.DataFrame, allowed: set[str]) -> pd.DataFrame:
    """Map messy country names to 2-letter codes in sender_country and receiver_country.

    Tutorial:
      1. Fill COUNTRY_MAP above from the notebook.
      2. For each column: `df[col] = df[col].replace(COUNTRY_MAP)`.
         Also try `.str.strip().str.upper()` first - that alone fixes "cn".
      3. Anything still not in `allowed` after mapping can't be fixed: leave it, and
         `clean_payments` will send the row to quarantine.
    """
    raise NotImplementedError("clean.standardise_countries")


def fix_negative_amounts(df: pd.DataFrame) -> pd.DataFrame:
    """Flip negative amounts to positive.

    Tutorial:
      1. In this dataset money direction lives in the `direction` column (OUT / IN), so
         `amount` should always be positive. A minus sign is an export bug, not information.
      2. `df["amount"] = df["amount"].abs()`.
      3. Check: does `amount_sgd` also go negative on those rows? Look in the notebook before
         deciding whether to fix it too.
    """
    raise NotImplementedError("clean.fix_negative_amounts")


def repair_settled_ts(df: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Replace impossible settled_ts values with the time of the SETTLED event.

    Tutorial:
      1. The event feed (raw/payment_events) is a second, independent record of each
         payment's life. When the two disagree, the event log wins: it is append-only.
      2. Build a lookup: events with status == "SETTLED", one row per payment_id,
         `events[events.status == "SETTLED"].groupby("payment_id")["event_ts"].max()`.
      3. Find the bad rows: settled_ts < initiated_ts (or < submitted_ts).
      4. For those rows only: `df.loc[bad, "settled_ts"] = df.loc[bad, "payment_id"].map(lookup)`.
      5. Rows where the lookup finds nothing stay bad -> quarantine.
    """
    raise NotImplementedError("clean.repair_settled_ts")


def standardise_currencies(df: pd.DataFrame, allowed: set[str]) -> pd.DataFrame:
    """Map misspelled currency codes ("usd", "US$", "EUR ", ...) to the real ISO code.

    Tutorial:
      1. Fill CURRENCY_MAP above from the notebook (value_counts of currency_code).
      2. `df["currency_code"] = df["currency_code"].replace(CURRENCY_MAP)`.
      3. Any code still not in `allowed` after the map stays as it is -> quarantine.
    """
    raise NotImplementedError("clean.standardise_currencies")


def clean_payments(
    df: pd.DataFrame,
    events: pd.DataFrame,
    currencies: set[str],
    countries: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply every repair in order, then split into (clean, quarantine).

    Tutorial:
      1. Chain the repairs:
             df = drop_duplicates(df)
             df = standardise_countries(df, countries)
             df = fix_negative_amounts(df)
             df = repair_settled_ts(df, events)
             df = standardise_currencies(df, currencies)
      2. Decide which rows are still unfixable. A row goes to quarantine if ANY of:
         currency_code not in currencies, sender/receiver not in countries,
         settled_ts < initiated_ts.
         (Null purpose_code does NOT quarantine: the payment is valid, just incomplete.)
      3. quarantine = df[bad].assign(quarantine_reason=...) - say *why* per row, so an ops
         person could fix it at source.
      4. Return (df[~bad], quarantine).

    Check: clean + quarantine should equal 106,206 rows (the real payments). If it doesn't,
    something was dropped or duplicated.
    """
    raise NotImplementedError("clean.clean_payments")
