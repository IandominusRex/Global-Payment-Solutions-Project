# Treasury Payments Analytics Platform

A portfolio project for Global Payments Solutions. It simulates a multinational
group's treasury: 11 entities, about 50 accounts, 6 currencies, and domestic,
instant and cross-border rails. From that simulation it builds a star-schema
warehouse, nine treasury analyses, dashboards, and a bank-style FastAPI service.

> **All data is synthetic.** Entities, banks, BICs, counterparties and names are
> generated. No real client or employer data is used.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,api]"
treasury-sim backfill             # 24 months of history -> data/warehouse/treasury.sqlite (~2 min)
treasury-sim stream --days 7      # then keep going live: 5 simulated minutes per real second
treasury-sim export-camt053 --account A001 --date 2026-09-15   # ISO 20022 bank statement XML
pytest                            # add -m "not contract" to skip the ~1 min full-size data checks
```

## Layout

| Path | Part | What |
|---|---|---|
| `config/simulation.yaml` | 1 | Every simulation parameter. Change a number, change the story. |
| `src/treasury/simulator/` | 1a | Event-driven generator (world, FX, business events, lifecycle, ledger, recon, injectors) |
| `src/treasury/pipeline/` | 1b | Raw landing → DQ checks → clean → warehouse |
| `sql/schema/` | 2 | Star schema (SQLite / Postgres) |
| `sql/analyses/` | 2 | One query file per analysis (01–09) |
| `src/treasury/analytics/` | 2/3 | Forecasting (5) and anomaly models (9) |
| `dashboards/` | 3 | BI files and screenshots |
| `src/treasury/api/` | 4 | FastAPI: balances, payment status, KPIs, forecast, exceptions |
| `docs/` | – | Architecture and the dataset design review |
| `data/` | – | Generated output (git-ignored, reproducible from seed): `warehouse/`, `landing/` (monthly Parquet), `truth/` (hidden answers) |

## The nine analyses

1. Money movement · 2. Cross-border · 3. Payment efficiency · 4. Failures ·
5. Liquidity forecast · 6. Cash concentration · 7. FX exposure · 8. Reconciliation ·
9. Anomaly detection

See [docs/dataset-design-review.md](docs/dataset-design-review.md) for how the data
guarantees each analysis has something to find.

## Status

- [x] Repo, config schema, star schema DDL
- [x] World builder, per-country calendar, FX engine (`treasury-sim build-world`)
- [x] v1: business events, invoices and simple lifecycle → Parquet landing + warehouse (`treasury-sim backfill`)
- [x] v2: lifecycle event trail, ledger (AM04, funding, concentration, ZBA sweeps), daily balances
- [x] v3: live stream on a simulated clock (warehouse upserts, Parquet micro-batches, webhooks)
- [x] v4: labelled anomalies, DQ defects in raw files, bank statements, hedges, multi-invoice/short payments, camt.053 XML
- [ ] Part 1b cleaning pipeline · Part 2 SQL analyses · Part 3 dashboards · Part 4 API

## What's in the data

| Table | Rows (default build) | Notes |
|---|---|---|
| `fact_payment` | ~850k | current state per payment (both legs for intercompany) |
| `fact_payment_event` | ~3.5M | CREATED → … → SETTLED / REJECTED / RETURNED, with gpi hops |
| `fact_invoice` | ~576k | AR/AP: paid, part_paid, open, written_off |
| `fact_statement_line` | ~1.2M | the bank's view (camt.053): truncated refs, charges lines |
| `fact_balance` | 36.5k | closing balance per account per day |
| `fact_sweep` | ~6k | nightly ZBA and same-entity funding transfers |
| `fact_fx_hedge` | ~1k | monthly forwards against last month's exposure |
| `fact_fx_rate` | ~4.9k | daily fixings to SGD |
