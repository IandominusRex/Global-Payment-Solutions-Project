# CLAUDE.md

Treasury payments analytics portfolio project. Source lesson plan is in Notion:
"8. Project: Treasury Payments Analytics Platform (Blueprint)" and "8.1 The Nine Analyses".

## Commands
- Setup: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev,api]"`
- Full data build: `.venv/bin/treasury-sim backfill` (~1 min). Dimensions/FX only: `treasury-sim build-world`
- Tests: `.venv/bin/pytest` (add `-m "not contract"` to skip the ~30s full-size contract checks) · Lint: `.venv/bin/ruff check src tests`
- The warehouse is rebuilt from scratch (tables dropped and recreated) on every build.

## Rules
- Synthetic data only. Use fictional banks and BICs, and never add real client data.
- Each engine gets its own RNG via `treasury.simulator.rng.stream(seed, "<engine>")`. Never share a Generator between engines.
- Ground truth (anomaly and DQ labels, true invoice allocations) goes to `data/truth/`, never into the warehouse.
- DQ defects go into the raw landing layer only. Business anomalies are real money and do post to the ledger.
- Timestamps are UTC. Business dates and cut-offs use the entity's timezone plus `dim_calendar`.
- Every new config field must be validated in `simulator/config.py`, and every schema change must go in `sql/schema/`.
- `docs/dataset-design-review.md` maps each analysis to the fields it needs. Keep it in sync with the schema.
- `tests/test_analysis_contracts.py` checks that each analysis's planted pattern exists. When tuning the simulator, keep these passing rather than loosening them.
- Intercompany payments have two legs (OUT and IN) that share `end_to_end_id`. Group totals must filter `is_intercompany`.
