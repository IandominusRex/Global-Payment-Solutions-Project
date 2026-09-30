"""treasury-sim command line.

  treasury-sim build-world   # dimensions, calendar and FX fixings only
  treasury-sim backfill      # full history: world + invoices + payments (v1)
  treasury-sim stream        # live simulated clock continuing after the backfill (v3)
  treasury-sim export-camt053 --account A001 --date 2026-09-15   # ISO 20022 statement XML (v4)
  treasury-sim export-all    # Excel workbook (one sheet per table), CSVs, dataset card + samples
"""

from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

from treasury.simulator.backfill import build_static, run_backfill, write_warehouse
from treasury.simulator.config import load_config
from treasury.simulator.sinks.exports import refresh_exports
from treasury.simulator.sinks.iso20022 import export_camt053
from treasury.simulator.sinks.warehouse import get_engine
from treasury.simulator.stream import run_stream


def build_world_cmd(config_path: str) -> None:
    t0 = time.perf_counter()
    cfg = load_config(config_path)
    _, tables, _, _ = build_static(cfg)
    write_warehouse(cfg, tables)  # also clears facts, so they never point at stale dimensions
    print(f"World built in {time.perf_counter() - t0:.1f}s -> {cfg.output.warehouse_url}")
    for name, df in tables.items():
        print(f"  {name:<20} {len(df):>8,} rows")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="treasury-sim")
    parser.add_argument("--config", default="config/simulation.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build-world", help="build dimensions, calendar and FX fixings")
    sub.add_parser("backfill", help="generate full history (world, invoices, payments)")
    st = sub.add_parser("stream", help="continue past the backfill on a live simulated clock")
    st.add_argument("--days", type=int, help="simulated days to stream (default: config stream.default_days)")
    st.add_argument("--speed", type=float, default=300.0,
                    help="simulated seconds per real second (300 = 5 sim minutes per second; 0 = no pacing)")
    st.add_argument("--max-ticks", type=int, help="stop after this many ticks")
    st.add_argument("--webhook", help="POST camt.054-style notifications to this URL")
    st.add_argument("--no-landing", action="store_true", help="skip Parquet micro-batches")
    ex = sub.add_parser("export-camt053", help="write an ISO 20022 camt.053 statement for one account and day")
    ex.add_argument("--account", required=True)
    ex.add_argument("--date", required=True, type=date.fromisoformat)
    ex.add_argument("--out", type=Path, help="default: data/iso20022/camt053_<account>_<date>.xml")
    sub.add_parser("export-all", help="rebuild the Excel workbook, CSVs and dataset card from the warehouse")
    args = parser.parse_args(argv)

    if args.cmd == "build-world":
        build_world_cmd(args.config)
    elif args.cmd == "backfill":
        run_backfill(load_config(args.config))
    elif args.cmd == "export-camt053":
        cfg = load_config(args.config)
        out = args.out or Path("data/iso20022") / f"camt053_{args.account}_{args.date:%Y%m%d}.xml"
        print(export_camt053(get_engine(cfg.output.warehouse_url), args.account, args.date, out))
    elif args.cmd == "export-all":
        refresh_exports(load_config(args.config))
    elif args.cmd == "stream":
        run_stream(load_config(args.config), days=args.days, speed=args.speed, max_ticks=args.max_ticks,
                   webhook=args.webhook, landing=not args.no_landing)


if __name__ == "__main__":
    main()
