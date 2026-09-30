"""Business anomalies (v4): suspicious but valid payments.

Injected BEFORE the ledger (they are real money movements): duplicates within
minutes, structuring just below threshold, round amounts, bursts, high-risk
countries, new beneficiary + high value, off-hours. Each injected payment_id is
written with its anomaly_type to data/truth/label_anomaly.parquet.
"""
