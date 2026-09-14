"""
YouTube channel monitor — no database, in-memory deduplication.

Polls configured channels with yt-dlp in metadata-only mode.
Calls the on_live_detected callback for new streams.
Tracks seen video IDs in a shared in-memory set to avoid re-recording.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime

from yt_live_archiver.config import AppConfig, ChannelConfig
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import RecordingInfo

logger = get_logger(__name__)


class LiveStreamInfo:
    """Information about a detected live stream."""

    def __init__(self, video_id: str, title: str, url: str) -> None:
        self.video_id = video_id
        self.title = title
        self.url = url


class ChannelMonitor:
    """Checks a single YouTube channel for an active live stream using yt-dlp."""

    def __init__(self, channel: ChannelConfig) -> None:
        self.channel = channel
        self._log = get_logger(__name__, channel=channel.id)

    def check_live(self) -> LiveStreamInfo | None:
        """Return LiveStreamInfo if the channel is live, else None."""
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--quiet",
            "--skip-download",
            "--dump-json",
            "--no-playlist",
            self.channel.url,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            self._log.warning("yt_dlp_check_timeout")
            return None
        except FileNotFoundError:
            self._log.error("yt_dlp_not_found")
            return None
        except Exception as exc:
            self._log.warning("yt_dlp_check_error", error=str(exc))
            return None

        if result.returncode != 0:
            self._log.debug("channel_not_live", returncode=result.returncode)
            return None

        stdout = result.stdout.strip()
        if not stdout:
            return None

        # yt-dlp may emit multiple JSON objects; take the first
        try:
            info = json.loads(stdout.splitlines()[0])
        except json.JSONDecodeError as exc:
            self._log.warning("yt_dlp_json_parse_error", error=str(exc))
            return None

        is_live = info.get("is_live") or info.get("live_status") == "is_live"
        if not is_live:
            self._log.debug("not_a_live_entry")
            return None

        video_id = info.get("id", "")
        if not video_id:
            self._log.warning("live_missing_video_id")
            return None

        title = info.get("title", "")
        url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        return LiveStreamInfo(video_id=video_id, title=title, url=url)


class MonitorLoop:
    """Continuously polls all configured channels.

    Uses an in-memory set (*seen_ids*) shared with the Application to
    prevent duplicate recordings within the same process lifetime.
    """

    def __init__(self, config: AppConfig, seen_ids: set[str]) -> None:
        self.config = config
        self._seen_ids = seen_ids
        self._stop_event = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self, on_live_detected) -> None:  # noqa: ANN001
        """Poll channels until stop() is called."""
        self._log.info("monitor_started", channels=len(self.config.channels))
        monitors = {ch.id: ChannelMonitor(ch) for ch in self.config.channels}

        while not self._stop_event.is_set():
            tasks = [
                self._check_channel(monitor, on_live_detected)
                for monitor in monitors.values()
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.poll_interval,
                )
            except TimeoutError:
                pass  # Normal — poll interval elapsed

        self._log.info("monitor_stopped")

    async def _check_channel(self, monitor: ChannelMonitor, on_live_detected) -> None:  # noqa: ANN001
        log = get_logger(__name__, channel=monitor.channel.id)
        try:
            live = await asyncio.get_event_loop().run_in_executor(None, monitor.check_live)
            if live is None:
                return

            log.info("live_detected", video_id=live.video_id, title=live.title)

            if live.video_id in self._seen_ids:
                log.debug("already_seen", video_id=live.video_id)
                return

            self._seen_ids.add(live.video_id)

            info = RecordingInfo(
                video_id=live.video_id,
                channel_id=monitor.channel.id,
                channel_name=monitor.channel.name,
                youtube_url=live.url,
                title=live.title,
                detected_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            await on_live_detected(info)

        except Exception as exc:
            log.error("channel_check_error", error=str(exc))
