-- =====================================================================================
-- 04 · Payment failures
-- =====================================================================================
-- Question : why do payments fail, and can we stop it at the source?
-- Metrics  : failure rate; failure reasons ranked (Pareto); value at risk in failed payments;
--            repeat-offender counterparties.
-- Chart    : Pareto chart (bars = failures per reason, line = cumulative %).
-- So what  : most failures come from a few DATA causes (wrong account, missing address) that can
--            be fixed once in the vendor master data.
-- Tables   : fact_payment, dim_failure_reason, dim_counterparty
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · Overall failure rate
-- -------------------------------------------------------------------------------------
SELECT COUNT(*)                                                       AS n_payments,
       SUM(status IN ('rejected', 'returned'))                        AS n_failed,
       ROUND(100.0 * AVG(status IN ('rejected', 'returned')), 2)      AS fail_rate_pct,
       ROUND(SUM(CASE WHEN status IN ('rejected', 'returned') THEN amount_sgd ELSE 0 END) / 1e6, 2)
                                                                      AS failed_value_sgd_m
FROM fact_payment
WHERE is_intercompany = 0
  AND direction = 'OUT'
  AND status <> 'pending';


-- -------------------------------------------------------------------------------------
-- Step 2 ✏️ TODO · Pareto of failure reasons
-- -------------------------------------------------------------------------------------
-- 1. Failed outgoing payments JOIN dim_failure_reason r ON r.reason_code = p.failure_reason.
-- 2. GROUP BY reason_code, description, category -> n_failed.
-- 3. Cumulative share with a running-total window:
--      100.0 * SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC) / SUM(COUNT(*)) OVER ()  AS cum_pct
--    (window functions run AFTER GROUP BY, so you can window over the COUNT(*)s)
-- ✔️ expect: the top 3 reasons (AC01 and RR03 lead by far) cover ~70% of failures.
-- Then group by category instead: how much is "data" (fixable at source)?


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Repeat offenders
-- -------------------------------------------------------------------------------------
-- Counterparties with the most failures:
--   GROUP BY counterparty_id, c.name, c.country
--   HAVING n_failed >= 5
--   ORDER BY n_failed DESC
-- Add their own fail rate (failed / all their payments). A supplier failing 30% of the time
-- has broken bank details in our master data.
-- ✔️ expect: a handful of suppliers stand out far above the rest.


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Failure rate by rail and by month
-- -------------------------------------------------------------------------------------
-- Is it getting better or worse over time? Any month that spikes?


-- -------------------------------------------------------------------------------------
-- Step 5 ✏️ TODO · Views
-- -------------------------------------------------------------------------------------
-- vw_04_failure_pareto, vw_04_repeat_offenders, vw_04_failure_trend
