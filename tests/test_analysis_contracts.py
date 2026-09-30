"""Per-analysis viability contracts (see docs/dataset-design-review.md).

Each test will run against a generated backfill once the payment engine exists.
Until then they are skipped so the suite documents what "viable data" means.
"""

import pytest

pytestmark = [pytest.mark.contract, pytest.mark.skip(reason="needs v1 payment engine")]


def test_01_money_movement_has_dominant_corridor(): ...           # top corridor >= 15% value
def test_02_flagged_corridors_stand_out(): ...                    # >=200 pmts, >=1.5x median P90/fail
def test_03_efficiency_has_long_tail_and_channel_gap(): ...       # P90/P50 >= 3, STP legacy < API
def test_04_failures_are_pareto(): ...                            # top-3 reasons >= 70%
def test_05_forecast_has_history_and_seasonality(): ...           # >= 24 months, weekly pattern
def test_06_idle_cash_coexists_with_overdraft(): ...              # same-day surplus and overdraft
def test_07_fx_exposure_long_eur_short_cny(): ...                 # net EUR > 0, net CNY < 0
def test_08_virtual_accounts_match_better(): ...                  # >= 20pp match-rate gap
def test_09_enough_labelled_anomalies(): ...                      # >= 100 per anomaly type
