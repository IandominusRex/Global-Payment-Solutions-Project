"""Score the rule alerts (vw_08_alerts) against the answer key, then compare with a model.

For each rule:
  precision = of the payments the rule flagged, how many really are that planted anomaly
  recall    = of the payments planted with that anomaly, how many the rule caught
The model (Isolation Forest) is given the same alert budget (as many alerts as all rules together)
so the comparison is fair.

    TREASURY_CONFIG=config/simulation.small.yaml .venv/bin/python -m treasury.analytics.score_alerts
"""

import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from treasury.analytics.views import clean_db_path
from treasury.simulator.config import load_config

# rule column in vw_08_alerts -> anomaly_type in the answer key
RULES = {
    "rule_high_risk": "high_risk_country",
    "rule_round": "round_amount",
    "rule_structuring": "structuring",
    "rule_new_bene": "new_beneficiary_high_value",
    "rule_duplicate": "duplicate_payment",
    "rule_burst": "burst",
    "rule_off_hours": "off_hours",
}

PAYMENTS = """
SELECT p.payment_id, p.amount, p.amount_sgd, p.initiated_ts, p.counterparty_id,
       c.first_seen_date, k.risk_rating,
       CASE e.timezone WHEN 'Asia/Singapore' THEN 480 WHEN 'Asia/Shanghai' THEN 480
                       WHEN 'Asia/Kolkata' THEN 330 WHEN 'Europe/Berlin' THEN 60
                       WHEN 'Europe/Amsterdam' THEN 60 WHEN 'Europe/London' THEN 0
                       WHEN 'America/New_York' THEN -300 END AS tz_offset_min
FROM fact_payment p
JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
JOIN dim_country      k ON k.country_code    = c.country
JOIN dim_account      a ON a.account_id      = p.account_id
JOIN dim_entity       e ON e.entity_id       = a.entity_id
WHERE p.direction = 'OUT' AND p.is_intercompany = 0
"""


def rule_scores(alerts: pd.DataFrame, answer: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for rule, kind in RULES.items():
        flagged = set(alerts.loc[alerts[rule] == 1, "payment_id"])
        planted = set(answer.loc[answer["anomaly_type"] == kind, "payment_id"])
        hit = len(flagged & planted)
        rows.append({
            "rule": rule, "alerts": len(flagged), "planted": len(planted), "caught": hit,
            "precision": hit / len(flagged) if flagged else float("nan"),
            "recall": hit / len(planted) if planted else float("nan"),
        })
    return pd.DataFrame(rows)


def model_features(pay: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(pay["initiated_ts"])
    local_hour = (ts + pd.to_timedelta(pay["tz_offset_min"], unit="m")).dt.hour
    pay_date = ts.dt.normalize()
    return pd.DataFrame({
        "log_amount_sgd": np.log1p(pay["amount_sgd"]),
        "local_hour": local_hour,
        "days_since_first_seen": (pay_date - pd.to_datetime(pay["first_seen_date"])).dt.days,
        "high_risk": (pay["risk_rating"] == "high").astype(int),
        "round_1000": ((pay["amount"] % 1000 == 0) & (pay["amount"] >= 10000)).astype(int),
        "cpty_n_that_day": pay.groupby(["counterparty_id", pay_date])["payment_id"].transform("count"),
        "same_amount_n": pay.groupby(["counterparty_id", "amount"])["payment_id"].transform("count"),
    })


def main() -> None:
    cfg = load_config(os.environ.get("TREASURY_CONFIG", "config/simulation.yaml"))
    answer = pd.read_parquet(cfg.output.answer_key_dir / "business_anomalies.parquet")
    with sqlite3.connect(clean_db_path()) as con:
        alerts = pd.read_sql("SELECT * FROM vw_08_alerts", con)
        pay = pd.read_sql(PAYMENTS, con)

    planted_ids = set(answer["payment_id"])
    n_days = pay["initiated_ts"].str[:10].nunique()

    scores = rule_scores(alerts, answer)
    print("== Rules, one at a time ==")
    print(scores.round(3).to_string(index=False))
    out = Path("dashboards/data/08_rule_scores.csv")        # the dashboard reads this next to the views
    out.parent.mkdir(parents=True, exist_ok=True)
    scores.round(3).to_csv(out, index=False)

    flagged = alerts["payment_id"]
    hits = flagged.isin(planted_ids).sum()
    print("\n== All rules together ==")
    print(f"alerts: {len(alerts)} ({len(alerts) / n_days:.1f} per day)")
    print(f"precision (alert is a planted anomaly of any type): {hits / len(alerts):.3f}")
    print(f"recall (planted anomalies caught by any rule):      {hits / len(planted_ids):.3f}")

    print("\n== More rules firing = more likely real? ==")
    alerts["is_planted"] = flagged.isin(planted_ids)
    print(alerts.groupby("n_rules")["is_planted"].agg(alerts="size", precision="mean").round(3).to_string())

    budget = len(alerts)
    feats = model_features(pay)
    model = IsolationForest(n_estimators=200, random_state=0).fit(feats)
    pay["score"] = -model.score_samples(feats)           # higher = more unusual
    top = pay.nlargest(budget, "score")["payment_id"]
    m_hits = top.isin(planted_ids).sum()
    print(f"\n== Isolation Forest at the same budget ({budget} alerts) ==")
    print(f"precision: {m_hits / budget:.3f}   recall: {m_hits / len(planted_ids):.3f}")


if __name__ == "__main__":
    main()
