"""Backfill orchestration: world -> events -> lifecycle -> landing / truth / warehouse."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from treasury.simulator.business import events
from treasury.simulator.business.events import Fx
from treasury.simulator.config import SimulationConfig
from treasury.simulator.lifecycle import simple
from treasury.simulator.market.fx import build_fx_rates
from treasury.simulator.sinks.warehouse import create_schema, get_engine, replace_rows
from treasury.simulator.world.builder import World, build_world
from treasury.simulator.world.calendar import BusinessCalendar, build_dim_calendar, build_dim_date

# Parents before children, so foreign keys are satisfied on insert (reverse for delete).
DIMENSIONS = [
    "dim_date", "dim_country", "dim_calendar", "dim_currency", "dim_bank", "dim_entity", "dim_account",
    "dim_counterparty", "dim_payment_type", "dim_failure_reason", "dim_purpose_code",
]
FACTS = ["fact_fx_rate", "fact_invoice", "fact_payment"]
ALL_TABLES = [
    "fact_payment_event", "fact_statement_line", "fact_sweep", "fact_balance", "fact_fx_hedge",
    *reversed(FACTS), *reversed(DIMENSIONS),
]


@dataclass
class Dataset:
    tables: dict[str, pd.DataFrame]   # warehouse tables, in load order
    truth: dict[str, pd.DataFrame]    # hidden ground truth, never loaded


def build_static(cfg: SimulationConfig) -> tuple[World, dict[str, pd.DataFrame], BusinessCalendar, Fx]:
    world = build_world(cfg)
    dim_date = build_dim_date(cfg.data_start, cfg.calendar_end)
    dim_calendar = build_dim_calendar(dim_date, list(cfg.countries))
    fx = build_fx_rates(cfg, cfg.data_start, cfg.end_date)
    tables = {"dim_date": dim_date, "dim_calendar": dim_calendar, **world.tables(), "fact_fx_rate": fx}
    return world, tables, BusinessCalendar(dim_date, dim_calendar), Fx.from_frame(fx)


def simulate(cfg: SimulationConfig) -> Dataset:
    world, tables, cal, fx = build_static(cfg)
    invoices, intents = events.generate(cfg, world, cal, fx)
    payments, allocation = simple.run(cfg, world, cal, fx, intents)

    tables["fact_invoice"] = _finalise_invoices(cfg, invoices, payments)

    # Keep only payments inside the reporting window; the warm-up exists so receipts
    # don't ramp up from zero on day one.
    payments = payments[payments["intended_date"] >= np.datetime64(cfg.start_date)].reset_index(drop=True)
    allocation = allocation[allocation["payment_id"].isin(payments["payment_id"])]
    allocation = allocation[allocation["invoice_id"].isin(tables["fact_invoice"]["invoice_id"])]

    first_seen = pd.to_datetime(payments.groupby("counterparty_id")["initiated_ts"].min()).dt.date
    cps = tables["dim_counterparty"].copy()
    cps["first_seen_date"] = cps["counterparty_id"].map(first_seen)
    tables["dim_counterparty"] = cps

    tables["fact_payment"] = payments[simple.PAYMENT_COLUMNS]
    truth = {
        "payment_invoice": allocation.reset_index(drop=True),
        "payment_flow": payments[["payment_id", "flow", "entity_id"]],
    }
    return Dataset(tables=tables, truth=truth)


def _finalise_invoices(cfg: SimulationConfig, invoices: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    """Invoice status from the full payment history (including the warm-up period)."""
    good = payments[payments["status"].isin(["completed", "delayed"]) & payments["invoice_id"].notna()]
    paid = set(good["invoice_id"])
    inv = invoices.copy()
    start, end = np.datetime64(cfg.start_date), np.datetime64(cfg.end_date)
    # Drop warm-up invoices that were fully settled before the window opened.
    paid_before = set(good.loc[good["intended_date"] < start, "invoice_id"])
    inv = inv[(inv["issue_date"] >= start) | ~inv["invoice_id"].isin(paid_before)]
    inv = inv[inv["issue_date"] <= end]
    status = np.where(inv["invoice_id"].isin(paid), "paid", "open").astype(object)
    # Only receivables get written off; unpaid payables stay owed.
    stale = ((inv["due_date"] < end - np.timedelta64(180, "D")) & (inv["direction"] == "AR")).to_numpy() \
        & (status == "open")
    status[stale] = "written_off"
    inv["status"] = status
    inv["issue_date_id"] = inv["issue_date"].dt.strftime("%Y%m%d").astype(int)
    inv["due_date_id"] = inv["due_date"].dt.strftime("%Y%m%d").astype(int)
    return inv[["invoice_id", "direction", "entity_id", "counterparty_id", "invoice_ref", "currency_code",
                "amount", "issue_date_id", "due_date_id", "status"]].reset_index(drop=True)


def write_landing(cfg: SimulationConfig, ds: Dataset) -> None:
    """Raw timestamped batches, one Parquet file per month, like host-to-host drops."""
    root = Path(cfg.output.landing_dir)
    for name, ts_col, df in [("payments", "initiated_ts", ds.tables["fact_payment"]),
                             ("invoices", "issue_date_id", ds.tables["fact_invoice"])]:
        out = root / name
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.parquet"):
            old.unlink()
        key = (pd.to_datetime(df[ts_col]).dt.strftime("%Y-%m") if ts_col.endswith("_ts")
               else df[ts_col].astype(str).str[:4] + "-" + df[ts_col].astype(str).str[4:6])
        for month, part in df.groupby(key):
            part.to_parquet(out / f"{name}_{month}.parquet", index=False)
    truth_dir = Path(cfg.output.truth_dir)
    truth_dir.mkdir(parents=True, exist_ok=True)
    for name, df in ds.truth.items():
        df.to_parquet(truth_dir / f"{name}.parquet", index=False)


def write_warehouse(cfg: SimulationConfig, tables: dict[str, pd.DataFrame]) -> None:
    """Full rebuild: drop and recreate every table so schema changes always apply."""
    engine = get_engine(cfg.output.warehouse_url)
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
    write_landing(cfg, ds)
    t1 = time.perf_counter()
    write_warehouse(cfg, ds.tables)
    log(f"Loaded warehouse in {time.perf_counter() - t1:.1f}s -> {cfg.output.warehouse_url}")
    for name, df in ds.tables.items():
        log(f"  {name:<20} {len(df):>10,} rows")
    return ds
