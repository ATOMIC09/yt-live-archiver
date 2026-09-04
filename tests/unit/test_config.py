"""Unit tests for configuration loading and validation."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from yt_live_archiver.config import AppConfig, ConfigError, load_config


MINIMAL_VALID_CONFIG = {
    "channels": [
        {
            "id": "test_channel",
            "name": "Test Channel",
            "url": "https://www.youtube.com/@TestChannel/live",
            "enabled": True,
        }
    ],
    "google_drive": {
        "enabled": False,  # Disabled so we don't need a real folder_id
    },
    "webhook": {
        "enabled": False,  # Disabled so we don't need a real URL
    },
}


def write_config(data: dict) -> Path:
    """Write a config dict to a temp file and return the path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    )
    yaml.dump(data, f)
    f.close()
    return Path(f.name)


class TestLoadConfig:
    def test_minimal_valid_config(self):
        path = write_config(MINIMAL_VALID_CONFIG)
        try:
            cfg = load_config(str(path))
            assert isinstance(cfg, AppConfig)
            assert len(cfg.channels) == 1
            assert cfg.channels[0].id == "test_channel"
        finally:
            path.unlink()

    def test_missing_file_raises(self):
        with pytest.raises(ConfigError, match="not found"):
            load_config("/nonexistent/path/config.yaml")

    def test_invalid_yaml_raises(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        )
        f.write("invalid: yaml: [\n")
        f.close()
        try:
            with pytest.raises(ConfigError, match="parse"):
                load_config(f.name)
        finally:
            Path(f.name).unlink()

    def test_no_channels_raises(self):
        data = {**MINIMAL_VALID_CONFIG, "channels": []}
        path = write_config(data)
        try:
            with pytest.raises(ConfigError, match="No channels"):
                load_config(str(path))
        finally:
            path.unlink()

    def test_channel_missing_id_raises(self):
        data = {
            **MINIMAL_VALID_CONFIG,
            "channels": [{"name": "Test", "url": "https://www.youtube.com/@Test/live"}],
        }
        path = write_config(data)
        try:
            with pytest.raises(ConfigError, match="missing"):
                load_config(str(path))
        finally:
            path.unlink()

    def test_channel_missing_url_raises(self):
        data = {
            **MINIMAL_VALID_CONFIG,
            "channels": [{"id": "test", "name": "Test"}],
        }
        path = write_config(data)
        try:
            with pytest.raises(ConfigError, match="missing"):
                load_config(str(path))
        finally:
            path.unlink()

    def test_drive_enabled_without_folder_id_raises(self):
        data = {
            **MINIMAL_VALID_CONFIG,
            "google_drive": {"enabled": True, "folder_id": ""},
            "webhook": {"enabled": False},
        }
        path = write_config(data)
        try:
            with pytest.raises(ConfigError, match="folder_id"):
                load_config(str(path))
        finally:
            path.unlink()

    def test_webhook_enabled_without_url_raises(self):
        data = {
            **MINIMAL_VALID_CONFIG,
            "google_drive": {"enabled": False},
            "webhook": {"enabled": True, "url": ""},
        }
        path = write_config(data)
        try:
            with pytest.raises(ConfigError, match="webhook.url"):
                load_config(str(path))
        finally:
            path.unlink()

    def test_env_override_webhook_url(self, monkeypatch):
        data = {
            **MINIMAL_VALID_CONFIG,
            "webhook": {"enabled": True, "url": ""},
            "google_drive": {"enabled": False},
        }
        path = write_config(data)
        monkeypatch.setenv("WEBHOOK_URL", "https://example.com/webhook")
        try:
            cfg = load_config(str(path))
            assert cfg.webhook.url == "https://example.com/webhook"
        finally:
            path.unlink()

    def test_default_values(self):
        path = write_config(MINIMAL_VALID_CONFIG)
        try:
            cfg = load_config(str(path))
            assert cfg.youtube.poll_interval_seconds == 30
            assert cfg.youtube.live_from_start is True
            assert cfg.recording.container == "mkv"
            assert cfg.verification.require_video is True
            assert cfg.verification.require_audio is True
        finally:
            path.unlink()
