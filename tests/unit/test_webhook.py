"""Unit tests for webhook payload building and retry logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.webhook import WebhookClient, build_webhook_payload


def make_recording() -> Recording:
    r = Recording()
    r.id = 1
    r.youtube_video_id = "abc123"
    r.channel_id = "nasa"
    r.channel_name = "NASA"
    r.channel_url = "https://www.youtube.com/@NASA/live"
    r.youtube_url = "https://www.youtube.com/watch?v=abc123"
    r.title = "NASA Live Stream"
    r.started_at = "2026-09-04T10:00:00Z"
    r.ended_at = "2026-09-04T12:00:00Z"
    r.duration_seconds = 7200.0
    r.local_path = "/data/working/nasa/abc123/nasa_2026-09-04_abc123_NASA Live Stream.mkv"
    r.local_size_bytes = 1_234_567_890
    r.container = "matroska"
    r.video_codec = "vp9"
    r.audio_codec = "opus"
    r.width = 1920
    r.height = 1080
    r.fps = 30.0
    r.drive_file_id = "drive_file_xyz"
    r.drive_folder_id = "drive_folder_abc"
    r.drive_size_bytes = 1_234_567_890
    r.media_verified = True
    r.drive_verified = True
    r.webhook_sent = False
    r.status = RecordingStatus.UPLOADED
    return r


def make_config(enabled: bool = True, url: str = "https://example.com/webhook"):
    config = MagicMock()
    config.webhook.enabled = enabled
    config.webhook.url = url
    config.webhook.timeout_seconds = 5
    config.webhook.max_attempts = 3
    config.retry.initial_delay_seconds = 0.01  # Fast for tests
    config.retry.multiplier = 2.0
    config.retry.max_delay_seconds = 0.1
    config.retry.jitter = False
    return config


class TestBuildWebhookPayload:
    def test_contains_required_fields(self):
        r = make_recording()
        payload = build_webhook_payload(r)

        assert payload["event"] == "youtube_live_recorded"
        assert payload["youtube"]["video_id"] == "abc123"
        assert payload["youtube"]["channel"] == "NASA"
        assert payload["youtube"]["title"] == "NASA Live Stream"
        assert payload["file"]["video_codec"] == "vp9"
        assert payload["file"]["audio_codec"] == "opus"
        assert payload["file"]["width"] == 1920
        assert payload["file"]["height"] == 1080
        assert payload["google_drive"]["file_id"] == "drive_file_xyz"
        assert payload["verification"]["ffprobe_valid"] is True
        assert payload["verification"]["drive_verified"] is True

    def test_no_credentials_in_payload(self):
        r = make_recording()
        payload = build_webhook_payload(r)
        payload_str = str(payload)
        # Ensure no common credential-like field names
        assert "password" not in payload_str.lower()
        assert "token" not in payload_str.lower()
        assert "secret" not in payload_str.lower()

    def test_filename_extracted_from_path(self):
        r = make_recording()
        payload = build_webhook_payload(r)
        assert "nasa_2026-09-04_abc123" in payload["file"]["name"]

    def test_discord_and_slack_fields(self):
        r = make_recording()
        payload = build_webhook_payload(r)
        # Discord: embed-only, content text omitted
        assert "content" not in payload
        assert "embeds" in payload and len(payload["embeds"]) == 1
        embed = payload["embeds"][0]
        assert embed["title"] == "NASA Live Stream"
        # Discord thumbnail image
        assert embed["image"]["url"] == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
        # Slack text fallback
        assert "text" in payload and payload["text"]
        # Field values are formatted in backticks
        channel_field = next(f for f in embed["fields"] if f["name"] == "Channel")
        assert channel_field["value"] == "`NASA`"
        duration_field = next(f for f in embed["fields"] if f["name"] == "Duration")
        assert duration_field["value"] == "`02:00:00`"
        # Check Drive link is in fields
        drive_field = next(f for f in embed["fields"] if f["name"] == "Google Drive")
        assert "drive_file_xyz" in drive_field["value"]
        assert "[`Open in Google Drive`]" in drive_field["value"]


class TestWebhookClient:
    def test_success_on_200(self):
        r = make_recording()
        config = make_config()
        db = MagicMock()

        with patch("yt_live_archiver.webhook.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            client = WebhookClient(config, db)
            result = client.send(r)

        assert result is True
        assert r.webhook_sent is True

    def test_disabled_webhook_marks_as_sent(self):
        r = make_recording()
        config = make_config(enabled=False)
        db = MagicMock()

        client = WebhookClient(config, db)
        result = client.send(r)

        assert result is True
        assert r.webhook_sent is True

    def test_retries_on_500(self):
        r = make_recording()
        config = make_config()
        config.webhook.max_attempts = 3
        db = MagicMock()

        call_count = 0
        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 500 if call_count < 3 else 200
            return resp

        with patch("yt_live_archiver.webhook.httpx.post", side_effect=mock_post):
            client = WebhookClient(config, db)
            result = client.send(r)

        assert result is True
        assert call_count == 3

    def test_permanent_failure_on_4xx(self):
        r = make_recording()
        config = make_config()
        db = MagicMock()

        with patch("yt_live_archiver.webhook.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.text = "Forbidden"
            mock_post.return_value = mock_response

            client = WebhookClient(config, db)
            result = client.send(r)

        # 403 is permanent — should not retry
        assert result is False
        assert mock_post.call_count == 1

    def test_already_sent_skips_delivery(self):
        r = make_recording()
        r.webhook_sent = True  # Already sent
        config = make_config()
        db = MagicMock()

        with patch("yt_live_archiver.webhook.httpx.post") as mock_post:
            client = WebhookClient(config, db)
            result = client.send(r)

        assert result is True
        mock_post.assert_not_called()
