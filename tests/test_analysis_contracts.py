"""Per-analysis viability contracts (see docs/dataset-design-review.md).

Each test asserts that the full-size backfill contains the pattern its analysis is
meant to find. Runs one full simulation per session (~30s); skip with -m "not contract".
"""

import numpy as np
import pandas as pd
import pytest

from treasury.simulator.backfill import simulate

pytestmark = pytest.mark.contract


@pytest.fixture(scope="session")
def ds(cfg):
    return simulate(cfg)


@pytest.fixture(scope="session")
def pay(ds):
    """External payments (intercompany legs excluded) with rail and flow attached."""
    p = ds.tables["fact_payment"]
    rails = ds.tables["dim_payment_type"].set_index("type_id")["rail"]
    p = p.assign(rail=rails.loc[p["type_id"]].to_numpy()).merge(ds.truth["payment_flow"], on="payment_id")
    return p[~p["is_intercompany"]]


def test_01_money_movement_sg_cn_is_top_cross_border_corridor(pay):
    xb = pay[pay["sender_country"] != pay["receiver_country"]]
    by_corridor = xb.groupby(["sender_country", "receiver_country"])["amount_sgd"].sum()
    assert by_corridor.idxmax() == ("SG", "CN")


def test_02_flagged_corridors_stand_out(cfg, pay):
    xb = pay[pay["rail"] == "SWIFT_XBORDER"]
    corridor = [xb["sender_country"], xb["receiver_country"]]
    n = xb.groupby(corridor).size()
    busy = n[n >= 200].index

    settled = xb[xb["settled_ts"].notna()]
    hours = (settled["settled_ts"] - settled["initiated_ts"]).dt.total_seconds() / 3600
    p90 = hours.groupby([settled["sender_country"], settled["receiver_country"]]).quantile(0.9).loc[busy]
    slow = tuple(cfg.scenarios.slow_corridor)
    assert n[slow] >= 200
    assert p90[slow] >= 1.5 * p90.median()

    fail = xb["status"].isin(["rejected", "returned"]).groupby(corridor).mean().loc[busy]
    bad = tuple(cfg.scenarios.high_failure_corridor)
    assert n[bad] >= 200
    assert fail[bad] >= 1.5 * fail.median()


def test_03_efficiency_has_long_tail_and_channel_gap(pay):
    # Within one rail, a few slow payments (holds, cut-off misses, extra hops) hide in the mean.
    xb = pay[pay["settled_ts"].notna() & (pay["rail"] == "SWIFT_XBORDER")]
    hours = (xb["settled_ts"] - xb["initiated_ts"]).dt.total_seconds() / 3600
    assert hours.quantile(0.9) / hours.median() >= 3
    assert hours.mean() > 1.2 * hours.median()
    stp = pay[pay["direction"] == "OUT"].groupby("channel")["is_stp"].mean()
    assert stp["LEGACY_FILE"] < stp["API"] - 0.05
    assert 0.02 <= pay["missed_cutoff"].mean() <= 0.20


def test_04_failures_are_pareto_with_repeat_offenders(pay):
    failed = pay[pay["status"].isin(["rejected", "returned"])]
    assert 0.005 <= len(failed) / len(pay) <= 0.05
    shares = failed["failure_reason"].value_counts(normalize=True)
    assert shares.head(3).sum() >= 0.70
    # A few counterparties account for a disproportionate share of failures.
    per_cp = failed.groupby("counterparty_id").size().sort_values(ascending=False)
    assert per_cp.head(10).sum() / per_cp.sum() >= 0.10


def test_05_forecast_has_history_trend_and_weekly_pattern(cfg, pay):
    days = pay["initiated_ts"].dt.normalize()
    assert (days.max() - days.min()).days >= 365 * 2 - 7
    by_dow = pay.groupby(pay["initiated_ts"].dt.dayofweek).size()
    assert by_dow.loc[[5, 6]].sum() < 0.01 * by_dow.sum()        # weekends quiet
    assert by_dow.loc[0:4].max() / by_dow.loc[0:4].min() >= 1.5  # payment-run days stand out
    q = pay.groupby(pay["initiated_ts"].dt.to_period("Q")).size()
    q = q[(q.index >= pd.Period(cfg.start_date, "Q")) & (q.index < pd.Period(cfg.end_date, "Q"))]
    assert q.iloc[-4:].mean() > q.iloc[:4].mean()                 # growth trend


def test_06_idle_cash_coexists_with_overdraft(ds):
    bal = ds.tables["fact_balance"]
    acc = ds.tables["dim_account"].set_index("account_id")
    bal = bal.assign(entity=acc.loc[bal["account_id"], "entity_id"].to_numpy(),
                     target_sgd=acc.loc[bal["account_id"], "target_balance"].to_numpy()
                     * bal["closing_balance_sgd"] / bal["closing_balance"].where(bal["closing_balance"] != 0))
    idle = bal[bal["closing_balance_sgd"] > 2 * bal["target_sgd"]].groupby("date_id")["closing_balance_sgd"].sum()
    overdrawn = bal[bal["closing_balance"] < 0].groupby("date_id")["closing_balance_sgd"].sum()
    both = idle.index.intersection(overdrawn.index)
    assert len(both) >= 0.5 * bal["date_id"].nunique()     # the pooling opportunity exists most days
    assert (idle.loc[both] > -overdrawn.loc[both]).mean() > 0.5  # idle cash could cover the overdraft
    # Pooled accounts are swept to zero every night.
    pooled = acc.index[acc["is_pooled"]]
    assert (bal.loc[bal["account_id"].isin(pooled), "closing_balance"].abs() < 0.01).all()


def test_07_fx_exposure_long_eur_short_cny(ds, pay):
    functional = ds.tables["dim_entity"].set_index("entity_id")["functional_currency"]
    foreign = pay[pay["currency_code"].to_numpy() != functional.loc[pay["entity_id"]].to_numpy()]
    settled = foreign[foreign["status"].isin(["completed", "delayed"])]
    net = (np.where(settled["direction"] == "IN", 1, -1) * settled["amount_sgd"]).groupby(
        settled["currency_code"]).sum()
    assert net["EUR"] > 0
    assert net["CNY"] < 0


def test_08_virtual_account_payers_reconcile_better(ds):
    """Match bank statement credits to invoices by reference, as a treasury system would."""
    lines = ds.tables["fact_statement_line"].merge(ds.truth["statement_payment"], on="line_id")
    flow = ds.truth["payment_flow"].set_index("payment_id")["flow"]
    ar = lines[(lines["bank_tx_code"] == "RCDT") & (lines["payment_id"].map(flow) == "ar_receipt")]
    refs = ds.tables["fact_invoice"].set_index("invoice_id")["invoice_ref"]
    alloc = ds.truth["payment_invoice"]
    true_refs = alloc.assign(ref=refs.reindex(alloc["invoice_id"]).to_numpy()).groupby("payment_id")["ref"].apply(list)
    matched = [isinstance(info, str) and all(r in info for r in true_refs.get(pid, ["<none>"]))
               for info, pid in zip(ar["remittance_info"], ar["payment_id"], strict=True)]
    payer = ds.tables["fact_payment"].set_index("payment_id").loc[ar["payment_id"], "counterparty_id"]
    va = ds.tables["dim_counterparty"].set_index("counterparty_id").loc[payer, "uses_virtual_account"].to_numpy()
    rate = pd.Series(matched).groupby(va).mean()
    assert rate[True] - rate[False] >= 0.20
    # Real-world messiness exists: part payments and multi-invoice payments.
    assert (ds.tables["fact_invoice"]["status"] == "part_paid").sum() > 0
    assert (alloc.groupby("payment_id").size() > 1).sum() > 0


def test_09_enough_labelled_anomalies(ds):
    labels = ds.truth["label_anomaly"]
    counts = labels["anomaly_type"].value_counts()
    from treasury.simulator.inject.anomalies import ANOMALY_TYPES
    assert set(counts.index) == set(ANOMALY_TYPES)
    assert (counts >= 100).all(), counts.to_dict()
    assert labels["payment_id"].isin(ds.tables["fact_payment"]["payment_id"]).all()
    # Labelled anomalies are a small minority, as in real alert queues.
    assert len(labels) / len(ds.tables["fact_payment"]) < 0.03


def test_07b_hedge_book_covers_part_of_the_exposure(ds):
    h = ds.tables["fact_fx_hedge"]
    assert len(h) > 0
    assert set(h["sell_currency"]) & {"EUR"} and set(h["buy_currency"]) & {"CNY"}
