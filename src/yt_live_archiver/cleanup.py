"""
Local file cleanup.

Performs final safety checks before deleting local recordings.
All conditions must be met; when uncertain, keeps the file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from yt_live_archiver.config import AppConfig
from yt_live_archiver.database import Database
from yt_live_archiver.drive import DriveClient, DriveError
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.state_machine import state_machine
from yt_live_archiver.utils import file_size_bytes, safe_remove

logger = get_logger(__name__)


class Cleanup:
    """Performs safe deletion of local recording files after all checks pass."""

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._log = get_logger(__name__)

    def _get_drive_client(self) -> DriveClient:
        return DriveClient(self.config.google_drive)

    def delete_local_file(self, recording: Recording) -> bool:
        """Delete the local recording file if all safety conditions are met.

        Returns True if deletion succeeded, False if conditions not met
        or deletion failed. Always errs on the side of keeping the file.
        """
        log = get_logger(
            __name__,
            video_id=recording.youtube_video_id,
            channel=recording.channel_id,
        )

        # --- 1. Confirm database state allows deletion ---
        require_webhook = self.config.cleanup.require_webhook
        if not recording.can_delete_local(require_webhook=require_webhook):
            log.warning(
                "cleanup_conditions_not_met",
                media_verified=recording.media_verified,
                drive_verified=recording.drive_verified,
                webhook_sent=recording.webhook_sent,
                require_webhook=require_webhook,
            )
            return False

        # --- 2. Confirm local file still exists ---
        if not recording.local_path:
            log.warning("cleanup_no_local_path")
            return False

        local_path = Path(recording.local_path)
        if not local_path.exists():
            log.info("cleanup_local_file_already_gone", path=str(local_path))
            # File is already gone — mark completed anyway
            if not recording.status.is_terminal:
                # Move through NOTIFYING if needed, or directly to COMPLETED
                if state_machine.can_transition(recording.status, RecordingStatus.COMPLETED):
                    state_machine.transition(recording, RecordingStatus.COMPLETED)
            self.db.update_recording(recording)
            return True

        # --- 3. Reconfirm Drive file exists ---
        if self.config.google_drive.enabled and recording.drive_file_id and \
                recording.drive_file_id != "DISABLED":
            log.info("cleanup_reconfirming_drive_file", file_id=recording.drive_file_id)
            try:
                drive = self._get_drive_client()
                info = drive.get_file(recording.drive_file_id)
                if info is None:
                    log.error(
                        "cleanup_aborted_drive_file_missing",
                        file_id=recording.drive_file_id,
                    )
                    self.db.set_error(
                        recording,
                        "Cleanup aborted: Drive file not found before deletion",
                    )
                    return False

                # Reconfirm size
                local_size = file_size_bytes(local_path)
                if info.size != local_size:
                    log.error(
                        "cleanup_aborted_drive_size_mismatch",
                        file_id=recording.drive_file_id,
                        local_size=local_size,
                        remote_size=info.size,
                    )
                    self.db.set_error(
                        recording,
                        f"Cleanup aborted: Drive size {info.size} != local size {local_size}",
                    )
                    return False

                log.info("cleanup_drive_reconfirmed", file_id=recording.drive_file_id)

            except DriveError as exc:
                log.error("cleanup_drive_reconfirmation_error", error=str(exc))
                self.db.set_error(recording, f"Cleanup drive check error: {exc}")
                return False

        # --- 4. Reconfirm notification state ---
        if require_webhook and not recording.webhook_sent:
            log.error("cleanup_aborted_webhook_not_sent")
            return False

        # --- 5. Delete the local file ---
        log.info("cleanup_deleting_local_file", path=str(local_path))
        deleted = safe_remove(local_path)

        if not deleted:
            log.error("cleanup_deletion_failed", path=str(local_path))
            self.db.set_error(recording, "Local file deletion failed")
            return False

        # --- 6. Confirm deletion ---
        if local_path.exists():
            log.error("cleanup_file_still_exists_after_delete", path=str(local_path))
            self.db.set_error(recording, "File still exists after delete attempt")
            return False

        log.info("local_file_deleted", path=str(local_path))

        # --- 7. Mark COMPLETED ---
        if state_machine.can_transition(recording.status, RecordingStatus.COMPLETED):
            state_machine.transition(recording, RecordingStatus.COMPLETED)
        self.db.update_recording(recording)
        return True
