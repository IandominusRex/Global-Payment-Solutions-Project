"""Warehouse sink: creates the star schema and bulk-loads DataFrames.

SQLAlchemy keeps SQLite (default) and PostgreSQL interchangeable via the URL.
SQLite runs in WAL mode so the API and BI tools can read while the stream writes.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, event, text

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
    """Idempotent load for dimensions / backfill: delete then append (keeps DDL types)."""
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {table}"))
        df.to_sql(table, conn, if_exists="append", index=False, chunksize=10_000)
