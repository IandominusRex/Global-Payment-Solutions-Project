"""Data-quality defects (v4): dirty data in the RAW landing layer only.

Applied AFTER the ledger to the emitted batch files, never to the ledger itself,
so balances still reconcile and the cleaning pipeline has a known right answer.
Defects: null purpose codes, non-allowed currency codes ("usd", "SGP", "EUR "),
negative amounts, settled < initiated, re-sent file duplicates, inconsistent
country names. Each defect is labelled in data/truth/label_dq.parquet.
"""
