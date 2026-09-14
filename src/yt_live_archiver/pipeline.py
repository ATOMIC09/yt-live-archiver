"""
Post-recording pipeline — flat sequential function, no state machine.

Steps (in order):
  1. Move failed recordings to the failed directory (if recording itself failed)
  2. Rename the output file to the final archive filename
  3. ffprobe verification (streams, duration)
  4. ffmpeg decode integrity test
  5. Upload to Google Drive (if enabled)
  6. Send webhook notification (if enabled)
  7. Delete local file (only if Drive upload succeeded)

On any error the local file is preserved and the function returns early.
No database state is written at any step.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from yt_live_archiver.config import AppConfig
from yt_live_archiver.drive import DriveAuthError, DriveClient, DriveError, DriveUploadError
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.media import MediaMetadata, MediaVerifier, VerificationConfig
from yt_live_archiver.models import RecordingInfo, RecordingResult
from yt_live_archiver.utils import build_archive_filename, ensure_dir, format_bytes
from yt_live_archiver.webhook import WebhookClient

logger = get_logger(__name__)


async def run_pipeline(
    info: RecordingInfo,
    result: RecordingResult,
    config: AppConfig,
) -> bool:
    """Run the full post-recording pipeline for one recording.

    Returns True if the recording was verified and processed successfully,
    False if recording or verification failed.
    """
    log = get_logger(__name__, video_id=info.video_id, channel=info.channel_id)
    loop = asyncio.get_event_loop()

    # ── Step 0: Guard — recording itself failed ────────────────────────────
    if not result.success or result.output_path is None:
        log.error(
            "recording_failed",
            error=result.error_message,
            exit_code=result.exit_code,
        )
        await loop.run_in_executor(None, lambda: _move_to_failed(result.output_path, info, config, log))
        return False

    # ── Step 1: Rename to final archive filename ───────────────────────────
    log.info("pipeline_starting", source=str(result.output_path))
    final_path = await loop.run_in_executor(
        None, lambda: _finalize_file(result.output_path, info, config, log)
    )
    if final_path is None:
        log.error("finalization_failed")
        return False

    # ── Step 2 & 3: Media verification ────────────────────────────────────
    log.info("verification_starting", path=str(final_path))
    verif_cfg = VerificationConfig(
        require_video=config.require_video,
        require_audio=config.require_audio,
        run_decode_test=config.decode_test,
        minimum_duration_seconds=config.min_duration,
    )
    verifier = MediaVerifier(verif_cfg)
    verification = await loop.run_in_executor(
        None, lambda: verifier.verify(final_path, info.video_id)
    )

    if not verification.passed:
        errors = "; ".join(verification.errors)
        log.error("verification_failed", errors=errors)
        await loop.run_in_executor(None, lambda: _move_to_failed(final_path, info, config, log))
        return False

    meta: MediaMetadata | None = verification.metadata
    log.info(
        "verification_passed",
        duration=meta.duration_seconds if meta else 0,
        video=meta.video.codec if (meta and meta.video) else "none",
        audio=meta.audio.codec if (meta and meta.audio) else "none",
    )

    # ── Step 4: Google Drive upload ────────────────────────────────────────
    drive_file_id: str | None = None

    if config.google_drive.enabled:
        drive_file_id = await loop.run_in_executor(
            None, lambda: _upload_to_drive(final_path, info, config, log)
        )
        if drive_file_id is None:
            log.error("upload_failed_local_file_preserved", path=str(final_path))
            return False  # Keep the local file intact

    # ── Step 5: Webhook notification ───────────────────────────────────────
    if config.webhook.enabled:
        webhook_client = WebhookClient(config)
        webhook_ok = await loop.run_in_executor(
            None,
            lambda: webhook_client.send(info, result, meta, drive_file_id, final_path),
        )
        if not webhook_ok:
            log.error("webhook_failed_local_file_preserved", path=str(final_path))
            return False  # Keep the local file intact

    # ── Step 6: Delete local file ──────────────────────────────────────────
    if config.google_drive.enabled and drive_file_id:
        try:
            final_path.unlink()
            log.info("local_file_deleted")
        except OSError as exc:
            log.warning("local_file_delete_failed", error=str(exc))
    else:
        log.info("local_file_kept", path=str(final_path))

    log.info("pipeline_completed", video_id=info.video_id)
    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _finalize_file(
    source_path: Path,
    info: RecordingInfo,
    config: AppConfig,
    log,  # noqa: ANN001
) -> Path | None:
    """Move the raw recording to OUTPUT_DIR/{channel_id}/{filename}."""
    date_str = (info.detected_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))[:10]

    filename = build_archive_filename(
        channel_id=info.channel_id,
        date_str=date_str,
        video_id=info.video_id,
        title=info.title,
        ext=config.recording_container,
    )

    dest_dir = Path(config.output_dir) / info.channel_id
    ensure_dir(dest_dir)
    dest_path = dest_dir / filename

    try:
        if source_path == dest_path:
            return dest_path
        shutil.move(str(source_path), str(dest_path))
        # Clean up the now-empty working directory for this video
        try:
            source_path.parent.rmdir()
        except OSError:
            pass  # Not empty or already gone — that's fine
        log.info("file_finalized", path=str(dest_path))
        return dest_path
    except Exception as exc:
        log.error("finalize_move_failed", error=str(exc))
        return None


def _move_to_failed(
    source_path: Path | None,
    info: RecordingInfo,
    config: AppConfig,
    log,  # noqa: ANN001
) -> None:
    """Move a failed recording to FAILED_DIR for manual inspection."""
    if source_path is None or not source_path.exists():
        return
    failed_dir = Path(config.failed_dir) / info.channel_id / info.video_id
    ensure_dir(failed_dir)
    dest = failed_dir / source_path.name
    try:
        shutil.move(str(source_path), str(dest))
        log.info("failed_recording_moved", path=str(dest))
    except Exception as exc:
        log.warning("failed_recording_move_error", error=str(exc))


def _upload_to_drive(
    local_path: Path,
    info: RecordingInfo,
    config: AppConfig,
    log,  # noqa: ANN001
) -> str | None:
    """Upload to Google Drive. Returns the Drive file ID on success, None on failure."""
    local_size = local_path.stat().st_size
    log.info("drive_upload_starting", size=format_bytes(local_size))

    remote_name = local_path.name  # Already the final archive filename
    subfolder = info.channel_name or info.channel_id

    try:
        client = DriveClient(config.google_drive)
        remote_info = client.upload_file(
            local_path=local_path,
            remote_name=remote_name,
            video_id=info.video_id,
            subfolder_name=subfolder,
        )

        # Size verification
        if not client.verify_file(remote_info.file_id, local_size):
            log.error(
                "drive_verification_failed",
                file_id=remote_info.file_id,
                expected=local_size,
                actual=remote_info.size,
            )
            return None

        log.info("drive_upload_verified", file_id=remote_info.file_id)
        return remote_info.file_id

    except DriveAuthError as exc:
        log.error("drive_auth_error", error=str(exc))
        return None
    except (DriveUploadError, DriveError) as exc:
        log.error("drive_upload_error", error=str(exc))
        return None
    except Exception as exc:
        log.error("drive_upload_unexpected_error", error=str(exc))
        return None


def read_title_from_file(path: Path) -> str | None:
    """Read the embedded title tag from a media file via ffprobe.

    Used by the orphan scan to recover the stream title from a pre-existing file.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            return info.get("format", {}).get("tags", {}).get("title")
    except Exception:
        pass
    return None
