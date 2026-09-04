"""
Webhook client.

Sends HTTP POST JSON notifications to a configured URL.
Supports retry with exponential backoff.
Persists attempt count in the database for idempotency.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from yt_live_archiver.config import AppConfig
from yt_live_archiver.database import Database
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.state_machine import state_machine
from yt_live_archiver.utils import (
    exponential_backoff_delays,
    format_bytes,
    format_duration,
)

logger = get_logger(__name__)

# HTTP status codes that should trigger a retry
_RETRYABLE_CODES = {429, 500, 502, 503, 504}


def build_webhook_payload(recording: Recording) -> dict:
    """Build the structured webhook JSON payload from a recording.

    Never includes credentials or tokens.
    Compatible with Discord, Slack, and generic webhook endpoints.
    """
    filename = Path(recording.local_path).name if recording.local_path else ""
    channel = recording.channel_name or recording.channel_id or "YouTube"
    title = recording.title or "Livestream"
    yt_url = (
        recording.youtube_url
        or f"https://www.youtube.com/watch?v={recording.youtube_video_id}"
    )
    duration_str = format_duration(recording.duration_seconds or 0)
    size_str = format_bytes(
        recording.drive_size_bytes or recording.local_size_bytes or 0
    )

    # Format timestamps cleanly
    started_str = recording.started_at or recording.detected_at or "N/A"
    ended_str = recording.ended_at or "N/A"
    if started_str != "N/A":
        started_str = started_str.replace("T", " ").replace("Z", " UTC")
    if ended_str != "N/A":
        ended_str = ended_str.replace("T", " ").replace("Z", " UTC")

    # Discord Embed Fields (all values inside code backticks)
    fields = [
        {"name": "Channel", "value": f"`{channel}`", "inline": True},
        {"name": "Duration", "value": f"`{duration_str}`", "inline": True},
        {"name": "File Size", "value": f"`{size_str}`", "inline": True},
    ]

    if recording.width and recording.height:
        fps_info = f" @ {recording.fps:.0f}fps" if recording.fps else ""
        fields.append(
            {
                "name": "Resolution",
                "value": f"`{recording.width}x{recording.height}{fps_info}`",
                "inline": True,
            }
        )

    if recording.video_codec or recording.audio_codec:
        codec_info = (
            f"{recording.video_codec or 'video'} / {recording.audio_codec or 'audio'}"
        )
        fields.append({"name": "Codecs", "value": f"`{codec_info}`", "inline": True})

    fields.append({"name": "Started At", "value": f"`{started_str}`", "inline": True})
    if recording.ended_at:
        fields.append({"name": "Ended At", "value": f"`{ended_str}`", "inline": True})

    if recording.drive_file_id:
        drive_url = f"https://drive.google.com/file/d/{recording.drive_file_id}/view"
        fields.append(
            {
                "name": "Google Drive",
                "value": f"[`Open in Google Drive`]({drive_url})",
                "inline": False,
            }
        )

    # High-resolution thumbnail image preview
    thumbnail_url = f"https://i.ytimg.com/vi/{recording.youtube_video_id}/hqdefault.jpg"

    embed: dict = {
        "title": title,
        "url": yt_url,
        "color": 0xFF0000,  # YouTube Red
        "fields": fields,
        "image": {
            "url": thumbnail_url,
        },
        "footer": {
            "text": "yt-live-archiver",
        },
    }
    if recording.ended_at:
        embed["timestamp"] = recording.ended_at

    plain_text = f"🔴 YouTube Stream Archived: {title} ({yt_url})"

    payload = {
        # Omit 'content' so Discord displays only the clean embed card
        "embeds": [embed],
        "text": plain_text,  # Slack compatibility
        "event": "youtube_live_recorded",
        "youtube": {
            "video_id": recording.youtube_video_id,
            "channel": recording.channel_name,
            "channel_id": recording.channel_id,
            "title": recording.title,
            "url": recording.youtube_url,
            "started_at": recording.started_at,
            "ended_at": recording.ended_at,
            "duration_seconds": recording.duration_seconds,
        },
        "file": {
            "name": filename,
            "size_bytes": recording.local_size_bytes,
            "container": recording.container,
            "video_codec": recording.video_codec,
            "audio_codec": recording.audio_codec,
            "width": recording.width,
            "height": recording.height,
            "fps": recording.fps,
        },
        "verification": {
            "ffprobe_valid": recording.media_verified,
            "decode_test_passed": recording.media_verified,
            "drive_verified": recording.drive_verified,
        },
        "google_drive": {
            "file_id": recording.drive_file_id,
            "folder_id": recording.drive_folder_id,
            "size_bytes": recording.drive_size_bytes,
        },
    }
    return payload


class WebhookClient:
    """Sends webhook notifications with retry and persistent state."""

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._log = get_logger(__name__)

    def send(self, recording: Recording) -> bool:
        """Send the webhook notification for *recording*.

        Returns True on success, False if all attempts exhausted.
        Updates recording.webhook_sent and recording.webhook_attempts in the DB.
        """
        log = get_logger(
            __name__,
            video_id=recording.youtube_video_id,
            channel=recording.channel_id,
        )

        if not self.config.webhook.enabled:
            log.info("webhook_skipped_disabled")
            recording.webhook_sent = True
            # Transition to COMPLETED (state machine allows UPLOADED -> COMPLETED directly)
            if state_machine.can_transition(recording.status, RecordingStatus.COMPLETED):
                state_machine.transition(recording, RecordingStatus.COMPLETED)
            self.db.update_recording(recording)
            return True

        if not self.config.webhook.url:
            log.error("webhook_url_not_configured")
            return False

        if recording.webhook_sent:
            log.info("webhook_already_sent")
            return True

        # Transition to NOTIFYING
        try:
            state_machine.transition(recording, RecordingStatus.NOTIFYING)
            self.db.update_recording(recording)
        except Exception as exc:
            if recording.status != RecordingStatus.NOTIFYING:
                log.warning("Could not transition to NOTIFYING", error=str(exc))

        payload = build_webhook_payload(recording)
        delays = exponential_backoff_delays(
            initial=self.config.retry.initial_delay_seconds,
            multiplier=self.config.retry.multiplier,
            cap=self.config.retry.max_delay_seconds,
            jitter=self.config.retry.jitter,
        )

        for attempt in range(1, self.config.webhook.max_attempts + 1):
            recording.webhook_attempts = attempt
            self.db.update_recording(recording)

            log.info("webhook_attempting", attempt=attempt, url=self.config.webhook.url)

            try:
                response = httpx.post(
                    self.config.webhook.url,
                    json=payload,
                    timeout=self.config.webhook.timeout_seconds,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "yt-live-archiver/1.0.1",
                    },
                )

                if response.status_code in {200, 201, 202, 204}:
                    log.info("webhook_sent", status=response.status_code)
                    recording.webhook_sent = True
                    state_machine.transition(recording, RecordingStatus.COMPLETED)
                    self.db.update_recording(recording)
                    return True

                if response.status_code in _RETRYABLE_CODES:
                    log.warning(
                        "webhook_retryable_error",
                        status=response.status_code,
                        attempt=attempt,
                    )
                    self.db.set_error(
                        recording,
                        f"Webhook HTTP {response.status_code} on attempt {attempt}",
                    )
                else:
                    # 4xx (except 429) — permanent failure
                    log.error(
                        "webhook_permanent_failure",
                        status=response.status_code,
                        body=response.text[:200],
                    )
                    self.db.set_error(
                        recording,
                        f"Webhook permanent failure: HTTP {response.status_code}",
                    )
                    state_machine.transition(recording, RecordingStatus.NOTIFICATION_FAILED)
                    self.db.update_recording(recording)
                    return False

            except httpx.TimeoutException:
                log.warning("webhook_timeout", attempt=attempt)
                self.db.set_error(recording, f"Webhook timeout on attempt {attempt}")

            except httpx.RequestError as exc:
                log.warning("webhook_request_error", error=str(exc), attempt=attempt)
                self.db.set_error(recording, f"Webhook request error: {exc}")

            except Exception as exc:
                log.error("webhook_unexpected_error", error=str(exc), attempt=attempt)
                self.db.set_error(recording, f"Webhook unexpected error: {exc}")

            if attempt < self.config.webhook.max_attempts:
                delay = next(delays)
                log.info("webhook_waiting_before_retry", delay=f"{delay:.1f}s")
                time.sleep(delay)

        log.error("webhook_all_attempts_exhausted", max_attempts=self.config.webhook.max_attempts)
        state_machine.transition(recording, RecordingStatus.NOTIFICATION_FAILED)
        self.db.update_recording(recording)
        return False
