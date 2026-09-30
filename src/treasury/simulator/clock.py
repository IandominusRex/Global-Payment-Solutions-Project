"""Simulated clock (v3).

Backfill and live mode share one engine; only the clock differs:
- backfill: jump straight to the next event timestamp (as fast as possible)
- live: sleep so that simulated time advances at `speed_multiplier` x real time

Wall-clock time must never influence *what* is generated, only *when* it is
emitted, so a live run with the same seed produces the same rows as a backfill.
"""
