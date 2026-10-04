"""Stage 5 - score: how good is the pipeline? Compare against the answer key.

Only this module may read data/answer_key/. Checks and repairs must never look at it.

Two questions, two scores:

1. Detection - did the checks find the planted defects?
     precision = flagged AND truly defective / all flagged      ("when I raise a flag, am I right?")
     recall    = flagged AND truly defective / all truly defective   ("did I find them all?")

2. Repair - did the fixes produce the right values?
     share of repaired rows in pipeline_payments_cleaned whose value equals fact_payment
     (fact_payment is what a perfect pipeline would output).

These numbers are your interview line: "the pipeline catches X% of defects with Y% precision,
and Z% of repairs match the ledger."
"""

from __future__ import annotations

import pandas as pd

# Your check_name -> the defect_type used in dq_defects.parquet.
CHECK_TO_DEFECT = {
    "duplicate": "resent_file_duplicate",
    "currency_code": "bad_currency_code",
    "country_code": "inconsistent_country_name",
    "negative_amount": "negative_amount",
    "time_order": "time_order_violation",
    "null_purpose_code": "null_purpose_code",
}


def score_detection(flags: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Precision and recall per defect type.

    Args:
      flags:  your output of run_all_checks (payment_id, check_name)
      labels: data/answer_key/dq_defects.parquet (payment_id, defect_type)

    Tutorial:
      1. Translate: flags["defect_type"] = flags["check_name"].map(CHECK_TO_DEFECT).
      2. Deduplicate both sides on (payment_id, defect_type) - a duplicate payment is flagged
         on both copies, but it is one defect.
      3. Outer-merge on (payment_id, defect_type) with indicator=True.
           "both"       -> true positive  (TP)
           "left_only"  -> false positive (FP): you flagged, the answer key says clean
           "right_only" -> false negative (FN): the answer key says defective, you missed it
      4. Group by defect_type, count TP / FP / FN, then
           precision = TP / (TP + FP),  recall = TP / (TP + FN).
      5. Return a DataFrame: defect_type, tp, fp, fn, precision, recall.

    Then investigate every FP and FN in the notebook. Each one is either a bug in your check
    or a finding worth a sentence in the write-up.
    """
    raise NotImplementedError("score.score_detection")


def score_repairs(clean: pd.DataFrame, correct_payments: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """For each repaired field, the share of repaired rows that now match fact_payment.

    Tutorial:
      1. correct_payments = fact_payment read from the clean database.
      2. For each (check_name, column) pair you repaired - e.g. ("currency_code", "currency_code"),
         ("country_code", "sender_country"), ("negative_amount", "amount"),
         ("time_order", "settled_ts") - take the payment_ids flagged by that check.
      3. Merge clean and correct_payments on payment_id for those ids, compare the column.
         Timestamps: convert both sides with pd.to_datetime before comparing.
      4. Return: check_name, n_repaired, n_correct, accuracy.
    """
    raise NotImplementedError("score.score_repairs")
