"""Unit tests for setup_wizard.py configuration writer and utilities."""

import sys
from pathlib import Path

# Add scripts to sys.path for test imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from setup_wizard import sanitize_channel_id, write_configuration

from yt_live_archiver.config import load_config


def test_sanitize_channel_id():
    assert sanitize_channel_id("https://www.youtube.com/@NASA") == "nasa"
    assert sanitize_channel_id("https://www.youtube.com/@SpaceX/live") == "spacex"
    assert sanitize_channel_id("https://www.youtube.com/channel/UC123-abc_xyz") == "channel"
    assert sanitize_channel_id("@My_Cool_Channel") == "my_cool_channel"


def test_write_configuration_complete(tmp_path: Path):
    # Setup test install directory
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)

    # Copy example config as base
    template_src = Path(__file__).parent.parent.parent / "config" / "config.example.yaml"
    (config_dir / "config.example.yaml").write_text(
        template_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    channels = [
        {
            "id": "nasa",
            "name": "NASA",
            "url": "https://www.youtube.com/@NASA/live",
            "enabled": True,
        },
        {
            "id": "spacex",
            "name": "SpaceX",
            "url": "https://www.youtube.com/@SpaceX/live",
            "enabled": True,
        },
    ]
    drive_cfg = {
        "enabled": True,
        "credentials_file": "/config/token.json",
        "folder_id": "test_folder_123",
        "shared_drive_id": "",
    }
    webhook_cfg = {
        "enabled": True,
        "url": "https://discord.com/api/webhooks/123/abc",
    }

    write_configuration(tmp_path, channels, drive_cfg, webhook_cfg)

    config_yaml = config_dir / "config.yaml"
    assert config_yaml.exists()

    # Load and validate with official AppConfig loader
    cfg = load_config(str(config_yaml))
    assert len(cfg.channels) == 2
    assert cfg.channels[0].id == "nasa"
    assert cfg.channels[0].url == "https://www.youtube.com/@NASA/live"
    assert cfg.channels[1].id == "spacex"
    assert cfg.channels[1].url == "https://www.youtube.com/@SpaceX/live"

    assert cfg.google_drive.enabled is True
    assert cfg.google_drive.credentials_file == "/config/token.json"
    assert cfg.google_drive.folder_id == "test_folder_123"

    assert cfg.webhook.enabled is True
    assert cfg.webhook.url == "https://discord.com/api/webhooks/123/abc"

    env_file = tmp_path / ".env"
    assert env_file.exists()
    env_content = env_file.read_text(encoding="utf-8")
    assert "WEBHOOK_URL=https://discord.com/api/webhooks/123/abc" in env_content


def test_write_configuration_disabled_drive_and_webhook(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)

    template_src = Path(__file__).parent.parent.parent / "config" / "config.example.yaml"
    (config_dir / "config.example.yaml").write_text(
        template_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    channels = [
        {
            "id": "single_channel",
            "name": "Single",
            "url": "https://www.youtube.com/@Single/live",
            "enabled": True,
        },
    ]
    drive_cfg = {"enabled": False}
    webhook_cfg = {"enabled": False, "url": ""}

    write_configuration(tmp_path, channels, drive_cfg, webhook_cfg)

    config_yaml = config_dir / "config.yaml"
    assert config_yaml.exists()

    cfg = load_config(str(config_yaml))
    assert len(cfg.channels) == 1
    assert cfg.channels[0].id == "single_channel"
    assert cfg.google_drive.enabled is False
    assert cfg.webhook.enabled is False
