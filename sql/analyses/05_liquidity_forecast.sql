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
-- Step 5 ✏️ TODO · Views for the Python notebook
-- -------------------------------------------------------------------------------------
-- vw_05_daily_balance, vw_05_daily_net_flow, vw_05_open_invoices_by_week
-- In Python:  pd.read_sql("SELECT * FROM vw_05_daily_net_flow", con)
-- then: baseline = average of the same weekday over the last 8 weeks; backtest by pretending
-- "today" is 13 weeks earlier and comparing to what actually happened (MAPE, bias).
