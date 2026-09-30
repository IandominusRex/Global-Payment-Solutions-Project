"""Webhook sink: camt.054-style debit/credit notifications, like a bank's real-time API.

Posts one JSON batch per tick for payments that settled, were rejected or came back.
Uses only the standard library; delivery failures are logged, never fatal.
"""

from __future__ import annotations

import json
import urllib.request

import pandas as pd

NOTIFY = {"SETTLED": "BOOKED", "REJECTED": "REJECTED", "RETURNED": "RETURNED"}


def build_notifications(events: pd.DataFrame, payments: pd.DataFrame) -> list[dict]:
    ev = events[events["status"].isin(NOTIFY)]
    if ev.empty:
        return []
    p = payments.set_index("payment_id")
    ev = ev[ev["payment_id"].isin(p.index)]
    return [{
        "message_type": "camt.054",
        "event_id": int(e.event_id),
        "notification": NOTIFY[e.status],
        "payment_id": e.payment_id,
        "end_to_end_id": p.at[e.payment_id, "end_to_end_id"],
        "account_id": p.at[e.payment_id, "account_id"],
        "credit_debit": "CRDT" if p.at[e.payment_id, "direction"] == "IN" else "DBIT",
        "amount": float(p.at[e.payment_id, "amount"]),
        "currency": p.at[e.payment_id, "currency_code"],
        "reason_code": e.reason_code,
        "booked_at": pd.Timestamp(e.event_ts).isoformat() + "Z",
    } for e in ev.itertuples()]


def post_notifications(url: str, events: pd.DataFrame, payments: pd.DataFrame, log=print) -> int:
    batch = build_notifications(events, payments)
    if not batch:
        return 0
    req = urllib.request.Request(url, data=json.dumps(batch).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2):
            pass
    except OSError as exc:
        log(f"  webhook delivery failed ({len(batch)} notifications): {exc}")
    return len(batch)
