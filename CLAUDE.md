# CLAUDE.md

Treasury payments analytics portfolio project. Source lesson plan is in Notion:
"8. Project: Treasury Payments Analytics Platform (Blueprint)" and "8.1 The Nine Analyses".

## Commands
- Setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,api]"`
- Full data build: `.venv/bin/treasury-sim backfill` (~2 min). Dimensions/FX only: `treasury-sim build-world`
- Live mode: `.venv/bin/treasury-sim stream --days 7 --speed 300` (`--speed 0` = no pacing, `--webhook URL`)
- camt.053: `.venv/bin/treasury-sim export-camt053 --account A001 --date 2026-09-15`
- Small profile (~106k payments, builds in ~20s, writes to `data_small/`): add `--config config/simulation.small.yaml` to any command. `export-all` rebuilds the Excel workbook, CSVs (`data_small/exports/`) and `docs/dataset/`; it runs automatically after backfill and during stream. Tests against it: `TREASURY_CONFIG=config/simulation.small.yaml .venv/bin/pytest`.
- Tests: `.venv/bin/pytest` (add `-m "not contract"` to skip the ~1 min full-size contract checks) · Lint: `.venv/bin/ruff check src tests`
- The clean database is rebuilt from scratch (tables dropped and recreated) on every build.

## Current scope
The cleaning pipeline (`src/treasury/pipeline/`) is parked on purpose and must not be built or required. Work starts from the clean database (`clean/treasury.sqlite`): SQL analyses, then dashboards, then the API. The raw-data notebook (`notebooks/01_explore_raw_data.ipynb`) is finished.

## Data folders (never use the old names landing / warehouse / truth)
- `raw/`: Parquet files as received; the payments files carry planted DQ defects. Config key `raw_dir`.
- `clean/treasury.sqlite`: the clean database (the data warehouse). Config key `clean_db_url`.
- `answer_key/`: what was planted (`dq_defects`, `business_anomalies`, `payment_to_invoice`, `statement_line_to_payment`, `payment_business_flow`). Config key `answer_key_dir`. Only scoring code reads it.
- Pipeline output tables in the clean database are prefixed `pipeline_`.

## Rules
- Synthetic data only. Use fictional banks and BICs, and never add real client data.
- Each engine gets its own RNG via `treasury.simulator.rng.stream(seed, "<engine>")`. Never share a Generator between engines.
- The answer key (anomaly and DQ labels, true invoice allocations) goes to `data/answer_key/`, never into the clean database.
- DQ defects go into the raw files only. Business anomalies are real money and do post to the ledger.
- Timestamps are UTC. Business dates and cut-offs use the entity's timezone plus `dim_calendar`.
- Every new config field must be validated in `simulator/config.py`, and every schema change must go in `sql/schema/`.
- `docs/dataset-design-review.md` maps each analysis to the fields it needs. Keep it in sync with the schema.
- `tests/test_analysis_contracts.py` checks that each analysis's planted pattern exists. When tuning the simulator, keep these passing rather than loosening them.
- Intercompany payments have two legs (OUT and IN) that share `end_to_end_id`. Group totals must filter `is_intercompany`.
- The lifecycle computes full outcomes. Anything time-dependent (pending status, what's visible) belongs in `asof.py`, never in the engines. That is what keeps backfill and stream identical.
- Internal simulation columns start with `_` and must never reach the clean database (`asof.public`, snapshot drops them).
- Ledger postings are rounded to cents. Same-timestamp ordering is set by `ledger.balances.PRIORITY` (sweeps before snapshots).
