-- First look at the clean database. Run one query at a time (in DBeaver: put the cursor in it, Ctrl/Cmd+Enter).
-- Each one walks part of the diagram in docs/data-model.md. Read the comment, guess the answer, then run it.


-- 1. How big is each table?
--    Expect ~106k payments, but ~4x as many events: every payment has several lifecycle steps.
SELECT 'fact_payment'        AS table_name, COUNT(*) AS n_rows FROM fact_payment
UNION ALL SELECT 'fact_payment_event',  COUNT(*) FROM fact_payment_event
UNION ALL SELECT 'fact_invoice',        COUNT(*) FROM fact_invoice
UNION ALL SELECT 'fact_statement_line', COUNT(*) FROM fact_statement_line
UNION ALL SELECT 'fact_balance',        COUNT(*) FROM fact_balance;


-- 2. One payment, fully described: fact_payment joined out to its dimensions.
--    This is the star schema in action: the fact holds IDs, the dimensions give them meaning.
SELECT p.payment_id,
       e.name            AS our_entity,
       a.account_id,
       c.name            AS counterparty,
       c.country         AS counterparty_country,
       t.rail,
       p.amount, p.currency_code,
       ROUND(p.amount_sgd, 2) AS amount_sgd,
       p.status
FROM fact_payment p
JOIN dim_account      a ON a.account_id      = p.account_id
JOIN dim_entity       e ON e.entity_id       = a.entity_id
JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
JOIN dim_payment_type t ON t.type_id         = p.type_id
WHERE p.payment_id = 'P00008587';


-- 3. The same payment's history: one row per lifecycle step.
--    hop_seq 1..3 are the correspondent-bank hops of a SWIFT payment.
SELECT event_ts, status, bank_id, hop_seq, reason_code
FROM fact_payment_event
WHERE payment_id = 'P00008587'
ORDER BY event_ts, event_id;


-- 4. Intercompany transfers appear twice (an OUT leg and an IN leg) with the same end_to_end_id.
--    Look at the value in the is_intercompany = 1 rows: counting both legs would inflate group totals.
SELECT is_intercompany, direction,
       COUNT(*)                        AS n_payments,
       ROUND(SUM(amount_sgd) / 1e6, 1) AS value_sgd_m
FROM fact_payment
GROUP BY is_intercompany, direction;

SELECT end_to_end_id, payment_id, direction, account_id, counterparty_id, amount, currency_code
FROM fact_payment
WHERE is_intercompany = 1
ORDER BY end_to_end_id
LIMIT 6;


-- 5. Where does the group hold its money today? Latest closing balance per entity, in SGD.
--    Negative = overdrawn. This is the starting point for cash concentration (analysis 6).
SELECT e.entity_id, e.name, e.country,
       COUNT(*)                                  AS n_accounts,
       ROUND(SUM(b.closing_balance_sgd) / 1e6, 2) AS balance_sgd_m
FROM fact_balance b
JOIN dim_account a ON a.account_id = b.account_id
JOIN dim_entity  e ON e.entity_id  = a.entity_id
WHERE b.date_id = (SELECT MAX(date_id) FROM fact_balance)
GROUP BY e.entity_id, e.name, e.country
ORDER BY balance_sgd_m DESC;
