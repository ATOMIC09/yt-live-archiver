"""
Recording data model and RecordingStatus state enum.

All status values live here. Nothing else should define status strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordingStatus(StrEnum):
    """All valid states of a recording job.

    Normal flow:
        DISCOVERED → RECORDING → FINALIZING → VERIFYING → VERIFIED
        → UPLOADING → UPLOADED → NOTIFYING → COMPLETED

    Failure states (recoverable unless marked permanent):
        RECORDING_FAILED
        VERIFICATION_FAILED
        UPLOAD_FAILED
        NOTIFICATION_FAILED
    """

    DISCOVERED = "DISCOVERED"
    RECORDING = "RECORDING"
    FINALIZING = "FINALIZING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    NOTIFYING = "NOTIFYING"
    COMPLETED = "COMPLETED"

    RECORDING_FAILED = "RECORDING_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"

    @property
    def is_terminal(self) -> bool:
        """Return True for states that have no further normal processing."""
        return self in {RecordingStatus.COMPLETED}

    @property
    def is_failed(self) -> bool:
        return self in {
            RecordingStatus.RECORDING_FAILED,
            RecordingStatus.VERIFICATION_FAILED,
            RecordingStatus.UPLOAD_FAILED,
            RecordingStatus.NOTIFICATION_FAILED,
        }

    @property
    def is_recoverable(self) -> bool:
        """States that may be retried on next startup."""
        return self in {
            RecordingStatus.RECORDING,
            RecordingStatus.FINALIZING,
            RecordingStatus.VERIFYING,
            RecordingStatus.UPLOADING,
            RecordingStatus.NOTIFYING,
            RecordingStatus.UPLOAD_FAILED,
            RecordingStatus.NOTIFICATION_FAILED,
        }


@dataclass
class Recording:
    """Represents one recording job and all its associated state."""

    # Identity
    id: int | None = None
    youtube_video_id: str = ""
    channel_id: str = ""
    channel_name: str = ""
    channel_url: str = ""
    youtube_url: str = ""
    title: str = ""

    # State
    status: RecordingStatus = RecordingStatus.DISCOVERED

    # Timestamps (ISO-8601 strings, UTC)
    detected_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

    # Local file info
    local_path: str | None = None
    local_size_bytes: int = 0

    # Media metadata
    duration_seconds: float | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_bitrate: int | None = None
    audio_bitrate: int | None = None

    # Google Drive
    drive_file_id: str | None = None
    drive_folder_id: str | None = None
    drive_size_bytes: int = 0

    # Verification flags
    media_verified: bool = False
    drive_verified: bool = False
    webhook_sent: bool = False

    # Attempt counters
    recording_attempts: int = 0
    verification_attempts: int = 0
    upload_attempts: int = 0
    webhook_attempts: int = 0

    # Error tracking
    last_error: str | None = None
    last_error_at: str | None = None

    # Created / updated
    created_at: str | None = None
    updated_at: str | None = None

    def can_delete_local(self, require_webhook: bool = True) -> bool:
        """Return True only when all safety conditions are satisfied."""
        base = self.media_verified and self.drive_verified
        if require_webhook:
            return base and self.webhook_sent
        return base

    def __repr__(self) -> str:
        return (
            f"Recording(id={self.id}, video_id={self.youtube_video_id!r}, "
            f"status={self.status.value}, channel={self.channel_id!r})"
        )
