"""
Webhook client — no DB state, plain function interface.

Sends an HTTP POST JSON notification to a configured URL.
Supports Discord embeds, Slack, and generic endpoints.
Retries with exponential backoff.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from yt_live_archiver.config import AppConfig
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.media import MediaMetadata
from yt_live_archiver.models import RecordingInfo, RecordingResult
from yt_live_archiver.utils import exponential_backoff_delays, format_bytes, format_duration

logger = get_logger(__name__)

_RETRYABLE_CODES = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------


def build_webhook_payload(
    info: RecordingInfo,
    result: RecordingResult,
    meta: MediaMetadata | None,
    drive_file_id: str | None,
    local_path: Path | None,
) -> dict:
    """Build the structured webhook JSON payload.

    Compatible with Discord embeds, Slack, and generic HTTP endpoints.
    """
    title = info.title or "Livestream"
    channel = info.channel_name or info.channel_id or "YouTube"
    yt_url = info.youtube_url or f"https://www.youtube.com/watch?v={info.video_id}"

    duration_secs = meta.duration_seconds if meta else 0
    duration_str = format_duration(duration_secs)

    file_size = local_path.stat().st_size if local_path and local_path.exists() else 0
    size_str = format_bytes(file_size)
    filename = local_path.name if local_path else ""

    # Timestamps
    started_str = (result.started_at or info.detected_at or "N/A").replace("T", " ").replace("Z", " UTC")
    ended_str = (result.ended_at or "N/A").replace("T", " ").replace("Z", " UTC")

    # Discord embed fields
    fields = [
        {"name": "Channel", "value": f"`{channel}`", "inline": True},
        {"name": "Duration", "value": f"`{duration_str}`", "inline": True},
        {"name": "File Size", "value": f"`{size_str}`", "inline": True},
    ]

    if meta and meta.video and meta.video.width and meta.video.height:
        fps_part = f" @ {meta.video.fps:.0f}fps" if meta.video.fps else ""
        fields.append({
            "name": "Resolution",
            "value": f"`{meta.video.width}x{meta.video.height}{fps_part}`",
            "inline": True,
        })

    if meta and (meta.video or meta.audio):
        codecs = f"{meta.video.codec if meta.video else 'video'} / {meta.audio.codec if meta.audio else 'audio'}"
        fields.append({"name": "Codecs", "value": f"`{codecs}`", "inline": True})

    fields.append({"name": "Started At", "value": f"`{started_str}`", "inline": True})
    fields.append({"name": "Ended At", "value": f"`{ended_str}`", "inline": True})

    if drive_file_id and drive_file_id != "DISABLED":
        drive_url = f"https://drive.google.com/file/d/{drive_file_id}/view"
        fields.append({
            "name": "Google Drive",
            "value": f"[`Open in Google Drive`]({drive_url})",
            "inline": False,
        })

    thumbnail_url = f"https://i.ytimg.com/vi/{info.video_id}/hqdefault.jpg"

    embed: dict = {
        "title": title,
        "url": yt_url,
        "color": 0xFF0000,  # YouTube Red
        "fields": fields,
        "image": {"url": thumbnail_url},
        "footer": {"text": "yt-live-archiver"},
    }
    if result.ended_at:
        embed["timestamp"] = result.ended_at

    return {
        "embeds": [embed],
        "text": f"🔴 YouTube Stream Archived: {title} ({yt_url})",  # Slack fallback
        "event": "youtube_live_recorded",
        "youtube": {
            "video_id": info.video_id,
            "channel": info.channel_name,
            "channel_id": info.channel_id,
            "title": info.title,
            "url": info.youtube_url,
            "started_at": result.started_at,
            "ended_at": result.ended_at,
            "duration_seconds": duration_secs,
        },
        "file": {
            "name": filename,
            "size_bytes": file_size,
            "container": meta.container if meta else None,
            "video_codec": meta.video.codec if (meta and meta.video) else None,
            "audio_codec": meta.audio.codec if (meta and meta.audio) else None,
            "width": meta.video.width if (meta and meta.video) else None,
            "height": meta.video.height if (meta and meta.video) else None,
            "fps": meta.video.fps if (meta and meta.video) else None,
        },
        "google_drive": {
            "file_id": drive_file_id,
        },
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class WebhookClient:
    """Sends webhook notifications with retry and exponential backoff."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._log = get_logger(__name__)

    def send(
        self,
        info: RecordingInfo,
        result: RecordingResult,
        meta: MediaMetadata | None,
        drive_file_id: str | None,
        local_path: Path | None,
    ) -> bool:
        """Send a webhook notification. Returns True on success."""
        log = get_logger(__name__, video_id=info.video_id, channel=info.channel_id)

        if not self.config.webhook.enabled:
            log.info("webhook_disabled")
            return True

        if not self.config.webhook.url:
            log.error("webhook_url_not_configured")
            return False

        payload = build_webhook_payload(info, result, meta, drive_file_id, local_path)
        cfg = self.config.webhook
        delays = exponential_backoff_delays(initial=5.0, multiplier=2.0, cap=300.0, jitter=True)

        for attempt in range(1, cfg.max_attempts + 1):
            log.info("webhook_attempting", attempt=attempt, url=cfg.url)

            try:
                response = httpx.post(
                    cfg.url,
                    json=payload,
                    timeout=cfg.timeout_seconds,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "yt-live-archiver/2.0.0",
                    },
                )

                if response.status_code in {200, 201, 202, 204}:
                    log.info("webhook_sent", status=response.status_code)
                    return True

                if response.status_code in _RETRYABLE_CODES:
                    log.warning("webhook_retryable_error", status=response.status_code, attempt=attempt)
                else:
                    log.error(
                        "webhook_permanent_failure",
                        status=response.status_code,
                        body=response.text[:200],
                    )
                    return False

            except httpx.TimeoutException:
                log.warning("webhook_timeout", attempt=attempt)
            except httpx.RequestError as exc:
                log.warning("webhook_request_error", error=str(exc), attempt=attempt)
            except Exception as exc:
                log.error("webhook_unexpected_error", error=str(exc), attempt=attempt)

            if attempt < cfg.max_attempts:
                delay = next(delays)
                log.info("webhook_retry_delay", delay=f"{delay:.1f}s")
                time.sleep(delay)

        log.error("webhook_all_attempts_exhausted", max_attempts=cfg.max_attempts)
        return False
