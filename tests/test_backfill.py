"""Integrity of a small backfill: determinism, lifecycle invariants, warehouse load."""

import sqlite3

import pandas as pd
import pytest

from treasury.simulator.backfill import run_backfill, simulate
from treasury.simulator.config import SimulationConfig


@pytest.fixture(scope="module")
def small_cfg(cfg, tmp_path_factory) -> SimulationConfig:
    tmp = tmp_path_factory.mktemp("sim")
    raw = cfg.model_dump()
    raw["backfill_months"] = 3
    raw["volumes"]["payments_per_business_day"] = 200
    raw["output"] = {"landing_dir": tmp / "landing", "truth_dir": tmp / "truth",
                     "warehouse_url": f"sqlite:///{tmp / 'wh.sqlite'}"}
    return SimulationConfig.model_validate(raw)


@pytest.fixture(scope="module")
def ds(small_cfg):
    return run_backfill(small_cfg, log=lambda *_: None)


def test_backfill_is_deterministic(small_cfg, ds):
    again = simulate(small_cfg)
    pd.testing.assert_frame_equal(ds.tables["fact_payment"], again.tables["fact_payment"])
    pd.testing.assert_frame_equal(ds.tables["fact_invoice"], again.tables["fact_invoice"])


def test_lifecycle_invariants(small_cfg, ds):
    p = ds.tables["fact_payment"]
    assert p["payment_id"].is_unique
    assert (p["amount"] > 0).all() and (p["amount_sgd"] > 0).all()
    assert (p["submitted_ts"] >= p["initiated_ts"]).all()
    settled = p[p["settled_ts"].notna()]
    assert (settled["settled_ts"] > settled["submitted_ts"]).all()
    assert p["initiated_ts"].min() >= pd.Timestamp(small_cfg.start_date) - pd.Timedelta(days=1)
    # Unsettled payments have no settlement data; failed ones always have a reason.
    unsettled = p["status"].isin(["rejected", "pending"])
    assert p.loc[unsettled, ["settled_ts", "value_date_id", "amount_received"]].isna().all().all()
    assert p.loc[p["status"].isin(["rejected", "returned"]), "failure_reason"].notna().all()
    assert p.loc[~p["status"].isin(["rejected", "returned"]), "failure_reason"].isna().all()
    # Cross-border payments carry gpi/charges fields; domestic ones don't.
    xb = p["uetr"].notna()
    assert p.loc[xb, "charge_bearer"].notna().all() and p.loc[~xb, "charge_bearer"].isna().all()


def test_intercompany_legs_mirror(ds):
    ic = ds.tables["fact_payment"]
    ic = ic[ic["is_intercompany"]]
    legs = ic.groupby("end_to_end_id")["direction"].apply(lambda s: "".join(sorted(s)))
    settled_out = ic[(ic["direction"] == "OUT") & ic["status"].isin(["completed", "delayed", "returned"])]
    assert (legs.loc[settled_out["end_to_end_id"]] == "INOUT").all()


def test_every_allocation_points_at_a_real_invoice(ds):
    alloc = ds.truth["payment_invoice"]
    assert alloc["invoice_id"].isin(ds.tables["fact_invoice"]["invoice_id"]).all()
    assert alloc["payment_id"].isin(ds.tables["fact_payment"]["payment_id"]).all()


def test_outputs_written(small_cfg, ds):
    assert list((small_cfg.output.landing_dir / "payments").glob("*.parquet"))
    assert (small_cfg.output.truth_dir / "payment_invoice.parquet").exists()
    con = sqlite3.connect(small_cfg.output.warehouse_url.removeprefix("sqlite:///"))
    n = con.execute("SELECT COUNT(*) FROM fact_payment").fetchone()[0]
    assert n == len(ds.tables["fact_payment"])
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    # Truth never reaches the warehouse.
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("label_") or t.startswith("truth") for t in tables)
