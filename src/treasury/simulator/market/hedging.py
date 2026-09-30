"""FX hedge book (v4): monthly forwards against each entity's foreign-currency exposure.

On the first business day of each month, an entity hedges a share (30-70%) of last
month's net foreign-currency flow in that currency - what it can see, not the future.
A long EUR position is hedged by selling EUR forward, a short CNY position by buying
CNY forward. Analysis 7 compares the exposure with the hedged notional.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from treasury.simulator.business.events import Fx
from treasury.simulator.config import SimulationConfig
from treasury.simulator.rng import stream
from treasury.simulator.world.calendar import BusinessCalendar

MIN_EXPOSURE_SGD = 500_000
HEDGE_COLUMNS = ["hedge_id", "entity_id", "trade_date_id", "maturity_date_id", "buy_currency", "sell_currency",
                 "buy_amount", "sell_amount", "forward_rate"]


def build(cfg: SimulationConfig, payments: pd.DataFrame, dim_entity: pd.DataFrame, cal: BusinessCalendar,
          fx: Fx) -> pd.DataFrame:
    rng = stream(cfg.seed, "hedging")
    ent = dim_entity.set_index("entity_id")
    p = payments[payments["status"].isin(["completed", "delayed"]) & ~payments["is_intercompany"]
                 & payments["settled_ts"].notna()]
    p = p[p["currency_code"].to_numpy() != ent.loc[p["entity_id"], "functional_currency"].to_numpy()]
    signed = np.where(p["direction"] == "IN", 1, -1) * p["amount"]
    month = pd.to_datetime(p["settled_ts"]).dt.to_period("M")
    net = signed.groupby([p["entity_id"], p["currency_code"], month]).sum().reset_index(name="net")

    rows = []
    for r in net.itertuples():
        trade_month = r.settled_ts + 1
        country = ent.loc[r.entity_id, "country"]
        first = np.datetime64(trade_month.start_time.date())
        last = np.datetime64(trade_month.end_time.date())
        trade = cal.roll_forward(np.array([country]), np.array([first]))[0]
        maturity = cal.roll_backward(np.array([country]), np.array([last]))[0]
        spot_sgd = fx.to_sgd(np.array([r.currency_code]), np.array([trade]))[0]
        if abs(r.net) * spot_sgd < MIN_EXPOSURE_SGD:
            continue
        functional = ent.loc[r.entity_id, "functional_currency"]
        cross = spot_sgd / fx.to_sgd(np.array([functional]), np.array([trade]))[0]  # functional per foreign
        fwd = cross * (1 + rng.normal(0, 0.002))
        hedged = abs(r.net) * rng.uniform(0.3, 0.7)
        if r.net > 0:   # long foreign: sell it forward
            buy, sell, buy_amt, sell_amt = functional, r.currency_code, hedged * fwd, hedged
        else:           # short foreign: buy it forward
            buy, sell, buy_amt, sell_amt = r.currency_code, functional, hedged, hedged * fwd
        rows.append({"entity_id": r.entity_id, "trade_date": trade, "maturity_date": maturity,
                     "buy_currency": buy, "sell_currency": sell, "buy_amount": round(buy_amt, 2),
                     "sell_amount": round(sell_amt, 2), "forward_rate": round(fwd, 6)})
    h = pd.DataFrame(rows)
    h = h[h["trade_date"] >= np.datetime64(cfg.start_date)] if len(h) else h
    if h.empty:
        return pd.DataFrame(columns=HEDGE_COLUMNS + ["_trade_date"])
    h = h.sort_values(["trade_date", "entity_id", "buy_currency", "sell_currency"]).reset_index(drop=True)
    h["hedge_id"] = [f"H{i:06d}" for i in range(1, len(h) + 1)]
    h["trade_date_id"] = pd.to_datetime(h["trade_date"]).dt.strftime("%Y%m%d").astype(int)
    h["maturity_date_id"] = pd.to_datetime(h["maturity_date"]).dt.strftime("%Y%m%d").astype(int)
    h["_trade_date"] = pd.to_datetime(h["trade_date"]) + pd.Timedelta(hours=9)  # booked in the morning
    return h[HEDGE_COLUMNS + ["_trade_date"]]
