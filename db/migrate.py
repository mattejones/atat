"""
migrate.py — Lightweight migration runner for ATAT's SQLite database.

Applies pending SQL migration files from db/migrations/ in order.
Tracks applied migrations in the schema_migrations table.

Called automatically on FastAPI startup — safe to run repeatedly.

Usage:
    python -m db.migrate          # apply pending migrations
    python -m db.migrate --status # show migration status
"""

import sqlite3
import sys
from pathlib import Path

# Migrations live next to this file
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def get_db_path() -> Path:
    """Read DB path from config (imports pipeline config)."""
    from pipeline.config import DB_PATH
    return Path(DB_PATH)


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")    # better concurrency
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def get_applied_versions(conn: sqlite3.Connection) -> set[str]:
    """Return the set of already-applied migration versions."""
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {row["version"] for row in rows}
    except sqlite3.OperationalError:
        # schema_migrations doesn't exist yet — first run
        return set()


def run_migrations(db_path: Path | None = None) -> int:
    """
    Apply all pending migrations. Returns the number of migrations applied.
    """
    if db_path is None:
        db_path = get_db_path()

    conn = get_connection(db_path)
    applied = get_applied_versions(conn)

    # Find all migration files, sorted by name (001_, 002_, ...)
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print("No migration files found.")
        return 0

    count = 0
    for path in migration_files:
        version = path.stem   # e.g. "001_initial"
        if version in applied:
            continue

        print(f"  Applying migration: {version}")
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(sql)
            # Record the migration — executescript commits, so use a new statement
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (version,)
            )
            conn.commit()
            count += 1
        except Exception as e:
            conn.rollback()
            print(f"  ✗ Migration {version} failed: {e}", file=sys.stderr)
            raise

    conn.close()
    if count:
        print(f"  {count} migration(s) applied.")
    else:
        print("  Database is up to date.")
    return count


def status(db_path: Path | None = None) -> None:
    """Print migration status."""
    if db_path is None:
        db_path = get_db_path()

    conn = get_connection(db_path)
    applied = get_applied_versions(conn)
    conn.close()

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    print(f"\nDB: {db_path}\n")
    for path in migration_files:
        version = path.stem
        mark = "✓" if version in applied else "○"
        print(f"  {mark} {version}")
    print()


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        run_migrations()
