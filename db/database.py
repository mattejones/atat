"""
database.py — SQLite connection and helper utilities for ATAT.

Provides:
  - get_db()     FastAPI dependency — yields a connection per request
  - execute()    convenience wrapper for single queries
  - fetchone()   convenience wrapper
  - fetchall()   convenience wrapper
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from pipeline.config import DB_PATH


def _make_connection() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager — use in background tasks or scripts."""
    conn = _make_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    FastAPI dependency.

    Usage:
        @router.get("/things")
        def list_things(db: sqlite3.Connection = Depends(get_db)):
            ...
    """
    conn = _make_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


def rows_to_list(rows: list[sqlite3.Row]) -> list[dict]:
    return [row_to_dict(r) for r in rows]
