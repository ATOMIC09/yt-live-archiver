"""
Database migration system.

Runs schema migrations in order when the schema version in the DB
is older than the current SCHEMA_VERSION in database.py.

Each migration is a function that receives an sqlite3.Connection.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

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
    """Initial schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS recordings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_video_id    TEXT    NOT NULL UNIQUE,
            channel_id          TEXT    NOT NULL,
            channel_name        TEXT    NOT NULL DEFAULT '',
            channel_url         TEXT    NOT NULL DEFAULT '',
            youtube_url         TEXT    NOT NULL DEFAULT '',
            title               TEXT    NOT NULL DEFAULT '',

            status              TEXT    NOT NULL DEFAULT 'DISCOVERED',

            detected_at         TEXT,
            started_at          TEXT,
            ended_at            TEXT,

            local_path          TEXT,
            local_size_bytes    INTEGER NOT NULL DEFAULT 0,

            duration_seconds    REAL,
            container           TEXT,
            video_codec         TEXT,
            audio_codec         TEXT,
            width               INTEGER,
            height              INTEGER,
            fps                 REAL,
            video_bitrate       INTEGER,
            audio_bitrate       INTEGER,

            drive_file_id       TEXT,
            drive_folder_id     TEXT,
            drive_size_bytes    INTEGER NOT NULL DEFAULT 0,

            media_verified      INTEGER NOT NULL DEFAULT 0,
            drive_verified      INTEGER NOT NULL DEFAULT 0,
            webhook_sent        INTEGER NOT NULL DEFAULT 0,

            recording_attempts      INTEGER NOT NULL DEFAULT 0,
            verification_attempts   INTEGER NOT NULL DEFAULT 0,
            upload_attempts         INTEGER NOT NULL DEFAULT 0,
            webhook_attempts        INTEGER NOT NULL DEFAULT 0,

            last_error          TEXT,
            last_error_at       TEXT,

            created_at          TEXT,
            updated_at          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_recordings_status ON recordings(status);
        CREATE INDEX IF NOT EXISTS idx_recordings_channel_id ON recordings(channel_id);
    """)
    # Insert the initial version row for this migration so the runner can update it
    conn.execute("INSERT INTO schema_version (version) VALUES (0)")


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
        try:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            current_version = row["version"] if row else 0
        except sqlite3.OperationalError:
            # Table does not exist on a fresh database
            current_version = 0

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
