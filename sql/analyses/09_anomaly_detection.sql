-- =====================================================================================
-- 09 · Anomaly and exception detection (SQL rules first, Python models later)
-- =====================================================================================
-- Question : which payments look wrong and deserve a human look?
-- Start    : RULES - amounts just below a reporting threshold, sudden bursts, round amounts,
--            high-risk countries, new beneficiaries paid a lot, duplicates, off-hours.
-- Then     : MODELS (Python) - z-score / IQR on amount per counterparty, Isolation Forest.
-- Metrics  : PRECISION (of my alerts, how many were real?) and ALERTS PER DAY.
--            Too many alerts is the real-world failure mode: ops teams stop reading them.
-- So what  : links to AML and fraud controls (lesson 9).
-- Tables   : fact_payment, dim_counterparty (first_seen_date, country), dim_country (risk_rating)
-- Answer key: data_small/answer_key/business_anomalies.parquet (payment_id, anomaly_type) - score in Python.
--
-- These are BUSINESS anomalies: real money that did move. Not the dirty data of the pipeline.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · Outgoing external payments with the fields the rules need
-- -------------------------------------------------------------------------------------
WITH pay AS (
    SELECT p.payment_id,
           p.account_id,
           p.counterparty_id,
           p.amount,
           p.amount_sgd,
           p.currency_code,
           p.initiated_ts,
           date(p.initiated_ts)                 AS pay_date,
           CAST(strftime('%H', p.initiated_ts) AS INTEGER) AS hour_utc,
           c.country                            AS cpty_country,
           k.risk_rating                        AS cpty_country_risk,
           c.first_seen_date
    FROM fact_payment p
    JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
    JOIN dim_country      k ON k.country_code    = c.country
    WHERE p.direction = 'OUT'
      AND p.is_intercompany = 0
)
SELECT * FROM pay LIMIT 20;


-- -------------------------------------------------------------------------------------
-- Step 2 ✏️ TODO · One rule at a time, each as a 0/1 column
-- -------------------------------------------------------------------------------------
-- Build them one by one; run each and ask "how many alerts per day would this generate?"
--
--   rule_high_risk   : cpty_country_risk = 'high'
--   rule_round       : amount >= 10000 AND amount % 1000 = 0       (tune the thresholds!)
--   rule_structuring : amount_sgd BETWEEN 0.9 * T AND T   for a reporting threshold T.
--                      Look at a histogram of amount_sgd (GROUP BY CAST(amount_sgd / 500 AS INT))
--                      - a bump just below a round number is the tell.
--   rule_new_bene    : julianday(pay_date) - julianday(first_seen_date) <= 7 AND amount_sgd is high
--                      (high = above that counterparty type's P95; start with a fixed number)
--   rule_duplicate   : another payment with the same counterparty_id and amount within 1 day
--                      -> EXISTS (SELECT 1 FROM pay q WHERE q.counterparty_id = pay.counterparty_id
--                                  AND q.amount = pay.amount AND q.payment_id <> pay.payment_id
--                                  AND ABS(julianday(q.initiated_ts) - julianday(pay.initiated_ts)) <= 1)
--   rule_burst       : counterparty's payment count that day >> its usual daily count
--                      (COUNT(*) OVER (PARTITION BY counterparty_id, pay_date))
--   rule_off_hours   : initiated outside business hours in the ENTITY's local time.
--                      hour_utc is UTC: SG = UTC+8, CN = +8, IN = +5.5, DE/NL = +1/+2, GB = 0/+1,
--                      US = -5..-8. Easiest to do this one in Python with zoneinfo.


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Combine into an alert table
-- -------------------------------------------------------------------------------------
-- One row per payment with any rule = 1: payment_id, pay_date, the rule columns,
-- n_rules = sum of the rule columns. Save as vw_09_alerts.
-- Alerts per day = COUNT(*) / COUNT(DISTINCT pay_date).


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Score in Python (notebooks/09_anomalies.ipynb, later)
-- -------------------------------------------------------------------------------------
--   alerts = pd.read_sql("SELECT * FROM vw_09_alerts", con)
--   answer = pd.read_parquet("data_small/answer_key/business_anomalies.parquet")
-- Per rule: precision = alerts that are in the answer key / all alerts of that rule;
--           recall    = answer-key rows of that type you alerted on / all answer-key rows of that type.
-- Then try a model (Isolation Forest on amount_sgd, hour, days-since-first-seen, ...) and compare
-- its precision at the SAME alerts-per-day budget. That's the rule-vs-model trade-off slide.
