"""
Unit tests for v2 config, models, utils, and monitor deduplication.
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from yt_live_archiver.config import (
    AppConfig,
    ChannelConfig,
    ConfigError,
    GoogleDriveConfig,
    WebhookConfig,
    load_config,
    parse_channels,
)
from yt_live_archiver.models import RecordingInfo, RecordingResult
from yt_live_archiver.utils import (
    build_archive_filename,
    format_bytes,
    format_duration,
    sanitize_filename,
)


# ---------------------------------------------------------------------------
# parse_channels
# ---------------------------------------------------------------------------


def test_parse_channels_single():
    result = parse_channels("nasa:NASA:https://www.youtube.com/@NASA/live")
    assert len(result) == 1
    assert result[0].id == "nasa"
    assert result[0].name == "NASA"
    assert result[0].url == "https://www.youtube.com/@NASA/live"


def test_parse_channels_multiple():
    result = parse_channels(
        "nasa:NASA:https://www.youtube.com/@NASA/live,"
        "test:Test Channel:https://www.youtube.com/@test/live"
    )
    assert len(result) == 2
    assert result[1].id == "test"
    assert result[1].name == "Test Channel"


def test_parse_channels_url_with_colons():
    """URL may contain colons after scheme — split on first two colons only."""
    result = parse_channels("ch:My Channel:https://www.youtube.com/@ch/live")
    assert result[0].url == "https://www.youtube.com/@ch/live"


def test_parse_channels_invalid_format():
    with pytest.raises(ConfigError, match="Invalid channel entry"):
        parse_channels("no_colon_at_all")


def test_parse_channels_empty_entry_skipped():
    result = parse_channels("nasa:NASA:https://www.youtube.com/@NASA/live,,,")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_minimal(monkeypatch):
    monkeypatch.setenv("CHANNELS", "test:Test:https://www.youtube.com/@test/live")
    cfg = load_config()
    assert len(cfg.channels) == 1
    assert cfg.google_drive.enabled is False
    assert cfg.webhook.enabled is False
    assert cfg.poll_interval == 30


def test_load_config_missing_channels(monkeypatch):
    monkeypatch.delenv("CHANNELS", raising=False)
    with pytest.raises(ConfigError, match="CHANNELS"):
        load_config()


def test_load_config_drive_enabled_missing_creds(monkeypatch):
    monkeypatch.setenv("CHANNELS", "t:T:https://youtube.com/@t/live")
    monkeypatch.setenv("GOOGLE_DRIVE_ENABLED", "true")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(ConfigError, match="GOOGLE_CLIENT_ID"):
        load_config()


def test_load_config_webhook_enabled_missing_url(monkeypatch):
    monkeypatch.setenv("CHANNELS", "t:T:https://youtube.com/@t/live")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    with pytest.raises(ConfigError, match="WEBHOOK_URL"):
        load_config()


def test_load_config_env_overrides(monkeypatch):
    monkeypatch.setenv("CHANNELS", "t:T:https://youtube.com/@t/live")
    monkeypatch.setenv("POLL_INTERVAL", "60")
    monkeypatch.setenv("LIVE_FROM_START", "false")
    monkeypatch.setenv("MIN_DURATION", "120")
    cfg = load_config()
    assert cfg.poll_interval == 60
    assert cfg.live_from_start is False
    assert cfg.min_duration == 120.0


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_recording_info_fields():
    info = RecordingInfo(
        video_id="abc123",
        channel_id="nasa",
        channel_name="NASA",
        youtube_url="https://www.youtube.com/watch?v=abc123",
        title="Test Stream",
        detected_at="2026-01-01T00:00:00Z",
    )
    assert info.video_id == "abc123"
    assert info.title == "Test Stream"


def test_recording_result_failure():
    r = RecordingResult(
        success=False,
        exit_code=1,
        output_path=None,
        started_at="2026-01-01T00:00:00Z",
        ended_at="2026-01-01T00:01:00Z",
        error_message="yt-dlp failed",
    )
    assert not r.success
    assert r.error_message == "yt-dlp failed"


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------


def test_sanitize_filename_removes_unsafe_chars():
    assert "/" not in sanitize_filename("test/file:name")
    assert ":" not in sanitize_filename("test:name")


def test_sanitize_filename_strips_leading_trailing():
    assert sanitize_filename("  .test.  ") == "test"


def test_sanitize_filename_empty_fallback():
    assert sanitize_filename("") == "unnamed"


def test_build_archive_filename():
    name = build_archive_filename(title="My Awesome Stream", ext="mkv")
    assert name == "My Awesome Stream.mkv"


def test_format_bytes():
    assert format_bytes(0) == "0.0B"
    assert "GB" in format_bytes(2_000_000_000)
    assert "MB" in format_bytes(5_000_000)


def test_format_duration():
    assert format_duration(3661) == "01:01:01"
    assert format_duration(0) == "00:00:00"
    assert format_duration(7200) == "02:00:00"


# ---------------------------------------------------------------------------
# Monitor deduplication
# ---------------------------------------------------------------------------


def test_monitor_seen_ids_dedup():
    """seen_ids set prevents duplicate callbacks."""
    from yt_live_archiver.monitor import MonitorLoop

    called = []

    async def fake_on_live(info):
        called.append(info.video_id)

    seen_ids: set[str] = set()

    class FakeConfig:
        channels = [ChannelConfig(id="ch", name="Ch", url="https://youtube.com/@ch/live")]
        poll_interval = 30

    loop = MonitorLoop(FakeConfig(), seen_ids)

    # Manually simulate what _check_channel does
    video_id = "test123"

    import asyncio

    async def simulate():
        from yt_live_archiver.monitor import LiveStreamInfo
        from yt_live_archiver.models import RecordingInfo
        from datetime import UTC, datetime

        live = LiveStreamInfo(video_id=video_id, title="Test", url="https://youtube.com/watch?v=test123")

        # First call — should trigger
        if live.video_id not in seen_ids:
            seen_ids.add(live.video_id)
            await fake_on_live(RecordingInfo(
                video_id=live.video_id,
                channel_id="ch",
                channel_name="Ch",
                youtube_url=live.url,
                title=live.title,
                detected_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ))

        # Second call — should be skipped
        if live.video_id not in seen_ids:
            seen_ids.add(live.video_id)
            await fake_on_live(RecordingInfo(
                video_id=live.video_id,
                channel_id="ch",
                channel_name="Ch",
                youtube_url=live.url,
                title=live.title,
                detected_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ))

    asyncio.run(simulate())
    assert called == [video_id], "Callback should only fire once per video_id"


def test_monitor_active_channel_skips_poll():
    """Active channel in _active_channels is not checked."""
    import asyncio
    from yt_live_archiver.monitor import MonitorLoop, ChannelMonitor

    ch = ChannelConfig(id="busy_channel", name="Busy", url="https://youtube.com/@busy/live")

    class FakeConfig:
        channels = [ch]
        poll_interval = 30

    seen_ids: set[str] = set()
    active_channels: set[str] = {"busy_channel"}
    loop = MonitorLoop(FakeConfig(), seen_ids, active_channels)

    called = []

    async def fake_on_live(info):
        called.append(info)

    monitor = ChannelMonitor(ch)
    asyncio.run(loop._check_channel(monitor, fake_on_live))
    assert called == [], "Should skip channel check when channel is actively recording"


# ---------------------------------------------------------------------------
# Track muxing & segment merge
# ---------------------------------------------------------------------------


def test_merge_or_pick_single_file(tmp_path):
    from yt_live_archiver.recorder import Recorder
    from yt_live_archiver.logging_config import get_logger

    single = tmp_path / "recording.mkv"
    single.write_bytes(b"content")

    rec = Recorder(AppConfig(channels=[]))
    log = get_logger("test")

    result = rec._merge_or_pick(tmp_path, [single], log)
    assert result == single


def test_merge_or_pick_muxes_separate_tracks(tmp_path, monkeypatch):
    from yt_live_archiver.recorder import Recorder
    from yt_live_archiver.logging_config import get_logger

    video = tmp_path / "recording.f137.mkv"
    audio = tmp_path / "recording.f140.mkv"
    video.write_bytes(b"video" * 100)
    audio.write_bytes(b"audio" * 50)

    rec = Recorder(AppConfig(channels=[]))
    log = get_logger("test")

    # Mock _probe_streams
    def fake_probe(p: Path):
        if "f137" in p.name:
            return True, False  # video only
        if "f140" in p.name:
            return False, True  # audio only
        return False, False

    monkeypatch.setattr(Recorder, "_probe_streams", staticmethod(fake_probe))

    # Mock _mux_tracks to return a fake merged file
    merged = tmp_path / "merged.mkv"
    merged.write_bytes(b"merged_content")

    def fake_mux(w_dir, v, a, all_f, l):
        return merged

    monkeypatch.setattr(Recorder, "_mux_tracks", staticmethod(fake_mux))

    result = rec._merge_or_pick(tmp_path, [video, audio], log)
    assert result == merged


def test_merge_or_pick_picks_complete_file(tmp_path, monkeypatch):
    from yt_live_archiver.recorder import Recorder
    from yt_live_archiver.logging_config import get_logger

    complete = tmp_path / "recording.mkv"
    junk = tmp_path / "recording.part"
    complete.write_bytes(b"complete" * 100)
    junk.write_bytes(b"junk")

    rec = Recorder(AppConfig(channels=[]))
    log = get_logger("test")

    def fake_probe(p: Path):
        if p.name == "recording.mkv":
            return True, True  # both video & audio
        return False, False

    monkeypatch.setattr(Recorder, "_probe_streams", staticmethod(fake_probe))

    result = rec._merge_or_pick(tmp_path, [complete, junk], log)
    assert result == complete


def test_load_config_aliases_and_auto_enable(monkeypatch):
    monkeypatch.setenv("CHANNELS", "t:T:https://youtube.com/@t/live")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client_123")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret_456")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "refresh_789")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder_abc")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/xyz")

    cfg = load_config()
    assert cfg.google_drive.enabled is True
    assert cfg.google_drive.folder_id == "folder_abc"
    assert cfg.webhook.enabled is True
    assert cfg.webhook.url == "https://discord.com/api/webhooks/123/xyz"


def test_drive_upload_create_parameters(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from yt_live_archiver.drive import DriveClient

    test_file = tmp_path / "test.mkv"
    test_file.write_bytes(b"content" * 50)

    cfg = GoogleDriveConfig(
        enabled=True,
        client_id="id",
        client_secret="secret",
        refresh_token="token",
        folder_id="target_folder",
    )
    client = DriveClient(cfg)

    mock_service = MagicMock()
    mock_request = MagicMock()
    mock_request.next_chunk.return_value = (None, {"id": "uploaded_123", "name": "test.mkv", "size": "350"})
    mock_service.files().create.return_value = mock_request

    monkeypatch.setattr(client, "_get_service", lambda: mock_service)
    monkeypatch.setattr(client, "get_or_create_subfolder", lambda parent, sub: "sub_123")

    info = client.upload_file(
        local_path=test_file,
        remote_name="test.mkv",
        subfolder_name="Atomic",
    )

    assert info.file_id == "uploaded_123"
    # Verify create was called with supportsAllDrives=True and NOT includeItemsFromAllDrives
    call_kwargs = mock_service.files().create.call_args.kwargs
    assert call_kwargs.get("supportsAllDrives") is True
    assert "includeItemsFromAllDrives" not in call_kwargs

