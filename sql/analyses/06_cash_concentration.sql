-- =====================================================================================
-- 06 · Cash concentration
-- =====================================================================================
-- Question : is cash fragmented across too many accounts - idle in some while others are overdrawn?
-- Metrics  : number of accounts holding cash; share of cash in the top accounts; idle balances above
--            target; overdrawn vs surplus positions that could offset; estimated benefit of pooling.
-- Chart    : balance vs target per account (bar with target marker); daily idle vs overdrawn (area).
-- So what  : an estimated S$ per year saved by pooling / sweeping - the number a treasurer acts on.
-- Tables   : fact_balance, dim_account (target_balance, overdraft_limit, credit_rate, debit_rate,
--            is_pooled, pool_header_account_id), fact_sweep
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · Latest balance per account vs its target
-- -------------------------------------------------------------------------------------
--   * excess_over_target > 0: cash sitting above what the account needs (idle)
--   * negative closing_balance: overdrawn, paying debit interest
WITH latest AS (
    SELECT MAX(date_id) AS date_id FROM fact_balance
)
SELECT a.account_id,
       a.entity_id,
       a.currency_code,
       a.is_pooled,
       ROUND(b.closing_balance, 0)                    AS balance,
       a.target_balance,
       ROUND(b.closing_balance - a.target_balance, 0) AS excess_over_target,
       a.overdraft_limit,
       ROUND(b.closing_balance_sgd, 0)                AS balance_sgd
FROM fact_balance b
JOIN latest l      ON l.date_id = b.date_id
JOIN dim_account a ON a.account_id = b.account_id
ORDER BY balance_sgd DESC;


-- -------------------------------------------------------------------------------------
-- Step 2 ✏️ TODO · Fragmentation
-- -------------------------------------------------------------------------------------
-- On the latest date: how many accounts hold positive cash? What % of total cash sits in the
-- top 3 accounts? (ROW_NUMBER() OVER (ORDER BY balance_sgd DESC), then SUM where rn <= 3.)


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Idle cash and overdraft on the SAME day in the SAME currency
-- -------------------------------------------------------------------------------------
-- For each date_id and currency_code:
--   idle_sgd      = SUM of (balance - target) over accounts where balance > target, in SGD
--   overdrawn_sgd = SUM of -balance over accounts where balance < 0, in SGD
--   offsettable   = MIN(idle_sgd, overdrawn_sgd)    -- what pooling could have netted
-- Tip: convert (balance - target) to SGD with closing_balance_sgd / closing_balance as the rate.
-- ✔️ expect: days where INR/other idle cash coexists with DE overdrafts.


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Estimated benefit of pooling (per year)
-- -------------------------------------------------------------------------------------
-- Daily cost of NOT pooling = overdraft interest paid + credit interest forgone:
--   overdrawn amount x debit_rate / 365   (debit_rate is annual, from dim_account)
-- Sum over a year of days. That's the headline number.
-- Question for your write-up: CNY and INR are restricted currencies (dim_currency.is_restricted).
-- Can their idle cash actually be pooled with other countries? What does that do to the benefit?


-- -------------------------------------------------------------------------------------
-- Step 5 ✏️ TODO · Views
-- -------------------------------------------------------------------------------------
-- vw_06_account_position, vw_06_daily_idle_vs_overdrawn, vw_06_pooling_benefit
