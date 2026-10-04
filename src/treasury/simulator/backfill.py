"""Orchestration: world -> events -> lifecycle -> ledger -> event log, then as-of snapshots.

run_simulation() computes the full future up to a horizon. snapshot(sim, t) is what the
clean database shows at simulated time t. The backfill is the snapshot at the end of the last
day; the live stream (stream.py) takes successive snapshots as its clock moves.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from treasury.simulator import asof
from treasury.simulator.business import events
from treasury.simulator.business.events import Fx
from treasury.simulator.config import SimulationConfig
from treasury.simulator.inject import anomalies, data_quality
from treasury.simulator.ledger import balances as ledger
from treasury.simulator.lifecycle import state_machine, timing
from treasury.simulator.market import hedging
from treasury.simulator.market.fx import build_fx_rates
from treasury.simulator.recon import statements
from treasury.simulator.sinks.clean_db import create_schema, get_engine, replace_rows
from treasury.simulator.sinks.exports import refresh_exports
from treasury.simulator.world.builder import World, build_world
from treasury.simulator.world.calendar import BusinessCalendar, build_dim_calendar, build_dim_date

# Parents before children, so foreign keys are satisfied on insert (reverse for drop).
DIMENSIONS = [
    "dim_date", "dim_country", "dim_calendar", "dim_currency", "dim_bank", "dim_entity", "dim_account",
    "dim_counterparty", "dim_payment_type", "dim_failure_reason", "dim_purpose_code",
]
FACTS = ["fact_fx_rate", "fact_invoice", "fact_payment", "fact_payment_event", "fact_balance", "fact_sweep",
         "fact_statement_line", "fact_fx_hedge"]
ALL_TABLES = [*reversed(FACTS), *reversed(DIMENSIONS)]


@dataclass
class Dataset:
    tables: dict[str, pd.DataFrame]   # clean-database tables, in load order
    answer_key: dict[str, pd.DataFrame]  # the answer key, never loaded into the clean database


@dataclass
class Simulation:
    """The full simulated future up to `horizon_end` (not an as-of view)."""
    cfg: SimulationConfig
    world: World
    static: dict[str, pd.DataFrame]
    fx: Fx
    invoices: pd.DataFrame
    allocation: pd.DataFrame      # payment_id, invoice_id, allocated_amount
    payments: pd.DataFrame        # final outcomes + internal "_" columns
    events: pd.DataFrame
    balances: pd.DataFrame        # includes internal _eod_ts
    sweeps: pd.DataFrame
    postings: pd.DataFrame
    opening: pd.Series
    statement_lines: pd.DataFrame  # includes internal _posting_ts
    statement_answer_key: pd.DataFrame
    hedges: pd.DataFrame
    horizon_end: date


def build_static(cfg: SimulationConfig, horizon_end: date | None = None
                 ) -> tuple[World, dict[str, pd.DataFrame], BusinessCalendar, Fx]:
    horizon_end = horizon_end or cfg.end_date
    world = build_world(cfg)
    dim_date = build_dim_date(cfg.data_start, horizon_end + timedelta(days=cfg.forward_days))
    dim_calendar = build_dim_calendar(dim_date, list(cfg.countries))
    fx = build_fx_rates(cfg, cfg.data_start, horizon_end + timedelta(days=3))
    tables = {"dim_date": dim_date, "dim_calendar": dim_calendar, **world.tables(), "fact_fx_rate": fx}
    return world, tables, BusinessCalendar(dim_date, dim_calendar), Fx.from_frame(fx)


def run_simulation(cfg: SimulationConfig, horizon_end: date | None = None) -> Simulation:
    horizon_end = horizon_end or cfg.end_date
    world, static, cal, fx = build_static(cfg, horizon_end)
    invoices, intents, alloc = events.generate(cfg, world, cal, fx, end=np.datetime64(horizon_end))
    intents, world = anomalies.inject(cfg, world, fx, intents)
    static["dim_counterparty"] = world.dim_counterparty
    payments = timing.run(cfg, world, cal, fx, intents)
    led = ledger.run(cfg, world, cal, fx, payments, np.datetime64(horizon_end) + np.timedelta64(2, "D"))
    payments = led.payments
    first = payments[payments["_mirror_of"].isna()].drop_duplicates("intent_id")[["intent_id", "payment_id"]]
    allocation = alloc.merge(first, on="intent_id")[["payment_id", "invoice_id", "allocated_amount"]]
    ev = state_machine.build_events(payments, world.dim_account)
    lines, line_answer_key = statements.build(led.postings, payments, world.dim_account, world.dim_entity,
                                         world.dim_counterparty)
    hedges = hedging.build(cfg, payments, world.dim_entity, cal, fx)
    return Simulation(cfg=cfg, world=world, static=static, fx=fx, invoices=invoices, allocation=allocation,
                      payments=payments, events=ev, balances=led.balances, sweeps=led.sweeps,
                      postings=led.postings, opening=led.opening, statement_lines=lines,
                      statement_answer_key=line_answer_key, hedges=hedges, horizon_end=horizon_end)


def snapshot(sim: Simulation, t: pd.Timestamp, balance_date_max: date | None = None) -> Dataset:
    """Clean-database tables as they look at simulated time t (UTC).

    balance_date_max also includes end-of-day snapshots up to that local date even if a
    western timezone's EOD falls a few hours after t (used by the backfill cut).
    """
    cfg = sim.cfg
    start = pd.Timestamp(cfg.start_date)
    tables = {k: v for k, v in sim.static.items() if k != "fact_fx_rate"}
    fx = sim.static["fact_fx_rate"]

    p_all = asof.payments_as_of(sim.payments, t)
    p = p_all[p_all["intended_date"] >= start].reset_index(drop=True)

    inv = sim.invoices.assign(issue_date_id=_date_id(sim.invoices["issue_date"]),
                              due_date_id=_date_id(sim.invoices["due_date"]))
    # Drop warm-up invoices already fully settled before the window opened.
    early = inv[inv["issue_date_id"] < int(start.strftime("%Y%m%d"))]
    at_start = asof.invoices_as_of(early, sim.allocation, asof.payments_as_of(sim.payments, start), start)
    inv = inv[~inv["invoice_id"].isin(at_start.loc[at_start["status"] == "paid", "invoice_id"])]
    inv = asof.invoices_as_of(inv, sim.allocation, p_all, t)

    cps = tables["dim_counterparty"].copy()
    first_seen = pd.to_datetime(p.groupby("counterparty_id")["initiated_ts"].min()).dt.date
    cps["first_seen_date"] = cps["counterparty_id"].map(first_seen)
    tables["dim_counterparty"] = cps

    ev = sim.events[sim.events["payment_id"].isin(p["payment_id"]) & (sim.events["event_ts"] <= t)]
    start_id = int(start.strftime("%Y%m%d"))
    bmax = int(balance_date_max.strftime("%Y%m%d")) if balance_date_max else 0
    bal = sim.balances[(sim.balances["date_id"] >= start_id)
                       & ((sim.balances["_eod_ts"] <= t) | (sim.balances["date_id"] <= bmax))]
    sw = sim.sweeps[(sim.sweeps["date_id"] >= start_id)
                    & ((sim.sweeps["sweep_ts"] <= t) | (sim.sweeps["date_id"] <= bmax))]

    tables.update({
        "fact_fx_rate": fx[fx["date_id"] <= min(int((t - pd.Timedelta(seconds=1)).strftime("%Y%m%d")),
                                                bmax or 99_999_999)].reset_index(drop=True),
        "fact_invoice": inv[["invoice_id", "direction", "entity_id", "counterparty_id", "invoice_ref",
                             "currency_code", "amount", "issue_date_id", "due_date_id", "status"]
                            ].reset_index(drop=True),
        "fact_payment": asof.public(p),
        "fact_payment_event": ev.reset_index(drop=True),
        "fact_balance": bal.drop(columns="_eod_ts").reset_index(drop=True),
        "fact_sweep": sw.reset_index(drop=True),
        "fact_statement_line": cut_lines(sim.statement_lines[sim.statement_lines["booking_date_id"] >= start_id], t),
        "fact_fx_hedge": sim.hedges[sim.hedges["_trade_date"] <= t].drop(columns="_trade_date").reset_index(drop=True),
    })
    lines = tables["fact_statement_line"]["line_id"]
    answer_key = {
        "payment_to_invoice": sim.allocation[sim.allocation["payment_id"].isin(p["payment_id"])].reset_index(drop=True),
        "payment_business_flow": p[["payment_id", "flow", "entity_id"]],
        "business_anomalies": p.loc[p["anomaly_type"].notna(), ["payment_id", "anomaly_type"]].reset_index(drop=True),
        "statement_line_to_payment": sim.statement_answer_key[
            sim.statement_answer_key["line_id"].isin(lines)].reset_index(drop=True),
    }
    return Dataset(tables=tables, answer_key=answer_key)


def cut_lines(lines: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    return lines[lines["_posting_ts"] <= t].drop(columns="_posting_ts").reset_index(drop=True)


def backfill_cut(cfg: SimulationConfig) -> pd.Timestamp:
    """The instant (UTC) the last entity timezone finishes end_date, so every table agrees."""
    day_after = pd.Timestamp(cfg.end_date) + pd.Timedelta(days=1)
    return max(day_after.tz_localize(cfg.countries[e.country].timezone).tz_convert("UTC").tz_localize(None)
               for e in cfg.entities)


def simulate(cfg: SimulationConfig) -> Dataset:
    return snapshot(run_simulation(cfg), backfill_cut(cfg), balance_date_max=cfg.end_date)


def _date_id(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y%m%d").astype(int)


# ---------------------------------------------------------------- sinks

RAW_FEEDS = {"payments": ("fact_payment", "initiated_ts"), "invoices": ("fact_invoice", "issue_date_id"),
           "payment_events": ("fact_payment_event", "event_ts"),
           "bank_statements": ("fact_statement_line", "booking_date_id")}


def write_raw(cfg: SimulationConfig, ds: Dataset) -> None:
    """Raw timestamped batches, one Parquet file per month, like host-to-host drops.

    The payments files carry injected data-quality defects (labels in data/answer_key/dq_defects).
    """
    root = Path(cfg.output.raw_dir)
    for name, (table, ts_col) in RAW_FEEDS.items():
        df = ds.tables[table]
        if table == "fact_payment":
            df = data_quality.apply(cfg, df)
        out = root / name
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.parquet"):
            old.unlink()
        key = (pd.to_datetime(df[ts_col]).dt.strftime("%Y-%m") if ts_col.endswith("_ts")
               else df[ts_col].astype(str).str[:4] + "-" + df[ts_col].astype(str).str[4:6])
        for month, part in df.groupby(key):
            part.to_parquet(out / f"{name}_{month}.parquet", index=False)
    write_answer_key(cfg, ds)


def write_answer_key(cfg: SimulationConfig, ds: Dataset) -> None:
    answer_key_dir = Path(cfg.output.answer_key_dir)
    answer_key_dir.mkdir(parents=True, exist_ok=True)
    for name, df in ds.answer_key.items():
        df.to_parquet(answer_key_dir / f"{name}.parquet", index=False)
    data_quality.label(cfg, ds.tables["fact_payment"]).to_parquet(answer_key_dir / "dq_defects.parquet", index=False)


def write_clean_db(cfg: SimulationConfig, tables: dict[str, pd.DataFrame]) -> None:
    """Full rebuild: drop and recreate every table so schema changes always apply."""
    engine = get_engine(cfg.output.clean_db_url)
    with engine.begin() as conn:
        for t in ALL_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
    create_schema(engine)
    for t in DIMENSIONS + FACTS:
        if t in tables:
            replace_rows(engine, t, tables[t])


def run_backfill(cfg: SimulationConfig, log=print) -> Dataset:
    t0 = time.perf_counter()
    ds = simulate(cfg)
    log(f"Simulated in {time.perf_counter() - t0:.1f}s")
    write_raw(cfg, ds)
    t1 = time.perf_counter()
    write_clean_db(cfg, ds.tables)
    log(f"Loaded clean database in {time.perf_counter() - t1:.1f}s -> {cfg.output.clean_db_url}")
    for name, df in ds.tables.items():
        log(f"  {name:<20} {len(df):>10,} rows")
    refresh_exports(cfg, log=log)
    return ds
