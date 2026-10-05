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

---

# Continued · FX exposure and the hedged share (Analysis 7)

## Problem
Analysis 7 asks which foreign currencies the group is long or short, by how much, and how much of that
is covered by forwards. The first hedged share query returned 0% for every position, and the roll-up
returned one row of nonsense.

## What I did
1. **Query 1, flows in a currency foreign to the entity.** Join `fact_payment` to `dim_account` and
   `dim_entity` and keep `currency_code <> functional_currency`. Exposure belongs to the entity, not the
   account, because the same EUR account is exposed for an SGD entity and not for a EUR one. `signed_sgd`
   is `+` for IN (long) and `-` for OUT (short).
2. **Query 2, net position** by currency, then by entity and currency (all-time).
3. **Query 3, value at risk.** `ABS(net) x 5%` and `x 10%` on the last 90 days of flows.
4. **Query 4, hedged share by entity and currency.** Live forwards are split into a buy leg and a sell leg,
   converted to SGD at the as-of rate and set against the 90-day net position.
5. **Query 5, the same rolled up to currency.**
6. **Query 6, three views:** `vw_07_net_position`, `vw_07_sensitivity` and `vw_07_hedged_share`. Tested on a
   copy of the database. The SQL file `sql/analyses/07_fx_exposure.sql` now has Steps 2-5.

## Decisions
- **Build exposure and hedges in separate CTEs, then join the totals.** Joining payments straight to
  hedges repeats every payment once per hedge of that entity and inflates the sums.
- **A hedge counts only if it is live on the as-of date:** `trade_date_id <= d < maturity_date_id`. Both
  columns are `yyyymmdd` integers, so no join to `dim_date` is needed.
- **Hedged share = `-hedge / exposure`.** Positive when the hedge opposes the position, negative when it
  points the wrong way, above 1 when over-hedged.
- **Sum amounts, then recompute the percentage.** Percentages are never added up.
- **Keep the entity table and add the currency roll-up.** The roll-up is the headline, but the entity
  table is where the action is: E02, E10 and E09 hold 56% of the gross exposure.
- **The window ends on the as-of date,** so flows and hedges are measured on the same day.
- **Pin 2026-09-15 in the README queries; the views pick the date themselves** (the last forward trade
  date, capped at the last payment, which is 2026-09-01 here). The numbers differ slightly and the README
  says so.
- **Did not work out a "hedge ratio at which hedging pays".** It needs forward points, a risk tolerance
  and forecast exposure, none of which is in the dataset.

## What went wrong
- **Everything came back 0% hedged.** The as-of date was the latest payment (2026-10-01), but the last
  forward matures on 2026-09-30, so no hedge was live. The SQL was fine; the date was the problem.
- **The 90-day window had no upper bound.** With the date pinned to 2026-09-15, flows to 2026-10-01 were
  still counted. Found while testing the views, fixed in Queries 4 and 5 and both tables regenerated
  (E01 CNY went from -4.6 to -4.2).
- **SQL slips again.** A final `SELECT` that grouped by currency but left `net_sgd` unaggregated, so SQLite
  returned one entity's values (it showed E01's) for each currency. A roll-up with no `GROUP BY` and
  `SUM(hedged_pct)`. A `WITH` with no final `SELECT`. A broken `dim_date` join with typos in the alias.
- **A test copy of the database was missing the hedge rows,** because `cp` skipped the write-ahead log.
  `sqlite3 ... ".backup"` copies it properly.

## Result
On 2026-09-15 the group is long USD (S$45.6m), long EUR (S$20.2m) and short CNY (S$31.1m), and only
10-18% of each position is covered. About S$17m of S$110m gross exposure (16%) is hedged. No position is
hedged the wrong way, so the issue is coverage. USD is the largest open position (roughly S$39m). For USD
and CNY, entities hold the same side, so netting between them removes little and forwards are the lever.
These are `data_small` figures.

## Next
- Check whether the hedge book stopping after 2026-09-01 is a real finding or a simulation artifact.
- Re-run Queries 4 and 5 on the full profile.
- Analysis 8 (anomaly detection). Reconciliation is deferred.
