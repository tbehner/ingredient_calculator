"""SQLite access, scoped to the Flask application context."""

import os
import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

DEFAULT_DB = "bls.sqlite3"


def database_path() -> Path:
    """Path to the database, from config or the INGREDIENTS_DB environment variable."""
    return Path(current_app.config["DATABASE"])


def get_db() -> sqlite3.Connection:
    """Read-only connection for the current request, opened lazily and reused."""
    if "db" not in g:
        path = database_path()
        # `mode=ro` never creates the file, so a missing one is a misconfigured
        # path rather than a first run. SQLite would only say "unable to open
        # database file", without naming it.
        if not path.exists():
            raise FileNotFoundError(
                f"No database at {path}. Point INGREDIENTS_DB at your SQLite file."
            )
        g.db = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(exception: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql: str, params: tuple = (), *, one: bool = False):
    """Run a SELECT and return rows (or a single row with `one=True`)."""
    cursor = get_db().execute(sql, params)
    rows = cursor.fetchall()
    cursor.close()
    if one:
        return rows[0] if rows else None
    return rows


def init_app(app: Flask) -> None:
    app.config.setdefault("DATABASE", os.environ.get("INGREDIENTS_DB", DEFAULT_DB))
    app.teardown_appcontext(close_db)
