"""
YouTube channel monitor.

Polls configured channels for active livestreams using yt-dlp.
Creates database records and dispatches recording tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

from yt_live_archiver.config import AppConfig, ChannelConfig
from yt_live_archiver.database import Database
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import Recording, RecordingStatus

logger = get_logger(__name__)


class LiveStreamInfo:
    """Information about a detected live stream."""

    def __init__(self, video_id: str, title: str, url: str) -> None:
        self.video_id = video_id
        self.title = title
        self.url = url


class ChannelMonitor:
    """Monitors a single YouTube channel for live streams.

    Uses yt-dlp in flat-playlist / metadata-only mode to check
    if the channel is currently live.
    """

    def __init__(self, channel: ChannelConfig, config: AppConfig) -> None:
        self.channel = channel
        self.config = config
        self._log = get_logger(__name__, channel=channel.id)

    def check_live(self) -> Optional[LiveStreamInfo]:
        """Check if the channel is currently live.

        Returns LiveStreamInfo if live, None if offline or error.
        """
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
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            self._log.warning("yt-dlp metadata check timed out")
            return None
        except FileNotFoundError:
            self._log.error("yt-dlp not found in PATH")
            return None
        except Exception as exc:
            self._log.warning("yt-dlp metadata check failed", error=str(exc))
            return None

        if result.returncode != 0:
            # Not live or channel offline — expected and non-alarming
            self._log.debug("Channel not live (yt-dlp exit=%d)", result.returncode)
            return None

        # Parse JSON output
        stdout = result.stdout.strip()
        if not stdout:
            return None

        # yt-dlp may emit multiple JSON objects (one per entry); take first
        first_line = stdout.splitlines()[0]
        try:
            info = json.loads(first_line)
        except json.JSONDecodeError as exc:
            self._log.warning("Failed to parse yt-dlp JSON output", error=str(exc))
            return None

        # Confirm this is a live broadcast
        is_live = info.get("is_live") or info.get("live_status") == "is_live"
        if not is_live:
            self._log.debug("yt-dlp returned a non-live entry; skipping")
            return None

        video_id = info.get("id", "")
        title = info.get("title", "")
        webpage_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"

        if not video_id:
            self._log.warning("Live video detected but missing video ID")
            return None

        return LiveStreamInfo(video_id=video_id, title=title, url=webpage_url)


class MonitorLoop:
    """Continuously monitors all configured channels.

    Runs channel checks in the configured interval.
    Dispatches callbacks when a new live stream is detected.
    """

    def __init__(self, config: AppConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._stop_event = asyncio.Event()
        self._log = get_logger(__name__)

    def stop(self) -> None:
        """Signal the monitor loop to stop."""
        self._stop_event.set()

    async def run(self, on_live_detected) -> None:  # noqa: ANN001
        """Main monitor loop. Calls on_live_detected(channel, video_id, title, url).

        Runs until stop() is called.
        """
        self._log.info("Monitor loop started", channels=len(self.config.channels))
        monitors = {
            ch.id: ChannelMonitor(ch, self.config)
            for ch in self.config.channels
            if ch.enabled
        }

        while not self._stop_event.is_set():
            tasks = [
                self._check_channel(monitor, on_live_detected)
                for monitor in monitors.values()
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.youtube.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass  # Normal — sleep expired, loop again

        self._log.info("Monitor loop stopped")

    async def _check_channel(self, monitor: ChannelMonitor, on_live_detected) -> None:  # noqa: ANN001
        """Check one channel and call on_live_detected if live and new."""
        log = get_logger(__name__, channel=monitor.channel.id)
        try:
            live = await asyncio.get_event_loop().run_in_executor(
                None, monitor.check_live
            )
            if live is None:
                return

            log.info("live_detected", video_id=live.video_id, title=live.title)

            # Duplicate check
            if self.db.video_id_exists(live.video_id):
                log.debug(
                    "Already have a record for this video, skipping",
                    video_id=live.video_id,
                )
                return

            # Create DB record immediately
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            recording = Recording(
                youtube_video_id=live.video_id,
                channel_id=monitor.channel.id,
                channel_name=monitor.channel.name,
                channel_url=monitor.channel.url,
                youtube_url=live.url,
                title=live.title,
                status=RecordingStatus.DISCOVERED,
                detected_at=now,
            )
            self.db.create_recording(recording)
            log.info(
                "recording_created",
                video_id=live.video_id,
                db_id=recording.id,
            )

            await on_live_detected(recording)

        except Exception as exc:
            log.error("Error checking channel", error=str(exc))
