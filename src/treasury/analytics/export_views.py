"""Export every vw_* view to a CSV in dashboards/data/ for Tableau Public (it cannot read SQLite).

Views with a `date_id` column (YYYYMMDD integer) also get a real `date` column, because Tableau
needs a proper date to draw a time axis.

    TREASURY_CONFIG=config/simulation.small.yaml .venv/bin/python -m treasury.analytics.views
    TREASURY_CONFIG=config/simulation.small.yaml .venv/bin/python -m treasury.analytics.export_views
"""

import sqlite3
from pathlib import Path

import pandas as pd

from treasury.analytics.views import clean_db_path

OUT_DIR = Path("dashboards/data")


def export_views(con: sqlite3.Connection, out_dir: Path = OUT_DIR) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("vw_*.csv"):           # drop CSVs of views that no longer exist
        old.unlink()
    query = "SELECT name FROM sqlite_master WHERE type = 'view' AND name LIKE 'vw\\_%' ESCAPE '\\' ORDER BY name"
    names = [r[0] for r in con.execute(query)]
    rows = {}
    for name in names:
        df = pd.read_sql(f"SELECT * FROM {name}", con)
        for col in [c for c in df.columns if c.endswith("date_id")]:
            df[col.removesuffix("_id")] = pd.to_datetime(df[col].astype(str), format="%Y%m%d").dt.date
        df.to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8")
        rows[name] = len(df)
    return rows


def main() -> None:
    with sqlite3.connect(clean_db_path()) as con:
        rows = export_views(con)
    for name, n in rows.items():
        print(f"{n:>7,}  {OUT_DIR / (name + '.csv')}")


if __name__ == "__main__":
    main()
