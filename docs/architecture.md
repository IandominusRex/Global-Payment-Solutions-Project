# Architecture

```
                     config/simulation.yaml  (validated by simulator/config.py)
                                  │
   ┌──────────────────────────────┴───────────────────────────────┐
   │ Part 1a  SIMULATOR  (src/treasury/simulator)                 │
   │  world/  ─ dimensions, calendar                 [implemented]│
   │  market/ ─ FX fixings                           [implemented]│
   │  business/ ─ invoices, payroll, AP runs, tax, card     [v1]  │
   │  lifecycle/ ─ payment state machine, rails, cut-offs   [v2]  │
   │  ledger/ ─ value-date posting, EOD balances, sweeps    [v2]  │
   │  recon/ ─ bank statement lines                         [v4]  │
   │  inject/ ─ anomalies (pre-ledger), DQ defects (post)   [v4]  │
   │  engine.py + clock.py ─ discrete-event loop, live mode [v1/3]│
   └───────────────┬───────────────────────────┬──────────────────┘
                   │ raw, dirty batches        │ hidden labels
                   ▼                           ▼
            data/landing/*.parquet        data/truth/*.parquet   (never loaded)
                   │
   ┌───────────────┴──────────────────┐
   │ Part 1b  PIPELINE (src/treasury/pipeline) │  DQ checks → quarantine → clean → load
   └───────────────┬──────────────────┘
                   ▼
   ┌──────────────────────────────────┐
   │ Part 2  WAREHOUSE  sql/schema/   │  star schema, SQLite (WAL) or Postgres
   │         sql/analyses/  01…09     │  one SQL file per analysis
   └──────┬─────────────────┬─────────┘
          ▼                 ▼
   Part 3 dashboards/   Part 4 src/treasury/api  (FastAPI, bearer token)
   (Power BI / web BI)      ▲
          └─────────────────┘  dashboards may read KPIs from the API
          src/treasury/analytics: forecasting (5) and anomaly models (9) in Python
```

## Principles

1. **Simulate the business, derive the tables.** Payments come from invoices and schedules;
   balances come only from settled payments. Nothing is generated independently.
2. **One engine, two clocks.** Backfill and live stream run identical code; the clock only
   paces emission. Same seed → same data.
3. **Independent RNG streams per engine** (`rng.stream(seed, name)`), so changing one engine
   does not reshuffle the others.
4. **Truth is hidden.** Anomaly labels, DQ labels and true invoice allocations live in
   `data/truth/` and are used only for scoring.
5. **Dirty data lives only in the raw layer.** The ledger is always clean and reconciles.
6. **UTC everywhere**, with each entity's timezone for local business dates and cut-offs.

## Business flows (v1)

| Flow | Schedule | Amount shape | Rails |
|---|---|---|---|
| Customer receipts (AR) | invoice due date + payer lateness | log-normal | SEPA_CT, ACH, FAST, SWIFT |
| Supplier payments (AP) | weekly runs (Tue/Thu) | log-normal, heavy tail | GIRO, SEPA_CT, CNAPS, SWIFT |
| Payroll | fixed day per country, holiday-shifted | stable, slow growth | GIRO, BACS, NEFT, ACH |
| Intercompany funding | rule on projected balance | round-ish | MEPS_RTGS, SWIFT, BOOK_TRANSFER |
| Urgent supplier | random, small share | medium | FAST, SEPA_INST |
| Tax | monthly / quarterly per country | large, predictable | RTGS / domestic |
| Card spend | daily, weekday-heavy | many small | CARD (T+2) |
| Sweeps | nightly | balance to zero/target | BOOK_TRANSFER |

Seasonality: month- and quarter-end spikes, Monday peaks, Chinese New Year dip for CN,
per-country holidays from `dim_calendar`, and `annual_growth` as the trend.
