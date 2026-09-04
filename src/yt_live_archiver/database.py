"""
SQLite database layer.

All recording persistence, queries, and updates go through Database.
Uses WAL journal mode for better concurrent read performance.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from yt_live_archiver.models import Recording, RecordingStatus

logger = logging.getLogger(__name__)

# Current schema version (increment on each migration)
SCHEMA_VERSION = 1


class Database:
    """SQLite-backed storage for recording jobs.

    Thread-safety: Each call acquires its own connection via context manager.
    All write operations use explicit transactions.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def create_recording(self, recording: Recording) -> Recording:
        """Insert a new recording record and return it with the assigned id."""
        now = self._now()
        recording.created_at = now
        recording.updated_at = now
        if recording.detected_at is None:
            recording.detected_at = now

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO recordings (
                    youtube_video_id, channel_id, channel_name, channel_url,
                    youtube_url, title, status, detected_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recording.youtube_video_id,
                    recording.channel_id,
                    recording.channel_name,
                    recording.channel_url,
                    recording.youtube_url,
                    recording.title,
                    recording.status.value,
                    recording.detected_at,
                    recording.created_at,
                    recording.updated_at,
                ),
            )
            recording.id = cur.lastrowid
        return recording

    def get_by_video_id(self, youtube_video_id: str) -> Optional[Recording]:
        """Return a Recording by YouTube video ID, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM recordings WHERE youtube_video_id = ?", (youtube_video_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_recording(row)

    def get_by_id(self, recording_id: int) -> Optional[Recording]:
        """Return a Recording by primary key, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_recording(row)

    def get_all_with_status(self, *statuses: RecordingStatus) -> list[Recording]:
        """Return all recordings with any of the given statuses."""
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        values = [s.value for s in statuses]
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM recordings WHERE status IN ({placeholders})", values
            ).fetchall()
        return [self._row_to_recording(r) for r in rows]

    def get_all(self) -> list[Recording]:
        """Return all recordings, ordered by created_at descending."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM recordings ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_recording(r) for r in rows]

    def video_id_exists(self, youtube_video_id: str) -> bool:
        """Return True if a recording for this video ID already exists."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM recordings WHERE youtube_video_id = ?",
                (youtube_video_id,),
            ).fetchone()
        return row is not None

    def update_recording(self, recording: Recording) -> None:
        """Persist all fields of *recording* to the database."""
        recording.updated_at = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE recordings SET
                    channel_id          = ?,
                    channel_name        = ?,
                    channel_url         = ?,
                    youtube_url         = ?,
                    title               = ?,
                    status              = ?,
                    detected_at         = ?,
                    started_at          = ?,
                    ended_at            = ?,
                    local_path          = ?,
                    local_size_bytes    = ?,
                    duration_seconds    = ?,
                    container           = ?,
                    video_codec         = ?,
                    audio_codec         = ?,
                    width               = ?,
                    height              = ?,
                    fps                 = ?,
                    video_bitrate       = ?,
                    audio_bitrate       = ?,
                    drive_file_id       = ?,
                    drive_folder_id     = ?,
                    drive_size_bytes    = ?,
                    media_verified      = ?,
                    drive_verified      = ?,
                    webhook_sent        = ?,
                    recording_attempts      = ?,
                    verification_attempts   = ?,
                    upload_attempts         = ?,
                    webhook_attempts        = ?,
                    last_error          = ?,
                    last_error_at       = ?,
                    updated_at          = ?
                WHERE id = ?
                """,
                (
                    recording.channel_id,
                    recording.channel_name,
                    recording.channel_url,
                    recording.youtube_url,
                    recording.title,
                    recording.status.value,
                    recording.detected_at,
                    recording.started_at,
                    recording.ended_at,
                    recording.local_path,
                    recording.local_size_bytes,
                    recording.duration_seconds,
                    recording.container,
                    recording.video_codec,
                    recording.audio_codec,
                    recording.width,
                    recording.height,
                    recording.fps,
                    recording.video_bitrate,
                    recording.audio_bitrate,
                    recording.drive_file_id,
                    recording.drive_folder_id,
                    recording.drive_size_bytes,
                    int(recording.media_verified),
                    int(recording.drive_verified),
                    int(recording.webhook_sent),
                    recording.recording_attempts,
                    recording.verification_attempts,
                    recording.upload_attempts,
                    recording.webhook_attempts,
                    recording.last_error,
                    recording.last_error_at,
                    recording.updated_at,
                    recording.id,
                ),
            )

    def set_error(self, recording: Recording, error: str) -> None:
        """Record an error message and timestamp without changing status."""
        now = self._now()
        recording.last_error = error
        recording.last_error_at = now
        recording.updated_at = now
        with self._conn() as conn:
            conn.execute(
                "UPDATE recordings SET last_error=?, last_error_at=?, updated_at=? WHERE id=?",
                (error, now, now, recording.id),
            )

    # ------------------------------------------------------------------
    # Row mapper
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_recording(row: sqlite3.Row) -> Recording:
        r = Recording()
        r.id = row["id"]
        r.youtube_video_id = row["youtube_video_id"]
        r.channel_id = row["channel_id"]
        r.channel_name = row["channel_name"]
        r.channel_url = row["channel_url"]
        r.youtube_url = row["youtube_url"]
        r.title = row["title"]
        r.status = RecordingStatus(row["status"])
        r.detected_at = row["detected_at"]
        r.started_at = row["started_at"]
        r.ended_at = row["ended_at"]
        r.local_path = row["local_path"]
        r.local_size_bytes = row["local_size_bytes"] or 0
        r.duration_seconds = row["duration_seconds"]
        r.container = row["container"]
        r.video_codec = row["video_codec"]
        r.audio_codec = row["audio_codec"]
        r.width = row["width"]
        r.height = row["height"]
        r.fps = row["fps"]
        r.video_bitrate = row["video_bitrate"]
        r.audio_bitrate = row["audio_bitrate"]
        r.drive_file_id = row["drive_file_id"]
        r.drive_folder_id = row["drive_folder_id"]
        r.drive_size_bytes = row["drive_size_bytes"] or 0
        r.media_verified = bool(row["media_verified"])
        r.drive_verified = bool(row["drive_verified"])
        r.webhook_sent = bool(row["webhook_sent"])
        r.recording_attempts = row["recording_attempts"] or 0
        r.verification_attempts = row["verification_attempts"] or 0
        r.upload_attempts = row["upload_attempts"] or 0
        r.webhook_attempts = row["webhook_attempts"] or 0
        r.last_error = row["last_error"]
        r.last_error_at = row["last_error_at"]
        r.created_at = row["created_at"]
        r.updated_at = row["updated_at"]
        return r
