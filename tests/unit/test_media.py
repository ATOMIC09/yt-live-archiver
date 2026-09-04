"""Unit tests for media verification."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from yt_live_archiver.config import VerificationConfig
from yt_live_archiver.media import MediaVerifier


def make_config(
    require_video: bool = True,
    require_audio: bool = True,
    run_decode_test: bool = True,
    min_duration: float = 30.0,
) -> VerificationConfig:
    return VerificationConfig(
        require_video=require_video,
        require_audio=require_audio,
        run_decode_test=run_decode_test,
        minimum_duration_seconds=min_duration,
    )


VALID_FFPROBE_OUTPUT = {
    "format": {
        "format_name": "matroska",
        "duration": "3600.0",
    },
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "vp9",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30/1",
            "bit_rate": "4000000",
        },
        {
            "codec_type": "audio",
            "codec_name": "opus",
            "sample_rate": "48000",
            "channels": 2,
            "bit_rate": "128000",
        },
    ],
}


class TestMediaVerifier:
    def test_fails_if_file_missing(self, tmp_path):
        verifier = MediaVerifier(make_config())
        result = verifier.verify(tmp_path / "nonexistent.mkv")
        assert not result.passed
        assert any("does not exist" in e for e in result.errors)

    def test_fails_if_file_is_zero_bytes(self, tmp_path):
        empty = tmp_path / "empty.mkv"
        empty.write_bytes(b"")
        verifier = MediaVerifier(make_config())
        result = verifier.verify(empty)
        assert not result.passed
        assert any("zero bytes" in e for e in result.errors)

    def test_passes_with_valid_ffprobe_output(self, tmp_path):
        test_file = tmp_path / "recording.mkv"
        test_file.write_bytes(b"fake data" * 1000)

        verifier = MediaVerifier(make_config(run_decode_test=False))

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(VALID_FFPROBE_OUTPUT)

        with patch("subprocess.run", return_value=mock_proc):
            result = verifier.verify(test_file)

        assert result.passed
        assert result.ffprobe_valid
        assert result.metadata is not None
        assert result.metadata.container == "matroska"
        assert result.metadata.video is not None
        assert result.metadata.video.codec == "vp9"
        assert result.metadata.video.width == 1920
        assert result.metadata.audio is not None
        assert result.metadata.audio.codec == "opus"

    def test_fails_when_ffprobe_returns_nonzero(self, tmp_path):
        test_file = tmp_path / "corrupt.mkv"
        test_file.write_bytes(b"garbage" * 100)

        verifier = MediaVerifier(make_config(run_decode_test=False))

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "Invalid data found"
        mock_proc.stdout = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = verifier.verify(test_file)

        assert not result.passed
        assert not result.ffprobe_valid

    def test_fails_when_no_video_stream_and_required(self, tmp_path):
        test_file = tmp_path / "audio_only.mkv"
        test_file.write_bytes(b"data" * 1000)

        audio_only = {**VALID_FFPROBE_OUTPUT, "streams": [VALID_FFPROBE_OUTPUT["streams"][1]]}
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(audio_only)

        verifier = MediaVerifier(make_config(require_video=True, run_decode_test=False))
        with patch("subprocess.run", return_value=mock_proc):
            result = verifier.verify(test_file)

        assert not result.passed
        assert any("video stream" in e for e in result.errors)

    def test_fails_when_duration_too_short(self, tmp_path):
        test_file = tmp_path / "short.mkv"
        test_file.write_bytes(b"data" * 1000)

        short_output = {
            **VALID_FFPROBE_OUTPUT,
            "format": {**VALID_FFPROBE_OUTPUT["format"], "duration": "5.0"},
        }
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps(short_output)

        verifier = MediaVerifier(make_config(run_decode_test=False, min_duration=30.0))
        with patch("subprocess.run", return_value=mock_proc):
            result = verifier.verify(test_file)

        assert not result.passed
        assert any("minimum" in e for e in result.errors)

    def test_decode_test_fail_marks_as_invalid(self, tmp_path):
        test_file = tmp_path / "corrupt_stream.mkv"
        test_file.write_bytes(b"data" * 1000)

        def mock_run_side_effect(cmd, *args, **kwargs):
            result = MagicMock()
            if "ffprobe" in cmd[0]:
                result.returncode = 0
                result.stdout = json.dumps(VALID_FFPROBE_OUTPUT)
            else:
                # ffmpeg decode test fails
                result.returncode = 1
                result.stderr = "Decode error: Invalid data"
            return result

        verifier = MediaVerifier(make_config(run_decode_test=True))
        with patch("subprocess.run", side_effect=mock_run_side_effect):
            result = verifier.verify(test_file)

        assert not result.passed
        assert not result.decode_test_passed
        assert any("decode test" in e.lower() for e in result.errors)
