"""treasury-sim command line.

  treasury-sim build-world   # dimensions, calendar and FX fixings only
  treasury-sim backfill      # full history: world + invoices + payments (v1)
  treasury-sim stream        # live simulated clock continuing after the backfill (v3)
"""

from __future__ import annotations

import argparse
import time

from treasury.simulator.backfill import build_static, run_backfill, write_warehouse
from treasury.simulator.config import load_config
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
    args = parser.parse_args(argv)

    if args.cmd == "build-world":
        build_world_cmd(args.config)
    elif args.cmd == "backfill":
        run_backfill(load_config(args.config))
    elif args.cmd == "stream":
        run_stream(load_config(args.config), days=args.days, speed=args.speed, max_ticks=args.max_ticks,
                   webhook=args.webhook, landing=not args.no_landing)


if __name__ == "__main__":
    main()
