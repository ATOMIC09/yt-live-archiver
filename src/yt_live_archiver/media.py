"""
Media verification using ffprobe and FFmpeg.

Checks:
1. File exists and is readable
2. File size > 0
3. File size is stable (not still being written)
4. ffprobe can parse the container
5. Required streams (video/audio) are present
6. Duration meets minimum threshold
7. FFmpeg decode test passes

Metadata extracted and returned for DB persistence.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from yt_live_archiver.config import VerificationConfig
from yt_live_archiver.logging_config import get_logger
from yt_live_archiver.utils import file_size_bytes, is_file_stable

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class VideoStreamInfo:
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bitrate: int = 0


@dataclass
class AudioStreamInfo:
    codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    bitrate: int = 0


@dataclass
class MediaMetadata:
    container: str = ""
    duration_seconds: float = 0.0
    video: VideoStreamInfo | None = None
    audio: AudioStreamInfo | None = None
    raw_ffprobe: dict = field(default_factory=dict)


@dataclass
class VerificationResult:
    passed: bool = False
    metadata: MediaMetadata | None = None
    ffprobe_valid: bool = False
    decode_test_passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class MediaVerifier:
    """Runs multi-stage media verification on a local file."""

    def __init__(self, config: VerificationConfig) -> None:
        self.config = config
        self._log = get_logger(__name__)

    def verify(self, file_path: str | Path, video_id: str = "") -> VerificationResult:
        """Run full media verification pipeline on *file_path*.

        Returns a VerificationResult regardless of outcome.
        On any failure, the result.passed is False.
        Never raises — all exceptions are caught and recorded.
        """
        log = get_logger(__name__, video_id=video_id)
        result = VerificationResult(passed=True)
        path = Path(file_path)

        # --- Stage 1: File checks ---
        if not path.exists():
            result.add_error(f"File does not exist: {path}")
            return result

        if not path.is_file():
            result.add_error(f"Path is not a file: {path}")
            return result

        size = file_size_bytes(path)
        if size == 0:
            result.add_error(f"File is zero bytes: {path}")
            return result

        if not is_file_stable(path, wait_seconds=2.0):
            result.add_error(f"File size is not stable (still being written?): {path}")
            return result

        log.debug("file_checks_passed", size=size)

        # --- Stage 2: ffprobe ---
        metadata = self._run_ffprobe(path, result, log)
        if metadata is None:
            return result  # errors already recorded

        result.metadata = metadata
        result.ffprobe_valid = True

        # --- Stage 3: Stream checks ---
        if self.config.require_video and metadata.video is None:
            result.add_error("No video stream found in media file")

        if self.config.require_audio and metadata.audio is None:
            result.add_error("No audio stream found in media file")

        if not result.passed:
            return result

        # --- Stage 4: Duration check ---
        if metadata.duration_seconds < self.config.minimum_duration_seconds:
            result.add_error(
                f"Duration {metadata.duration_seconds:.1f}s is below minimum "
                f"{self.config.minimum_duration_seconds}s"
            )
            return result

        log.debug("stream_checks_passed", duration=metadata.duration_seconds)

        # --- Stage 5: Decode test ---
        if self.config.run_decode_test:
            decode_ok, decode_error = self._run_decode_test(path, log)
            if decode_ok:
                result.decode_test_passed = True
                log.debug("decode_test_passed")
            else:
                result.add_error(f"FFmpeg decode test failed: {decode_error}")
                return result

        log.info(
            "media_verification_passed",
            duration=metadata.duration_seconds,
            video_codec=metadata.video.codec if metadata.video else "none",
            audio_codec=metadata.audio.codec if metadata.audio else "none",
        )
        return result

    def _run_ffprobe(
        self, path: Path, result: VerificationResult, log
    ) -> MediaMetadata | None:
        """Run ffprobe and parse output into MediaMetadata."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            result.add_error("ffprobe timed out after 120 seconds")
            return None
        except FileNotFoundError:
            result.add_error("ffprobe not found in PATH")
            return None
        except Exception as exc:
            result.add_error(f"ffprobe failed: {exc}")
            return None

        if proc.returncode != 0:
            stderr = proc.stderr.strip()[:500]
            result.add_error(f"ffprobe returned exit code {proc.returncode}: {stderr}")
            return None

        try:
            info = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            result.add_error(f"Failed to parse ffprobe JSON: {exc}")
            return None

        return self._parse_ffprobe_output(info)

    @staticmethod
    def _parse_ffprobe_output(info: dict) -> MediaMetadata:
        """Extract structured metadata from raw ffprobe JSON."""
        fmt = info.get("format", {})
        streams = info.get("streams", [])

        duration = 0.0
        try:
            duration = float(fmt.get("duration", 0) or 0)
        except (TypeError, ValueError):
            pass

        container = fmt.get("format_name", "")

        video_info: VideoStreamInfo | None = None
        audio_info: AudioStreamInfo | None = None

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            if codec_type == "video" and video_info is None:
                fps = 0.0
                avg_fps = stream.get("avg_frame_rate", "0/1")
                try:
                    num, den = avg_fps.split("/")
                    if int(den) != 0:
                        fps = int(num) / int(den)
                except Exception:
                    pass

                video_info = VideoStreamInfo(
                    codec=stream.get("codec_name", ""),
                    width=int(stream.get("width", 0) or 0),
                    height=int(stream.get("height", 0) or 0),
                    fps=round(fps, 3),
                    bitrate=int(stream.get("bit_rate", 0) or 0),
                )

            elif codec_type == "audio" and audio_info is None:
                audio_info = AudioStreamInfo(
                    codec=stream.get("codec_name", ""),
                    sample_rate=int(stream.get("sample_rate", 0) or 0),
                    channels=int(stream.get("channels", 0) or 0),
                    bitrate=int(stream.get("bit_rate", 0) or 0),
                )

        return MediaMetadata(
            container=container,
            duration_seconds=duration,
            video=video_info,
            audio=audio_info,
            raw_ffprobe=info,
        )

    @staticmethod
    def _run_decode_test(path: Path, log) -> tuple[bool, str]:
        """Run FFmpeg null decode test.

        Returns (passed, error_message).
        """
        cmd = [
            "ffmpeg",
            "-v", "error",
            "-i", str(path),
            "-f", "null",
            "-",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return False, "FFmpeg decode test timed out after 300 seconds"
        except FileNotFoundError:
            return False, "ffmpeg not found in PATH"
        except Exception as exc:
            return False, str(exc)

        if proc.returncode != 0:
            stderr = proc.stderr.strip()[:500]
            return False, f"FFmpeg exit code {proc.returncode}: {stderr}"

        # Log any stderr warnings even on success
        if proc.stderr.strip():
            log.debug("ffmpeg_decode_warnings", warnings=proc.stderr.strip()[:200])

        return True, ""
