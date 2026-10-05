import pandas as pd

from treasury.simulator.market.fx import build_fx_rates
from treasury.simulator.world.builder import build_world
from treasury.simulator.world.calendar import build_dim_calendar, build_dim_date


def test_world_is_deterministic(cfg):
    a, b = build_world(cfg).tables(), build_world(cfg).tables()
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])


def test_account_structure_supports_concentration_analysis(cfg):
    acc = build_world(cfg).dim_account
    assert acc["account_id"].is_unique
    assert acc["is_pooled"].any() and (~acc["is_pooled"]).any()   # fragmentation to find
    # Restricted currencies and forced entities stay outside the pool (idle-cash story)
    restricted = acc["currency_code"].isin(["CNY", "INR"])
    assert not acc.loc[restricted, "is_pooled"].any()
    assert not acc.loc[acc["entity_id"] == "E05", "is_pooled"].any()
    # Germany can run an overdraft
    assert (acc.loc[acc["entity_id"] == "E06", "overdraft_limit"] > 0).any()
    # Every pooled account points at an existing header in the same currency
    headers = acc.set_index("account_id")
    pooled = acc[acc["is_pooled"]]
    assert pooled["pool_header_account_id"].isin(headers.index).all()
    assert (headers.loc[pooled["pool_header_account_id"], "currency_code"].values
            == pooled["currency_code"].values).all()


def test_counterparties_cover_analysis_needs(cfg):
    cp = build_world(cfg).dim_counterparty
    assert len(cp) == cfg.counterparties.count
    assert cp["uses_virtual_account"].any() and (~cp["uses_virtual_account"]).any()  # reconciliation (deferred)
    assert (cp["risk_rating"] == "high").sum() >= 1                                  # analysis 8
    assert (cp["data_quality_score"] < 0.8).sum() >= cfg.counterparties.repeat_offender_suppliers
    assert (cp["avg_days_late"] > 0).any()                                           # analysis 5


def test_calendar_is_per_country(cfg):
    dd = build_dim_date(pd.Timestamp("2025-10-01").date(), pd.Timestamp("2025-10-07").date())
    cal = build_dim_calendar(dd, ["SG", "CN"]).set_index(["date_id", "country_code"])
    assert not cal.loc[(20251001, "CN"), "is_business_day"]   # China National Day
    assert cal.loc[(20251001, "SG"), "is_business_day"]


def test_fx_has_no_gaps_and_managed_currencies_stay_in_band(cfg):
    start = cfg.start_date
    end = (pd.Timestamp(start) + pd.DateOffset(months=cfg.backfill_months)).date()
    fx = build_fx_rates(cfg, start, end)
    n_days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    assert len(fx) == n_days * len(cfg.currencies)
    assert (fx["rate_to_sgd"] > 0).all()
    cny = fx.loc[fx["currency_code"] == "CNY", "rate_to_sgd"]
    start_cny = cfg.currencies["CNY"].start_rate_to_sgd
    assert cny.between(start_cny * 0.9, start_cny * 1.1).all()
