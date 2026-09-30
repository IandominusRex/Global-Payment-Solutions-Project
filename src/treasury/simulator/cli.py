"""treasury-sim command line.

  treasury-sim build-world   # dimensions, calendar and FX fixings -> warehouse   (v1, works)
  treasury-sim backfill      # history through the full event engine              (v1-v2, todo)
  treasury-sim stream        # live simulated clock                               (v3, todo)
"""

from __future__ import annotations

import argparse
import time

import pandas as pd
from sqlalchemy import text

from treasury.simulator.config import load_config
from treasury.simulator.market.fx import build_fx_rates
from treasury.simulator.sinks.warehouse import create_schema, get_engine, replace_rows
from treasury.simulator.world.builder import build_world
from treasury.simulator.world.calendar import build_dim_calendar, build_dim_date

# Parents before children, so foreign keys are satisfied on load.
LOAD_ORDER = [
    "dim_country", "dim_currency", "dim_bank", "dim_entity", "dim_account",
    "dim_counterparty", "dim_payment_type", "dim_failure_reason", "dim_purpose_code",
]


def build_world_cmd(config_path: str) -> None:
    t0 = time.perf_counter()
    cfg = load_config(config_path)
    end = (pd.Timestamp(cfg.start_date) + pd.DateOffset(months=cfg.backfill_months)).date()

    world = build_world(cfg).tables()
    dim_date = build_dim_date(cfg.start_date, end)
    dim_calendar = build_dim_calendar(dim_date, list(cfg.countries))
    fx = build_fx_rates(cfg, cfg.start_date, end)

    engine = get_engine(cfg.output.warehouse_url)
    create_schema(engine)
    # Children first on delete, parents first on insert.
    for table in ["fact_fx_rate", "dim_calendar", "dim_date", *reversed(LOAD_ORDER)]:
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {table}"))
    replace_rows(engine, "dim_date", dim_date)
    for name in LOAD_ORDER:
        replace_rows(engine, name, world[name])
    replace_rows(engine, "dim_calendar", dim_calendar)
    replace_rows(engine, "fact_fx_rate", fx)

    print(f"World built in {time.perf_counter() - t0:.1f}s -> {cfg.output.warehouse_url}")
    for name, df in {**{n: world[n] for n in LOAD_ORDER}, "dim_date": dim_date,
                     "dim_calendar": dim_calendar, "fact_fx_rate": fx}.items():
        print(f"  {name:<20} {len(df):>8,} rows")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="treasury-sim")
    parser.add_argument("--config", default="config/simulation.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-world", help="build dimensions, calendar and FX fixings")
    sub.add_parser("backfill", help="generate history (not implemented yet)")
    sub.add_parser("stream", help="run the live simulated clock (not implemented yet)")
    args = parser.parse_args(argv)

    if args.cmd == "build-world":
        build_world_cmd(args.config)
    else:
        raise SystemExit(f"'{args.cmd}' is on the roadmap - see docs/architecture.md (build order)")


if __name__ == "__main__":
    main()
