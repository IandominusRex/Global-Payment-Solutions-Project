"""dim_date (one row per date) and dim_calendar (one row per date x country).

The blueprint's single `is_business_day` flag cannot express that 1 Oct is a
holiday in CN but not in SG, so business-day logic lives in the bridge table.
"""

from __future__ import annotations

from datetime import date

import holidays
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
