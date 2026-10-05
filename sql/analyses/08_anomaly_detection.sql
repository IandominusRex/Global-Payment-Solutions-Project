-- =====================================================================================
-- 08 · Anomaly and exception detection (SQL rules first, Python models later)
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
-- Step 3 ✅ · Combine the rules into one alert view
-- -------------------------------------------------------------------------------------
-- One row per outgoing external payment that trips at least one rule. n_rules = how many.
-- Alerts per day = COUNT(*) / COUNT(DISTINCT pay_date).
-- The thresholds (10,000 round, 18,000-20,000 structuring, 50,000 new beneficiary, 10 burst,
-- 06:00-21:59 local) were read off the data: see the README, Analysis 8.

DROP VIEW IF EXISTS vw_08_alerts;
CREATE VIEW vw_08_alerts AS
WITH pay AS (
    SELECT p.payment_id,
           p.counterparty_id,
           c.name                AS counterparty_name,
           e.name                AS entity_name,
           p.amount,
           p.amount_sgd,
           p.currency_code,
           p.initiated_ts,
           date(p.initiated_ts)  AS pay_date,
           c.country             AS cpty_country,
           k.risk_rating         AS cpty_country_risk,
           c.first_seen_date,
           LAG(p.initiated_ts)  OVER dup AS prev_same_ts,
           LEAD(p.initiated_ts) OVER dup AS next_same_ts,
           COUNT(*) OVER (PARTITION BY p.counterparty_id, date(p.initiated_ts)) AS n_day,
           COUNT(*) OVER (PARTITION BY p.counterparty_id)                       AS n_cpty,
           CASE e.timezone WHEN 'Asia/Singapore'   THEN 480
                           WHEN 'Asia/Shanghai'    THEN 480
                           WHEN 'Asia/Kolkata'     THEN 330
                           WHEN 'Europe/Berlin'    THEN 60
                           WHEN 'Europe/Amsterdam' THEN 60
                           WHEN 'Europe/London'    THEN 0
                           WHEN 'America/New_York' THEN -300 END AS tz_offset_min
    FROM fact_payment p
    JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
    JOIN dim_country      k ON k.country_code    = c.country
    JOIN dim_account      a ON a.account_id      = p.account_id
    JOIN dim_entity       e ON e.entity_id       = a.entity_id
    WHERE p.direction = 'OUT'
      AND p.is_intercompany = 0
    WINDOW dup AS (PARTITION BY p.counterparty_id, p.amount ORDER BY p.initiated_ts)
),
flagged AS (
    SELECT payment_id, counterparty_id, counterparty_name, entity_name, cpty_country, amount, amount_sgd, currency_code, initiated_ts, pay_date,
           CASE WHEN cpty_country_risk = 'high' THEN 1 ELSE 0 END                         AS rule_high_risk,
           CASE WHEN amount >= 10000 AND amount = CAST(amount AS INTEGER)
                 AND CAST(amount AS INTEGER) % 1000 = 0 THEN 1 ELSE 0 END                 AS rule_round,
           CASE WHEN amount_sgd >= 18000 AND amount_sgd < 20000 THEN 1 ELSE 0 END        AS rule_structuring,
           CASE WHEN julianday(pay_date) - julianday(first_seen_date) <= 7
                 AND amount_sgd >= 50000 THEN 1 ELSE 0 END                                AS rule_new_bene,
           CASE WHEN julianday(initiated_ts) - julianday(prev_same_ts) <= 1
                  OR julianday(next_same_ts) - julianday(initiated_ts) <= 1 THEN 1 ELSE 0 END AS rule_duplicate,
           CASE WHEN n_day >= 10
                 AND n_day >= 5 * n_cpty * 1.0 / (SELECT COUNT(DISTINCT pay_date) FROM pay) THEN 1 ELSE 0 END AS rule_burst,
           CASE WHEN CAST(strftime('%H', datetime(initiated_ts, tz_offset_min || ' minutes')) AS INTEGER)
                     NOT BETWEEN 6 AND 21 THEN 1 ELSE 0 END                               AS rule_off_hours
    FROM pay
)
SELECT *,
       rule_high_risk + rule_round + rule_structuring + rule_new_bene
     + rule_duplicate + rule_burst + rule_off_hours AS n_rules,
       -- readable list of the rules that fired, for tooltips and tables
       TRIM(CASE WHEN rule_high_risk  = 1 THEN 'high-risk country, ' ELSE '' END
         || CASE WHEN rule_round      = 1 THEN 'round amount, '      ELSE '' END
         || CASE WHEN rule_structuring = 1 THEN 'structuring, '      ELSE '' END
         || CASE WHEN rule_new_bene   = 1 THEN 'new beneficiary, '   ELSE '' END
         || CASE WHEN rule_duplicate  = 1 THEN 'duplicate, '         ELSE '' END
         || CASE WHEN rule_burst      = 1 THEN 'burst, '             ELSE '' END
         || CASE WHEN rule_off_hours  = 1 THEN 'off-hours, '        ELSE '' END, ', ') AS rules_triggered
FROM flagged
WHERE rule_high_risk + rule_round + rule_structuring + rule_new_bene
    + rule_duplicate + rule_burst + rule_off_hours > 0;


-- -------------------------------------------------------------------------------------
-- Step 4 ✅ · Score the alerts in Python
-- -------------------------------------------------------------------------------------
-- Run:  .venv/bin/python -m treasury.analytics.score_alerts
-- It compares vw_08_alerts with data_small/answer_key/business_anomalies.parquet and prints
-- precision and recall per rule, then an Isolation Forest model at the same alerts-per-day budget.
