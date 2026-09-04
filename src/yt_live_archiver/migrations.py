"""
Database migration system.

Runs schema migrations in order when the schema version in the DB
is older than the current SCHEMA_VERSION in database.py.

Each migration is a function that receives an sqlite3.Connection.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator

from yt_live_archiver.database import SCHEMA_VERSION

logger = logging.getLogger(__name__)

# Type alias for a migration function
Migration = Callable[[sqlite3.Connection], None]

# Registry of migrations: list index + 1 == target schema version
# Migration[0] brings version 0 → 1, etc.
_MIGRATIONS: list[Migration] = []


def register(fn: Migration) -> Migration:
    """Decorator to register a migration function."""
    _MIGRATIONS.append(fn)
    return fn


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


@register
def _migration_1(conn: sqlite3.Connection) -> None:
    """Initial schema — created by database._init_db(). Nothing additional needed."""
    pass  # Version 1 is established by the base schema in database.py


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_migrations(db_path: str) -> None:
    """Apply any pending migrations to the database at *db_path*.

    Reads current version from schema_version table, runs all migrations
    that bring it up to SCHEMA_VERSION, and updates the stored version.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current_version = row["version"] if row else 0

        if current_version >= SCHEMA_VERSION:
            logger.debug(
                "Database schema is up to date (version %d)", current_version
            )
            conn.close()
            return

        logger.info(
            "Migrating database from version %d to %d",
            current_version,
            SCHEMA_VERSION,
        )

        for target_version in range(current_version + 1, SCHEMA_VERSION + 1):
            migration_fn = _MIGRATIONS[target_version - 1]
            logger.info("Running migration %d: %s", target_version, migration_fn.__name__)
            try:
                migration_fn(conn)
                conn.execute(
                    "UPDATE schema_version SET version = ?", (target_version,)
                )
                conn.commit()
                logger.info("Migration %d completed", target_version)
            except Exception as exc:
                conn.rollback()
                logger.error("Migration %d failed: %s", target_version, exc)
                raise

    finally:
        conn.close()
