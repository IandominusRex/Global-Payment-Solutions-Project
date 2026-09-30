"""Simulated clock for live mode (v3).

The clock advances simulated time in fixed ticks. `speed` only controls how long each
tick takes in real time (speed=300: a 5-minute tick takes 1 real second; speed=0 runs
as fast as possible). Wall-clock time never influences *what* is generated - only
*when* it is emitted - so a live run produces exactly the rows a backfill would.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pandas as pd


class SimClock:
    def __init__(self, start: pd.Timestamp, tick_seconds: int, speed: float, end: pd.Timestamp | None = None):
        self.now = pd.Timestamp(start)
        self.tick = pd.Timedelta(seconds=tick_seconds)
        self.speed = speed
        self.end = end

    def ticks(self, max_ticks: int | None = None) -> Iterator[tuple[pd.Timestamp, pd.Timestamp]]:
        """Yield (previous, now) windows; sleeps between ticks according to speed."""
        n = 0
        while (max_ticks is None or n < max_ticks) and (self.end is None or self.now < self.end):
            started = time.monotonic()
            prev, self.now = self.now, min(self.now + self.tick, self.end) if self.end else self.now + self.tick
            yield prev, self.now
            n += 1
            if self.speed > 0:
                budget = self.tick.total_seconds() / self.speed
                time.sleep(max(0.0, budget - (time.monotonic() - started)))
