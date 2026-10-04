"""Stage 6 - run the whole pipeline end to end.

    python -m treasury.pipeline.run --config config/simulation.small.yaml

This file is already written: it only calls your functions in order. Until a function is
filled in, the run stops at it with NotImplementedError telling you which one is next.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

from treasury.pipeline import clean, dq_checks, extract, load, score
from treasury.simulator.config import load_config


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Raw -> DQ checks -> clean -> clean database")
    ap.add_argument("--config", default="config/simulation.yaml")
    args = ap.parse_args(argv)

    out = load_config(args.config).output
    url = out.clean_db_url
    engine = create_engine(url)

    # Reference data: what "valid" means comes from the dimensions, not hard-coded lists.
    currencies = set(pd.read_sql("SELECT currency_code FROM dim_currency", engine)["currency_code"])
    countries = set(pd.read_sql("SELECT country_code FROM dim_country", engine)["country_code"])

    print("1/5 extract")
    raw = extract.read_raw(Path(out.raw_dir), "payments")
    events = extract.read_raw(Path(out.raw_dir), "payment_events")
    print(f"    {len(raw):,} payment rows, {len(events):,} event rows")

    print("2/5 checks")
    flags = dq_checks.run_all_checks(raw, currencies, countries)
    print(flags["check_name"].value_counts().to_string())

    print("3/5 clean")
    good, quarantine = clean.clean_payments(raw, events, currencies, countries)
    print(f"    {len(good):,} clean, {len(quarantine):,} quarantined")

    print("4/5 load")
    load.write_table(good, "pipeline_payments_cleaned", url)
    load.write_table(quarantine, "pipeline_quarantine", url)
    load.write_table(flags, "pipeline_dq_flags", url)
    log = pd.DataFrame([{
        "run_ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows_in": len(raw), "rows_clean": len(good), "rows_quarantined": len(quarantine),
    }])
    log.to_sql("pipeline_run_log", engine, if_exists="append", index=False)

    print("5/5 score (against data/answer_key - the answer key)")
    labels = pd.read_parquet(Path(out.answer_key_dir) / "dq_defects.parquet")
    print(score.score_detection(flags, labels).to_string(index=False))
    correct_payments = pd.read_sql("SELECT * FROM fact_payment", engine)
    print(score.score_repairs(good, correct_payments, flags).to_string(index=False))


if __name__ == "__main__":
    main()
