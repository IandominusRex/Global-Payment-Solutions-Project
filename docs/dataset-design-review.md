# Dataset design review

Review of the synthetic-data design proposed in the Claude Desktop chat, checked against
the project blueprint (lesson 8) and the nine analyses (lesson 8.1).

**Verdict:** the design is sound and fits the project. It is event-driven (invoices lead to
payments, which post to the ledger), it keeps an append-only event log, and it stores
anomaly labels in a separate answer key. Because of that, balances reconcile to payments,
and analyses 5, 8 and 9 have real signal to find.

The review found **6 blocking issues** and **10 smaller gaps**. All are fixed in this repo:
in `config/simulation.yaml`, `sql/schema/*.sql` and the engine docstrings. The build
order is also moved up (see the end of this page).

## Blocking issues (fixed)

| # | Issue | Why it matters | Fix in repo |
|---|---|---|---|
| B1 | **Entities and currencies don't agree.** It proposes 12 entities (HK, JP, VN, MY, AU…) but only 6 currencies. A VN entity pays local payroll in VND, which isn't modelled. | Either the FX exposure (7) and balances (6) are wrong, or the currency list grows past the blueprint's 5–6. | There are 11 entities, all in SG/CN/IN/DE/NL/GB/US, so every functional currency is one of the 6. VN, MY, ID, JP, AU, AE and two fictional high-risk countries are **counterparty-only** countries that settle in USD, which matches real Asian trade invoicing. The config validator enforces this. |
| B2 | **Dirty data and correct ledger data are mixed up.** DQ defects (duplicate rows, negative amounts) are listed alongside business anomalies, but there is no rule for which layer they live in. | If a re-sent-file duplicate posts to the ledger, balances stop reconciling. Analyses 5 and 6 then run on corrupted numbers, and the cleaning pipeline has no right answer to check against. | Two layers. **Business anomalies** are injected *before* the ledger, because they are real money. **DQ defects** are injected *after*, into raw files only. Both are labelled in `data/answer_key/`. |
| B3 | **Reconciliation has no answer key.** Statement lines and remittance quality are designed, but nothing records which invoice a payment *actually* paid. | You can compute an auto-match *rate* but not its *accuracy*. A matcher that confidently mismatches looks good. | The true payment → invoice allocation is written to `data/answer_key/` (see `recon/statements.py`). |
| B4 | **Circular dependency in intercompany funding.** Funding is "triggered when a subsidiary's forecast goes short", but the forecast is what analysis 5 builds. | The data would depend on the model being evaluated, and forecast accuracy would be partly self-fulfilling. | Funding is triggered by a **rule** on projected balances (balance + scheduled AP, payroll and tax over the next N business days). |
| B5 | **STP and repair can't be measured.** Analysis 3 needs STP rate, manual-repair share and a breakdown by cut-off. The design's `fact_payment` has no field for any of these. It also mentions a "legacy file" penalty with no channel column. | You can't compute analysis 3 KPIs from the table. | `fact_payment` gets `channel`, `is_stp`, `repair_count` and `missed_cutoff`. There is a `REPAIRED` lifecycle state and a `HELD` state for sanctions review. |
| B6 | **One `is_business_day` per date** (blueprint `dim_date`). | 1 Oct is a holiday in CN but not in SG. Cut-offs, value dates and the forecast all need per-country business days. | `dim_calendar(date_id, country_code, is_business_day, holiday_name)` bridge table, built from the `holidays` library. |

## Smaller gaps (fixed or noted)

| # | Gap | Fix |
|---|---|---|
| G1 | 18 months of backfill gives 13-week forecasting only one annual cycle, so there is no year-on-year seasonality to validate against. | `backfill_months: 24`. |
| G2 | The "estimated benefit of pooling" (6) needs interest rates, which the design doesn't have. | `credit_rate` / `debit_rate` per account in `dim_account`, set in config. |
| G3 | The corridor needs both ends. The blueprint only has `payer_account_id` and cannot represent incoming receipts. | `direction` (OUT/IN), `account_id` (our side), and `sender_country` / `receiver_country` stored on the row. |
| G4 | `"SDG"` is proposed as an invalid currency code, but it is a **real ISO code** (Sudanese pound), so an ISO-4217 check would pass it. | Defects are misspellings of the true code (`"usd"`, `"US$"`, `"EUR "`, ...), so a lookup map can undo them. `dq_checks.check_currency_codes` validates against the *modelled* set. |
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
| 1 | Money movement | `fact_payment.amount_sgd`, `sender_country`, `receiver_country`, `currency_code`, `dim_entity` | SG→CN is the dominant cross-border corridor, EUR inflows | SG→CN is the top cross-border corridor by value |
| 2 | Cross-border | `fact_payment_event` hops, `settled_ts - initiated_ts`, status by corridor/rail | SG→VN slow, US→IN high failure | ≥ 200 payments on each flagged corridor. Its P90 / failure rate is at least 1.5× the median |
| 3 | Efficiency | `is_stp`, `repair_count`, `missed_cutoff`, `channel`, timestamps | Sanctions holds and cut-off misses create a long tail. Legacy-file channel has lower STP | Cross-border P90/P50 ≥ 3 and mean > 1.2× median. STP(LEGACY_FILE) < STP(API) by 5 pp |
| 4 | Failures | `status`, `failure_reason` → `dim_failure_reason.category`, `counterparty_id` | About 3 reasons ≈ 80%. 8 repeat-offender suppliers | Top 3 reasons ≥ 70% of failures |
| 5 | Forecast | `fact_balance`, `fact_invoice` (due dates), `dim_calendar` | Weekly and monthly seasonality, trend, late payers | ≥ 24 months of history. Weekly seasonality is detectable |
| 6 | Concentration | `fact_balance`, `dim_account.is_pooled`, `overdraft_limit`, rates, `fact_sweep` | Idle INR cash while DE is overdrawn | At least one day has idle cash > overdraft in the same group |
| 7 | FX exposure | Flows where `currency_code ≠ entity.functional_currency`, `fact_fx_rate`, `fact_fx_hedge` | Long EUR, short CNY, partially hedged | Net EUR > 0 and net CNY < 0 in SGD terms |
| 8 | Reconciliation | `fact_statement_line`, `fact_invoice`, answer-key allocation | Virtual-account payers match better | Exact-reference rate, VA − free text ≥ 20 pp (v1 proxy; true match rate needs v4 statements) |
| 9 | Anomalies | `fact_payment`, `dim_counterparty.first_seen_date`, answer-key labels | 8 labelled anomaly types | ≥ 100 labels per type across the backfill |

## v1 verification results

Measured on the full default backfill (seed 42, 24 months). `tests/test_analysis_contracts.py` enforces these, so they can't silently regress.

| # | Check | Result |
|---|---|---|
| – | Volume | 834k payments at v1 (about 850k after v4 adds anomalies and ledger transfers), 576k invoices |
| – | Integrity | 0 time-order violations, 0 foreign-key violations, and the same seed produces identical data |
| 1 | Top cross-border corridor | SG→CN (7% of cross-border value). Domestic trade dominates overall, as in real groups. |
| 2 | Slow corridor SG→VN | about 2,300 payments. P90 is well above the median corridor. |
| 2 | Failure hotspot US→IN | 10.6% fail rate against a 2.0% median corridor |
| 3 | Cross-border timing | P50 4.3h, P90 about 28h, 88% credited within 24h, close to published SWIFT gpi figures |
| 3 | STP by channel | API 96.7%, LEGACY_FILE 86.5% |
| 4 | Failure Pareto | AC01 + RR03 + AM04 ≈ 72% of failures. Overall failure rate 1.1%. |
| 5 | Trend and seasonality | Tue/Thu payment-run peaks, quiet weekends, month-end spikes, Chinese New Year dip in CN |
| 7 | FX exposure | net long EUR, net short CNY (non-functional-currency flows) |
| 8 | Reference quality | virtual-account payers 100% exact references, free-text payers 56% |

### What the checks caught while building v1

- **A sorting bug** paired payment dates with the wrong invoices. Monthly value grew 9× over two years instead of about 8% a year. It was fixed and is now guarded by the trend check.
- **57% of payments were cross-border**, because counterparties had no home-country bias. There is now a domestic preference, so cross-border is about 28%, which is realistic.
- **Cross-border P50 was 16h.** Per-hop times were far slower than gpi reality. Hops now take minutes, and delay comes from time zones, cut-offs and holds.
- **7% of invoices were marked written off**, because warm-up invoices were judged against payments that had already been filtered out. Only AR can now be written off, and payables stay owed.

## v2–v4 verification results

`tests/test_analysis_contracts.py` enforces these, and all 9 analyses (plus a hedge-book check) pass on the full build.

| Version | Check | Result |
|---|---|---|
| v2 | Ledger invariant | opening + postings = closing for every account, to the cent |
| v2 | Pooling | pooled accounts close at 0 every night. HQ facility use returns to about 0 through weekly concentration. |
| v2 | Analysis 6 | trapped CNY (Shanghai, Shenzhen) and INR (India) cash builds up while Germany is overdrawn about 96% of days |
| v2 | AM04 | about 180 insufficient-funds rejections, all from real balances, mostly on unscheduled payments |
| v2 | Event trail | 3.5M events, strictly ordered per payment, and SETTLED always equals `settled_ts` |
| v3 | Stream = snapshot | streaming tick by tick leaves the clean database identical to a snapshot at the same time |
| v4 | Analysis 8 | full-reference match on statement lines: virtual-account payers 100%, free-text payers about 55%. About 5k part-paid invoices and about 3k multi-invoice payments. |
| v4 | Analysis 9 | 7 anomaly types, 250–8,400 labels each, under 3% of payments |
| v4 | Statements | the sum of statement lines equals the ledger movement per account. camt.053 OPBD + entries = CLBD. |
| v4 | DQ | raw files differ from the clean database by exactly the labelled defects, and the clean database has none |

### What the checks caught while building v2–v4

- **Cash piled up in the wrong places.** Collections landed in collection accounts and payments drained disbursement accounts, so HQ funded every shortfall (−4.4bn). Fixed with same-entity funding first and weekly concentration back to HQ.
- **An end-of-day ordering bug.** Two SG entities share an EOD instant, so HQ sometimes snapshotted before SG Ops' sweep into it. Fixed by splitting EOD into a sweep phase and a snapshot phase.
- **Statements were 3.40 off the ledger** from sub-cent rounding over 2 years. The ledger now books in cents, as banks do.
- **camt.053 namespace.** Only the root element was namespaced, so the XML wasn't valid camt.053. Every element is now namespaced, and the test parses the XML with the namespace.

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
- Parquet raw files as the first streaming sink (it matches the Part 1 Pandas pipeline)

## Build order

The world builder, calendar and FX engine are **implemented** in this repo, not just
designed (`treasury-sim build-world`).

1. **v1:** business events, a simple lifecycle and Parquet output. Unblocks SQL (Part 2) and dashboards (Part 3).
2. **v2:** full state machine, cut-offs, holidays, ledger, sweeps.
3. **v3:** live clock plus sinks (files → DB → webhooks).
4. **v4:** anomaly/DQ injection with labels, statement lines, optional ISO 20022 XML.
