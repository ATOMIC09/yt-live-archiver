"""
Upload orchestrator.

Manages the upload pipeline for verified recordings:
- Respects max_parallel_uploads semaphore
- Coordinates with DriveClient
- Updates recording state
- Handles upload recovery
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from yt_live_archiver.config import AppConfig
from yt_live_archiver.database import Database
from yt_live_archiver.drive import DriveAuthError, DriveClient, DriveError, DriveUploadError
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.state_machine import InvalidTransitionError, state_machine
from yt_live_archiver.utils import build_archive_filename, format_bytes

logger = get_logger(__name__)


class Uploader:
    """Coordinates uploads from verified recordings to Google Drive."""

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._semaphore = asyncio.Semaphore(config.processing.max_parallel_uploads)
        self._drive_client: Optional[DriveClient] = None
        self._log = get_logger(__name__)

    def _get_drive_client(self) -> DriveClient:
        if self._drive_client is None:
            self._drive_client = DriveClient(self.config.google_drive)
        return self._drive_client

    async def upload_recording(self, recording: Recording) -> bool:
        """Upload a verified recording to Google Drive.

        Returns True on success, False on failure.
        State transitions are applied and persisted.
        """
        async with self._semaphore:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._upload_sync, recording
            )

    def _upload_sync(self, recording: Recording) -> bool:
        """Synchronous upload logic (runs in executor)."""
        log = get_logger(
            __name__,
            video_id=recording.youtube_video_id,
            channel=recording.channel_id,
        )

        if not self.config.google_drive.enabled:
            log.info("drive_upload_skipped_disabled")
            # Mark as if uploaded so pipeline can continue
            recording.drive_verified = True
            recording.drive_file_id = "DISABLED"
            state_machine.transition(recording, RecordingStatus.UPLOADED)
            self.db.update_recording(recording)
            return True

        local_path = Path(recording.local_path) if recording.local_path else None
        if local_path is None or not local_path.exists():
            log.error("upload_failed_no_local_file", path=str(local_path))
            self.db.set_error(recording, "Local file missing before upload")
            return False

        local_size = local_path.stat().st_size
        log.info("drive_upload_starting", size=format_bytes(local_size))

        # Transition to UPLOADING
        try:
            state_machine.transition(recording, RecordingStatus.UPLOADING)
        except InvalidTransitionError:
            if recording.status != RecordingStatus.UPLOADING:
                log.error("invalid_transition_to_uploading", status=recording.status.value)
                return False
        recording.upload_attempts += 1
        self.db.update_recording(recording)

        # Build remote filename
        started_date = ""
        if recording.started_at:
            started_date = recording.started_at[:10]  # YYYY-MM-DD
        elif recording.detected_at:
            started_date = recording.detected_at[:10]
        else:
            started_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        remote_name = build_archive_filename(
            channel_id=recording.channel_id,
            date_str=started_date,
            video_id=recording.youtube_video_id,
            title=recording.title,
            ext=self.config.recording.container,
        )

        # Check if we already have a Drive file ID (recovery case)
        if recording.drive_file_id and recording.drive_file_id != "DISABLED":
            log.info(
                "drive_upload_checking_existing",
                file_id=recording.drive_file_id,
            )
            try:
                drive = self._get_drive_client()
                info = drive.get_file(recording.drive_file_id)
                if info and info.size == local_size:
                    log.info("drive_upload_already_complete_from_previous_run")
                    recording.drive_size_bytes = info.size
                    recording.drive_folder_id = self.config.google_drive.folder_id
                    recording.drive_verified = True
                    state_machine.transition(recording, RecordingStatus.UPLOADED)
                    self.db.update_recording(recording)
                    return True
            except Exception as exc:
                log.warning("Could not verify existing Drive file", error=str(exc))

        # Perform the upload
        try:
            drive = self._get_drive_client()
            remote_info = drive.upload_file(
                local_path=local_path,
                remote_name=remote_name,
                video_id=recording.youtube_video_id,
            )
        except DriveAuthError as exc:
            log.error("drive_auth_error", error=str(exc))
            self.db.set_error(recording, f"Drive auth error: {exc}")
            state_machine.transition(recording, RecordingStatus.UPLOAD_FAILED)
            self.db.update_recording(recording)
            return False
        except (DriveUploadError, DriveError) as exc:
            log.error("drive_upload_error", error=str(exc))
            self.db.set_error(recording, f"Drive upload error: {exc}")
            state_machine.transition(recording, RecordingStatus.UPLOAD_FAILED)
            self.db.update_recording(recording)
            return False
        except Exception as exc:
            log.error("drive_upload_unexpected_error", error=str(exc))
            self.db.set_error(recording, f"Unexpected upload error: {exc}")
            state_machine.transition(recording, RecordingStatus.UPLOAD_FAILED)
            self.db.update_recording(recording)
            return False

        # Persist Drive file ID immediately (before verification)
        recording.drive_file_id = remote_info.file_id
        recording.drive_folder_id = self.config.google_drive.folder_id
        recording.drive_size_bytes = remote_info.size
        self.db.update_recording(recording)

        # Verify remote file
        log.info("drive_verifying_upload", file_id=remote_info.file_id)
        try:
            verified = drive.verify_file(remote_info.file_id, local_size)
        except Exception as exc:
            log.error("drive_verification_error", error=str(exc))
            self.db.set_error(recording, f"Drive verification error: {exc}")
            # Do NOT transition to UPLOAD_FAILED — file may exist, just can't verify
            return False

        if not verified:
            log.error(
                "drive_verification_failed",
                file_id=remote_info.file_id,
                local_size=local_size,
                remote_size=remote_info.size,
            )
            self.db.set_error(
                recording,
                f"Drive verification failed: size mismatch (local={local_size} remote={remote_info.size})",
            )
            state_machine.transition(recording, RecordingStatus.UPLOAD_FAILED)
            self.db.update_recording(recording)
            return False

        log.info("drive_verification_passed", file_id=remote_info.file_id)
        recording.drive_verified = True
        state_machine.transition(recording, RecordingStatus.UPLOADED)
        self.db.update_recording(recording)
        return True
