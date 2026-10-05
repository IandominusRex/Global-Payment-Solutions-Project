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
--
-- Approach (unpooled accounts only, in-house bank excluded):
--   * idle_sgd / overdrawn_sgd per account per day, converted with fact_fx_rate
--   * only unrestricted idle cash is movable; CNY / INR idle cash stays trapped
--   * offsettable = MIN(movable idle, overdrawn), compared across the whole group per day
--   * rates are weighted by the amount they apply to: SUM(amount x rate) / SUM(amount)
--   * saving = offsettable x (weighted debit rate - weighted credit rate) / 365
--   * gross benefit: the data has no cost of running a pool
WITH acct AS (
    SELECT b.date_id, c.is_restricted, a.credit_rate, a.debit_rate,
           MAX(0, b.closing_balance - a.target_balance) * fx.rate_to_sgd AS idle_sgd,
           MAX(0, -b.closing_balance)                   * fx.rate_to_sgd AS overdrawn_sgd
    FROM fact_balance b
    JOIN dim_account a   ON a.account_id = b.account_id
    JOIN dim_entity e    ON e.entity_id = a.entity_id
    JOIN dim_currency c  ON c.currency_code = b.currency_code
    JOIN fact_fx_rate fx ON fx.date_id = b.date_id AND fx.currency_code = b.currency_code
    WHERE a.is_pooled = 0
      AND e.is_in_house_bank = 0
),
daily AS (
    SELECT date_id,
           SUM(CASE WHEN is_restricted = 0 THEN idle_sgd ELSE 0 END)               AS idle_movable_sgd,
           SUM(CASE WHEN is_restricted = 1 THEN idle_sgd ELSE 0 END)               AS idle_trapped_sgd,
           SUM(CASE WHEN is_restricted = 0 THEN idle_sgd * credit_rate ELSE 0 END) AS idle_x_credit,
           SUM(overdrawn_sgd)                                                      AS overdrawn_sgd,
           SUM(overdrawn_sgd * debit_rate)                                         AS overdrawn_x_debit
    FROM acct
    GROUP BY date_id
),
sgd_conversion AS (
    SELECT date_id, idle_movable_sgd, idle_trapped_sgd, overdrawn_sgd,
           MIN(idle_movable_sgd, overdrawn_sgd)                                    AS offsettable_sgd,
           overdrawn_sgd - MIN(idle_movable_sgd, overdrawn_sgd)                    AS remaining_overdraft_sgd,
           COALESCE(overdrawn_x_debit / NULLIF(overdrawn_sgd, 0), 0)               AS weighted_debit_rate,
           COALESCE(idle_x_credit     / NULLIF(idle_movable_sgd, 0), 0)            AS weighted_credit_rate
    FROM daily
),
offset_dr_cr AS (
    SELECT date_id,
           offsettable_sgd,
           offsettable_sgd * weighted_credit_rate / 365 AS idle_credit_interest_foregone,
           offsettable_sgd * weighted_debit_rate  / 365 AS overdraft_debit_interest_avoided
    FROM sgd_conversion
)
SELECT dd.year,
       ROUND(SUM(idle_credit_interest_foregone), 0)    AS sum_idle_credit_interest_foregone_sgd,
       ROUND(SUM(overdraft_debit_interest_avoided), 0) AS sum_overdraft_debit_interest_avoided,
       ROUND(SUM(overdraft_debit_interest_avoided - idle_credit_interest_foregone), 0) AS net_benefit_from_pooling
FROM offset_dr_cr odc
JOIN dim_date dd ON dd.date_id = odc.date_id
GROUP BY dd.year
ORDER BY dd.year;


-- -------------------------------------------------------------------------------------
-- Step 4b ✅ · The same benefit under three treatments of the HQ in-house bank (E01)
-- -------------------------------------------------------------------------------------
-- E01 holds almost all of the overdraft, so whether it counts decides the headline number.
--   A: subsidiaries only (E01 excluded)
--   B: E01 included, all overdraft can be offset
--   C: E01 included, restricted-currency (CNY / INR) overdraft cannot be offset (lower bound)
-- annual_saving_sgd = total over all days / (days / 365), so a partial first year does not skew it.
WITH acct AS (
    SELECT b.date_id, c.is_restricted, e.is_in_house_bank AS hq, a.credit_rate, a.debit_rate,
           MAX(0, b.closing_balance - a.target_balance) * fx.rate_to_sgd AS idle_sgd,
           MAX(0, -b.closing_balance)                   * fx.rate_to_sgd AS od_sgd
    FROM fact_balance b
    JOIN dim_account a   ON a.account_id = b.account_id
    JOIN dim_entity e    ON e.entity_id = a.entity_id
    JOIN dim_currency c  ON c.currency_code = b.currency_code
    JOIN fact_fx_rate fx ON fx.date_id = b.date_id AND fx.currency_code = b.currency_code
    WHERE a.is_pooled = 0
),
scen(name, incl_hq, od_unrestricted_only) AS (
    VALUES ('A: subsidiaries only (excl. HQ)', 0, 0),
           ('B: incl. HQ, all overdraft', 1, 0),
           ('C: incl. HQ, unrestricted overdraft only', 1, 1)
),
daily AS (
    SELECT s.name, x.date_id,
           SUM(CASE WHEN x.is_restricted = 0 THEN x.idle_sgd ELSE 0 END)                AS idle_mov,
           SUM(CASE WHEN x.is_restricted = 0 THEN x.idle_sgd * x.credit_rate ELSE 0 END) AS idle_x_cr,
           SUM(CASE WHEN s.od_unrestricted_only = 1 AND x.is_restricted = 1 THEN 0 ELSE x.od_sgd END)            AS od,
           SUM(CASE WHEN s.od_unrestricted_only = 1 AND x.is_restricted = 1 THEN 0 ELSE x.od_sgd * x.debit_rate END) AS od_x_db
    FROM acct x JOIN scen s ON (s.incl_hq = 1 OR x.hq = 0)
    GROUP BY s.name, x.date_id
),
cost AS (
    SELECT name, date_id, od, MIN(idle_mov, od) AS offs,
           MIN(idle_mov, od) * (COALESCE(od_x_db / NULLIF(od, 0), 0)
                              - COALESCE(idle_x_cr / NULLIF(idle_mov, 0), 0)) / 365 AS saving
    FROM daily
)
SELECT name,
       SUM(od > 0)                                   AS days_overdrawn,
       ROUND(AVG(od))                                AS avg_overdrawn_sgd,
       ROUND(AVG(offs))                              AS avg_offsettable_sgd,
       ROUND(SUM(saving) / (COUNT(*) / 365.0))       AS annual_saving_sgd
FROM cost
GROUP BY name
ORDER BY name;


-- -------------------------------------------------------------------------------------
-- Step 5 ✅ · Views
-- -------------------------------------------------------------------------------------
-- vw_06_account_position         latest balance, idle cash, overdraft and headroom per account (SGD)
-- vw_06_daily_idle_vs_overdrawn  group idle vs overdrawn per day: the area chart (unpooled accounts, HQ included,
--                                overdrawn_hq_sgd shows how much of the overdraft is the in-house bank)
-- vw_06_pooling_benefit          annual saving under the three E01 scenarios (A / B / C)

DROP VIEW IF EXISTS vw_06_account_position;
CREATE VIEW vw_06_account_position AS
SELECT a.account_id, a.entity_id, a.currency_code, a.is_pooled, c.is_restricted,
       b.date_id,
       ROUND(b.closing_balance, 0)                                                AS balance,
       ROUND(b.closing_balance * fx.rate_to_sgd, 0)                               AS balance_sgd,
       ROUND(a.target_balance * fx.rate_to_sgd, 0)                                AS target_sgd,
       ROUND(MAX(0, b.closing_balance - a.target_balance) * fx.rate_to_sgd, 0)    AS idle_sgd,
       ROUND(MAX(0, -b.closing_balance) * fx.rate_to_sgd, 0)                      AS overdrawn_sgd,
       ROUND((a.overdraft_limit + MIN(0, b.closing_balance)) * fx.rate_to_sgd, 0) AS headroom_sgd
FROM fact_balance b
JOIN dim_account a   ON a.account_id = b.account_id
JOIN dim_currency c  ON c.currency_code = b.currency_code
JOIN fact_fx_rate fx ON fx.date_id = b.date_id AND fx.currency_code = b.currency_code
WHERE b.date_id = (SELECT MAX(date_id) FROM fact_balance);

DROP VIEW IF EXISTS vw_06_daily_idle_vs_overdrawn;
CREATE VIEW vw_06_daily_idle_vs_overdrawn AS
WITH acct AS (
    SELECT b.date_id, c.is_restricted, e.is_in_house_bank AS hq,
           MAX(0, b.closing_balance - a.target_balance) * fx.rate_to_sgd AS idle_sgd,
           MAX(0, -b.closing_balance)                   * fx.rate_to_sgd AS overdrawn_sgd
    FROM fact_balance b
    JOIN dim_account a   ON a.account_id = b.account_id
    JOIN dim_entity e    ON e.entity_id = a.entity_id
    JOIN dim_currency c  ON c.currency_code = b.currency_code
    JOIN fact_fx_rate fx ON fx.date_id = b.date_id AND fx.currency_code = b.currency_code
    WHERE a.is_pooled = 0
),
daily AS (
    SELECT date_id,
           SUM(CASE WHEN is_restricted = 0 THEN idle_sgd ELSE 0 END)  AS idle_movable_sgd,
           SUM(CASE WHEN is_restricted = 1 THEN idle_sgd ELSE 0 END)  AS idle_trapped_sgd,
           SUM(overdrawn_sgd)                                         AS overdrawn_sgd,
           SUM(CASE WHEN hq = 1 THEN overdrawn_sgd ELSE 0 END)        AS overdrawn_hq_sgd
    FROM acct
    GROUP BY date_id
)
SELECT date_id,
       ROUND(idle_movable_sgd, 0)                                  AS idle_movable_sgd,
       ROUND(idle_trapped_sgd, 0)                                  AS idle_trapped_sgd,
       ROUND(overdrawn_sgd, 0)                                     AS overdrawn_sgd,
       ROUND(overdrawn_hq_sgd, 0)                                  AS overdrawn_hq_sgd,
       ROUND(MIN(idle_movable_sgd, overdrawn_sgd), 0)              AS offsettable_sgd,
       ROUND(overdrawn_sgd - MIN(idle_movable_sgd, overdrawn_sgd), 0) AS remaining_overdraft_sgd
FROM daily;

DROP VIEW IF EXISTS vw_06_pooling_benefit;
CREATE VIEW vw_06_pooling_benefit AS
WITH acct AS (
    SELECT b.date_id, c.is_restricted, e.is_in_house_bank AS hq, a.credit_rate, a.debit_rate,
           MAX(0, b.closing_balance - a.target_balance) * fx.rate_to_sgd AS idle_sgd,
           MAX(0, -b.closing_balance)                   * fx.rate_to_sgd AS od_sgd
    FROM fact_balance b
    JOIN dim_account a   ON a.account_id = b.account_id
    JOIN dim_entity e    ON e.entity_id = a.entity_id
    JOIN dim_currency c  ON c.currency_code = b.currency_code
    JOIN fact_fx_rate fx ON fx.date_id = b.date_id AND fx.currency_code = b.currency_code
    WHERE a.is_pooled = 0
),
scen(name, incl_hq, od_unrestricted_only) AS (
    VALUES ('A: subsidiaries only (excl. HQ)', 0, 0),
           ('B: incl. HQ, all overdraft', 1, 0),
           ('C: incl. HQ, unrestricted overdraft only', 1, 1)
),
daily AS (
    SELECT s.name, x.date_id,
           SUM(CASE WHEN x.is_restricted = 0 THEN x.idle_sgd ELSE 0 END)                AS idle_mov,
           SUM(CASE WHEN x.is_restricted = 0 THEN x.idle_sgd * x.credit_rate ELSE 0 END) AS idle_x_cr,
           SUM(CASE WHEN s.od_unrestricted_only = 1 AND x.is_restricted = 1 THEN 0 ELSE x.od_sgd END)            AS od,
           SUM(CASE WHEN s.od_unrestricted_only = 1 AND x.is_restricted = 1 THEN 0 ELSE x.od_sgd * x.debit_rate END) AS od_x_db
    FROM acct x JOIN scen s ON (s.incl_hq = 1 OR x.hq = 0)
    GROUP BY s.name, x.date_id
),
cost AS (
    SELECT name, date_id, od, MIN(idle_mov, od) AS offs,
           MIN(idle_mov, od) * (COALESCE(od_x_db / NULLIF(od, 0), 0)
                              - COALESCE(idle_x_cr / NULLIF(idle_mov, 0), 0)) / 365 AS saving
    FROM daily
)
SELECT name                                          AS scenario,
       SUM(od > 0)                                   AS days_overdrawn,
       ROUND(AVG(od))                                AS avg_overdrawn_sgd,
       ROUND(AVG(offs))                              AS avg_offsettable_sgd,
       ROUND(SUM(saving) / (COUNT(*) / 365.0))       AS annual_saving_sgd
FROM cost
GROUP BY name;
