"""
Utility functions shared across the application.

- Filename sanitization
- Human-readable file size formatting
- Exponential backoff with jitter
- Filesystem helpers
"""

from __future__ import annotations

import os
import random
import re
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path

# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MULTIPLE_SPACES = re.compile(r"\s+")
_LEADING_TRAILING_DOTS_SPACES = re.compile(r"^[\s.]+|[\s.]+$")

# Windows reserved names
_WINDOWS_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..+)?$", re.IGNORECASE
)

MAX_FILENAME_LENGTH = 200  # characters (well under most filesystem limits)


def sanitize_filename(raw: str) -> str:
    """Return a filesystem-safe version of *raw*.

    Rules applied:
    - Unicode NFKC normalization
    - Remove control characters
    - Replace unsafe characters (slash, colon, etc.) with underscores
    - Collapse repeated whitespace
    - Strip leading/trailing dots and spaces
    - Truncate to MAX_FILENAME_LENGTH
    - Replace Windows reserved names
    """
    # Normalize unicode
    name = unicodedata.normalize("NFKC", raw)
    # Remove control chars
    name = _CONTROL_CHARS.sub("", name)
    # Replace unsafe chars
    name = _UNSAFE_CHARS.sub("_", name)
    # Collapse whitespace
    name = _MULTIPLE_SPACES.sub(" ", name).strip()
    # Strip leading/trailing dots and spaces
    name = _LEADING_TRAILING_DOTS_SPACES.sub("", name)
    # Truncate
    if len(name) > MAX_FILENAME_LENGTH:
        name = name[:MAX_FILENAME_LENGTH]
    # Handle Windows reserved names
    if _WINDOWS_RESERVED.match(name):
        name = "_" + name
    # Ensure non-empty
    if not name:
        name = "unnamed"
    return name


def build_archive_filename(
    channel_id: str = "",
    date_str: str = "",
    video_id: str = "",
    title: str = "",
    ext: str = "mkv",
    include_metadata: bool = False,
) -> str:
    """Build the clean archive filename using only the stream title.

    Format: {title}.{ext}
    Example: Live Video from the International Space Station.mkv
    """
    safe_title = sanitize_filename(title) if title else "Livestream"
    if not safe_title or safe_title == "unnamed":
        safe_title = "Livestream"

    if include_metadata and (channel_id or date_str or video_id):
        max_title = MAX_FILENAME_LENGTH - len(channel_id) - len(date_str) - len(video_id) - 3
        if len(safe_title) > max_title:
            safe_title = safe_title[:max_title]
        return f"{channel_id}_{date_str}_{video_id}_{safe_title}.{ext}"

    max_title = MAX_FILENAME_LENGTH - len(ext) - 1
    if len(safe_title) > max_title:
        safe_title = safe_title[:max_title]
    return f"{safe_title}.{ext}"


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------


def exponential_backoff_delays(
    initial: float = 5.0,
    multiplier: float = 2.0,
    cap: float = 300.0,
    jitter: bool = True,
) -> Iterator[float]:
    """Infinite generator of exponentially increasing delay values (seconds)."""
    delay = initial
    while True:
        actual = delay
        if jitter:
            actual = delay * (0.5 + random.random())  # ± 50% jitter
        yield min(actual, cap)
        delay = min(delay * multiplier, cap)


# ---------------------------------------------------------------------------
# File/directory helpers
# ---------------------------------------------------------------------------


def ensure_dir(path: str | Path) -> Path:
    """Create directory and all parents if they do not exist. Return the Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def file_size_bytes(path: str | Path) -> int:
    """Return file size in bytes. Returns 0 if the file does not exist."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def format_bytes(num_bytes: int) -> str:
    """Return human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024.0  # type: ignore[assignment]
    return f"{num_bytes:.1f}PB"


def is_file_stable(path: str | Path, wait_seconds: float = 2.0) -> bool:
    """Return True if the file size has not changed after *wait_seconds*.

    Used to confirm yt-dlp has finished writing a file.
    """
    p = Path(path)
    if not p.exists():
        return False
    size_before = p.stat().st_size
    time.sleep(wait_seconds)
    size_after = p.stat().st_size
    return size_before == size_after and size_after > 0


def safe_remove(path: str | Path) -> bool:
    """Delete a file, returning True on success, False if it did not exist."""
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as HH:MM:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
