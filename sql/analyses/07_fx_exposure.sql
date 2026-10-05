-- =====================================================================================
-- 07 · FX exposure
-- =====================================================================================
-- Question : which foreign currencies are we long or short, by how much, and how much is hedged?
-- Metrics  : net position by currency (receipts minus payments) in non-functional currencies;
--            value at risk from a 5% / 10% currency move; hedged vs unhedged share.
-- Chart    : waterfall or stacked bar by currency (long up, short down; hedged vs unhedged).
-- So what  : where a forward contract or netting would help.
-- Tables   : fact_payment, dim_account, dim_entity (functional_currency), fact_fx_rate, fact_fx_hedge
--
-- Key idea: a EUR payment is only FX EXPOSURE for an entity whose home (functional) currency is
-- NOT EUR. Germany GmbH paying in EUR has no exposure; SG Operations receiving EUR does.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · Flows in a currency that is foreign to the entity
-- -------------------------------------------------------------------------------------
--   * signed_sgd: + for money in (we become LONG that currency), - for money out (SHORT)
WITH fx_flows AS (
    SELECT p.payment_id,
           e.entity_id,
           e.functional_currency,
           p.currency_code,
           date(p.settled_ts) AS settle_date,
           CASE WHEN p.direction = 'IN' THEN p.amount     ELSE -p.amount     END AS signed_amount,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
)
SELECT * FROM fx_flows LIMIT 20;


-- -------------------------------------------------------------------------------------
-- Step 2 ✅ · Net position by currency (the headline), then by entity and currency
-- -------------------------------------------------------------------------------------
-- Positive = long, negative = short. All-time flows (the 90-day window comes in step 3).
WITH fx_flows AS (
    SELECT p.payment_id,
           e.entity_id,
           e.functional_currency,
           p.currency_code,
           date(p.settled_ts) AS settle_date,
           CASE WHEN p.direction = 'IN' THEN p.amount     ELSE -p.amount     END AS signed_amount,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
)
SELECT currency_code, ROUND(SUM(signed_sgd)/1e6, 1) AS net_sgd_m
FROM fx_flows
GROUP BY currency_code;

WITH fx_flows AS (
    SELECT p.payment_id,
           e.entity_id,
           e.functional_currency,
           p.currency_code,
           date(p.settled_ts) AS settle_date,
           CASE WHEN p.direction = 'IN' THEN p.amount     ELSE -p.amount     END AS signed_amount,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
)
SELECT entity_id, functional_currency, currency_code, ROUND(SUM(signed_sgd)/1e6, 1) AS net_sgd_m
FROM fx_flows
GROUP BY entity_id, currency_code;


-- -------------------------------------------------------------------------------------
-- Step 3 ✅ · Value at risk from a currency move
-- -------------------------------------------------------------------------------------
-- A simple sensitivity (not statistical VaR) on the last 90 days of flows.
WITH fx_flows AS (
    SELECT p.payment_id,
           e.entity_id,
           e.functional_currency,
           p.currency_code,
           date(p.settled_ts) AS settle_date,
           CASE WHEN p.direction = 'IN' THEN p.amount     ELSE -p.amount     END AS signed_amount,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
      AND p.settled_ts >= date((SELECT MAX(settled_ts) FROM fact_payment), '-90 days')
)
SELECT currency_code,
       ROUND(ABS(SUM(signed_sgd)) / 1e6, 1)        AS exposure_sgd_m,
       ROUND(ABS(SUM(signed_sgd)) * 0.05 / 1e6, 2) AS loss_5pct_m,
       ROUND(ABS(SUM(signed_sgd)) * 0.10 / 1e6, 2) AS loss_10pct_m
FROM fx_flows
GROUP BY currency_code;


-- -------------------------------------------------------------------------------------
-- Step 4 ✅ · Hedged share
-- -------------------------------------------------------------------------------------
-- A forward is LIVE on date d if trade_date_id <= d < maturity_date_id. The date is pinned to 2026-09-15:
-- the last forward matures on 2026-09-30, so on the last payment date (2026-10-01) no hedge is live.
-- Each forward is split into a buy leg (+) and a sell leg (-); only legs in a currency foreign to the
-- entity count. hedged_pct = -hedge / exposure: positive when the hedge opposes the exposure,
-- negative when it points the wrong way, above 1 when over-hedged.
WITH asof AS (
    SELECT '2026-09-15' AS d, 20260915 AS d_id
),
fx_flows AS (
    SELECT e.entity_id, e.functional_currency, p.currency_code,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
      AND date(p.settled_ts) > date((SELECT d FROM asof), '-90 days')
      AND date(p.settled_ts) <= (SELECT d FROM asof)
),
exposure AS (
    SELECT entity_id, currency_code, SUM(signed_sgd) AS net_sgd
    FROM fx_flows GROUP BY entity_id, currency_code
),
hedge_legs AS (
    SELECT h.entity_id, h.buy_currency AS currency_code, h.buy_amount AS signed_amount
    FROM fact_fx_hedge h, asof
    WHERE h.trade_date_id <= asof.d_id AND asof.d_id < h.maturity_date_id
    UNION ALL
    SELECT h.entity_id, h.sell_currency, -h.sell_amount
    FROM fact_fx_hedge h, asof
    WHERE h.trade_date_id <= asof.d_id AND asof.d_id < h.maturity_date_id
),
hedged AS (
    SELECT l.entity_id, l.currency_code,
           SUM(l.signed_amount * r.rate_to_sgd) AS hedge_sgd
    FROM hedge_legs l
    JOIN dim_entity e ON e.entity_id = l.entity_id
    JOIN asof ON 1 = 1
    JOIN fact_fx_rate r ON r.currency_code = l.currency_code AND r.date_id = asof.d_id
    WHERE l.currency_code <> e.functional_currency
    GROUP BY l.entity_id, l.currency_code
)
SELECT x.entity_id, x.currency_code,
       ROUND(x.net_sgd / 1e6, 1)                      AS net_sgd_m,
       ROUND(COALESCE(h.hedge_sgd, 0) / 1e6, 1)       AS hedge_sgd_m,
       ROUND(-COALESCE(h.hedge_sgd, 0) / x.net_sgd, 2) AS hedged_pct
FROM exposure x
LEFT JOIN hedged h ON h.entity_id = x.entity_id AND h.currency_code = x.currency_code
ORDER BY x.entity_id, x.currency_code;

-- Roll-up to currency level (sum the amounts, then recompute the percentage; never sum percentages)
WITH asof AS (
    SELECT '2026-09-15' AS d, 20260915 AS d_id
),
fx_flows AS (
    SELECT e.entity_id, e.functional_currency, p.currency_code,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
      AND date(p.settled_ts) > date((SELECT d FROM asof), '-90 days')
      AND date(p.settled_ts) <= (SELECT d FROM asof)
),
exposure AS (
    SELECT entity_id, currency_code, SUM(signed_sgd) AS net_sgd
    FROM fx_flows GROUP BY entity_id, currency_code
),
hedge_legs AS (
    SELECT h.entity_id, h.buy_currency AS currency_code, h.buy_amount AS signed_amount
    FROM fact_fx_hedge h, asof
    WHERE h.trade_date_id <= asof.d_id AND asof.d_id < h.maturity_date_id
    UNION ALL
    SELECT h.entity_id, h.sell_currency, -h.sell_amount
    FROM fact_fx_hedge h, asof
    WHERE h.trade_date_id <= asof.d_id AND asof.d_id < h.maturity_date_id
),
hedged AS (
    SELECT l.entity_id, l.currency_code,
           SUM(l.signed_amount * r.rate_to_sgd) AS hedge_sgd
    FROM hedge_legs l
    JOIN dim_entity e ON e.entity_id = l.entity_id
    JOIN asof ON 1 = 1
    JOIN fact_fx_rate r ON r.currency_code = l.currency_code AND r.date_id = asof.d_id
    WHERE l.currency_code <> e.functional_currency
    GROUP BY l.entity_id, l.currency_code
),
hedged_by_entity_currency AS (
    SELECT x.entity_id, x.currency_code, x.net_sgd, COALESCE(h.hedge_sgd, 0) AS hedge_sgd
    FROM exposure x
    LEFT JOIN hedged h ON h.entity_id = x.entity_id AND h.currency_code = x.currency_code
)
SELECT currency_code,
       ROUND(SUM(net_sgd) / 1e6, 1)             AS total_net_sgd_m,
       ROUND(SUM(hedge_sgd) / 1e6, 1)           AS total_hedged_sgd_m,
       ROUND(-SUM(hedge_sgd) / SUM(net_sgd), 2) AS total_hedged_pct
FROM hedged_by_entity_currency
GROUP BY currency_code;


-- -------------------------------------------------------------------------------------
-- Step 5 ✅ · Views
-- -------------------------------------------------------------------------------------
-- vw_07_net_position   net position per entity and currency, last 90 days to the as-of date (LONG / SHORT)
-- vw_07_sensitivity    exposure and the loss from a 5% / 10% move, per currency
-- vw_07_hedged_share   live hedge and hedged share per entity and currency
-- The as-of date is not pinned: it is the last forward trade date, capped at the last payment (2026-09-01 here).

DROP VIEW IF EXISTS vw_07_net_position;
CREATE VIEW vw_07_net_position AS
WITH asof AS (                        -- latest day the hedge book is live, capped at the last payment
    SELECT m.id AS d_id,
           date(substr(m.id, 1, 4) || '-' || substr(m.id, 5, 2) || '-' || substr(m.id, 7, 2)) AS d
    FROM (SELECT MIN((SELECT MAX(trade_date_id) FROM fact_fx_hedge),
                     (SELECT CAST(strftime('%Y%m%d', MAX(settled_ts)) AS INTEGER) FROM fact_payment)) AS id) m
),
fx_flows AS (
    SELECT e.entity_id, e.functional_currency, p.currency_code,
           CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END AS signed_sgd
    FROM fact_payment p
    JOIN dim_account a ON a.account_id = p.account_id
    JOIN dim_entity  e ON e.entity_id  = a.entity_id
    WHERE p.is_intercompany = 0
      AND p.status IN ('completed', 'delayed')
      AND p.currency_code <> e.functional_currency
      AND date(p.settled_ts) >  date((SELECT d FROM asof), '-90 days')
      AND date(p.settled_ts) <= (SELECT d FROM asof)
)
SELECT f.entity_id, f.functional_currency, f.currency_code,
       (SELECT d_id FROM asof)                                  AS as_of_date_id,
       ROUND(SUM(f.signed_sgd), 0)                              AS net_sgd,
       CASE WHEN SUM(f.signed_sgd) >= 0 THEN 'LONG' ELSE 'SHORT' END AS position
FROM fx_flows f
GROUP BY f.entity_id, f.functional_currency, f.currency_code;

DROP VIEW IF EXISTS vw_07_sensitivity;
CREATE VIEW vw_07_sensitivity AS
SELECT currency_code,
       as_of_date_id,
       ROUND(SUM(net_sgd), 0)              AS net_sgd,
       ROUND(ABS(SUM(net_sgd)), 0)         AS exposure_sgd,
       ROUND(ABS(SUM(net_sgd)) * 0.05, 0)  AS loss_5pct_sgd,
       ROUND(ABS(SUM(net_sgd)) * 0.10, 0)  AS loss_10pct_sgd
FROM vw_07_net_position
GROUP BY currency_code, as_of_date_id;

DROP VIEW IF EXISTS vw_07_hedged_share;
CREATE VIEW vw_07_hedged_share AS
WITH asof AS (
    SELECT MAX(as_of_date_id) AS d_id FROM vw_07_net_position
),
hedge_legs AS (                       -- one row per leg of each LIVE forward
    SELECT h.entity_id, h.buy_currency AS currency_code, h.buy_amount AS signed_amount
    FROM fact_fx_hedge h, asof
    WHERE h.trade_date_id <= asof.d_id AND asof.d_id < h.maturity_date_id
    UNION ALL
    SELECT h.entity_id, h.sell_currency, -h.sell_amount
    FROM fact_fx_hedge h, asof
    WHERE h.trade_date_id <= asof.d_id AND asof.d_id < h.maturity_date_id
),
hedged AS (
    SELECT l.entity_id, l.currency_code,
           SUM(l.signed_amount * r.rate_to_sgd) AS hedge_sgd
    FROM hedge_legs l
    JOIN dim_entity  e ON e.entity_id = l.entity_id
    JOIN asof          ON 1 = 1
    JOIN fact_fx_rate r ON r.currency_code = l.currency_code AND r.date_id = asof.d_id
    WHERE l.currency_code <> e.functional_currency
    GROUP BY l.entity_id, l.currency_code
)
SELECT n.entity_id, n.functional_currency, n.currency_code, n.as_of_date_id,
       n.net_sgd,
       ROUND(COALESCE(h.hedge_sgd, 0), 0)                                AS hedge_sgd,
       ROUND(-COALESCE(h.hedge_sgd, 0) / NULLIF(n.net_sgd, 0), 4)        AS hedged_pct
FROM vw_07_net_position n
LEFT JOIN hedged h ON h.entity_id = n.entity_id AND h.currency_code = n.currency_code;
