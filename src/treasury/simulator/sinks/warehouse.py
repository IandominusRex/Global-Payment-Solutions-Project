"""Warehouse sink: creates the star schema and bulk-loads DataFrames.

SQLAlchemy keeps SQLite (default) and PostgreSQL interchangeable via the URL.
SQLite runs in WAL mode so the API and BI tools can read while the stream writes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, Engine, MetaData, Table, create_engine, event, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "sql" / "schema"


def get_engine(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def create_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
            # Strip "--" comments first: they may contain ";" (DDL has no string literals).
            sql = "\n".join(line.split("--", 1)[0] for line in sql_file.read_text().splitlines())
            for stmt in sql.split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))


def replace_rows(engine: Engine, table: str, df: pd.DataFrame) -> None:
    """Load into an existing DDL table (keeps its types and constraints): delete then append."""
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table}"))
        df.to_sql(table, conn, if_exists="append", index=False, chunksize=50_000)


_TABLES: dict[tuple[int, str], Table] = {}


def upsert(conn: Connection, table: str, df: pd.DataFrame, keys: list[str]) -> None:
    """Insert or update rows by primary key (SQLite and PostgreSQL)."""
    cache_key = (id(conn.engine), table)
    if cache_key not in _TABLES:
        _TABLES[cache_key] = Table(table, MetaData(), autoload_with=conn)
    t = _TABLES[cache_key]
    records = df.astype(object).where(df.notna(), None).to_dict("records")
    for r in records:  # pandas Timestamps -> datetime, numpy scalars -> python
        for k, v in r.items():
            if isinstance(v, pd.Timestamp):
                r[k] = v.to_pydatetime()
            elif hasattr(v, "item"):
                r[k] = v.item()
    insert = sqlite_insert if conn.dialect.name == "sqlite" else pg_insert
    for i in range(0, len(records), 5_000):
        stmt = insert(t).values(records[i:i + 5_000])
        update = {c.name: stmt.excluded[c.name] for c in t.columns if c.name not in keys}
        conn.execute(stmt.on_conflict_do_update(index_elements=keys, set_=update) if update
                     else stmt.on_conflict_do_nothing(index_elements=keys))
