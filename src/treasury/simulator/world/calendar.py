"""dim_date (one row per date) and dim_calendar (one row per date x country).

The blueprint's single `is_business_day` flag cannot express that 1 Oct is a
holiday in CN but not in SG, so business-day logic lives in the bridge table.
"""

from __future__ import annotations

from datetime import date

import holidays
import numpy as np
import pandas as pd


def build_dim_date(start: date, end: date) -> pd.DataFrame:
    d = pd.DataFrame({"date": pd.date_range(start, end, freq="D")})
    d["date_id"] = d["date"].dt.strftime("%Y%m%d").astype(int)
    d["week"] = d["date"].dt.isocalendar().week.astype(int)
    d["month"] = d["date"].dt.month
    d["quarter"] = d["date"].dt.quarter
    d["year"] = d["date"].dt.year
    d["day_of_week"] = d["date"].dt.dayofweek
    d["is_weekend"] = d["day_of_week"] >= 5
    d["is_month_end"] = d["date"].dt.is_month_end
    d["is_quarter_end"] = d["date"].dt.is_quarter_end
    d["date"] = d["date"].dt.date
    return d[["date_id", "date", "week", "month", "quarter", "year",
              "day_of_week", "is_weekend", "is_month_end", "is_quarter_end"]]


def build_dim_calendar(dim_date: pd.DataFrame, countries: list[str]) -> pd.DataFrame:
    years = sorted(dim_date["year"].unique().tolist())
    rows = []
    for cc in countries:
        try:
            hol = holidays.country_holidays(cc, years=years)
        except NotImplementedError:  # fictional countries (XA, XB): weekends only
            hol = {}
        for date_id, d, is_weekend in dim_date[["date_id", "date", "is_weekend"]].itertuples(
            index=False
        ):
            name = hol.get(d)
            rows.append((date_id, cc, not is_weekend and name is None, name))
    return pd.DataFrame(rows, columns=["date_id", "country_code", "is_business_day", "holiday_name"])


class BusinessCalendar:
    """Vectorised business-day arithmetic per country, backed by dim_calendar.

    Days are numpy datetime64[D]. Results past the calendar end are clipped to the
    last business day, which is why dim_date extends `forward_days` beyond end_date.
    """

    def __init__(self, dim_date: pd.DataFrame, dim_calendar: pd.DataFrame):
        by_id = dict(zip(dim_date["date_id"], pd.to_datetime(dim_date["date"]).values.astype("datetime64[D]"),
                         strict=True))
        bd = dim_calendar[dim_calendar["is_business_day"]]
        self._days: dict[str, np.ndarray] = {
            cc: np.sort(np.array([by_id[d] for d in g["date_id"]], dtype="datetime64[D]"))
            for cc, g in bd.groupby("country_code")
        }

    def countries(self) -> list[str]:
        return list(self._days)

    def business_days(self, country: str) -> np.ndarray:
        return self._days[country]

    def _apply(self, countries: np.ndarray, days: np.ndarray, fn) -> np.ndarray:
        countries = np.asarray(countries)
        days = np.asarray(days, dtype="datetime64[D]")
        out = np.empty(len(days), dtype="datetime64[D]")
        for cc in np.unique(countries):
            m = countries == cc
            out[m] = fn(self._days[cc], days[m], m)
        return out

    def is_business_day(self, countries: np.ndarray, days: np.ndarray) -> np.ndarray:
        rolled = self.roll_forward(countries, days)
        return rolled == np.asarray(days, dtype="datetime64[D]")

    def roll_forward(self, countries: np.ndarray, days: np.ndarray) -> np.ndarray:
        """First business day on or after each day."""
        def fn(bdays, d, _):
            return bdays[np.clip(np.searchsorted(bdays, d, side="left"), 0, len(bdays) - 1)]
        return self._apply(countries, days, fn)

    def roll_backward(self, countries: np.ndarray, days: np.ndarray) -> np.ndarray:
        """Last business day on or before each day."""
        def fn(bdays, d, _):
            return bdays[np.clip(np.searchsorted(bdays, d, side="right") - 1, 0, len(bdays) - 1)]
        return self._apply(countries, days, fn)

    def add_business_days(self, countries: np.ndarray, days: np.ndarray, n: np.ndarray | int) -> np.ndarray:
        """Roll forward to a business day, then move n business days further."""
        n_all = np.broadcast_to(np.asarray(n), np.shape(days))

        def fn(bdays, d, m):
            idx = np.searchsorted(bdays, d, side="left") + n_all[m]
            return bdays[np.clip(idx, 0, len(bdays) - 1)]
        return self._apply(countries, days, fn)
