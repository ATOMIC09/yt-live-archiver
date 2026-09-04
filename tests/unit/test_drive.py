"""Unit tests for Google Drive client."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_live_archiver.config import GoogleDriveConfig
from yt_live_archiver.drive import DriveAuthError, DriveClient, DriveUploadError, RemoteFileInfo


@pytest.fixture
def drive_config(tmp_path: Path) -> GoogleDriveConfig:
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")
    return GoogleDriveConfig(
        enabled=True,
        credentials_file=str(creds),
        folder_id="test-folder-id",
        chunk_size_mb=16,
    )


def test_get_service_service_account(drive_config: GoogleDriveConfig, tmp_path: Path):
    client = DriveClient(drive_config)
    with patch(
        "yt_live_archiver.drive.service_account.Credentials.from_service_account_file"
    ) as mock_sa, patch("yt_live_archiver.drive.build") as mock_build:
        mock_sa.return_value = MagicMock()
        mock_build.return_value = MagicMock()

        service = client._get_service()
        assert service is not None
        mock_sa.assert_called_once()
        mock_build.assert_called_once()


def test_get_service_authorized_user(tmp_path: Path):
    token_file = tmp_path / "token.json"
    token_file.write_text(
        json.dumps({
            "token": "test-token",
            "refresh_token": "test-refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test-cid",
            "client_secret": "test-csec",
        }),
        encoding="utf-8",
    )
    config = GoogleDriveConfig(
        enabled=True,
        credentials_file=str(token_file),
        folder_id="my-folder",
    )
    client = DriveClient(config)

    with patch("yt_live_archiver.drive.Credentials.from_authorized_user_file") as mock_user, \
         patch("yt_live_archiver.drive.build") as mock_build:
        mock_user.return_value = MagicMock()
        mock_build.return_value = MagicMock()

        service = client._get_service()
        assert service is not None
        mock_user.assert_called_once()
        mock_build.assert_called_once()


def test_get_service_missing_file():
    config = GoogleDriveConfig(credentials_file="/nonexistent/creds.json")
    client = DriveClient(config)
    with pytest.raises(DriveAuthError, match="not found"):
        client._get_service()


def test_upload_file_nonexistent(drive_config: GoogleDriveConfig):
    client = DriveClient(drive_config)
    with pytest.raises(DriveUploadError, match="Local file does not exist"):
        client.upload_file("/nonexistent/video.mkv", "video.mkv")


def test_upload_file_success(drive_config: GoogleDriveConfig, tmp_path: Path):
    video = tmp_path / "video.mkv"
    video.write_bytes(b"dummy video data")

    client = DriveClient(drive_config)
    mock_service = MagicMock()
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (
        None,
        {"id": "drive123", "size": "16", "md5Checksum": "abc"},
    )
    mock_service.files().create.return_value = mock_request
    client._service = mock_service

    result = client.upload_file(video, "remote_video.mkv")
    assert isinstance(result, RemoteFileInfo)
    assert result.file_id == "drive123"
    assert result.size == 16


def test_upload_file_empty_folder_id(tmp_path: Path):
    video = tmp_path / "video.mkv"
    video.write_bytes(b"dummy video data")

    config = GoogleDriveConfig(
        enabled=True,
        credentials_file=str(tmp_path / "dummy.json"),
        folder_id="",  # root
    )
    client = DriveClient(config)
    mock_service = MagicMock()
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "drive456", "size": "16"})
    mock_service.files().create.return_value = mock_request
    client._service = mock_service

    result = client.upload_file(video, "remote_video.mkv")
    assert result.file_id == "drive456"

    # Verify create was called without "parents"
    called_body = mock_service.files().create.call_args[1]["body"]
    assert "parents" not in called_body


def test_get_or_create_subfolder_existing(drive_config: GoogleDriveConfig):
    client = DriveClient(drive_config)
    mock_service = MagicMock()
    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {"files": [{"id": "subfolder_789", "name": "NASA"}]}
    mock_service.files().list.return_value = mock_list_req
    client._service = mock_service

    folder_id = client.get_or_create_subfolder("parent_123", "NASA")
    assert folder_id == "subfolder_789"
    assert ("parent_123", "NASA") in client._folder_cache

    # Subsequent call uses cache (list not called again)
    mock_service.files().list.reset_mock()
    folder_id_cached = client.get_or_create_subfolder("parent_123", "NASA")
    assert folder_id_cached == "subfolder_789"
    mock_service.files().list.assert_not_called()


def test_get_or_create_subfolder_creates_new(drive_config: GoogleDriveConfig):
    client = DriveClient(drive_config)
    mock_service = MagicMock()
    # First search returns empty list
    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {"files": []}
    mock_service.files().list.return_value = mock_list_req

    # Create returns new folder id
    mock_create_req = MagicMock()
    mock_create_req.execute.return_value = {"id": "new_folder_999"}
    mock_service.files().create.return_value = mock_create_req
    client._service = mock_service

    folder_id = client.get_or_create_subfolder("parent_123", "SpaceX")
    assert folder_id == "new_folder_999"
    mock_service.files().create.assert_called_once()
    create_body = mock_service.files().create.call_args[1]["body"]
    assert create_body["name"] == "SpaceX"
    assert create_body["parents"] == ["parent_123"]


def test_upload_file_with_subfolder(drive_config: GoogleDriveConfig, tmp_path: Path):
    video = tmp_path / "video.mkv"
    video.write_bytes(b"dummy video data")

    client = DriveClient(drive_config)
    mock_service = MagicMock()
    # Mock subfolder resolution to return cached or found subfolder
    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {"files": [{"id": "channel_folder_555", "name": "NASA"}]}
    mock_service.files().list.return_value = mock_list_req

    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "drive777", "size": "16"})
    mock_service.files().create.return_value = mock_request
    client._service = mock_service

    result = client.upload_file(video, "video.mkv", subfolder_name="NASA")
    assert result.file_id == "drive777"
    assert result.folder_id == "channel_folder_555"

    # Verify upload was created with subfolder parent
    called_body = mock_service.files().create.call_args[1]["body"]
    assert called_body["parents"] == ["channel_folder_555"]

