"""
Google Drive client — credentials built from ENV vars, no file mount.

Authentication flow:
  1. Supply GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN.
  2. The google-auth library auto-refreshes the access token as needed.
  3. No token.json file is read or written.

Supports both personal accounts (OAuth 2.0) and Workspace Shared Drives.
"""

from __future__ import annotations

import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from yt_live_archiver.config import GoogleDriveConfig
from yt_live_archiver.logging_config import get_logger

logger = get_logger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_RETRYABLE_CODES = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DriveError(Exception):
    """General Drive operation error."""


class DriveAuthError(DriveError):
    """Authentication failure (invalid credentials)."""


class DriveUploadError(DriveError):
    """Upload failure."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class RemoteFileInfo:
    """Minimal metadata about a file in Drive."""

    def __init__(self, file_id: str, name: str, size: int, folder_id: str = "") -> None:
        self.file_id = file_id
        self.name = name
        self.size = size
        self.folder_id = folder_id


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DriveClient:
    """Google Drive client with resumable uploads and subfolder management."""

    def __init__(self, config: GoogleDriveConfig) -> None:
        self.config = config
        self._service = None
        self._log = get_logger(__name__)
        self._folder_cache: dict[tuple[str, str], str] = {}

    def _get_service(self):
        """Build and cache the Drive API service (lazy, with credential validation)."""
        if self._service is not None:
            return self._service

        try:
            creds = Credentials(
                token=None,
                refresh_token=self.config.refresh_token,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=_SCOPES,
            )
            # Eagerly refresh to surface auth errors at startup rather than
            # mid-upload.
            creds.refresh(Request())
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            raise DriveAuthError(
                f"Failed to authenticate with Google Drive: {exc}\n"
                "Check GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN."
            ) from exc

        self._log.info("drive_authenticated")
        return self._service

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def get_or_create_subfolder(self, parent_id: str, folder_name: str) -> str:
        """Return the ID of *folder_name* under *parent_id*, creating it if needed."""
        if not folder_name:
            return parent_id

        cache_key = (parent_id, folder_name)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        service = self._get_service()
        escaped = folder_name.replace("'", "\\'")
        query = (
            f"mimeType = 'application/vnd.google-apps.folder' and "
            f"name = '{escaped}' and trashed = false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"

        try:
            response = service.files().list(
                q=query,
                spaces="drive",
                fields="files(id,name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files = response.get("files", [])
            if files:
                folder_id = files[0]["id"]
                self._folder_cache[cache_key] = folder_id
                return folder_id

            # Create the folder
            metadata: dict = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                metadata["parents"] = [parent_id]

            created = service.files().create(
                body=metadata,
                fields="id",
                supportsAllDrives=True,
            ).execute()
            folder_id = created["id"]
            self._log.info("drive_folder_created", name=folder_name, id=folder_id)
            self._folder_cache[cache_key] = folder_id
            return folder_id

        except Exception as exc:
            self._log.warning(
                "drive_folder_resolution_failed",
                error=str(exc),
                fallback=parent_id,
            )
            return parent_id

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(
        self,
        local_path: str | Path,
        remote_name: str,
        mime_type: str = "video/x-matroska",
        video_id: str = "",
        subfolder_name: str = "",
    ) -> RemoteFileInfo:
        """Upload *local_path* to Google Drive using a resumable chunked transfer.

        Returns RemoteFileInfo. Raises DriveUploadError on failure.
        """
        local_path = Path(local_path)
        if not local_path.exists():
            raise DriveUploadError(f"Local file not found: {local_path}")

        local_size = local_path.stat().st_size
        chunk_bytes = self.config.chunk_size_mb * 1024 * 1024
        log = get_logger(__name__, video_id=video_id)

        # Resolve target folder
        target_folder_id = self.config.folder_id
        if subfolder_name and target_folder_id:
            target_folder_id = self.get_or_create_subfolder(target_folder_id, subfolder_name)

        log.info(
            "drive_upload_starting",
            file=remote_name,
            size_bytes=local_size,
            folder_id=target_folder_id,
        )

        service = self._get_service()

        file_metadata: dict = {"name": remote_name}
        if target_folder_id:
            file_metadata["parents"] = [target_folder_id]
        if self.config.shared_drive_id:
            file_metadata["driveId"] = self.config.shared_drive_id

        media = MediaFileUpload(
            str(local_path),
            mimetype=mime_type,
            chunksize=chunk_bytes,
            resumable=True,
        )

        request = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id,name,size",
            supportsAllDrives=True,
        )

        # Execute with automatic retry on transient errors
        response = None
        attempt = 0
        delay = 5.0

        while response is None:
            attempt += 1
            try:
                status, response = request.next_chunk()
                if status:
                    log.debug("drive_upload_progress", pct=int(status.progress() * 100))
            except HttpError as exc:
                code = exc.resp.status
                if code in _RETRYABLE_CODES:
                    log.warning("drive_upload_http_error", code=code, attempt=attempt, delay=delay)
                    time.sleep(delay)
                    delay = min(delay * 2, 300)
                else:
                    raise DriveUploadError(f"HTTP {code} during upload: {exc}") from exc
            except Exception as exc:
                if attempt > 10:
                    raise DriveUploadError(f"Upload failed after {attempt} attempts: {exc}") from exc
                log.warning("drive_upload_error", error=str(exc), attempt=attempt, delay=delay)
                time.sleep(delay)
                delay = min(delay * 2, 300)

        file_id = response.get("id", "")
        remote_size = int(response.get("size", 0) or 0)

        log.info("drive_upload_completed", file_id=file_id, remote_size=remote_size)

        return RemoteFileInfo(
            file_id=file_id,
            name=remote_name,
            size=remote_size,
            folder_id=target_folder_id,
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_file(self, file_id: str, expected_size: int) -> bool:
        """Return True if the remote file exists and matches *expected_size*."""
        try:
            service = self._get_service()
            info = service.files().get(
                fileId=file_id,
                fields="id,size",
                supportsAllDrives=True,
            ).execute()
            remote_size = int(info.get("size", 0) or 0)
            if remote_size != expected_size:
                self._log.warning(
                    "drive_size_mismatch",
                    file_id=file_id,
                    expected=expected_size,
                    actual=remote_size,
                )
                return False
            return True
        except HttpError as exc:
            if exc.resp.status == 404:
                return False
            raise DriveError(f"Failed to verify file {file_id}: {exc}") from exc
        except Exception as exc:
            raise DriveError(f"Failed to verify file {file_id}: {exc}") from exc
