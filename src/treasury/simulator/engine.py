"""Discrete-event core used by the ledger (v2) and replayed by the live stream (v3).

Most events are known up front (every settlement posting, every end-of-day), so they
are passed in as pre-sorted numpy arrays - fast even for ~1M events. Events created
while running (an intercompany top-up decided at 07:00 that settles at 09:10) go on a
heap. Iteration always yields the earliest event next, so handlers see the world
strictly in time order, exactly like a bank's own ledger.

Ordering key: (timestamp, priority, sequence). Lower priority runs first at equal times.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator

import numpy as np


class EventLoop:
    def __init__(self, ts: np.ndarray, prio: np.ndarray, kind: np.ndarray, ref: np.ndarray):
        order = np.lexsort((np.arange(len(ts)), prio, ts))
        self._ts = ts[order].tolist()
        self._prio = prio[order].tolist()
        self._kind = kind[order].tolist()
        self._ref = ref[order].tolist()
        self._i = 0
        self._heap: list[tuple[int, int, int, int, int]] = []
        self._seq = len(ts)

    def schedule(self, ts: int, prio: int, kind: int, ref: int) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (ts, prio, self._seq, kind, ref))

    def __iter__(self) -> Iterator[tuple[int, int, int]]:
        ts, prio, kinds, refs, heap = self._ts, self._prio, self._kind, self._ref, self._heap
        n = len(ts)
        while self._i < n or heap:
            if heap and (self._i >= n or (heap[0][0], heap[0][1]) < (ts[self._i], prio[self._i])):
                t, _, _, k, r = heapq.heappop(heap)
                yield t, k, r
            else:
                i = self._i
                self._i += 1
                yield ts[i], kinds[i], refs[i]
