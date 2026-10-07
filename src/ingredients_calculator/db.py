"""SQLite access, scoped to the Flask application context."""

import os
import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

DEFAULT_DB = "ingredients.sqlite3"


def database_path() -> Path:
    """Path to the database, from config or the INGREDIENTS_DB environment variable."""
    return Path(current_app.config["DATABASE"])


def get_db() -> sqlite3.Connection:
    """Read-only connection for the current request, opened lazily and reused."""
    if "db" not in g:
        g.db = sqlite3.connect(
            f"file:{database_path()}?mode=ro",
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


def tables() -> list[str]:
    """Names of the tables and views in the attached database."""
    rows = query(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row["name"] for row in rows]


def init_app(app: Flask) -> None:
    app.config.setdefault("DATABASE", os.environ.get("INGREDIENTS_DB", DEFAULT_DB))
    app.teardown_appcontext(close_db)
