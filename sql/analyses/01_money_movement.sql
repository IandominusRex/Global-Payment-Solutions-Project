-- =====================================================================================
-- 01 · Where is money moving?
-- =====================================================================================
-- Question : which entities, countries and currencies carry the most volume, and at what scale?
-- Metrics  : total value by currency, by corridor (sender country -> receiver country),
--            by entity; payment counts.
-- Chart    : corridor heat map (sender x receiver) or Sankey; bar of top currencies.
-- So what  : reveals FX exposure, dependence on certain corridors, candidates for netting.
-- Tables   : fact_payment, dim_account, dim_entity
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · The base set: settled, external payments, labelled with our entity.
-- -------------------------------------------------------------------------------------
-- Read each line and say out loud why it's there:
--   * is_intercompany = 0  -> internal transfers are not "money moving" for the group
--   * status completed/delayed -> only money that actually moved
--   * dim_account -> dim_entity  -> payments point to an ACCOUNT; the account knows the company
WITH base AS (
    SELECT p.payment_id,
           e.entity_id,
           e.name            AS entity_name,
           p.direction,
           p.sender_country,
           p.receiver_country,
           p.currency_code,
           p.amount_sgd,
           date(p.initiated_ts) AS pay_date
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
)
SELECT * FROM base LIMIT 20;


-- -------------------------------------------------------------------------------------
-- Step 2 ✏️ TODO · Value by currency
-- -------------------------------------------------------------------------------------
-- 1. Copy the WITH base AS (...) block from Step 1 (every step needs it).
-- 2. GROUP BY currency_code; show payment count, total value in SGD millions, and each
--    currency's share of the total.
-- 3. Share of total = SUM(amount_sgd) / SUM(SUM(amount_sgd)) OVER ()
--    (a window function over the grouped result: "my group / all groups").
-- ✔️ expect: 6 rows, shares add up to 100%.
--
-- WITH base AS ( ...copy from Step 1... )
-- SELECT currency_code,
--        COUNT(*)                                        AS n_payments,
--        ROUND(SUM(amount_sgd) / 1e6, 1)                 AS value_sgd_m,
--        ROUND(100.0 * SUM(amount_sgd) / SUM(SUM(amount_sgd)) OVER (), 1) AS pct_of_value
-- FROM base
-- GROUP BY ___
-- ORDER BY ___ DESC;


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Top cross-border corridors
-- -------------------------------------------------------------------------------------
-- 1. A corridor = sender_country || ' -> ' || receiver_country.
-- 2. Cross-border only: sender_country <> receiver_country.
-- 3. Group by corridor, order by value, LIMIT 15.
-- ✔️ expect: SG -> CN is the top corridor by value.
--
-- SELECT sender_country || ' -> ' || receiver_country AS corridor,
--        COUNT(*) AS n_payments,
--        ROUND(SUM(amount_sgd) / 1e6, 1) AS value_sgd_m
-- FROM base
-- WHERE ___
-- GROUP BY ___
-- ORDER BY value_sgd_m DESC
-- LIMIT 15;
--
-- Then: what share of ALL external value is cross-border vs domestic? (one extra query)


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Inflows vs outflows per entity
-- -------------------------------------------------------------------------------------
-- 1. Group by entity_name.
-- 2. Conditional aggregation: SUM(CASE WHEN direction = 'IN' THEN amount_sgd ELSE 0 END) AS inflow_sgd,
--    and the same for OUT. Then net = inflow - outflow.
-- 3. Which entities are net receivers, which are net payers? That tells you who funds whom
--    (look for it in the intercompany payments later).


-- -------------------------------------------------------------------------------------
-- Step 5 ✏️ TODO · Monthly trend (for the dashboard line chart)
-- -------------------------------------------------------------------------------------
-- strftime('%Y-%m', pay_date) AS month; value by month and direction.
-- ✔️ expect: gentle upward trend, a dip around Chinese New Year for CN entities.


-- -------------------------------------------------------------------------------------
-- Step 6 ✅ · Save as views for the dashboard (one view per chart)
-- -------------------------------------------------------------------------------------
-- Views store the QUERY, not the result: they re-run each time, so they stay current when
-- new data arrives (including live stream mode).
-- vw_01_currency   value and count per currency
-- vw_01_corridor   cross-border corridors by value (all corridors; the dashboard takes the top N);
--                  sender / receiver country are separate columns so a map can use them
-- vw_01_entity     inflow / outflow / net per entity
-- vw_01_monthly    monthly value, inflow, outflow and net (SGD m)

DROP VIEW IF EXISTS vw_01_currency;
CREATE VIEW vw_01_currency AS
SELECT p.currency_code,
       COUNT(*)                          AS n_payments,
       ROUND(SUM(p.amount_sgd) / 1e6, 1) AS value_sgd_m
FROM fact_payment p
WHERE p.is_intercompany = 0
  AND p.status IN ('completed', 'delayed')
GROUP BY p.currency_code;

DROP VIEW IF EXISTS vw_01_corridor;
CREATE VIEW vw_01_corridor AS
SELECT p.sender_country || ' -> ' || p.receiver_country AS corridor,
       p.sender_country,
       p.receiver_country,
       COUNT(*)                          AS n_payments,
       ROUND(SUM(p.amount_sgd) / 1e6, 1) AS value_sgd_m
FROM fact_payment p
WHERE p.is_intercompany = 0
  AND p.status IN ('completed', 'delayed')
  AND p.sender_country <> p.receiver_country
GROUP BY p.sender_country, p.receiver_country;

DROP VIEW IF EXISTS vw_01_entity;
CREATE VIEW vw_01_entity AS
SELECT e.entity_id,
       e.name AS entity_name,
       ROUND(SUM(CASE WHEN p.direction = 'IN'  THEN p.amount_sgd ELSE 0 END), 2) AS inflow_sgd,
       ROUND(SUM(CASE WHEN p.direction = 'OUT' THEN p.amount_sgd ELSE 0 END), 2) AS outflow_sgd,
       ROUND(SUM(CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END), 2) AS net_inflow_sgd
FROM fact_payment p
JOIN dim_account a ON a.account_id = p.account_id
JOIN dim_entity  e ON e.entity_id  = a.entity_id
WHERE p.is_intercompany = 0
  AND p.status IN ('completed', 'delayed')
GROUP BY e.entity_id, e.name;

DROP VIEW IF EXISTS vw_01_monthly;
CREATE VIEW vw_01_monthly AS
SELECT strftime('%Y-%m', p.initiated_ts)    AS month,
       date(p.initiated_ts, 'start of month') AS month_start,      -- a real date: Tableau needs one for a time axis
       COUNT(*)                          AS n_payments,
       ROUND(SUM(p.amount_sgd) / 1e6, 1) AS value_sgd_m,
       ROUND(SUM(CASE WHEN p.direction = 'IN'  THEN p.amount_sgd ELSE 0 END) / 1e6, 1) AS inflow_sgd_m,
       ROUND(SUM(CASE WHEN p.direction = 'OUT' THEN p.amount_sgd ELSE 0 END) / 1e6, 1) AS outflow_sgd_m,
       ROUND(SUM(CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END) / 1e6, 1) AS net_inflow_sgd_m
FROM fact_payment p
WHERE p.is_intercompany = 0
  AND p.status IN ('completed', 'delayed')
GROUP BY month;
