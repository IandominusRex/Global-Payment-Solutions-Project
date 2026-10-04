"""As-of views: what the clean database shows at a given moment of simulated time.

The simulator computes every payment's full future (when it will settle, bounce or
come back). A bank's data at time t only shows what has happened by t. This module
turns the full simulation into that view:

  - payments initiated after t don't exist yet
  - payments not yet settled/rejected at t are 'pending' with no settlement fields
  - a payment returned after t still shows as completed/delayed
  - invoices, events, balances and sweeps are cut at t

The backfill is as_of(end of last day). The live stream (v3) calls as_of repeatedly
as its clock advances, so both modes are guaranteed to agree.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from treasury.simulator.lifecycle.timing import PAYMENT_COLUMNS


def payments_as_of(p: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    p = p[p["initiated_ts"] <= t].copy()
    rejected_now = (p["status"] == "rejected") & (p["_rejected_ts"] <= t)
    settled_now = p["settled_ts"].notna() & (p["settled_ts"] <= t)
    returned_now = p["_returned_ts"].notna() & (p["_returned_ts"] <= t)
    pending = ~rejected_now & ~settled_now

    status = p["_settled_status"].where(~returned_now, "returned")
    status = status.where(~rejected_now, "rejected").where(~pending, "pending")
    p["status"] = status
    p.loc[pending | (settled_now & ~returned_now), "failure_reason"] = None
    for c in ["settled_ts", "value_date_id", "amount_received", "fx_rate_applied"]:
        p.loc[pending, c] = None
    p.loc[pending, "amount_sgd"] = p.loc[pending, "_amount_sgd_initiated"]
    return p


def invoices_as_of(inv: pd.DataFrame, alloc: pd.DataFrame, p_asof: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    day_id = int(t.strftime("%Y%m%d"))
    inv = inv[inv["issue_date_id"] <= day_id].copy()
    good = p_asof.loc[p_asof["status"].isin(["completed", "delayed"]), "payment_id"]
    paid_amt = alloc[alloc["payment_id"].isin(good)].groupby("invoice_id")["allocated_amount"].sum()
    received = inv["invoice_id"].map(paid_amt).fillna(0.0)
    status = np.where(received >= inv["amount"] - 0.005, "paid", np.where(received > 0, "part_paid", "open"))
    stale_day = int((t - pd.Timedelta(days=180)).strftime("%Y%m%d"))
    written_off = (inv["direction"] == "AR").to_numpy() & (status == "open") & (inv["due_date_id"] < stale_day)
    inv["status"] = np.where(written_off, "written_off", status)
    return inv


def cut(df: pd.DataFrame, ts_col: str, t: pd.Timestamp) -> pd.DataFrame:
    return df[df[ts_col] <= t]


def public(p: pd.DataFrame) -> pd.DataFrame:
    return p[PAYMENT_COLUMNS]
