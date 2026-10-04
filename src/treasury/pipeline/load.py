"""Stage 4 - load: write the pipeline's output tables to the clean database.

Tables this pipeline owns (the simulator never touches them):

    pipeline_payments_cleaned   cleaned payments, same columns as fact_payment
    pipeline_quarantine       rows that could not be repaired, plus quarantine_reason
    pipeline_dq_flags            every (payment_id, check_name) the checks raised
    pipeline_run_log          one row per run: timestamp, rows in, rows clean, rows quarantined

Note: `treasury-sim backfill` rebuilds the clean database, so re-run the pipeline after a rebuild.
"""

from __future__ import annotations

import pandas as pd


def write_table(df: pd.DataFrame, name: str, clean_db_url: str) -> int:
    """Replace table `name` with `df`. Returns the number of rows written.

    Tutorial:
      1. Add `from sqlalchemy import create_engine` to the imports, then
         `engine = create_engine(clean_db_url)`. The URL comes from the config, e.g.
         "sqlite:///data_small/clean/treasury.sqlite". Same code works for Postgres.
      2. Drop internal columns first: `df = df.drop(columns=[c for c in df.columns if c.startswith("_")])`.
         (Keep `_source_file` for pipeline_quarantine though - lineage is useful there. Your call:
         rename it to source_file for that table.)
      3. `df.to_sql(name, engine, if_exists="replace", index=False, chunksize=10_000)`.
         "replace" makes the pipeline idempotent: running it twice gives the same result.
      4. Return len(df).

    Check afterwards in DBeaver:  SELECT COUNT(*) FROM pipeline_payments_cleaned;
    """
    raise NotImplementedError("load.write_table")

