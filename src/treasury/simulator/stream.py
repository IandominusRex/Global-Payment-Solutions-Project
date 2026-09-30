"""Live stream (v3): replay the simulation on a ticking clock.

  1. Simulate the full horizon (end_date + N days) from the seed.
  2. Load the warehouse as of the clock's start (end of the backfill period).
  3. Each tick: find every row whose visible state changed in (previous, now] and
     push it to the sinks - warehouse upserts, Parquet micro-batches, webhooks.

Because every tick is an as-of view of the same simulation, streaming to time t
leaves the warehouse identical to a snapshot at t (tested).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from treasury.simulator import asof
from treasury.simulator.backfill import Simulation, backfill_cut, run_simulation, snapshot, write_truth, write_warehouse
from treasury.simulator.clock import SimClock
from treasury.simulator.config import SimulationConfig
from treasury.simulator.sinks.warehouse import get_engine, upsert
from treasury.simulator.sinks.webhook import post_notifications


@dataclass
class TickResult:
    now: pd.Timestamp
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)

    def rows(self) -> int:
        return sum(len(v) for v in self.tables.values())


class Streamer:
    """Computes per-tick deltas from a Simulation."""

    def __init__(self, sim: Simulation):
        self.sim = sim
        p = sim.payments
        self.start = pd.Timestamp(sim.cfg.start_date)
        # Every moment a payment's visible state can change.
        cols = ["initiated_ts", "settled_ts", "_rejected_ts", "_returned_ts"]
        ts = np.concatenate([pd.to_datetime(p[c]).to_numpy() for c in cols])
        rows = np.tile(np.arange(len(p)), len(cols))
        ok = ~np.isnat(ts)
        order = np.argsort(ts[ok], kind="stable")
        self.change_ts, self.change_row = ts[ok][order], rows[ok][order]
        self.ev_ts = sim.events["event_ts"].to_numpy()
        self.alloc_by_payment = sim.allocation.set_index("payment_id")["invoice_id"]
        self.inv = sim.invoices.assign(issue_date_id=_date_id(sim.invoices["issue_date"]),
                                       due_date_id=_date_id(sim.invoices["due_date"]))
        self.seen_cp: set[str] = set()

    def _window(self, arr: np.ndarray, prev: pd.Timestamp, now: pd.Timestamp) -> slice:
        return slice(np.searchsorted(arr, prev.to_datetime64(), "right"),
                     np.searchsorted(arr, now.to_datetime64(), "right"))

    def prime(self, ds_tables: dict[str, pd.DataFrame]) -> None:
        self.seen_cp = set(ds_tables["fact_payment"]["counterparty_id"])
        self.visible_payments = set(ds_tables["fact_payment"]["payment_id"])

    def tick(self, prev: pd.Timestamp, now: pd.Timestamp) -> TickResult:
        sim = self.sim
        out = TickResult(now=now)
        rows = np.unique(self.change_row[self._window(self.change_ts, prev, now)])
        p = asof.payments_as_of(sim.payments.iloc[rows], now)
        p = p[p["intended_date"] >= self.start]
        out.tables["fact_payment"] = asof.public(p).reset_index(drop=True)
        self.visible_payments.update(p["payment_id"])

        new_cp = sorted(set(p["counterparty_id"]) - self.seen_cp)
        if new_cp:
            self.seen_cp.update(new_cp)
            first = p[p["counterparty_id"].isin(new_cp)].groupby("counterparty_id")["initiated_ts"].min()
            cps = sim.static["dim_counterparty"].set_index("counterparty_id").loc[new_cp].reset_index()
            cps["first_seen_date"] = cps["counterparty_id"].map(pd.to_datetime(first).dt.date)
            out.tables["dim_counterparty"] = cps

        prev_day, now_day = _day_id(prev), _day_id(now)
        touched = set(self.alloc_by_payment.reindex(p["payment_id"]).dropna())
        inv = self.inv[((self.inv["issue_date_id"] > prev_day) & (self.inv["issue_date_id"] <= now_day))
                       | self.inv["invoice_id"].isin(touched)]
        if len(inv):
            p_all = asof.payments_as_of(sim.payments[sim.payments["payment_id"].isin(
                sim.allocation.loc[sim.allocation["invoice_id"].isin(inv["invoice_id"]), "payment_id"])], now)
            inv = asof.invoices_as_of(inv, sim.allocation, p_all, now)
            out.tables["fact_invoice"] = inv[["invoice_id", "direction", "entity_id", "counterparty_id",
                                              "invoice_ref", "currency_code", "amount", "issue_date_id",
                                              "due_date_id", "status"]].reset_index(drop=True)

        ev = sim.events.iloc[self._window(self.ev_ts, prev, now)]
        out.tables["fact_payment_event"] = ev[ev["payment_id"].isin(self.visible_payments)].reset_index(drop=True)

        b = sim.balances
        out.tables["fact_balance"] = b[(b["_eod_ts"] > prev) & (b["_eod_ts"] <= now)].drop(
            columns="_eod_ts").reset_index(drop=True)
        s = sim.sweeps
        out.tables["fact_sweep"] = s[(s["sweep_ts"] > prev) & (s["sweep_ts"] <= now)].reset_index(drop=True)
        fx = sim.static["fact_fx_rate"]
        fx_prev, fx_now = _day_id(prev - pd.Timedelta(seconds=1)), _day_id(now - pd.Timedelta(seconds=1))
        out.tables["fact_fx_rate"] = fx[(fx["date_id"] > fx_prev) & (fx["date_id"] <= fx_now)].reset_index(drop=True)
        return out


UPSERT_ORDER = [("dim_counterparty", ["counterparty_id"]), ("fact_fx_rate", ["date_id", "currency_code"]),
                ("fact_invoice", ["invoice_id"]), ("fact_payment", ["payment_id"]),
                ("fact_payment_event", ["event_id"]), ("fact_balance", ["date_id", "account_id"]),
                ("fact_sweep", ["sweep_id"])]


def run_stream(cfg: SimulationConfig, days: int | None = None, speed: float = 300.0,
               max_ticks: int | None = None, webhook: str | None = None, landing: bool = True,
               log=print) -> Simulation:
    days = days or cfg.stream.default_days
    t0 = time.perf_counter()
    sim = run_simulation(cfg, horizon_end=cfg.end_date + timedelta(days=days))
    start, end = backfill_cut(cfg), backfill_cut(cfg) + pd.Timedelta(days=days)
    ds = snapshot(sim, start, balance_date_max=cfg.end_date)
    write_warehouse(cfg, ds.tables)
    write_truth(cfg, snapshot(sim, end))  # truth for everything the stream will emit
    log(f"Simulated {days}-day horizon and loaded history as of {start} in {time.perf_counter() - t0:.0f}s")

    streamer = Streamer(sim)
    streamer.prime(ds.tables)
    engine = get_engine(cfg.output.warehouse_url)
    stream_dir = Path(cfg.output.landing_dir) / "stream"
    clock = SimClock(start, cfg.stream.tick_sim_seconds, speed, end=end)
    try:
        for prev, now in clock.ticks(max_ticks):
            res = streamer.tick(prev, now)
            with engine.begin() as conn:
                for table, keys in UPSERT_ORDER:
                    df = res.tables.get(table)
                    if df is not None and len(df):
                        upsert(conn, table, df, keys)
            if landing:
                _write_micro_batch(stream_dir, res)
            if webhook:
                post_notifications(webhook, res.tables["fact_payment_event"], res.tables["fact_payment"], log)
            log(f"[{now:%Y-%m-%d %H:%M} UTC] "
                + ", ".join(f"{k.removeprefix('fact_')}={len(v)}" for k, v in res.tables.items() if len(v)))
    except KeyboardInterrupt:
        log(f"Stopped at simulated {clock.now}")
    return sim


def _write_micro_batch(root: Path, res: TickResult) -> None:
    stamp = res.now.strftime("%Y%m%dT%H%M%S")
    for table in ("fact_payment", "fact_payment_event"):
        df = res.tables.get(table)
        if df is not None and len(df):
            out = root / table.removeprefix("fact_")
            out.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out / f"{stamp}.parquet", index=False)


def _day_id(t: pd.Timestamp) -> int:
    return int(t.strftime("%Y%m%d"))


def _date_id(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y%m%d").astype(int)
