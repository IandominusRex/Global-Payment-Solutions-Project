from pathlib import Path

import pytest

from treasury.simulator.config import SimulationConfig, load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg() -> SimulationConfig:
    return load_config(ROOT / "config" / "simulation.yaml")
