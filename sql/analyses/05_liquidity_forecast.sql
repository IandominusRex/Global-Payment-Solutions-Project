-- =====================================================================================
-- 05 · Liquidity and cash forecast (SQL prepares, Python forecasts)
-- =====================================================================================
-- Question : how much cash will each entity and currency have over the next 13 weeks?
-- Method   : start with a moving average / seasonal baseline, compare with actuals, then improve
--            it with KNOWN future flows (open invoices by due date).
-- Metrics  : forecast error, MAPE, bias.
-- Chart    : actual vs forecast line with a band; error by week.
-- So what  : lets treasury fund shortfalls early (cheaply) instead of via overdraft (expensively).
-- Tables   : fact_balance, fact_payment, fact_invoice, dim_calendar, dim_account
--
-- SQL's job here: build clean daily/weekly time series. The forecasting model itself goes in
-- notebooks/05_forecast.ipynb (later), reading these views.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · Daily closing balance per entity and currency
-- -------------------------------------------------------------------------------------
-- fact_balance is one row per account per day. Sum accounts up to entity + currency.
-- Keep the ORIGINAL currency here: forecasting in SGD would mix in FX moves.
SELECT b.date_id,
       a.entity_id,
       a.currency_code,
       ROUND(SUM(b.closing_balance), 2) AS closing_balance
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
GROUP BY b.date_id, a.entity_id, a.currency_code
ORDER BY a.entity_id, a.currency_code, b.date_id
LIMIT 30;


-- -------------------------------------------------------------------------------------
-- Step 2 ✏️ TODO · Daily net cash flow per entity and currency
-- -------------------------------------------------------------------------------------
-- Settled payments only. Use the settlement DATE (date(settled_ts)), not initiation: cash
-- moves when it settles.
--   net_flow = SUM(CASE WHEN direction = 'IN' THEN amount ELSE -amount END)
-- Keep intercompany IN here: from ONE entity's point of view, funding from HQ is real cash.
-- (Question: why is that different from analysis 01?)


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Weekly series + seasonality checks
-- -------------------------------------------------------------------------------------
-- 1. Weekly: strftime('%Y-%W', ...) or join dim_date for year/week.
-- 2. Seasonality: average net flow by day_of_week (dim_date.day_of_week) and by is_month_end.
-- ✔️ expect: Tuesday/Thursday payment-run peaks, quiet weekends, month-end spikes (payroll, tax).


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Known future flows: open invoices by due week
-- -------------------------------------------------------------------------------------
-- fact_invoice WHERE status IN ('open', 'part_paid'): AR = expected receipts, AP = expected payments.
-- Group by entity, currency and due week (due_date_id -> dim_date).
-- Business days: a due date on a holiday pays on the next business day in that COUNTRY -
-- dim_calendar(date_id, country_code, is_business_day) tells you which.
-- Remember customers pay late: dim_counterparty.avg_days_late. A good forecast shifts AR by it.


-- -------------------------------------------------------------------------------------
-- Step 5 ✅ · Views for the Python notebook and the dashboard
-- -------------------------------------------------------------------------------------
-- vw_05_daily_balance     closing balance per day, entity and currency (own currency and SGD)
-- vw_05_daily_net_flow    settled net and gross flow per day, entity and currency (own currency and SGD)
-- vw_05_weekly_net_flow   the same by week (week_start = the Monday)
-- vw_05_weekday_pattern   average net / gross flow by day of week (the payment-run rhythm)
-- Amounts in the *_sgd columns can be summed across currencies and entities; the own-currency
-- columns cannot (only add them up within one currency).
-- (open invoices by due week is not built yet)
-- In Python:  pd.read_sql("SELECT * FROM vw_05_daily_net_flow", con)

DROP VIEW IF EXISTS vw_05_daily_balance;
CREATE VIEW vw_05_daily_balance AS
SELECT b.date_id, a.entity_id, e.name AS entity_name, a.currency_code,
       ROUND(SUM(b.closing_balance), 2)                    AS closing_balance,
       ROUND(SUM(b.closing_balance * fx.rate_to_sgd), 2)   AS closing_balance_sgd
FROM fact_balance b
JOIN dim_account a   ON a.account_id = b.account_id
JOIN dim_entity e    ON e.entity_id = a.entity_id
JOIN fact_fx_rate fx ON fx.date_id = b.date_id AND fx.currency_code = b.currency_code
GROUP BY b.date_id, a.entity_id, e.name, a.currency_code;

DROP VIEW IF EXISTS vw_05_daily_net_flow;
CREATE VIEW vw_05_daily_net_flow AS
SELECT CAST(strftime('%Y%m%d', p.settled_ts) AS INTEGER) AS date_id,
       a.entity_id, e.name AS entity_name, a.currency_code,
       ROUND(SUM(CASE WHEN p.direction = 'IN' THEN p.amount ELSE -p.amount END), 2)         AS net_flow,
       ROUND(SUM(p.amount), 2)                                                              AS gross_flow,
       ROUND(SUM(CASE WHEN p.direction = 'IN' THEN p.amount_sgd ELSE -p.amount_sgd END), 2) AS net_flow_sgd,
       ROUND(SUM(p.amount_sgd), 2)                                                          AS gross_flow_sgd
FROM fact_payment p
JOIN dim_account a ON a.account_id = p.account_id
JOIN dim_entity e  ON e.entity_id = a.entity_id
WHERE p.status IN ('completed', 'delayed') AND p.settled_ts IS NOT NULL   -- delayed payments settle too
GROUP BY 1, 2, 3, 4;

DROP VIEW IF EXISTS vw_05_weekly_net_flow;
CREATE VIEW vw_05_weekly_net_flow AS
SELECT date(d.date, '-' || d.day_of_week || ' days') AS week_start,      -- day_of_week: 0 = Monday
       f.entity_id, f.entity_name, f.currency_code,
       ROUND(SUM(f.net_flow), 2)     AS weekly_net_flow,
       ROUND(SUM(f.net_flow_sgd), 2) AS weekly_net_flow_sgd,
       COUNT(*)                      AS active_days
FROM vw_05_daily_net_flow f
JOIN dim_date d ON d.date_id = f.date_id
GROUP BY week_start, f.entity_id, f.entity_name, f.currency_code;

DROP VIEW IF EXISTS vw_05_weekday_pattern;
CREATE VIEW vw_05_weekday_pattern AS
SELECT d.day_of_week,
       CASE d.day_of_week WHEN 0 THEN 'Mon' WHEN 1 THEN 'Tue' WHEN 2 THEN 'Wed' WHEN 3 THEN 'Thu'
                          WHEN 4 THEN 'Fri' WHEN 5 THEN 'Sat' ELSE 'Sun' END AS weekday,
       f.entity_id, f.entity_name, f.currency_code,
       COUNT(*)                        AS n_days,
       ROUND(AVG(f.net_flow), 2)       AS avg_net_flow,
       ROUND(AVG(f.gross_flow), 2)     AS avg_gross_flow,
       ROUND(AVG(f.net_flow_sgd), 2)   AS avg_net_flow_sgd,
       ROUND(AVG(f.gross_flow_sgd), 2) AS avg_gross_flow_sgd
FROM vw_05_daily_net_flow f
JOIN dim_date d ON d.date_id = f.date_id
GROUP BY d.day_of_week, f.entity_id, f.entity_name, f.currency_code;
