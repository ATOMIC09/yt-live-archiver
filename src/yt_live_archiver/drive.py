"""
Google Drive service layer.

Isolated from the rest of the application: the rest of the code
uses DriveClient methods, not raw Google API objects.

Authentication: Service Account (google-credentials.json).
Supports Shared Drives.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from yt_live_archiver.config import GoogleDriveConfig
from yt_live_archiver.logging_config import get_logger

logger = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Retry-able HTTP status codes
_RETRYABLE_CODES = {429, 500, 502, 503, 504}


class DriveError(Exception):
    """General Drive operation error."""


class DriveAuthError(DriveError):
    """Raised for authentication failures (permanent)."""


class DriveUploadError(DriveError):
    """Raised for upload failures."""


class RemoteFileInfo:
    """Minimal metadata about a file in Drive."""

    def __init__(self, file_id: str, name: str, size: int, md5: str = "") -> None:
        self.file_id = file_id
        self.name = name
        self.size = size
        self.md5 = md5


class DriveClient:
    """High-level Google Drive client supporting resumable uploads to Shared Drives."""

    def __init__(self, config: GoogleDriveConfig) -> None:
        self.config = config
        self._service = None
        self._log = get_logger(__name__)

    def _get_service(self):
        """Lazily build and cache the Drive service."""
        if self._service is not None:
            return self._service

        creds_file = self.config.credentials_file
        if not Path(creds_file).exists():
            raise DriveAuthError(
                f"Google credentials file not found: {creds_file}"
            )

        try:
            credentials = service_account.Credentials.from_service_account_file(
                creds_file, scopes=_SCOPES
            )
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            raise DriveAuthError(f"Failed to authenticate with Google Drive: {exc}") from exc

        return self._service

    def upload_file(
        self,
        local_path: str | Path,
        remote_name: str,
        mime_type: str = "video/x-matroska",
        video_id: str = "",
    ) -> RemoteFileInfo:
        """Upload *local_path* to Google Drive with resumable transfer.

        Returns RemoteFileInfo with the new file's ID and size.
        Raises DriveUploadError on failure.
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise DriveUploadError(f"Local file does not exist: {local_path}")

        local_size = local_path.stat().st_size
        chunk_bytes = self.config.chunk_size_mb * 1024 * 1024
        log = get_logger(__name__, video_id=video_id)

        log.info(
            "drive_upload_starting",
            file=remote_name,
            size=local_size,
            chunks=self.config.chunk_size_mb,
        )

        service = self._get_service()

        file_metadata = {
            "name": remote_name,
            "parents": [self.config.folder_id],
        }

        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type,
            chunksize=chunk_bytes,
            resumable=True,
        )

        # Create upload request
        request = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id,name,size,md5Checksum",
            supportsAllDrives=True,
        )
        if self.config.shared_drive_id:
            request = service.files().create(
                body={**file_metadata, "driveId": self.config.shared_drive_id},
                media_body=media,
                fields="id,name,size,md5Checksum",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )

        # Execute with retry
        response = None
        attempt = 0
        delay = 5.0

        while response is None:
            attempt += 1
            try:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    log.debug("drive_upload_progress", percent=pct)
            except HttpError as exc:
                code = exc.resp.status
                if code in _RETRYABLE_CODES:
                    log.warning(
                        "drive_upload_http_error_retrying",
                        code=code,
                        attempt=attempt,
                        delay=delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 300)
                else:
                    raise DriveUploadError(
                        f"Non-retryable HTTP {code} during upload: {exc}"
                    ) from exc
            except Exception as exc:
                if attempt > 10:
                    raise DriveUploadError(f"Upload failed after {attempt} attempts: {exc}") from exc
                log.warning(
                    "drive_upload_error_retrying",
                    error=str(exc),
                    attempt=attempt,
                    delay=delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 300)

        file_id = response.get("id", "")
        remote_size = int(response.get("size", 0) or 0)
        md5 = response.get("md5Checksum", "")

        log.info(
            "drive_upload_completed",
            file_id=file_id,
            remote_size=remote_size,
        )

        return RemoteFileInfo(
            file_id=file_id,
            name=remote_name,
            size=remote_size,
            md5=md5,
        )

    def get_file(self, file_id: str) -> Optional[RemoteFileInfo]:
        """Retrieve metadata for a Drive file by ID.

        Returns None if the file does not exist.
        """
        try:
            service = self._get_service()
            info = service.files().get(
                fileId=file_id,
                fields="id,name,size,md5Checksum",
                supportsAllDrives=True,
            ).execute()
            return RemoteFileInfo(
                file_id=info.get("id", ""),
                name=info.get("name", ""),
                size=int(info.get("size", 0) or 0),
                md5=info.get("md5Checksum", ""),
            )
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise DriveError(f"Failed to get file {file_id}: {exc}") from exc
        except DriveAuthError:
            raise
        except Exception as exc:
            raise DriveError(f"Failed to get file {file_id}: {exc}") from exc

    def verify_file(self, file_id: str, expected_size: int) -> bool:
        """Verify that *file_id* exists in Drive and has the expected size.

        Returns True if verified, False otherwise.
        """
        info = self.get_file(file_id)
        if info is None:
            return False
        if info.size != expected_size:
            self._log.warning(
                "drive_size_mismatch",
                file_id=file_id,
                expected=expected_size,
                actual=info.size,
            )
            return False
        return True
