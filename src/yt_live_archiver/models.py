"""
Data models for yt-live-archiver v2.

Two lean dataclasses — no status enum, no DB fields, no attempt counters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RecordingInfo:
    """Metadata about a detected live stream."""

    video_id: str
    channel_id: str
    channel_name: str
    youtube_url: str
    title: str
    detected_at: str  # ISO-8601 UTC


@dataclass
class RecordingResult:
    """Result from a completed yt-dlp recording attempt."""

    success: bool
    exit_code: int
    output_path: Path | None
    started_at: str   # ISO-8601 UTC
    ended_at: str     # ISO-8601 UTC
    error_message: str | None = None
    stderr_output: str = ""
