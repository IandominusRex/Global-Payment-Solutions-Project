-- =====================================================================================
-- Deferred · Reconciliation (SQL matches, Python scores)
-- Not part of the current scope: kept here for later, not numbered and not built.
-- =====================================================================================
-- Question : how many incoming payments match an expected invoice automatically?
-- Metrics  : auto-match rate; unmatched value; ageing of unmatched items; match rate by reference
--            quality (virtual-account payers vs free-text payers).
-- Chart    : match rate by rule (funnel); match rate VA vs free text (bar); unmatched ageing buckets.
-- So what  : reference quality drives the match rate - which is why banks sell virtual accounts
--            and push structured remittance data.
-- Tables   : fact_payment (IN), fact_invoice (AR), dim_counterparty (uses_virtual_account)
-- Answer key: data_small/answer_key/payment_to_invoice.parquet (score in Python, never join it here)
--
-- There is NO payment -> invoice column. Building that link is the whole analysis.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- Step 1 ✅ given · The two sides to match
-- -------------------------------------------------------------------------------------
-- Receipts: external incoming payments. remittance_ref is whatever the customer typed.
SELECT p.payment_id, p.account_id, p.counterparty_id, p.amount, p.currency_code,
       date(p.settled_ts) AS received_date, p.remittance_ref, c.uses_virtual_account
FROM fact_payment p
JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
WHERE p.direction = 'IN' AND p.is_intercompany = 0 AND p.status IN ('completed', 'delayed')
LIMIT 20;

-- Open receivables: what customers owe us.
SELECT invoice_id, entity_id, counterparty_id, invoice_ref, amount, currency_code, due_date_id, status
FROM fact_invoice
WHERE direction = 'AR'
LIMIT 20;


-- -------------------------------------------------------------------------------------
-- Step 2 ✏️ TODO · Rule 1: exact reference match
-- -------------------------------------------------------------------------------------
-- JOIN receipts to AR invoices ON p.remittance_ref = i.invoice_ref
--                              AND p.counterparty_id = i.counterparty_id
--                              AND p.currency_code  = i.currency_code
-- Count matched receipts / all receipts = exact-match rate.
-- Then split by uses_virtual_account.
-- ✔️ expect: virtual-account payers ~100% exact; free-text payers far lower (a 20+ point gap).


-- -------------------------------------------------------------------------------------
-- Step 3 ✏️ TODO · Rule 2: fuzzy reference (for what Rule 1 missed)
-- -------------------------------------------------------------------------------------
-- Look at unmatched remittance_ref values first. You'll see truncations, typos, extra text.
-- Ideas: extract the digits (the invoice number) and compare; LIKE '%' || digits || '%';
-- strip spaces and dashes. SQL gets clumsy here - fine to finish this rule in Python.


-- -------------------------------------------------------------------------------------
-- Step 4 ✏️ TODO · Rule 3: amount + customer + date window (no usable reference)
-- -------------------------------------------------------------------------------------
-- Same counterparty, same currency, same amount (ABS(p.amount - i.amount) < 0.01), paid within
-- e.g. 60 days of due date. Risk: a customer with two invoices of the same amount -> ambiguous.
-- Only accept when exactly ONE invoice qualifies (COUNT(*) = 1 per payment).
-- Watch out: short payments (fees deducted) and one payment covering several invoices.


-- -------------------------------------------------------------------------------------
-- Step 5 ✏️ TODO · The match table + unmatched ageing
-- -------------------------------------------------------------------------------------
-- UNION the rules into one table: payment_id, invoice_id, match_rule. Save as vw_recon_matches.
-- Unmatched receipts: days since received -> buckets 0-7, 8-30, 31-90, 90+; sum value per bucket.
--
-- Then in Python: compare vw_recon_matches with answer_key/payment_to_invoice.parquet.
--   match rate = how many you matched;  ACCURACY = how many of those were the right invoice.
-- A matcher that confidently matches the wrong invoice is worse than one that leaves it unmatched.
--
-- Stretch: bank statement lines (fact_statement_line, bank_tx_code = 'RCDT') -> payments.
-- Answer key: answer_key/statement_line_to_payment.parquet.
