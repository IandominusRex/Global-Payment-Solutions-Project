# Architecture

```
                     config/simulation.yaml  (validated by simulator/config.py)
                                  │
   ┌──────────────────────────────┴────────────────────────────────────────┐
   │ Part 1a  SIMULATOR  (src/treasury/simulator)                          │
   │  world/      dimensions, per-country calendar                         │
   │  market/     FX fixings, monthly hedge book                           │
   │  business/   invoices → payment intents (AR, AP runs, payroll, tax,   │
   │              card, intercompany), multi-invoice and short payments    │
   │  inject/     anomalies (before the lifecycle, labelled)               │
   │  lifecycle/  timing.py: rail, channel, cut-offs, hops, holds, failures│
   │              state_machine.py: the timestamped event trail            │
   │  ledger/     discrete-event replay: AM04, funding, sweeps, balances   │
   │  recon/      bank statement lines (camt.053 view)                     │
   │  asof.py     what is visible at simulated time t                      │
   │  backfill.py run_simulation() → snapshot(t) → sinks                   │
   │  stream.py + clock.py  live mode: successive snapshots on a clock     │
   └──────┬──────────────────────────┬──────────────────────┬──────────────┘
          │ raw batches + DQ defects │ hidden labels         │ clean load / upserts
          ▼                          ▼                       ▼
   data/raw/*.parquet     data/answer_key/*.parquet     Part 2 CLEAN DATABASE (sql/schema/)
          │                   (never loaded)            SQLite (WAL) or Postgres
          ▼                                                  │
   Part 1b PIPELINE (src/treasury/pipeline)  ─ clean ───────►│
                                                             ▼
                                   Part 3 dashboards/   Part 4 src/treasury/api
                                   src/treasury/analytics (forecast 5, anomaly models 9)
```

The simulator writes correct data straight into the clean database, so Parts 2–4 are never blocked.
The raw files carry the same payments with injected defects. Cleaning them in
Part 1b and comparing the result with the clean database and `dq_defects` is the Part 1 exercise.

## Principles

1. **Simulate the business, derive the tables.** Payments come from invoices and schedules.
   Balances come only from settled payments, and statements only from ledger postings.
2. **Compute the future once, then show it as of a moment.** `run_simulation()` decides
   every payment's full outcome. `snapshot(t)` shows only what happened by `t`. A backfill is
   the snapshot at the end of the last day. The live stream takes successive snapshots.
   Both use the same engine, so a stream stopped at `t` equals a backfill to `t` (tested).
3. **Independent RNG streams per engine** (`rng.stream(seed, name)`), so changing one engine
   does not reshuffle the others. The same seed and horizon always give the same data.
4. **The answer key is hidden.** Anomaly labels, DQ labels, true invoice allocations and the
   statement-line → payment link live in `data/answer_key/` and are used only for scoring.
5. **Dirty data lives only in the raw files.** The ledger and the clean database are always clean.
6. **UTC everywhere.** Local business dates, cut-offs and end-of-day use each entity's
   timezone and `dim_calendar`.
7. **Banks book in cents.** Every ledger posting is rounded, so statements reconcile exactly.

## Business flows

| Flow | Schedule | Amount shape | Rails |
|---|---|---|---|
| Customer receipts (AR) | due date + payer lateness. Some pay several invoices at once or short-pay | log-normal | SEPA, ACH, FAST, GIRO, SWIFT |
| Supplier payments (AP) | weekly runs (Tue/Thu) before the due date | log-normal, heavy tail | GIRO, SEPA_CT, CNAPS, ACH, SWIFT |
| Urgent supplier | same day | medium | FAST, SEPA_INST, FEDWIRE |
| Payroll | fixed day per country, rolled back over holidays | stable, bonus month | GIRO, BACS, NEFT, ACH |
| Tax | monthly, quarterly instalments | large, predictable | MEPS_RTGS, FEDWIRE, domestic |
| Card spend | daily, weekday-heavy | many small | CARD (T+2) |
| IC funding / repatriation | monthly / quarterly schedule | round | BOOK_TRANSFER, SWIFT |
| IC top-up (ledger) | 07:00 local, when projected balance falls below buffer | rounded up | BOOK_TRANSFER, SWIFT |
| IC concentration (ledger) | weekly, surplus above 3× target | rounded down | BOOK_TRANSFER, SWIFT |
| ZBA sweeps (ledger) | nightly at entity end-of-day | balance to zero | internal (fact_sweep) |

Seasonality comes from month- and quarter-end spikes, Tue/Thu payment runs, the Chinese New Year
dip for CN, per-country holidays and 8% annual growth.

## Ledger rules (v2)

Events are replayed strictly in time order (`engine.EventLoop`). Known events are pre-sorted
arrays, and events created during the run (top-ups) go on a heap.

1. **Debits** happen at execution: when routed (cross-border) or at settlement (domestic).
   A debit that would breach a non-pooled account's overdraft limit is rejected with AM04.
2. **Funding** runs at 07:00 local on business days. Projected balance = balance − scheduled
   outflows over the next 3 business days. A shortfall is covered by same-entity surplus
   first, then by an HQ top-up. Card and urgent payments aren't scheduled, which is why a
   few still bounce.
3. **Concentration** runs weekly. Surplus goes back to HQ, except restricted currencies
   (CNY, INR) and unpooled entities. That exception is the trapped cash analysis 6 finds.
4. **End of day** has two phases. First every entity sweeps its pooled accounts to the
   header, then every entity snapshots `fact_balance`. Splitting the phases prevents
   same-instant ordering bugs between entities in the same timezone.

## Live stream (v3)

`treasury-sim stream --days 7 --speed 300` simulates `end_date + 7 days`, loads history as of
the clock start, then advances in 5-minute ticks. Each tick it upserts only what changed:
new payments, status changes, events, invoices, balances, sweeps, statement lines, hedges
and FX. It also writes Parquet micro-batches (payments with DQ defects) and can POST
camt.054-style notifications to `--webhook`.

## Outputs

| Layer | Where | Contents |
|---|---|---|
| Clean database (the data warehouse) | `data/clean/treasury.sqlite` | star schema, clean |
| Raw data | `data/raw/{payments,invoices,payment_events,bank_statements}/` | monthly Parquet. Payments contain DQ defects |
| Stream batches | `data/raw/stream/` | per-tick Parquet |
| Answer key | `data/answer_key/` | `payment_to_invoice`, `payment_business_flow`, `business_anomalies`, `dq_defects`, `statement_line_to_payment` |
| ISO 20022 | `data/iso20022/` | camt.053 XML via `treasury-sim export-camt053` |
