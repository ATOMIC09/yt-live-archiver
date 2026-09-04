"""Unit tests for the cleanup module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_archiver.cleanup import Cleanup
from yt_live_archiver.models import Recording, RecordingStatus


def make_config(require_webhook: bool = True, drive_enabled: bool = True):
    """Build a minimal config mock."""
    from yt_live_archiver.config import (
        AppConfig, CleanupConfig, GoogleDriveConfig, ProcessingConfig,
        RecordingConfig, RetryConfig
    )
    config = MagicMock()
    config.cleanup.require_webhook = require_webhook
    config.google_drive.enabled = drive_enabled
    config.google_drive.folder_id = "test_folder"
    config.google_drive.credentials_file = "/creds/google.json"
    config.google_drive.chunk_size_mb = 64
    config.google_drive.shared_drive_id = ""
    return config


def make_db():
    """Build a mock Database."""
    db = MagicMock()
    db.set_error = MagicMock()
    db.update_recording = MagicMock()
    return db


def make_recording(local_path: str, status: RecordingStatus = RecordingStatus.UPLOADED) -> Recording:
    r = Recording()
    r.id = 1
    r.youtube_video_id = "testvidid"
    r.channel_id = "testchannel"
    r.local_path = local_path
    r.media_verified = True
    r.drive_verified = True
    r.webhook_sent = True
    r.drive_file_id = "drive_file_123"
    r.drive_folder_id = "drive_folder_123"
    r.status = status
    return r


class TestCleanup:
    def test_deletes_file_when_all_conditions_met(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data" * 1000)

        r = make_recording(str(local_file))
        r.local_size_bytes = local_file.stat().st_size
        r.drive_size_bytes = local_file.stat().st_size

        config = make_config()
        db = make_db()

        # Mock DriveClient to return matching size
        with patch("yt_live_archiver.cleanup.DriveClient") as MockDriveClient:
            mock_drive = MagicMock()
            mock_drive.get_file.return_value = MagicMock(size=local_file.stat().st_size)
            MockDriveClient.return_value = mock_drive

            cleanup = Cleanup(config, db)
            result = cleanup.delete_local_file(r)

        assert result is True
        assert not local_file.exists()

    def test_refuses_to_delete_when_media_not_verified(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data")

        r = make_recording(str(local_file))
        r.media_verified = False

        config = make_config()
        db = make_db()
        cleanup = Cleanup(config, db)
        result = cleanup.delete_local_file(r)

        assert result is False
        assert local_file.exists()

    def test_refuses_to_delete_when_drive_not_verified(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data")

        r = make_recording(str(local_file))
        r.drive_verified = False

        config = make_config()
        db = make_db()
        cleanup = Cleanup(config, db)
        result = cleanup.delete_local_file(r)

        assert result is False
        assert local_file.exists()

    def test_refuses_to_delete_when_webhook_not_sent(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data")

        r = make_recording(str(local_file))
        r.webhook_sent = False

        config = make_config(require_webhook=True)
        db = make_db()
        cleanup = Cleanup(config, db)
        result = cleanup.delete_local_file(r)

        assert result is False
        assert local_file.exists()

    def test_allows_deletion_without_webhook_when_not_required(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data" * 1000)

        r = make_recording(str(local_file))
        r.webhook_sent = False  # Not sent, but not required
        r.local_size_bytes = local_file.stat().st_size
        r.drive_size_bytes = local_file.stat().st_size

        config = make_config(require_webhook=False)
        db = make_db()

        with patch("yt_live_archiver.cleanup.DriveClient") as MockDriveClient:
            mock_drive = MagicMock()
            mock_drive.get_file.return_value = MagicMock(size=local_file.stat().st_size)
            MockDriveClient.return_value = mock_drive

            cleanup = Cleanup(config, db)
            result = cleanup.delete_local_file(r)

        assert result is True

    def test_aborts_when_drive_file_missing(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data")

        r = make_recording(str(local_file))

        config = make_config()
        db = make_db()

        with patch("yt_live_archiver.cleanup.DriveClient") as MockDriveClient:
            mock_drive = MagicMock()
            mock_drive.get_file.return_value = None  # File not found in Drive
            MockDriveClient.return_value = mock_drive

            cleanup = Cleanup(config, db)
            result = cleanup.delete_local_file(r)

        assert result is False
        assert local_file.exists()

    def test_aborts_when_drive_size_mismatch(self, tmp_path):
        local_file = tmp_path / "recording.mkv"
        local_file.write_bytes(b"data" * 100)

        r = make_recording(str(local_file))
        r.local_size_bytes = local_file.stat().st_size

        config = make_config()
        db = make_db()

        with patch("yt_live_archiver.cleanup.DriveClient") as MockDriveClient:
            mock_drive = MagicMock()
            mock_drive.get_file.return_value = MagicMock(size=999)  # Wrong size
            MockDriveClient.return_value = mock_drive

            cleanup = Cleanup(config, db)
            result = cleanup.delete_local_file(r)

        assert result is False
        assert local_file.exists()

    def test_handles_already_missing_local_file(self, tmp_path):
        """If local file is already gone, mark as COMPLETED anyway."""
        r = make_recording(str(tmp_path / "nonexistent.mkv"))

        config = make_config()
        db = make_db()

        cleanup = Cleanup(config, db)
        result = cleanup.delete_local_file(r)

        # Should succeed (file was already gone)
        assert result is True
