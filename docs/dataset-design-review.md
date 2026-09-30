# Dataset design review

Review of the synthetic-data design proposed in the Claude Desktop chat, checked against
the project blueprint (lesson 8) and the nine analyses (lesson 8.1).

**Verdict:** the design is sound and fits the project. It is event-driven (invoices lead to
payments, which post to the ledger), it keeps an append-only event log, and it stores
anomaly labels as hidden ground truth. Because of that, balances reconcile to payments,
and analyses 5, 8 and 9 have real signal to find.

The review found **6 blocking issues** and **10 smaller gaps**. All are fixed in this repo:
in `config/simulation.yaml`, `sql/schema/*.sql` and the engine docstrings. The build
order is also moved up (see the end of this page).

## Blocking issues (fixed)

| # | Issue | Why it matters | Fix in repo |
|---|---|---|---|
| B1 | **Entities and currencies don't agree.** It proposes 12 entities (HK, JP, VN, MY, AU…) but only 6 currencies. A VN entity pays local payroll in VND, which isn't modelled. | Either the FX exposure (7) and balances (6) are wrong, or the currency list grows past the blueprint's 5–6. | There are 11 entities, all in SG/CN/IN/DE/NL/GB/US, so every functional currency is one of the 6. VN, MY, ID, JP, AU, AE and two fictional high-risk countries are **counterparty-only** countries that settle in USD, which matches real Asian trade invoicing. The config validator enforces this. |
| B2 | **Dirty data and ledger truth are mixed up.** DQ defects (duplicate rows, negative amounts) are listed alongside business anomalies, but there is no rule for which layer they live in. | If a re-sent-file duplicate posts to the ledger, balances stop reconciling. Analyses 5 and 6 then run on corrupted numbers, and the cleaning pipeline has no right answer to check against. | Two layers. **Business anomalies** are injected *before* the ledger, because they are real money. **DQ defects** are injected *after*, into raw landing files only. Both are labelled in `data/truth/`. |
| B3 | **Reconciliation has no ground truth.** Statement lines and remittance quality are designed, but nothing records which invoice a payment *actually* paid. | You can compute an auto-match *rate* but not its *accuracy*. A matcher that confidently mismatches looks good. | The true payment → invoice allocation is written to `data/truth/` (see `recon/statements.py`). |
| B4 | **Circular dependency in intercompany funding.** Funding is "triggered when a subsidiary's forecast goes short", but the forecast is what analysis 5 builds. | The data would depend on the model being evaluated, and forecast accuracy would be partly self-fulfilling. | Funding is triggered by a **rule** on projected balances (balance + scheduled AP, payroll and tax over the next N business days). |
| B5 | **STP and repair can't be measured.** Analysis 3 needs STP rate, manual-repair share and a breakdown by cut-off. The design's `fact_payment` has no field for any of these. It also mentions a "legacy file" penalty with no channel column. | You can't compute analysis 3 KPIs from the table. | `fact_payment` gets `channel`, `is_stp`, `repair_count` and `missed_cutoff`. There is a `REPAIRED` lifecycle state and a `HELD` state for sanctions review. |
| B6 | **One `is_business_day` per date** (blueprint `dim_date`). | 1 Oct is a holiday in CN but not in SG. Cut-offs, value dates and the forecast all need per-country business days. | `dim_calendar(date_id, country_code, is_business_day, holiday_name)` bridge table, built from the `holidays` library. |

## Smaller gaps (fixed or noted)

| # | Gap | Fix |
|---|---|---|
| G1 | 18 months of backfill gives 13-week forecasting only one annual cycle, so there is no year-on-year seasonality to validate against. | `backfill_months: 24`. |
| G2 | The "estimated benefit of pooling" (6) needs interest rates, which the design doesn't have. | `credit_rate` / `debit_rate` per account in `dim_account`, set in config. |
| G3 | The corridor needs both ends. The blueprint only has `payer_account_id` and cannot represent incoming receipts. | `direction` (OUT/IN), `account_id` (our side), and `sender_country` / `receiver_country` stored on the row. |
| G4 | `"SDG"` is proposed as an invalid currency code, but it is a **real ISO code** (Sudanese pound), so an ISO-4217 check would pass it. | Defects use `"usd"`, `"SGP"` and `"EUR "`. `dq_checks.check_currency_codes` validates against the *modelled* set. |
| G5 | Real bank names or BICs would make a public portfolio repo look like it used real data. | Fictional banks, with BICs in a `…ZZ` pattern. |
| G6 | Faker company names can collide with real companies. | Acceptable for synthetic data. The README states that all names are generated. |
| G7 | Which FX fixing does `amount_sgd` use? The design doesn't say. | The value-date fixing. Weekends carry Friday forward (`is_published_fixing = false`), so there are no gaps. |
| G8 | Live mode must still be reproducible. | The clock only paces emission and never affects what is generated (`clock.py`). |
| G9 | Streaming writes while Power BI and the API read. | SQLite in WAL mode. The SQLAlchemy URL switches to Postgres. |
| G10 | *Found by the tests:* weighting counterparty countries by `high_risk_country_share` (0.01) left **zero** high-risk counterparties, so the high-risk rule in analysis 9 had nothing to fire on. | A separate `high_risk_counterparty_share: 0.03` (about 15 counterparties), guarded by `test_counterparties_cover_analysis_needs`. |

## Can each analysis actually be computed?

Each row below becomes an assertion in `tests/test_analysis_contracts.py`, so "the data is
viable" is checked by tests.

| # | Analysis | Required fields / tables | Planted pattern | Viability check |
|---|---|---|---|---|
| 1 | Money movement | `fact_payment.amount_sgd`, `sender_country`, `receiver_country`, `currency_code`, `dim_entity` | SG↔CN is the dominant corridor, EUR inflows | Top corridor ≥ 15% of value |
| 2 | Cross-border | `fact_payment_event` hops, `settled_ts - initiated_ts`, status by corridor/rail | SG→VN slow, US→IN high failure | ≥ 200 payments on each flagged corridor. Its P90 / failure rate is at least 1.5× the median |
| 3 | Efficiency | `is_stp`, `repair_count`, `missed_cutoff`, `channel`, timestamps | Sanctions holds and cut-off misses create a long tail. Legacy-file channel has lower STP | P90/P50 settle time ≥ 3. STP(LEGACY_FILE) < STP(API) |
| 4 | Failures | `status`, `failure_reason` → `dim_failure_reason.category`, `counterparty_id` | About 3 reasons ≈ 80%. 8 repeat-offender suppliers | Top 3 reasons ≥ 70% of failures |
| 5 | Forecast | `fact_balance`, `fact_invoice` (due dates), `dim_calendar` | Weekly and monthly seasonality, trend, late payers | ≥ 24 months of history. Weekly seasonality is detectable |
| 6 | Concentration | `fact_balance`, `dim_account.is_pooled`, `overdraft_limit`, rates, `fact_sweep` | Idle INR cash while DE is overdrawn | At least one day has idle cash > overdraft in the same group |
| 7 | FX exposure | Flows where `currency_code ≠ entity.functional_currency`, `fact_fx_rate`, `fact_fx_hedge` | Long EUR, short CNY, partially hedged | Net EUR > 0 and net CNY < 0 in SGD terms |
| 8 | Reconciliation | `fact_statement_line`, `fact_invoice`, truth allocation | Virtual-account payers match better | VA match rate − free-text match rate ≥ 20 pp |
| 9 | Anomalies | `fact_payment`, `dim_counterparty.first_seen_date`, truth labels | 8 labelled anomaly types | ≥ 100 labels per type across the backfill |

## Volume sanity check

At 1,500 payments per business day, 24 months comes to about 780k payments and about
4.5M events (roughly 6 per payment). That is fine for SQLite (a few hundred MB) and for
Power BI import mode.

A pure-Python heap simulation handles this in minutes, provided handlers stay
vectorised where possible (e.g. drawing a whole day's amounts at once). If backfill gets
slow, the fallback is to lower `payments_per_business_day` for development and use the
full volume for the final build.

## Things that are *not* issues (kept as designed)

- The event log plus current-state table (`fact_payment_event` + `fact_payment`)
- Seeded randomness and a UTC timestamp convention
- The ISO 20022-style reason codes, UETR, and OUR/SHA/BEN charge bearer
- Parquet landing files as the first streaming sink (it matches the Part 1 Pandas pipeline)

## Build order

The world builder, calendar and FX engine are **implemented** in this repo, not just
designed (`treasury-sim build-world`).

1. **v1:** business events, a simple lifecycle and Parquet output. Unblocks SQL (Part 2) and dashboards (Part 3).
2. **v2:** full state machine, cut-offs, holidays, ledger, sweeps.
3. **v3:** live clock plus sinks (files → DB → webhooks).
4. **v4:** anomaly/DQ injection with labels, statement lines, optional ISO 20022 XML.
