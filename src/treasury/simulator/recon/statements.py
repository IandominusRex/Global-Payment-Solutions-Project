"""Bank statement layer (v4): camt.053-like lines, the bank's view of every posting.

Deliberately separate from fact_payment (the ERP's view). Reconciliation (deferred)
means matching the two, and the bank's formatting is what makes that hard:
  - references are cut to the rail's field length (BACS 18, GIRO 35, ACH 80, others 140)
  - counterparty names arrive uppercased and truncated
  - charges are booked as separate lines
  - one customer payment may cover several invoices, or fall short of the invoice

The true line -> payment link is written to data/answer_key/statement_line_to_payment.parquet only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REF_LIMIT = {"BACS": 18, "GIRO": 35, "ACH": 80}
STATEMENT_COLUMNS = ["line_id", "account_id", "booking_date_id", "value_date_id", "credit_debit", "amount",
                     "currency_code", "bank_tx_code", "bank_reference", "remittance_info", "counterparty_name"]


def build(postings: pd.DataFrame, payments: pd.DataFrame, dim_account: pd.DataFrame, dim_entity: pd.DataFrame,
          dim_counterparty: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (statement lines with internal _posting_ts, answer key line->payment)."""
    acc = dim_account.set_index("account_id")
    tz = acc["entity_id"].map(dim_entity.set_index("entity_id")["timezone"])
    p = payments.set_index("payment_id")
    cp_name = dim_counterparty.set_index("counterparty_id")["name"]

    post = postings.copy()
    is_pay = post["payment_id"].notna().to_numpy()
    pp = p.reindex(post["payment_id"])
    conv = pp["fx_rate_applied"].astype(float).fillna(1.0).to_numpy()
    fee = np.round(np.where(is_pay & (post["kind"] != "return").to_numpy() & (pp["direction"] == "OUT").to_numpy(),
                            pp["fees"].astype(float).fillna(0).to_numpy() * conv, 0.0), 2)

    lines = post.assign(amount_signed=post["delta"].astype(float).to_numpy() + fee)
    charges = post[fee > 0.005].assign(amount_signed=-fee[fee > 0.005], kind="charges")
    lines = pd.concat([lines, charges], ignore_index=True)
    lp = p.reindex(lines["payment_id"])
    rail = lp["_rail"].to_numpy()

    code = np.select(
        [lines["kind"] == "charges", lines["kind"] == "sweep", lines["kind"] == "return",
         rail == "CARD", lp["is_intercompany"].fillna(False).to_numpy(dtype=bool),
         lines["amount_signed"] > 0],
        ["CHRG", "SWEEP", "RTRN", "CARD", "INTC", "RCDT"], default="ICDT")
    ref = lp["remittance_ref"].to_numpy(dtype=object)
    limit = np.array([REF_LIMIT.get(r, 140) for r in rail])
    ref = np.array([r[:n] if isinstance(r, str) else None for r, n in zip(ref, limit, strict=True)], dtype=object)
    info = np.select(
        [code == "CHRG", code == "SWEEP", code == "RTRN", code == "CARD"],
        [np.full(len(lines), "BANK CHARGES", dtype=object),
         np.full(len(lines), "CASH POOL TRANSFER", dtype=object),
         ("RETURN " + lp["end_to_end_id"].fillna("") + " " + lp["failure_reason"].fillna("")).to_numpy(dtype=object),
         np.full(len(lines), None, dtype=object)],
        default=ref)
    names = cp_name.reindex(lp["counterparty_id"]).str.upper().str.slice(0, 35).to_numpy(dtype=object)
    names = np.where(np.isin(code, ["CHRG", "SWEEP"]), None, names)

    local = pd.DatetimeIndex(lines["posting_ts"])
    booking = np.empty(len(lines), dtype=object)
    line_tz = tz.reindex(lines["account_id"]).to_numpy()
    for z in np.unique(line_tz):
        m = line_tz == z
        booking[m] = local[m].tz_localize("UTC").tz_convert(z).strftime("%Y%m%d")
    booking = booking.astype(int)
    value = lp["value_date_id"].to_numpy(dtype=float)
    value = np.where(np.isnan(value) | (code == "RTRN") | (code == "SWEEP"), booking, value).astype(int)

    out = pd.DataFrame({
        "account_id": lines["account_id"].to_numpy(),
        "booking_date_id": booking,
        "value_date_id": np.minimum(value, booking),
        "credit_debit": np.where(lines["amount_signed"] >= 0, "CRDT", "DBIT"),
        "amount": np.round(np.abs(lines["amount_signed"].to_numpy()), 2),
        "currency_code": acc.loc[lines["account_id"], "currency_code"].to_numpy(),
        "bank_tx_code": code,
        "remittance_info": info,
        "counterparty_name": names,
        "_posting_ts": lines["posting_ts"].to_numpy(),
        "_payment_id": lines["payment_id"].to_numpy(),
        "_bank": acc.loc[lines["account_id"], "bank_id"].to_numpy(),
    })
    out = out[out["amount"] > 0].sort_values(["_posting_ts", "account_id", "bank_tx_code"], kind="stable")
    out = out.reset_index(drop=True)
    out["line_id"] = [f"L{i:09d}" for i in range(1, len(out) + 1)]
    out["bank_reference"] = out["_bank"] + out["booking_date_id"].astype(str) + out["line_id"].str[-7:]
    answer_key = out.loc[out["_payment_id"].notna(), ["line_id", "_payment_id"]].rename(
        columns={"_payment_id": "payment_id"})
    return out[STATEMENT_COLUMNS + ["_posting_ts"]], answer_key.reset_index(drop=True)
