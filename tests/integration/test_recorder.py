"""Integration tests for the recorder."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.recorder import Recorder, RecordingResult


def make_config(tmp_path: Path):
    config = MagicMock()
    config.recording.working_dir = str(tmp_path / "working")
    config.recording.failed_dir = str(tmp_path / "failed")
    config.recording.format = "bv*+ba/best"
    config.recording.container = "mkv"
    config.youtube.live_from_start = True
    config.youtube.wait_for_video_seconds = 300
    return config


def make_recording() -> Recording:
    r = Recording()
    r.id = 1
    r.youtube_video_id = "testvid001"
    r.channel_id = "testchannel"
    r.channel_name = "Test Channel"
    r.youtube_url = "https://www.youtube.com/watch?v=testvid001"
    r.title = "Test Stream"
    r.status = RecordingStatus.RECORDING
    return r


class TestRecorder:
    def test_handles_ytdlp_not_found(self, tmp_path):
        """Recorder returns failure when yt-dlp is not in PATH."""
        config = make_config(tmp_path)
        recorder = Recorder(config)
        recording = make_recording()

        with patch("subprocess.Popen", side_effect=FileNotFoundError("yt-dlp not found")):
            result = recorder.record(recording)

        assert not result.success
        assert "not found" in result.error_message.lower()

    def test_handles_ytdlp_exit_zero_no_file(self, tmp_path):
        """Recorder returns failure when yt-dlp exits 0 but produces no file."""
        config = make_config(tmp_path)
        recorder = Recorder(config)
        recording = make_recording()

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter([])
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            result = recorder.record(recording)

        assert not result.success
        assert result.error_message is not None

    def test_handles_ytdlp_exit_nonzero_with_file(self, tmp_path):
        """Non-zero exit with existing file is treated as partial success."""
        config = make_config(tmp_path)
        recorder = Recorder(config)
        recording = make_recording()

        # Pre-create the output file
        working_dir = Path(config.recording.working_dir) / recording.channel_id / recording.youtube_video_id
        working_dir.mkdir(parents=True, exist_ok=True)
        fake_output = working_dir / "recording.mkv"
        fake_output.write_bytes(b"fake mkv data" * 1000)

        mock_proc = MagicMock()
        mock_proc.returncode = 1  # Non-zero
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter([])
        mock_proc.wait.return_value = 1

        with patch("subprocess.Popen", return_value=mock_proc):
            result = recorder.record(recording)

        # Should succeed because file exists and is non-empty
        assert result.success is True
        assert result.output_path == fake_output

    def test_result_has_timestamps(self, tmp_path):
        """Result should always have started_at and ended_at."""
        config = make_config(tmp_path)
        recorder = Recorder(config)
        recording = make_recording()

        with patch("subprocess.Popen", side_effect=FileNotFoundError("not found")):
            result = recorder.record(recording)

        assert result.started_at is not None
        assert result.ended_at is not None
