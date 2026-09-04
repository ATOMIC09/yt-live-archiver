"""
yt-dlp recorder subprocess wrapper.

Responsibilities:
- Build the yt-dlp command
- Launch the subprocess
- Stream and capture output (for logging)
- Record timestamps
- Return a structured result
- Manage working directory files
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from yt_live_archiver.config import AppConfig, RecordingConfig
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import Recording
from yt_live_archiver.utils import ensure_dir

logger = get_logger(__name__)


@dataclass
class RecordingResult:
    """Result from a completed yt-dlp recording attempt."""

    success: bool
    exit_code: int
    output_path: Optional[Path]
    started_at: str
    ended_at: str
    error_message: Optional[str] = None
    stderr_output: str = ""


class Recorder:
    """Runs yt-dlp as a subprocess to record a YouTube livestream."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._log = get_logger(__name__)
        self._active_processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def _build_command(self, recording: Recording, output_path: Path) -> list[str]:
        """Construct the yt-dlp command for this recording."""
        cfg = self.config.recording
        yt_cfg = self.config.youtube

        cmd = [
            "yt-dlp",
            "--no-warnings",
            # Format selection
            "--format", cfg.format,
            # Output template — yt-dlp writes to this exact path
            "--output", str(output_path),
            # Merge into MKV container
            "--merge-output-format", cfg.container,
            "--remux-video", cfg.container,
            # HLS reliability
            "--hls-use-mpegts",
            # Retries
            "--retries", "infinite",
            "--fragment-retries", "infinite",
            # Network
            "--socket-timeout", "30",
            # Metadata embedding (useful for post-processing)
            "--add-metadata",
            # No part files — we manage our own working path
            "--no-part",
        ]

        if yt_cfg.live_from_start:
            cmd.append("--live-from-start")

        # Wait for video to become available (for pre-scheduled streams)
        cmd.extend([
            "--wait-for-video",
            str(yt_cfg.wait_for_video_seconds),
        ])

        # Target URL
        cmd.append(recording.youtube_url)

        return cmd

    def _working_path(self, recording: Recording) -> Path:
        """Return the working directory for this recording."""
        return Path(self.config.recording.working_dir) / recording.channel_id / recording.youtube_video_id

    def _output_template(self, recording: Recording) -> Path:
        """Return the yt-dlp output path (without extension — yt-dlp adds it)."""
        return self._working_path(recording) / "recording"

    def record(self, recording: Recording) -> RecordingResult:
        """Synchronously run yt-dlp and return a RecordingResult.

        Blocks until yt-dlp exits (normal stream end, error, or external kill).
        """
        log = get_logger(__name__, channel=recording.channel_id, video_id=recording.youtube_video_id)

        working_dir = self._working_path(recording)
        ensure_dir(working_dir)

        output_template = self._output_template(recording)
        expected_output = working_dir / f"recording.{self.config.recording.container}"

        cmd = self._build_command(recording, output_template)
        log.info("recording_starting", cmd=" ".join(cmd))

        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stderr_lines: list[str] = []

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            with self._lock:
                self._active_processes[recording.youtube_video_id] = proc

            # Stream stderr in a thread so we don't deadlock
            def _read_stderr() -> None:
                for line in proc.stderr:  # type: ignore[union-attr]
                    line = line.rstrip()
                    if line:
                        stderr_lines.append(line)
                        log.debug("yt-dlp_stderr", line=line)

            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stderr_thread.start()

            # Drain stdout too (avoid pipe buffer fill)
            for line in proc.stdout:  # type: ignore[union-attr]
                log.debug("yt-dlp_stdout", line=line.rstrip())

            proc.wait()
            stderr_thread.join(timeout=5)

        except FileNotFoundError:
            log.error("yt-dlp not found in PATH")
            return RecordingResult(
                success=False,
                exit_code=-1,
                output_path=None,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                error_message="yt-dlp not found in PATH",
            )
        except Exception as exc:
            log.error("Failed to launch yt-dlp", error=str(exc))
            return RecordingResult(
                success=False,
                exit_code=-1,
                output_path=None,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                error_message=str(exc),
            )
        finally:
            with self._lock:
                self._active_processes.pop(recording.youtube_video_id, None)

        ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        exit_code = proc.returncode
        stderr_text = "\n".join(stderr_lines[-50:])  # Keep last 50 lines

        log.info(
            "recording_finished",
            exit_code=exit_code,
        )

        # Check for output file
        # yt-dlp may produce a slightly different filename; search the working dir
        output_path = self._find_output_file(working_dir)

        if exit_code != 0:
            error_msg = f"yt-dlp exited with code {exit_code}"
            if output_path and output_path.stat().st_size > 0:
                # Non-zero exit but we have a file — treat as partial success
                log.warning(
                    "yt-dlp non-zero exit but output file exists, treating as complete",
                    path=str(output_path),
                    size=output_path.stat().st_size,
                )
                return RecordingResult(
                    success=True,
                    exit_code=exit_code,
                    output_path=output_path,
                    started_at=started_at,
                    ended_at=ended_at,
                    error_message=error_msg,
                    stderr_output=stderr_text,
                )

            return RecordingResult(
                success=False,
                exit_code=exit_code,
                output_path=output_path,
                started_at=started_at,
                ended_at=ended_at,
                error_message=error_msg,
                stderr_output=stderr_text,
            )

        if output_path is None or not output_path.exists():
            return RecordingResult(
                success=False,
                exit_code=exit_code,
                output_path=None,
                started_at=started_at,
                ended_at=ended_at,
                error_message="yt-dlp exited 0 but no output file found",
                stderr_output=stderr_text,
            )

        if output_path.stat().st_size == 0:
            return RecordingResult(
                success=False,
                exit_code=exit_code,
                output_path=output_path,
                started_at=started_at,
                ended_at=ended_at,
                error_message="Output file exists but is zero bytes",
                stderr_output=stderr_text,
            )

        log.info(
            "recording_file_ready",
            path=str(output_path),
            size=output_path.stat().st_size,
        )
        return RecordingResult(
            success=True,
            exit_code=exit_code,
            output_path=output_path,
            started_at=started_at,
            ended_at=ended_at,
            stderr_output=stderr_text,
        )

    def _find_output_file(self, working_dir: Path) -> Optional[Path]:
        """Search working_dir for a media file produced by yt-dlp."""
        extensions = {".mkv", ".mp4", ".webm", ".ts", ".m4a", ".ogg"}
        candidates: list[Path] = []
        for p in working_dir.iterdir():
            if p.suffix.lower() in extensions and p.is_file():
                candidates.append(p)
        if not candidates:
            return None
        # Return largest file (most likely the merged output)
        return max(candidates, key=lambda p: p.stat().st_size)

    def terminate_recording(self, video_id: str) -> bool:
        """Gracefully terminate an active yt-dlp process.

        Returns True if a process was found and terminated.
        """
        with self._lock:
            proc = self._active_processes.get(video_id)
        if proc is None:
            return False
        try:
            proc.terminate()
            return True
        except Exception:
            return False
