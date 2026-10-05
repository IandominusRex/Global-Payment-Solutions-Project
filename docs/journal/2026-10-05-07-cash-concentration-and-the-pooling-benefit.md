# 2026-10-05 · Cash concentration and the pooling benefit

## Problem
Analysis 6 asks whether cash is fragmented: idle in some accounts while others are overdrawn, and how
much pooling would save. I was new to the terms (pooling, sweeping, overdraft limit), and the first
answer I got looked far too small to be useful (about S$131 a year).

## What I did
1. **Learned the concepts and mapped them to tables.** Idle cash, overdraft drawn, headroom, credit and
   debit rates, ZBA and internal sweeps, concentration, restricted currencies. The columns that matter are
   in `dim_account`, `fact_balance`, `fact_sweep`, `dim_currency` and `fact_fx_rate`.
2. **Query 1, latest balance per account.** Added `overdraft_drawn`, `headroom` and `idle_cash`.
3. **Query 2, idle cash against overdraft per day.** In SGD, with idle cash split into movable and
   trapped (CNY and INR), and `offsettable = MIN(movable idle, overdrawn)`.
4. **Query 3, benefit per year.** Offsettable amount times the weighted debit rate, minus the same amount
   times the weighted credit rate, divided by 365.
5. **Query 4, three scenarios for the HQ in-house bank (E01).** Run side by side so the effect of that one
   choice is visible. Written into the README and `sql/analyses/06_cash_concentration.sql` (Step 4b).
6. **Query 5, three views:** `vw_06_account_position`, `vw_06_daily_idle_vs_overdrawn` and
   `vw_06_pooling_benefit`. I tested them on a copy of the database and they match the query results.

## Decisions
- **Convert to SGD before comparing anything.** An INR balance and a EUR overdraft cannot be compared in
  local units. I used `fact_fx_rate`, not `closing_balance_sgd / closing_balance`, because pooled accounts
  close at 0 and the ratio would divide by zero.
- **Compute idle and overdrawn per account first, then sum.** Summing `closing_balance_sgd` nets positives
  against negatives and hides the problem.
- **Offset with `MIN`.** Cash can only cover debt that exists, and debt can only be covered by cash that
  exists.
- **Group by day, not by day and currency.** The in-house bank can convert cash from one currency to cover
  an overdraft in another, so the comparison is group-wide.
- **Weight the rates.** Rates differ by currency, so the daily rate is `SUM(amount x rate) / SUM(amount)`.
- **Restricted currencies stay trapped.** CNY and INR idle cash is never used to offset anything.
- **Report E01 as a choice, not a default.** I first excluded the in-house bank on a guess that its
  overdraft was group funding, not waste. The data shows it holds almost all the overdraft, so that guess
  decided the whole result. The README now shows three scenarios.

## What went wrong
- **The first result was tiny and I nearly accepted it.** With E01 excluded, only Germany is left, and it
  is overdrawn on 44 of 730 days (average about S$143k). The net benefit was S$131, S$192 and S$396 a
  year. Checking where the overdraft sits is what found the cause.
- **A wrong idea about overdraft limits.** I thought amounts over the limit are charged at a high rate.
  The limit is how far below zero the bank allows, and interest is charged on everything drawn from the
  first euro below zero.
- **SQL slips.** `INT()` is not a SQLite function and would have turned every rate into 0. Aggregate
  queries had bare columns (rates, a `CASE` outside a `SUM`) that SQLite fills from an arbitrary row.
  There was a trailing comma before the final `SELECT`, and a `SUM` with no `GROUP BY`.
- **An unfinished sentence in the README** after Query 3, now completed.

## Result
Pooling could save roughly S$4-5m a year (scenario B S$5.4m, scenario C S$4.2m), almost all from netting
the in-house bank's overdraft against subsidiary cash. Excluding E01 the saving is about S$359 a year.
These are `data_small` figures and gross of the cost of running a pool.

## Next
- Check whether E01's overdraft is double counted: look at the funding flows in `fact_sweep` and
  intercompany payments before treating B and C as real savings.
- Re-run Query 4 on the full profile.
- Step 2 (fragmentation, top 3 accounts' share of cash) is still open in the SQL file.
- Analysis 7 (FX exposure).
