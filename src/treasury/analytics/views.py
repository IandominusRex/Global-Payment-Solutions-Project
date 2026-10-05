"""Apply every `DROP VIEW` / `CREATE VIEW` in sql/analyses/*.sql to the clean database.

The analysis files mix exploration queries and views. Only the view statements are run here, so the
dashboards always read views that match the SQL files. The clean database is rebuilt on every build
(which drops its views), so run this again after a backfill:

    TREASURY_CONFIG=config/simulation.small.yaml .venv/bin/python -m treasury.analytics.views
"""

import os
import re
import sqlite3
from pathlib import Path

from treasury.simulator.config import load_config

ANALYSES_DIR = Path("sql/analyses")
_VIEW_STMT = re.compile(r"^\s*(DROP VIEW|CREATE VIEW)\b", re.IGNORECASE)


def view_statements(sql: str) -> list[str]:
    """The DROP VIEW / CREATE VIEW statements of one SQL file (comments removed)."""
    no_comments = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in no_comments.split(";") if _VIEW_STMT.match(s)]


def apply_views(con: sqlite3.Connection, analyses_dir: Path = ANALYSES_DIR) -> list[str]:
    """Run all view statements; returns the names of the views created.

    Views named vw_* that are no longer defined in the SQL files are dropped, so the database
    (and the CSV export) never keeps a view that was removed from the analyses."""
    created = []
    for path in sorted(analyses_dir.glob("*.sql")):
        for stmt in view_statements(path.read_text()):
            con.execute(stmt)
            if stmt.upper().startswith("CREATE"):
                created.append(stmt.split()[2])
    stale = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'view' AND name LIKE 'vw%'")}
    for name in stale - set(created):
        con.execute(f"DROP VIEW {name}")
    con.commit()
    return created


def clean_db_path() -> Path:
    cfg = load_config(os.environ.get("TREASURY_CONFIG", "config/simulation.yaml"))
    return Path(cfg.output.clean_db_url.removeprefix("sqlite:///"))


def main() -> None:
    db = clean_db_path()
    with sqlite3.connect(db) as con:
        created = apply_views(con)
    print(f"{len(created)} views created in {db}:")
    for name in created:
        print(f"  {name}")


if __name__ == "__main__":
    main()
