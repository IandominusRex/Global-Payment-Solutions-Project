-- =====================================================================================
-- 02 · Cross-border payments
-- =====================================================================================
-- Question : where does money go across borders, how, how long does it take, how often does it fail?
-- Metrics  : average and 90th-percentile hours to settle; failure rate by corridor and rail.
-- Chart    : corridor scorecard table with conditional formatting (red = slow / failing).
-- So what  : which corridors deserve a different rail or bank.
-- Tables   : fact_payment, dim_payment_type
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · Cross-border payments with settlement time in hours and a failure flag
-- -------------------------------------------------------------------------------------
--   * pending excluded: not settled, not failed, yet - it would distort both metrics
--   * hours_to_settle only for successful payments (failed ones never settled)
--   * julianday() turns the text timestamp into a day number; x 24 = hours
WITH xb AS (
    SELECT p.payment_id,
           p.sender_country || ' -> ' || p.receiver_country AS corridor,
           t.rail,
           p.amount_sgd,
           p.status,
           CASE WHEN p.status IN ('rejected', 'returned') THEN 1 ELSE 0 END AS is_failed,
           CASE WHEN p.status IN ('completed', 'delayed')
                THEN (julianday(p.settled_ts) - julianday(p.initiated_ts)) * 24 END AS hours_to_settle
    FROM fact_payment p
    JOIN dim_payment_type t ON t.type_id = p.type_id
    WHERE p.is_intercompany = 0
      AND p.sender_country <> p.receiver_country
      AND p.status <> 'pending'
)
SELECT * FROM xb LIMIT 20;


-- -------------------------------------------------------------------------------------
-- Step 2 ✅ given (worked example) · Percentiles in SQLite with the rank trick
-- -------------------------------------------------------------------------------------
-- SQLite has no PERCENTILE_CONT. So:
--   1. number each payment within its corridor, fastest first  (ROW_NUMBER)
--   2. count payments per corridor                              (COUNT(*) OVER)
--   3. P50 = the row at position 0.5 x count; P90 = at 0.9 x count
-- Study this one carefully - you'll reuse it in analysis 03.
WITH xb AS (
    SELECT p.sender_country || ' -> ' || p.receiver_country AS corridor,
           (julianday(p.settled_ts) - julianday(p.initiated_ts)) * 24 AS hours_to_settle
    FROM fact_payment p
    WHERE p.is_intercompany = 0
      AND p.sender_country <> p.receiver_country
      AND p.status IN ('completed', 'delayed')
),
ranked AS (
    SELECT corridor, hours_to_settle,
           ROW_NUMBER() OVER (PARTITION BY corridor ORDER BY hours_to_settle) AS rn,
           COUNT(*)     OVER (PARTITION BY corridor)                          AS n
    FROM xb
)
SELECT corridor,
       n,
       ROUND(MAX(CASE WHEN rn = CAST(0.5 * n AS INTEGER) + 1 THEN hours_to_settle END), 1) AS p50_hours,
       ROUND(MAX(CASE WHEN rn = CAST(0.9 * n AS INTEGER) + 1 THEN hours_to_settle END), 1) AS p90_hours
FROM ranked
GROUP BY corridor, n
HAVING n >= 200            -- small corridors give noisy percentiles
ORDER BY p90_hours DESC;


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · The corridor scorecard
-- -------------------------------------------------------------------------------------
-- One row per corridor with: n_payments, value_sgd_m, fail_rate_pct, avg_hours, p50_hours, p90_hours.
-- 1. Start from the Step 1 CTE (xb), which has is_failed.
-- 2. fail_rate_pct = 100.0 * SUM(is_failed) / COUNT(*)
-- 3. avg_hours = AVG(hours_to_settle)  (AVG ignores the NULLs of failed payments - good)
-- 4. Join in P50/P90 from Step 2 (put Step 2's final SELECT in a CTE `pct` and JOIN on corridor).
-- 5. HAVING COUNT(*) >= 200.
-- ✔️ expect: SG -> VN has one of the highest P90s; US -> IN has the highest fail rate of the
--            big corridors, several times the median corridor (small dataset: ~6% vs ~2%).


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Same scorecard by rail
-- -------------------------------------------------------------------------------------
-- Swap corridor for rail. Is the problem the corridor or the rail? (A slow corridor on a fast
-- rail points at the correspondent banks; check fact_payment_event.bank_id for its hops.)


-- -------------------------------------------------------------------------------------
-- Step 5 ✏️ TODO · Views
-- -------------------------------------------------------------------------------------
-- CREATE VIEW vw_02_corridor_scorecard AS ... ;
-- CREATE VIEW vw_02_rail_scorecard AS ... ;
