"""Named, independent random streams derived from one seed.

Each engine asks for its own stream by name. Adding a new engine or drawing
more numbers in one engine therefore never shifts the numbers another engine
sees, which keeps datasets comparable across code changes.
"""

from __future__ import annotations

import hashlib

import numpy as np


def stream(seed: int, name: str) -> np.random.Generator:
    key = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
    return np.random.default_rng(np.random.SeedSequence([seed, key]))
