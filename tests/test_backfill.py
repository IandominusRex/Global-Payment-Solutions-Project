"""Integrity of a small simulation: determinism, lifecycle, ledger and as-of invariants."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from treasury.simulator.backfill import backfill_cut, run_backfill, run_simulation, simulate, snapshot
from treasury.simulator.config import SimulationConfig

LIFECYCLE_ORDER = ["CREATED", "APPROVED", "REPAIRED", "SUBMITTED", "HELD", "SCREENED", "ROUTED", "IN_FLIGHT",
                   "SETTLED", "REJECTED", "RETURNED"]


@pytest.fixture(scope="module")
def small_cfg(cfg, tmp_path_factory) -> SimulationConfig:
    tmp = tmp_path_factory.mktemp("sim")
    raw = cfg.model_dump()
    raw["backfill_months"] = 3
    raw["volumes"]["payments_per_business_day"] = 200
    raw["output"] = {"raw_dir": tmp / "raw", "answer_key_dir": tmp / "answer_key",
                     "clean_db_url": f"sqlite:///{tmp / 'wh.sqlite'}"}
    return SimulationConfig.model_validate(raw)


@pytest.fixture(scope="module")
def sim(small_cfg):
    return run_simulation(small_cfg)


@pytest.fixture(scope="module")
def ds(small_cfg):
    return run_backfill(small_cfg, log=lambda *_: None)


def test_backfill_is_deterministic(small_cfg, ds):
    again = simulate(small_cfg)
    for t in ["fact_payment", "fact_invoice", "fact_payment_event", "fact_balance", "fact_sweep"]:
        pd.testing.assert_frame_equal(ds.tables[t], again.tables[t])


def test_lifecycle_invariants(small_cfg, ds):
    p = ds.tables["fact_payment"]
    assert p["payment_id"].is_unique
    assert (p["amount"] > 0).all() and (p["amount_sgd"] > 0).all()
    assert (p["submitted_ts"] >= p["initiated_ts"]).all()
    settled = p[p["settled_ts"].notna()]
    assert (settled["settled_ts"] > settled["submitted_ts"]).all()
    assert (settled["settled_ts"] <= backfill_cut(small_cfg)).all()
    unsettled = p["status"].isin(["rejected", "pending"])
    assert p.loc[unsettled, ["settled_ts", "value_date_id", "amount_received"]].isna().all().all()
    assert p.loc[p["status"].isin(["rejected", "returned"]), "failure_reason"].notna().all()
    assert p.loc[~p["status"].isin(["rejected", "returned"]), "failure_reason"].isna().all()
    xb = p["uetr"].notna()
    assert p.loc[xb, "charge_bearer"].notna().all() and p.loc[~xb, "charge_bearer"].isna().all()


def test_event_trail_is_ordered_and_matches_payment(ds):
    p = ds.tables["fact_payment"].set_index("payment_id")
    ev = ds.tables["fact_payment_event"]
    assert ev["event_id"].is_unique and ev["payment_id"].isin(p.index).all()
    rank = ev["status"].map({s: i for i, s in enumerate(LIFECYCLE_ORDER)})
    ordered = ev.assign(rank=rank).sort_values(["payment_id", "event_ts", "rank", "hop_seq"])
    assert (ordered.groupby("payment_id")["rank"].apply(lambda r: r.is_monotonic_increasing)).all()
    first = ev.groupby("payment_id")["status"].first()
    assert (first == "CREATED").all()
    settled = ev[ev["status"] == "SETTLED"].set_index("payment_id")["event_ts"]
    assert (settled == p.loc[settled.index, "settled_ts"]).all()
    assert ev.loc[ev["status"].isin(["REJECTED", "RETURNED"]), "reason_code"].notna().all()


def test_ledger_balances_reconcile(sim):
    last = sim.balances.sort_values("_eod_ts").groupby("account_id").tail(1).set_index("account_id")
    post = sim.postings
    in_window = post["posting_ts"].to_numpy() <= last["_eod_ts"].reindex(post["account_id"]).to_numpy()
    moved = post[in_window].groupby("account_id")["delta"].sum()
    closing = sim.opening + moved.reindex(sim.opening.index).fillna(0)
    assert np.allclose(closing, last["closing_balance"].reindex(sim.opening.index), atol=0.01)
    pooled = sim.world.dim_account.loc[sim.world.dim_account["is_pooled"], "account_id"]
    assert (sim.balances.loc[sim.balances["account_id"].isin(pooled), "closing_balance"].abs() < 0.01).all()


def test_insufficient_funds_come_from_the_ledger(sim):
    p = sim.payments
    am04 = p[p["failure_reason"] == "AM04"]
    assert (am04["direction"] == "OUT").all() and am04["settled_ts"].isna().all()
    # A bounced intercompany payment never credits the receiving entity.
    assert not p["_mirror_of"].isin(am04["payment_id"]).any()


def test_snapshot_hides_the_future(small_cfg, sim):
    t = pd.Timestamp(small_cfg.start_date) + pd.Timedelta(days=40, hours=13)
    ds = snapshot(sim, t)
    p = ds.tables["fact_payment"]
    assert (p["initiated_ts"] <= t).all()
    assert (p["settled_ts"].dropna() <= t).all()
    assert (p["status"] == "pending").any()
    assert (ds.tables["fact_payment_event"]["event_ts"] <= t).all()
    assert (ds.tables["fact_sweep"]["sweep_ts"] <= t).all()


def test_intercompany_legs_mirror(ds):
    ic = ds.tables["fact_payment"]
    ic = ic[ic["is_intercompany"]]
    legs = ic.groupby("end_to_end_id")["direction"].apply(lambda s: "".join(sorted(s)))
    settled_out = ic[(ic["direction"] == "OUT") & ic["status"].isin(["completed", "delayed", "returned"])]
    assert (legs.loc[settled_out["end_to_end_id"]] == "INOUT").all()


def test_every_allocation_points_at_a_real_invoice(ds):
    alloc = ds.answer_key["payment_to_invoice"]
    assert alloc["invoice_id"].isin(ds.tables["fact_invoice"]["invoice_id"]).all()
    assert alloc["payment_id"].isin(ds.tables["fact_payment"]["payment_id"]).all()


def test_outputs_written(small_cfg, ds):
    assert list((small_cfg.output.raw_dir / "payments").glob("*.parquet"))
    assert (small_cfg.output.answer_key_dir / "payment_to_invoice.parquet").exists()
    con = sqlite3.connect(small_cfg.output.clean_db_url.removeprefix("sqlite:///"))
    for t in ["fact_payment", "fact_payment_event", "fact_balance", "fact_sweep"]:
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == len(ds.tables[t])
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not set(ds.answer_key) & tables  # answer-key tables never enter the clean database


def test_stream_ends_identical_to_a_snapshot(small_cfg, tmp_path):
    """Streaming tick by tick must leave the clean database exactly as a snapshot at the same time."""
    from treasury.simulator.stream import run_stream

    raw = small_cfg.model_dump()
    raw["output"] = {"raw_dir": tmp_path / "raw", "answer_key_dir": tmp_path / "answer_key",
                     "clean_db_url": f"sqlite:///{tmp_path / 'stream.sqlite'}"}
    raw["stream"]["tick_sim_seconds"] = 3 * 3600
    cfg = SimulationConfig.model_validate(raw)
    sim = run_stream(cfg, days=2, speed=0, log=lambda *_: None)
    expected = snapshot(sim, backfill_cut(cfg) + pd.Timedelta(days=2), balance_date_max=cfg.end_date)

    con = sqlite3.connect(tmp_path / "stream.sqlite")
    for table, key in [("fact_payment", "payment_id"), ("fact_payment_event", "event_id"),
                       ("fact_invoice", "invoice_id"), ("fact_balance", ["date_id", "account_id"])]:
        got = pd.read_sql(f"SELECT * FROM {table}", con).sort_values(key).reset_index(drop=True)
        exp = expected.tables[table].sort_values(key).reset_index(drop=True)
        assert len(got) == len(exp), table
        for col in ["status"] if "status" in exp else []:
            assert (got[col].to_numpy() == exp[col].to_numpy()).all(), table
    got = pd.read_sql("SELECT payment_id, settled_ts FROM fact_payment", con).set_index("payment_id")
    exp = expected.tables["fact_payment"].set_index("payment_id")["settled_ts"]
    assert (pd.to_datetime(got["settled_ts"]).reindex(exp.index).fillna(pd.Timestamp(0))
            == exp.fillna(pd.Timestamp(0))).all()
    assert list((tmp_path / "raw" / "stream" / "payment").glob("*.parquet"))


def test_bank_statements_reconcile_to_the_ledger(ds):
    """Statement continuity: closing(last day) - closing(first day) == sum of lines booked in between."""
    lines = ds.tables["fact_statement_line"]
    bal = ds.tables["fact_balance"]
    first, last = bal["date_id"].min(), bal["date_id"].max()
    window = lines[(lines["booking_date_id"] > first) & (lines["booking_date_id"] <= last)]
    moved = pd.Series(np.where(window["credit_debit"] == "CRDT", 1, -1) * window["amount"]).groupby(
        window["account_id"].to_numpy()).sum()
    b = bal.pivot(index="account_id", columns="date_id", values="closing_balance")
    change = b[last] - b[first]
    assert np.allclose(moved.reindex(change.index).fillna(0), change, atol=0.05)
    assert lines["booking_date_id"].min() >= first
    answer_key = ds.answer_key["statement_line_to_payment"]
    assert answer_key["line_id"].isin(lines["line_id"]).all() and lines["line_id"].is_unique


def test_raw_is_dirty_but_clean_db_is_clean(small_cfg, ds):
    raw = pd.concat(pd.read_parquet(f) for f in (small_cfg.output.raw_dir / "payments").glob("*.parquet"))
    labels = pd.read_parquet(small_cfg.output.answer_key_dir / "dq_defects.parquet")
    assert set(labels["defect_type"]) >= {"null_purpose_code", "resent_file_duplicate"}
    n_dup = (labels["defect_type"] == "resent_file_duplicate").sum()
    assert len(raw) == len(ds.tables["fact_payment"]) + n_dup
    assert (raw["amount"] < 0).sum() == (labels["defect_type"] == "negative_amount").sum()
    clean = ds.tables["fact_payment"]
    assert (clean["amount"] > 0).all() and clean["purpose_code"].notna().all()
    assert clean["currency_code"].isin(small_cfg.currencies).all() and clean["payment_id"].is_unique


def test_anomalies_are_labelled_and_new_beneficiaries_are_new(ds):
    labels = ds.answer_key["business_anomalies"]
    p = ds.tables["fact_payment"].merge(labels, on="payment_id")
    nb = p[p["anomaly_type"] == "new_beneficiary_high_value"]
    cps = ds.tables["dim_counterparty"].set_index("counterparty_id")
    first = pd.to_datetime(cps.loc[nb["counterparty_id"], "first_seen_date"]).to_numpy()
    assert (pd.to_datetime(nb["initiated_ts"]).dt.normalize().to_numpy() == first).all()


def test_camt053_export_balances(small_cfg, ds):
    import xml.etree.ElementTree as ET

    from treasury.simulator.sinks.clean_db import get_engine
    from treasury.simulator.sinks.iso20022 import NS, build_camt053

    lines = ds.tables["fact_statement_line"]
    busiest = lines.groupby(["account_id", "booking_date_id"]).size().idxmax()
    day = pd.to_datetime(str(busiest[1])).date()
    tree = build_camt053(get_engine(small_cfg.output.clean_db_url), busiest[0], day)
    ns = {"c": NS}
    entries = tree.getroot().findall(".//c:Ntry", ns)
    assert len(entries) == len(lines[(lines["account_id"] == busiest[0]) & (lines["booking_date_id"] == busiest[1])])
    bals = {b.find(".//c:Cd", ns).text: float(b.find("c:Amt", ns).text) * (1 if b.find("c:CdtDbtInd", ns).text
                                                                           == "CRDT" else -1)
            for b in tree.getroot().findall(".//c:Bal", ns)}
    moved = sum(float(e.find("c:Amt", ns).text) * (1 if e.find("c:CdtDbtInd", ns).text == "CRDT" else -1)
                for e in entries)
    assert abs(bals["OPBD"] + moved - bals["CLBD"]) < 0.05
    ET.tostring(tree.getroot())  # serialises
