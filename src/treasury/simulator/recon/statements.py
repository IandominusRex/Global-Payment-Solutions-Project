"""Bank statement layer (v4): camt.053-like lines, separate from ERP records.

Remittance quality by payer: virtual-account payers -> exact invoice_ref;
others -> truncated / typo / missing / one-payment-many-invoices / short-pay.
The TRUE payment -> invoice allocation is written to data/truth/ only, so
the reconciliation matcher's auto-match accuracy can be scored (analysis 8).
"""
