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


def test_06_idle_cash_coexists_with_overdraft():
    pytest.skip("needs fact_balance (v2 ledger)")


def test_07_fx_exposure_long_eur_short_cny(ds, pay):
    functional = ds.tables["dim_entity"].set_index("entity_id")["functional_currency"]
    foreign = pay[pay["currency_code"].to_numpy() != functional.loc[pay["entity_id"]].to_numpy()]
    settled = foreign[foreign["status"].isin(["completed", "delayed"])]
    net = (np.where(settled["direction"] == "IN", 1, -1) * settled["amount_sgd"]).groupby(
        settled["currency_code"]).sum()
    assert net["EUR"] > 0
    assert net["CNY"] < 0


def test_08_virtual_account_payers_have_cleaner_references(ds, pay):
    ar = pay[pay["flow"] == "ar_receipt"]
    alloc = ds.truth["payment_invoice"].set_index("payment_id")["invoice_id"]
    refs = ds.tables["fact_invoice"].set_index("invoice_id")["invoice_ref"]
    true_ref = refs.reindex(alloc.reindex(ar["payment_id"]).to_numpy()).to_numpy()
    exact = ar["remittance_ref"].to_numpy() == true_ref
    va = ds.tables["dim_counterparty"].set_index("counterparty_id").loc[ar["counterparty_id"], "uses_virtual_account"]
    by_va = pd.Series(exact).groupby(va.to_numpy()).mean()
    assert by_va[True] - by_va[False] >= 0.20


def test_09_enough_labelled_anomalies():
    pytest.skip("needs anomaly injection with labels (v4)")
