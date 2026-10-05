# 2026-10-05 · Liquidity forecast, and starting cash concentration

## Problem
Analysis 5 needs a cash position per entity and currency that can be forecast. The data gives two views
of the same cash, balances (`fact_balance`) and payments (`fact_payment`), and I had to build the series
and check they agree before forecasting anything. I was also new to the treasury ideas behind analysis 6
(pooling, sweeping, overdraft limits), so I needed to understand them before writing any SQL.

## What I did
1. **Analysis 5, liquidity forecast.** Six steps, all written up in the README:
   - Daily closing balance per entity and currency (join `fact_balance` to `dim_account`).
   - Daily net cash flow from `fact_payment`: IN counts as positive and OUT as negative.
   - A reconciliation query that puts balance, net flow and day-on-day balance change side by side.
   - A weekly series, using `dim_date` for year and week.
   - A seasonality view by day of week, with both net flow and gross flow (the activity level).
   - Two saved views, `vw_05_daily_balance` and `vw_05_daily_net_flow`, as the base for the forecast.
2. **Learned the analysis 6 concepts** and wrote the opening of its README section.
   - Overdraft limit, idle cash, credit and debit rates, sweeping (`zba` and `internal`), pooling,
     weekly concentration and restricted currencies (CNY and INR).
   - Mapped each idea to its table and column: `dim_account` (`overdraft_limit`, `target_balance`,
     `is_pooled`, `pool_header_account_id`, `credit_rate`, `debit_rate`), `fact_sweep`,
     `fact_balance` and `dim_currency.is_restricted`.
   - Found where the pattern is planted: India is deliberately unpooled, the China entities hold
     restricted CNY, and Germany GmbH runs an overdraft.

## Decisions
- **Balances and flows are checked against each other first.** On the rows I looked at, the balance
  change on a day matches that day's net flow (for example -2,741,935.48 on 2024-11-05). Balances stay
  flat on days with no payments. This is the check that the two tables describe the same cash.
- **Only completed payments count as cash.** The reconciliation, weekly and seasonality queries filter
  on `status = 'completed'`. A payment that is pending, rejected or returned has not moved cash.
- **Gross flow next to net flow.** Net flow can be near zero on a busy day when money in and out cancel
  out. Gross flow shows how active the day was, so seasonality reads better with both.
- **Weekly numbers keep `active_days`.** Many weeks have only one or two settlement days, so the count
  tells me how much a weekly figure should be trusted.
- **Saved views, not copied queries.** The balance and net flow queries are reused by later steps, so they
  live in `vw_05_*` views.
- **Group totals only in SGD.** For analysis 6 I will use `closing_balance_sgd`. Adding balances in
  different currencies in local units would be meaningless.

## What went wrong
- A typo in the README ("analaysis").
- The README results are very long. Pasting the full output makes the file hard to read, and the early
  rows are all one flat series (E01 CNY, flat at -41.9m until the first payment), which tells the reader
  very little. The balance query had a `LIMIT 30` for this reason, and the others should be trimmed too.
- The first Analysis 6 paragraph was placed after the views, with the Layout section directly below it,
  so the section reads as unfinished until the queries are added.
- The views are in the README only. `sql/analyses/05_liquidity_forecast.sql` still has the old draft in
  it, so the two are out of step.

## Next
Copy the finished analysis 5 views into `sql/analyses/05_liquidity_forecast.sql`, trim the long result
tables in the README, then build analysis 6 on the daily balance table: idle cash against overdraft per
day (in SGD, split into movable and restricted), the rate spread, and the estimated pooling benefit.
