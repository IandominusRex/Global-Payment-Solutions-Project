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
-- Step 2 ✏️ TODO · Net position by currency (the headline)
-- -------------------------------------------------------------------------------------
-- GROUP BY currency_code: SUM(signed_sgd) as net_sgd_m. Positive = long, negative = short.
-- Then by (entity_id, currency_code) for the detail table.
-- ✔️ expect: net LONG EUR, net SHORT CNY.


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Value at risk from a currency move
-- -------------------------------------------------------------------------------------
-- A simple sensitivity (not statistical VaR): loss if the currency moves 5% / 10% against you
--   = ABS(net_sgd) * 0.05   and   ABS(net_sgd) * 0.10
-- Use a recent window (e.g. last 90 days of flows): that's the exposure you carry now.


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Hedged share
-- -------------------------------------------------------------------------------------
-- fact_fx_hedge: forwards traded by each entity. A hedge is LIVE on date d if
--   trade_date_id <= d < maturity_date_id.
-- For each foreign currency: hedged notional = SUM(buy_amount where buy_currency = X)
--                                            - SUM(sell_amount where sell_currency = X)
-- Convert to SGD with fact_fx_rate on that date, then hedged_pct = hedged / exposure.
-- ✔️ expect: partially hedged - not 0%, not 100%.


-- -------------------------------------------------------------------------------------
-- Step 5 ✏️ TODO · Views
-- -------------------------------------------------------------------------------------
-- vw_07_net_position, vw_07_sensitivity, vw_07_hedged_share
