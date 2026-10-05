import pytest
import yaml

from tests.conftest import ROOT
from treasury.simulator.config import SimulationConfig


def _raw() -> dict:
    return yaml.safe_load((ROOT / "config" / "simulation.yaml").read_text())


def test_config_loads(cfg):
    assert 5 <= len(cfg.currencies) <= 6          # blueprint: 5-6 currencies
    assert 10 <= len(cfg.entities) <= 15          # blueprint: 10-15 entities
    assert cfg.backfill_months >= 24              # analysis 5 needs a full annual cycle
    assert cfg.high_risk_countries                # analysis 8 needs high-risk countries


def test_entity_functional_currency_must_be_modelled():
    raw = _raw()
    raw["entities"][0]["functional_ccy"] = "VND"
    with pytest.raises(ValueError, match="functional_ccy VND not modelled"):
        SimulationConfig.model_validate(raw)


def test_counterparty_mix_must_sum_to_one():
    raw = _raw()
    raw["counterparties"]["mix"]["customer"] = 0.9
    with pytest.raises(ValueError, match="must sum to 1"):
        SimulationConfig.model_validate(raw)
