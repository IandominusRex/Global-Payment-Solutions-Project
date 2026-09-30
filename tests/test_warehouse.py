import sqlite3

import yaml

from tests.conftest import ROOT
from treasury.simulator.cli import main


def test_build_world_loads_star_schema(tmp_path):
    raw = yaml.safe_load((ROOT / "config" / "simulation.yaml").read_text())
    db = tmp_path / "wh.sqlite"
    raw["output"]["warehouse_url"] = f"sqlite:///{db}"
    cfg_path = tmp_path / "sim.yaml"
    cfg_path.write_text(yaml.safe_dump(raw))

    main(["--config", str(cfg_path), "build-world"])
    main(["--config", str(cfg_path), "build-world"])   # idempotent re-run

    con = sqlite3.connect(db)
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["dim_entity", "dim_account", "dim_counterparty", "dim_date",
                        "dim_calendar", "fact_fx_rate"]}
    assert counts["dim_entity"] == 11
    assert counts["dim_counterparty"] == 500
    assert counts["dim_calendar"] == counts["dim_date"] * len(raw["countries"])
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
