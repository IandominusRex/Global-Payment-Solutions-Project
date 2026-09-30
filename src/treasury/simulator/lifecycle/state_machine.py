"""Payment state machine (v2): the timestamped event trail behind every payment.

  OUT:  CREATED -> APPROVED -> [REPAIRED] -> SUBMITTED -> [HELD] -> SCREENED
        -> ROUTED -> [IN_FLIGHT x hops] -> SETTLED -> [RETURNED]
        side exit: REJECTED (bank/beneficiary rejection, or AM04 at execution)
  IN:   CREATED -> [IN_FLIGHT x hops, visible via gpi] -> SETTLED -> [RETURNED]  (or REJECTED)
  CARD: CREATED -> SETTLED  (or REJECTED / RETURNED)

This is the gpi-tracker view: analyses 2 and 3 use it to see where time is lost
(approval, repair, sanctions hold, correspondent hops, cut-off waits).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from treasury.simulator.lifecycle.timing import CORRESPONDENT_BY_CCY, MAX_HOPS

EVENT_COLUMNS = ["event_id", "payment_id", "event_ts", "status", "bank_id", "hop_seq", "reason_code"]


def build_events(p: pd.DataFrame, dim_account: pd.DataFrame) -> pd.DataFrame:
    bank_of = dim_account.set_index("account_id")["bank_id"]
    our_bank = bank_of.loc[p["account_id"]].to_numpy()
    out = (p["direction"] == "OUT").to_numpy()
    card = (p["_rail"] == "CARD").to_numpy()
    rejected = (p["status"] == "rejected").to_numpy()
    routed_before_reject = ~rejected | (p["_routed_ts"] <= p["_rejected_ts"]).to_numpy()
    full_out = out & ~card
    frames = []

    def add(mask, ts, status, bank=None, hop=None, reason=None):
        mask = np.asarray(mask, dtype=bool) & pd.notna(ts).to_numpy()
        if not mask.any():
            return
        frames.append(pd.DataFrame({
            "payment_id": p["payment_id"].to_numpy()[mask],
            "event_ts": pd.to_datetime(ts).to_numpy()[mask],
            "status": status,
            "bank_id": None if bank is None else np.asarray(bank, dtype=object)[mask],
            "hop_seq": hop,
            "reason_code": None if reason is None else np.asarray(reason, dtype=object)[mask],
        }))

    all_rows = np.ones(len(p), dtype=bool)
    add(all_rows, p["initiated_ts"], "CREATED")
    add(full_out, p["_approved_ts"], "APPROVED")
    add(full_out, p["_repaired_ts"], "REPAIRED")
    add(full_out, p["submitted_ts"], "SUBMITTED", bank=our_bank)
    add(full_out & p["_held"].to_numpy(dtype=bool), p["submitted_ts"] + pd.Timedelta(seconds=30), "HELD",
        bank=our_bank)
    add(full_out, p["_screened_ts"], "SCREENED", bank=our_bank)
    add(full_out & routed_before_reject, p["_routed_ts"], "ROUTED", bank=our_bank)

    corr = p["currency_code"].map(CORRESPONDENT_BY_CCY).fillna("B01").to_numpy()
    for i in range(1, MAX_HOPS + 1):
        add(~card & ~rejected, p[f"_hop{i}_ts"], "IN_FLIGHT", bank=corr, hop=i)

    add(~rejected, p["settled_ts"], "SETTLED", bank=np.where(out, None, our_bank))
    add(rejected, p["_rejected_ts"], "REJECTED", reason=p["failure_reason"])
    add(p["_returned_ts"].notna().to_numpy(), p["_returned_ts"], "RETURNED", reason=p["failure_reason"])

    ev = pd.concat(frames, ignore_index=True)
    order = {s: i for i, s in enumerate(["CREATED", "APPROVED", "REPAIRED", "SUBMITTED", "HELD", "SCREENED",
                                         "ROUTED", "IN_FLIGHT", "SETTLED", "REJECTED", "RETURNED"])}
    ev["_o"] = ev["status"].map(order)
    ev = ev.sort_values(["event_ts", "payment_id", "_o", "hop_seq"], kind="stable").drop(columns="_o")
    ev["hop_seq"] = ev["hop_seq"].astype("Int64")
    ev["event_id"] = np.arange(1, len(ev) + 1)
    return ev[EVENT_COLUMNS].reset_index(drop=True)
