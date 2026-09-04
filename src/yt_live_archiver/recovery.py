"""
Crash recovery and startup reconciliation.

On startup, inspects all non-terminal database records and compares:
- Database state
- Filesystem state
- Google Drive state (when applicable)
- Webhook state

Then continues processing from the safest valid point.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from yt_live_archiver.config import AppConfig
from yt_live_archiver.database import Database
from yt_live_archiver.drive import DriveClient, DriveError
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.state_machine import state_machine
from yt_live_archiver.utils import file_size_bytes

logger = get_logger(__name__)

# States that need reconciliation on startup
_RECOVERABLE_STATES = {
    RecordingStatus.RECORDING,
    RecordingStatus.FINALIZING,
    RecordingStatus.VERIFYING,
    RecordingStatus.VERIFIED,
    RecordingStatus.UPLOADING,
    RecordingStatus.UPLOADED,
    RecordingStatus.NOTIFYING,
    RecordingStatus.UPLOAD_FAILED,
    RecordingStatus.NOTIFICATION_FAILED,
}


class RecoveryResult:
    """Records what the reconciler decided to do with a recording."""

    def __init__(self, recording: Recording, action: str, notes: str = "") -> None:
        self.recording = recording
        self.action = action
        self.notes = notes


class RecoveryManager:
    """Performs startup reconciliation of in-flight recordings."""

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._log = get_logger(__name__)

    def reconcile_all(self) -> list[RecoveryResult]:
        """Scan all non-terminal recordings and determine recovery actions.

        Returns a list of RecoveryResult describing what was decided.
        Does NOT start recording again; that is the responsibility of the
        processor/app. The results indicate what state each recording
        should be re-entered into.
        """
        self._log.info("recovery_starting")
        candidates = self.db.get_all_with_status(*_RECOVERABLE_STATES)

        if not candidates:
            self._log.info("recovery_no_candidates")
            return []

        self._log.info("recovery_candidates_found", count=len(candidates))
        results: list[RecoveryResult] = []

        drive_client = DriveClient(self.config.google_drive) if self.config.google_drive.enabled else None

        for recording in candidates:
            result = self._reconcile_one(recording, drive_client)
            results.append(result)

        self._log.info("recovery_complete", processed=len(results))
        return results

    def _reconcile_one(self, recording: Recording, drive_client: Optional[DriveClient]) -> RecoveryResult:
        """Determine the correct next state for *recording* and update the DB."""
        log = get_logger(
            __name__,
            video_id=recording.youtube_video_id,
            channel=recording.channel_id,
            current_status=recording.status.value,
        )

        log.info("recovery_examining")

        # Check filesystem state
        local_exists = False
        local_size = 0
        if recording.local_path:
            p = Path(recording.local_path)
            local_exists = p.exists() and p.is_file()
            if local_exists:
                local_size = file_size_bytes(p)
        elif recording.status in {RecordingStatus.RECORDING, RecordingStatus.FINALIZING}:
            # During recording, local_path is None, so search the working directory
            from yt_live_archiver.recorder import Recorder
            recorder = Recorder(self.config)
            working_dir = recorder._working_path(recording)
            if working_dir.exists():
                p = recorder._find_output_file(working_dir)
                if p and p.exists() and p.is_file():
                    local_exists = True
                    local_size = file_size_bytes(p)
                    # Update local_path so verification knows where to look!
                    recording.local_path = str(p)

        # Check Drive state
        drive_exists = False
        drive_size = 0
        if (
            drive_client is not None
            and recording.drive_file_id
            and recording.drive_file_id != "DISABLED"
        ):
            try:
                info = drive_client.get_file(recording.drive_file_id)
                if info is not None:
                    drive_exists = True
                    drive_size = info.size
            except DriveError as exc:
                log.warning("recovery_drive_check_error", error=str(exc))

        log.info(
            "recovery_state_summary",
            local_exists=local_exists,
            local_size=local_size,
            drive_exists=drive_exists,
            drive_size=drive_size,
            media_verified=recording.media_verified,
            drive_verified=recording.drive_verified,
            webhook_sent=recording.webhook_sent,
        )

        # --- Decision tree ---

        if recording.status in {RecordingStatus.RECORDING, RecordingStatus.FINALIZING}:
            # Was mid-recording when we crashed
            if local_exists and local_size > 0:
                # File exists — treat as finalized, re-verify
                log.info("recovery_action: file_exists_after_interrupted_recording -> VERIFYING")
                recording.status = RecordingStatus.VERIFYING
                recording.local_size_bytes = local_size
                self.db.update_recording(recording)
                return RecoveryResult(recording, "re_verify", "File found after interrupted recording")
            else:
                # No file — mark as failed
                log.warning("recovery_action: no_file_after_recording -> RECORDING_FAILED")
                recording.status = RecordingStatus.RECORDING_FAILED
                self.db.set_error(recording, "Recording interrupted: no output file found on restart")
                self.db.update_recording(recording)
                return RecoveryResult(recording, "mark_failed", "No file found after interrupted recording")

        if recording.status == RecordingStatus.VERIFYING:
            if local_exists and local_size > 0:
                log.info("recovery_action: retry_verification")
                return RecoveryResult(recording, "re_verify", "Restart during verification")
            else:
                log.warning("recovery_action: no_file_during_verification -> VERIFICATION_FAILED")
                recording.status = RecordingStatus.VERIFICATION_FAILED
                self.db.set_error(recording, "Verification interrupted: local file missing on restart")
                self.db.update_recording(recording)
                return RecoveryResult(recording, "mark_failed", "File missing during verification")

        if recording.status in {RecordingStatus.VERIFIED, RecordingStatus.UPLOAD_FAILED}:
            # Ready to upload (or retry upload)
            if local_exists and local_size > 0:
                log.info("recovery_action: retry_upload")
                recording.status = RecordingStatus.VERIFIED
                self.db.update_recording(recording)
                return RecoveryResult(recording, "upload", "Retry upload after restart")
            else:
                log.warning("recovery_action: local_file_missing_before_upload -> UPLOAD_FAILED")
                recording.status = RecordingStatus.UPLOAD_FAILED
                self.db.set_error(recording, "Local file missing before upload on restart")
                self.db.update_recording(recording)
                return RecoveryResult(recording, "mark_failed", "No local file to upload")

        if recording.status == RecordingStatus.UPLOADING:
            # Was uploading when we crashed — check if upload completed
            if drive_exists and drive_size == local_size and local_size > 0:
                log.info("recovery_action: upload_completed_before_crash -> UPLOADED")
                recording.drive_verified = True
                recording.drive_size_bytes = drive_size
                recording.status = RecordingStatus.UPLOADED
                self.db.update_recording(recording)
                return RecoveryResult(recording, "webhook", "Upload was complete, send webhook")
            elif local_exists and local_size > 0:
                log.info("recovery_action: retry_upload")
                recording.status = RecordingStatus.VERIFIED
                self.db.update_recording(recording)
                return RecoveryResult(recording, "upload", "Retry interrupted upload")
            else:
                log.warning("recovery_action: no_local_file_and_no_drive_file -> UPLOAD_FAILED")
                recording.status = RecordingStatus.UPLOAD_FAILED
                self.db.set_error(recording, "Both local and Drive files missing on restart")
                self.db.update_recording(recording)
                return RecoveryResult(recording, "mark_failed", "Nothing to upload or recover")

        if recording.status in {RecordingStatus.UPLOADED, RecordingStatus.NOTIFYING,
                                  RecordingStatus.NOTIFICATION_FAILED}:
            if recording.webhook_sent:
                log.info("recovery_action: webhook_already_sent -> cleanup")
                return RecoveryResult(recording, "cleanup", "Webhook already sent, proceed to cleanup")
            else:
                log.info("recovery_action: retry_webhook")
                recording.status = RecordingStatus.UPLOADED
                self.db.update_recording(recording)
                return RecoveryResult(recording, "webhook", "Retry webhook after restart")

        # Fallback — don't know what to do, leave it alone
        log.warning("recovery_action: unhandled_state -> no_action")
        return RecoveryResult(recording, "no_action", f"Unhandled state: {recording.status.value}")
