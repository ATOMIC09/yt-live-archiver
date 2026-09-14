"""
yt-dlp recorder subprocess wrapper with automatic segment merging.

Responsibilities:
  - Build and launch the yt-dlp command
  - Stream and log subprocess output
  - Detect multiple output segments produced by yt-dlp (stream restarts)
  - Merge segments into a single file via ffmpeg concat
  - Return a structured RecordingResult
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from yt_live_archiver.config import AppConfig
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.models import RecordingInfo, RecordingResult
from yt_live_archiver.utils import ensure_dir

logger = get_logger(__name__)

_MEDIA_EXTENSIONS = {".mkv", ".mp4", ".webm", ".ts", ".m4a", ".ogg"}


class Recorder:
    """Runs yt-dlp as a subprocess and returns the recording result."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._log = get_logger(__name__)
        self._active_processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _working_path(self, info: RecordingInfo) -> Path:
        """Return the working directory for this recording."""
        return Path(self.config.working_dir) / info.channel_id / info.video_id

    def _output_template(self, info: RecordingInfo) -> Path:
        """Return the yt-dlp output path (yt-dlp appends the extension)."""
        return self._working_path(info) / "recording"

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def _build_command(self, info: RecordingInfo, output_template: Path) -> list[str]:
        cfg = self.config

        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--format", cfg.recording_format,
            "--output", str(output_template),
            "--merge-output-format", cfg.recording_container,
            "--remux-video", cfg.recording_container,
            "--hls-use-mpegts",
            "--retries", str(cfg.retries),
            "--fragment-retries", str(cfg.fragment_retries),
            "--retry-sleep", "fragment:exp=1:20",
            "--socket-timeout", "30",
            "--add-metadata",
            "--no-part",
        ]

        if cfg.live_from_start:
            cmd.append("--live-from-start")

        cmd.extend(["--wait-for-video", str(cfg.wait_for_video)])
        cmd.append(info.youtube_url)
        return cmd

    # ------------------------------------------------------------------
    # Core recording
    # ------------------------------------------------------------------

    def record(self, info: RecordingInfo) -> RecordingResult:
        """Synchronously run yt-dlp and return a RecordingResult.

        Blocks until yt-dlp exits. Merges multiple output segments if needed.
        """
        log = get_logger(__name__, channel=info.channel_id, video_id=info.video_id)
        raw_log = get_logger(__name__)

        working_dir = self._working_path(info)
        ensure_dir(working_dir)

        output_template = self._output_template(info)
        cmd = self._build_command(info, output_template)
        log.info("recording_starting", cmd=" ".join(cmd))

        started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
                self._active_processes[info.video_id] = proc

            def _read_stderr() -> None:
                for line in proc.stderr:  # type: ignore[union-attr]
                    line = line.strip()
                    if line:
                        stderr_lines.append(line)
                        raw_log.debug(f"[yt-dlp] {line}")

            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stderr_thread.start()

            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if line:
                    raw_log.debug(f"[yt-dlp] {line}")

            proc.wait()
            stderr_thread.join(timeout=5)

        except FileNotFoundError:
            log.error("yt_dlp_not_found")
            return self._failure(started_at, "yt-dlp not found in PATH")
        except Exception as exc:
            log.error("yt_dlp_launch_error", error=str(exc))
            return self._failure(started_at, str(exc))
        finally:
            with self._lock:
                self._active_processes.pop(info.video_id, None)

        ended_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        exit_code = proc.returncode
        stderr_text = "\n".join(stderr_lines[-50:])

        log.info("recording_finished", exit_code=exit_code)

        # Find and (if needed) merge output files
        media_files = self._find_media_files(working_dir)

        if not media_files:
            if exit_code == 0:
                return self._failure(started_at, "yt-dlp exited 0 but produced no output file",
                                     exit_code=exit_code, stderr=stderr_text, ended_at=ended_at)
            return self._failure(started_at, f"yt-dlp exited {exit_code}, no output file",
                                 exit_code=exit_code, stderr=stderr_text, ended_at=ended_at)

        # Merge segments if more than one file was produced
        output_path = self._merge_or_pick(working_dir, media_files, log)

        if exit_code != 0:
            if output_path and output_path.stat().st_size > 0:
                log.warning(
                    "non_zero_exit_but_file_exists",
                    exit_code=exit_code,
                    path=str(output_path),
                )
                # Treat as success — stream may have ended with a non-zero code
            else:
                return RecordingResult(
                    success=False,
                    exit_code=exit_code,
                    output_path=output_path,
                    started_at=started_at,
                    ended_at=ended_at,
                    error_message=f"yt-dlp exit code {exit_code}",
                    stderr_output=stderr_text,
                )

        if output_path.stat().st_size == 0:
            return self._failure(started_at, "Output file is zero bytes",
                                 exit_code=exit_code, stderr=stderr_text, ended_at=ended_at,
                                 output_path=output_path)

        log.info("recording_file_ready", path=str(output_path), size=output_path.stat().st_size)
        return RecordingResult(
            success=True,
            exit_code=exit_code,
            output_path=output_path,
            started_at=started_at,
            ended_at=ended_at,
            stderr_output=stderr_text,
        )

    # ------------------------------------------------------------------
    # Segment helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_media_files(working_dir: Path) -> list[Path]:
        """Return all media files in *working_dir*."""
        return [
            p for p in working_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _MEDIA_EXTENSIONS
        ]

    @staticmethod
    def _probe_streams(path: Path) -> tuple[bool, bool]:
        """Return (has_video, has_audio) using ffprobe. (False, False) on failure."""
        if not path.exists() or path.stat().st_size == 0:
            return False, False
        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return False, False
            info = json.loads(proc.stdout)
            types = {s.get("codec_type") for s in info.get("streams", [])}
            return ("video" in types, "audio" in types)
        except Exception:
            return False, False

    @staticmethod
    def _mux_tracks(
        working_dir: Path,
        video_file: Path,
        audio_file: Path,
        all_files: list[Path],
        log,  # noqa: ANN001
    ) -> Path | None:
        """Mux a video-only track and an audio-only track into merged.mkv via ffmpeg.

        Used when yt-dlp was interrupted and left separate video and audio streams
        without completing its post-processing merge.
        """
        merged_path = working_dir / "merged.mkv"
        cmd = [
            "ffmpeg",
            "-i", str(video_file),
            "-i", str(audio_file),
            "-c", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-y",
            str(merged_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if result.returncode == 0 and merged_path.exists() and merged_path.stat().st_size > 0:
                # Clean up source files
                for p in all_files:
                    try:
                        p.unlink()
                    except OSError:
                        pass
                log.info(
                    "tracks_muxed",
                    video=video_file.name,
                    audio=audio_file.name,
                    output=str(merged_path),
                    size=merged_path.stat().st_size,
                )
                return merged_path
            log.warning(
                "tracks_mux_failed",
                returncode=result.returncode,
                stderr=result.stderr[:300],
            )
            return None
        except Exception as exc:
            log.warning("tracks_mux_error", error=str(exc))
            return None

    def _merge_or_pick(
        self,
        working_dir: Path,
        files: list[Path],
        log,  # noqa: ANN001
    ) -> Path:
        """Return a single output path from potentially multiple segment files.

        1. Probes streams in each file with ffprobe.
        2. If separate video-only and audio-only files exist (interrupted yt-dlp download),
           muxes them into merged.mkv.
        3. If a complete file (both video + audio) exists, uses it.
        4. If multiple sequential segments exist, concatenates via ffmpeg concat.
        5. Falls back to largest file.
        """
        if len(files) == 1:
            return files[0]

        log.info("multiple_segments_found", count=len(files))

        # Probe streams in each file
        probed = [(p, self._probe_streams(p)) for p in files]
        video_only = [p for p, (has_v, has_a) in probed if has_v and not has_a]
        audio_only = [p for p, (has_v, has_a) in probed if has_a and not has_v]
        complete = [p for p, (has_v, has_a) in probed if has_v and has_a]

        # Case 1: Separate video and audio tracks from interrupted yt-dlp download
        if video_only and audio_only:
            v_file = max(video_only, key=lambda p: p.stat().st_size)
            a_file = max(audio_only, key=lambda p: p.stat().st_size)
            log.info("detected_separate_tracks", video=v_file.name, audio=a_file.name)
            merged = self._mux_tracks(working_dir, v_file, a_file, files, log)
            if merged is not None:
                return merged

        # Case 2: One of the files is already a complete recording with video + audio
        if len(complete) == 1:
            log.info("found_complete_file", path=str(complete[0]))
            return complete[0]
        elif len(complete) > 1:
            # Multiple complete segments (e.g. stream dropped and reconnected) -> concat
            merged = self._merge_segments(working_dir, complete, log)
            if merged is not None:
                return merged

        # Case 3: Multiple video files without audio or mixed -> concat
        if len(video_only) > 1:
            merged = self._merge_segments(working_dir, video_only, log)
            if merged is not None:
                return merged

        # Case 4: General concat fallback across all files
        merged = self._merge_segments(working_dir, files, log)
        if merged is not None:
            return merged

        # Final fallback — pick largest file
        log.warning("merge_failed_using_largest_file")
        return max(files, key=lambda p: p.stat().st_size)

    @staticmethod
    def _merge_segments(working_dir: Path, files: list[Path], log) -> Path | None:  # noqa: ANN001
        """Merge segment files into a single MKV via ffmpeg concat.

        Files are sorted by modification time (oldest first) to preserve
        chronological order. Returns the merged path on success, None on failure.
        """
        files_sorted = sorted(files, key=lambda p: p.stat().st_mtime)
        concat_list = working_dir / "concat_list.txt"
        merged_path = working_dir / "merged.mkv"

        try:
            concat_list.write_text(
                "\n".join(f"file '{p.resolve()}'" for p in files_sorted),
                encoding="utf-8",
            )

            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                "-y",
                str(merged_path),
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=3600
            )

            if result.returncode == 0 and merged_path.exists() and merged_path.stat().st_size > 0:
                # Clean up source segments
                for p in files_sorted:
                    try:
                        p.unlink()
                    except OSError:
                        pass
                concat_list.unlink(missing_ok=True)
                log.info("segments_merged", output=str(merged_path), segments=len(files_sorted))
                return merged_path

            log.warning(
                "ffmpeg_merge_failed",
                returncode=result.returncode,
                stderr=result.stderr[:300],
            )
            return None

        except subprocess.TimeoutExpired:
            log.warning("ffmpeg_merge_timeout")
            return None
        except Exception as exc:
            log.warning("ffmpeg_merge_error", error=str(exc))
            return None
        finally:
            concat_list.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _failure(
        started_at: str,
        error_message: str,
        exit_code: int = -1,
        stderr: str = "",
        ended_at: str | None = None,
        output_path: Path | None = None,
    ) -> RecordingResult:
        return RecordingResult(
            success=False,
            exit_code=exit_code,
            output_path=output_path,
            started_at=started_at,
            ended_at=ended_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            error_message=error_message,
            stderr_output=stderr,
        )

    def terminate_recording(self, video_id: str) -> bool:
        """Gracefully terminate an active yt-dlp process."""
        with self._lock:
            proc = self._active_processes.get(video_id)
        if proc is None:
            return False
        try:
            proc.terminate()
            return True
        except Exception:
            return False
