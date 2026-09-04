"""
Post-recording pipeline processor.

Orchestrates the finalization → verification → upload → webhook → cleanup
pipeline for completed recordings.

One recording flows through the pipeline sequentially.
Multiple recordings can be processed concurrently (limited by upload semaphore).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from yt_live_archiver.cleanup import Cleanup
from yt_live_archiver.config import AppConfig
from yt_live_archiver.database import Database
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.media import MediaVerifier
from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.recorder import RecordingResult, Recorder
from yt_live_archiver.state_machine import state_machine
from yt_live_archiver.uploader import Uploader
from yt_live_archiver.utils import build_archive_filename, ensure_dir, file_size_bytes
from yt_live_archiver.webhook import WebhookClient

logger = get_logger(__name__)


class Processor:
    """Orchestrates the full post-recording pipeline."""

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._verifier = MediaVerifier(config.verification)
        self._uploader = Uploader(config, db)
        self._webhook_client = WebhookClient(config, db)
        self._cleanup = Cleanup(config, db)
        self._log = get_logger(__name__)

    async def handle_recording_result(
        self,
        recording: Recording,
        result: RecordingResult,
    ) -> None:
        """Handle the result of a completed yt-dlp run.

        Performs finalization → verification → upload → webhook → cleanup.
        Each stage persists state to DB. Never loses the recording on error.
        """
        log = get_logger(
            __name__,
            video_id=recording.youtube_video_id,
            channel=recording.channel_id,
        )

        # Update timestamps from result
        recording.started_at = result.started_at
        recording.ended_at = result.ended_at
        self.db.update_recording(recording)

        if not result.success or result.output_path is None:
            log.error(
                "recording_failed",
                error=result.error_message,
                exit_code=result.exit_code,
            )
            state_machine.transition(recording, RecordingStatus.RECORDING_FAILED)
            self.db.set_error(
                recording,
                result.error_message or f"yt-dlp exit code {result.exit_code}",
            )
            self.db.update_recording(recording)
            # Move to failed directory for inspection
            await self._move_to_failed(recording, result.output_path)
            return

        # --- FINALIZING ---
        log.info("finalizing_started")
        state_machine.transition(recording, RecordingStatus.FINALIZING)
        self.db.update_recording(recording)

        # Move file to stable processing location
        final_path = await asyncio.get_event_loop().run_in_executor(
            None, self._finalize_file, recording, result.output_path
        )

        if final_path is None:
            log.error("finalization_failed")
            state_machine.transition(recording, RecordingStatus.RECORDING_FAILED)
            self.db.set_error(recording, "Finalization failed: could not move output file")
            self.db.update_recording(recording)
            return

        recording.local_path = str(final_path)
        recording.local_size_bytes = file_size_bytes(final_path)
        self.db.update_recording(recording)

        # --- VERIFYING ---
        log.info("verification_started", path=str(final_path))
        state_machine.transition(recording, RecordingStatus.VERIFYING)
        recording.verification_attempts += 1
        self.db.update_recording(recording)

        verification = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._verifier.verify(final_path, recording.youtube_video_id),
        )

        if not verification.passed:
            errors = "; ".join(verification.errors)
            log.error("verification_failed", errors=errors)
            state_machine.transition(recording, RecordingStatus.VERIFICATION_FAILED)
            self.db.set_error(recording, f"Verification failed: {errors}")
            self.db.update_recording(recording)
            return

        # Persist metadata from verification
        if verification.metadata:
            meta = verification.metadata
            recording.duration_seconds = meta.duration_seconds
            recording.container = meta.container
            if meta.video:
                recording.video_codec = meta.video.codec
                recording.width = meta.video.width
                recording.height = meta.video.height
                recording.fps = meta.video.fps
                recording.video_bitrate = meta.video.bitrate
            if meta.audio:
                recording.audio_codec = meta.audio.codec
                recording.audio_bitrate = meta.audio.bitrate

        recording.media_verified = True
        state_machine.transition(recording, RecordingStatus.VERIFIED)
        self.db.update_recording(recording)
        log.info("media_verification_passed", duration=recording.duration_seconds)

        # --- UPLOADING ---
        upload_ok = await self._uploader.upload_recording(recording)
        if not upload_ok:
            log.error("upload_failed", video_id=recording.youtube_video_id)
            return  # State already set by uploader

        log.info("drive_upload_completed")

        # --- NOTIFYING ---
        webhook_ok = await asyncio.get_event_loop().run_in_executor(
            None, self._webhook_client.send, recording
        )
        if not webhook_ok:
            log.error("webhook_failed")
            return  # State already set by webhook client

        log.info("webhook_sent")

        # --- CLEANUP ---
        cleanup_ok = await asyncio.get_event_loop().run_in_executor(
            None, self._cleanup.delete_local_file, recording
        )
        if cleanup_ok:
            log.info("local_file_deleted")
        else:
            log.warning("cleanup_skipped_or_failed")

    def _finalize_file(self, recording: Recording, source_path: Path) -> Optional[Path]:
        """Move the yt-dlp output to a stable final working path.

        The final path includes the video ID so it's unambiguous.
        Returns the final path, or None on failure.
        """
        log = get_logger(__name__, video_id=recording.youtube_video_id)

        started_date = ""
        if recording.started_at:
            started_date = recording.started_at[:10]
        elif recording.detected_at:
            started_date = recording.detected_at[:10]
        else:
            started_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        filename = build_archive_filename(
            channel_id=recording.channel_id,
            date_str=started_date,
            video_id=recording.youtube_video_id,
            title=recording.title,
            ext=self.config.recording.container,
        )

        dest_dir = (
            Path(self.config.recording.working_dir)
            / recording.channel_id
            / recording.youtube_video_id
        )
        ensure_dir(dest_dir)
        dest_path = dest_dir / filename

        try:
            if source_path == dest_path:
                return dest_path
            shutil.move(str(source_path), str(dest_path))
            log.info("file_finalized", path=str(dest_path))
            return dest_path
        except Exception as exc:
            log.error("finalize_move_failed", error=str(exc))
            return None

    async def _move_to_failed(self, recording: Recording, source_path: Optional[Path]) -> None:
        """Move a failed recording to the failed directory for inspection."""
        if source_path is None or not source_path.exists():
            return

        log = get_logger(__name__, video_id=recording.youtube_video_id)
        failed_dir = (
            Path(self.config.recording.failed_dir)
            / recording.channel_id
            / recording.youtube_video_id
        )

        try:
            await asyncio.get_event_loop().run_in_executor(None, lambda: ensure_dir(failed_dir))
            dest = failed_dir / source_path.name
            shutil.move(str(source_path), str(dest))
            log.info("failed_recording_moved", path=str(dest))
        except Exception as exc:
            log.warning("failed_recording_move_error", error=str(exc))

    async def reprocess_recording(self, recording: Recording) -> None:
        """Re-enter a recording into the pipeline from its current state.

        Used by the recovery manager to continue interrupted recordings.
        """
        log = get_logger(__name__, video_id=recording.youtube_video_id, status=recording.status.value)
        log.info("reprocessing_recording")

        if recording.status == RecordingStatus.VERIFYING:
            if recording.local_path and Path(recording.local_path).exists():
                # Re-run verification
                verification = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._verifier.verify(recording.local_path, recording.youtube_video_id),
                )
                if verification.passed:
                    if verification.metadata:
                        meta = verification.metadata
                        recording.duration_seconds = meta.duration_seconds
                        recording.container = meta.container
                        if meta.video:
                            recording.video_codec = meta.video.codec
                            recording.width = meta.video.width
                            recording.height = meta.video.height
                            recording.fps = meta.video.fps
                        if meta.audio:
                            recording.audio_codec = meta.audio.codec
                    recording.media_verified = True
                    state_machine.transition(recording, RecordingStatus.VERIFIED)
                    self.db.update_recording(recording)
                    await self._continue_from_verified(recording)
                else:
                    errors = "; ".join(verification.errors)
                    state_machine.transition(recording, RecordingStatus.VERIFICATION_FAILED)
                    self.db.set_error(recording, f"Verification failed on recovery: {errors}")
                    self.db.update_recording(recording)

        elif recording.status == RecordingStatus.VERIFIED:
            await self._continue_from_verified(recording)

        elif recording.status in {RecordingStatus.UPLOADING}:
            # Reset to VERIFIED for clean retry
            recording.status = RecordingStatus.VERIFIED
            self.db.update_recording(recording)
            await self._continue_from_verified(recording)

        elif recording.status == RecordingStatus.UPLOADED:
            await self._continue_from_uploaded(recording)

        elif recording.status in {RecordingStatus.NOTIFYING, RecordingStatus.NOTIFICATION_FAILED}:
            recording.status = RecordingStatus.UPLOADED
            self.db.update_recording(recording)
            await self._continue_from_uploaded(recording)

    async def _continue_from_verified(self, recording: Recording) -> None:
        upload_ok = await self._uploader.upload_recording(recording)
        if not upload_ok:
            return
        await self._continue_from_uploaded(recording)

    async def _continue_from_uploaded(self, recording: Recording) -> None:
        webhook_ok = await asyncio.get_event_loop().run_in_executor(
            None, self._webhook_client.send, recording
        )
        if not webhook_ok:
            return
        await asyncio.get_event_loop().run_in_executor(
            None, self._cleanup.delete_local_file, recording
        )
