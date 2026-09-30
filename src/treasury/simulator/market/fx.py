"""Market Engine: daily FX fixings to the reporting currency.

Log-space random walk with mean reversion towards the starting level. Restricted
currencies (CNY, INR) get low volatility in config, so they stay in a managed band.
Rates are published on every calendar day (weekend = Friday's fixing carried
forward) so any value date can be converted to SGD without gaps.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from treasury.simulator.config import SimulationConfig
from treasury.simulator.rng import stream


def build_fx_rates(cfg: SimulationConfig, start: date, end: date) -> pd.DataFrame:
    rng = stream(cfg.seed, "fx")
    days = pd.date_range(start, end, freq="D")
    weekday = days.dayofweek < 5
    frames = []
    for ccy, c in cfg.currencies.items():
        if ccy == cfg.reporting_currency:
            rates = np.ones(len(days))
        else:
            log_level, anchor = np.log(c.start_rate_to_sgd), np.log(c.start_rate_to_sgd)
            rates = np.empty(len(days))
            for i, is_wd in enumerate(weekday):
                if is_wd:
                    log_level += cfg.fx.mean_reversion * (anchor - log_level)
                    log_level += rng.normal(0, c.daily_vol)
                rates[i] = np.exp(log_level)
        frames.append(pd.DataFrame({
            "date_id": days.strftime("%Y%m%d").astype(int),
            "currency_code": ccy,
            "rate_to_sgd": np.round(rates, 6),
            "is_published_fixing": weekday,
        }))
    return pd.concat(frames, ignore_index=True)


def spread_bps(cfg: SimulationConfig, ccy: str) -> float:
    return cfg.fx.bank_spread_bps.get(ccy, cfg.fx.bank_spread_bps["default"])
