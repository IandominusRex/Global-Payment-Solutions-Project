"""Stage 1 - extract: read raw files into one DataFrame per feed.

Raw data layout (one folder per feed, one Parquet file per month):

    data_small/raw/payments/payments_2024-10.parquet
    data_small/raw/payments/payments_2024-11.parquet
    ...

Feeds: "payments", "payment_events", "invoices", "bank_statements".
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

FEEDS = ("payments", "payment_events", "invoices", "bank_statements")


def read_raw(raw_dir: Path, feed: str) -> pd.DataFrame:
    """Read every monthly file of one feed and stack them into a single DataFrame.

    Tutorial:
      1. Check `feed` is one of FEEDS; raise ValueError otherwise (a typo should fail loudly,
         not return an empty table).
      2. Find the files: `sorted((raw_dir / feed).glob(f"{feed}_*.parquet"))`.
         Sorting keeps months in order, so row order is stable between runs.
      3. If no files were found, raise FileNotFoundError with the folder path in the message.
      4. Read each with `pd.read_parquet(path)` and add a column `_source_file = path.name`.
         Lineage: when a row is quarantined you can say which file it came from.
         (Columns starting with "_" are internal and must never reach the fact_* tables.)
      5. `pd.concat(frames, ignore_index=True)` and return.

    Check: for "payments" on the small profile you should get 106,621 rows
    (106,206 real payments + 415 re-sent duplicates).
    """
    raise NotImplementedError("extract.read_raw: see the tutorial in the docstring")
