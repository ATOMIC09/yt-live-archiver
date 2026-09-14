"""
Configuration loader — environment variables only.

No YAML files, no mounted config volumes.
Every setting has a sensible default so only CHANNELS (and Drive/webhook
credentials when those features are enabled) are truly required.

Quick reference
───────────────
  CHANNELS                 id:Name:https://youtube.com/@handle/live[,...]
  POLL_INTERVAL            30         seconds between polls
  LIVE_FROM_START          true       record from stream beginning
  WAIT_FOR_VIDEO           300        wait for scheduled stream (seconds)
  RECORDING_FORMAT         bv*[vcodec^=vp9]+ba/bv+ba/best
  RECORDING_CONTAINER      mkv
  WORKING_DIR              /data/working
  OUTPUT_DIR               /data/archive
  FAILED_DIR               /data/failed
  MIN_DURATION             30         minimum valid duration (seconds)
  REQUIRE_VIDEO            true
  REQUIRE_AUDIO            true
  DECODE_TEST              true       ffmpeg null-decode integrity check
  GOOGLE_DRIVE_ENABLED     false
  GOOGLE_CLIENT_ID         (required if Drive enabled)
  GOOGLE_CLIENT_SECRET     (required if Drive enabled)
  GOOGLE_REFRESH_TOKEN     (required if Drive enabled)
  GOOGLE_FOLDER_ID         (required if Drive enabled)
  GOOGLE_SHARED_DRIVE_ID   (optional, Workspace Shared Drives only)
  GOOGLE_CHUNK_SIZE_MB     64
  WEBHOOK_ENABLED          false
  WEBHOOK_URL              (required if webhook enabled)
  WEBHOOK_TIMEOUT          15
  WEBHOOK_MAX_ATTEMPTS     10
  LOG_LEVEL                INFO
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass
class ChannelConfig:
    id: str
    name: str
    url: str


@dataclass
class GoogleDriveConfig:
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    folder_id: str = ""
    shared_drive_id: str = ""
    chunk_size_mb: int = 64


@dataclass
class WebhookConfig:
    enabled: bool = False
    url: str = ""
    timeout_seconds: int = 15
    max_attempts: int = 10


@dataclass
class AppConfig:
    channels: list[ChannelConfig]
    working_dir: str = "/data/working"
    output_dir: str = "/data/archive"
    failed_dir: str = "/data/failed"
    poll_interval: int = 30
    live_from_start: bool = True
    wait_for_video: int = 300
    recording_format: str = "bv*[vcodec^=vp9]+ba/bv+ba/best"
    recording_container: str = "mkv"
    min_duration: float = 30.0
    require_video: bool = True
    require_audio: bool = True
    decode_test: bool = True
    google_drive: GoogleDriveConfig = field(default_factory=GoogleDriveConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_bool(key: str, default: bool) -> bool:
    val = _env(key).lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no"):
        return False
    return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key) or default)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Channel parsing
# ---------------------------------------------------------------------------


def parse_channels(env_str: str) -> list[ChannelConfig]:
    """Parse CHANNELS env var: 'id:Name:url[,id2:Name2:url2,...]'

    Example:
        nasa:NASA:https://www.youtube.com/@NASA/live,test:Test:https://youtube.com/@test/live
    """
    channels: list[ChannelConfig] = []
    for raw in env_str.split(","):
        entry = raw.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) < 3:
            raise ConfigError(
                f"Invalid channel entry '{entry}'. "
                "Expected format: id:Display Name:https://youtube.com/@handle/live"
            )
        ch_id, ch_name, ch_url = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not ch_id or not ch_url:
            raise ConfigError(f"Channel entry '{entry}' is missing id or url.")
        channels.append(ChannelConfig(id=ch_id, name=ch_name or ch_id, url=ch_url))
    return channels


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_config() -> AppConfig:
    """Read all settings from environment variables and return AppConfig.

    Raises ConfigError on invalid or missing required values.
    """
    channels_raw = _env("CHANNELS")
    if not channels_raw:
        raise ConfigError(
            "CHANNELS environment variable is required.\n"
            "Example: CHANNELS=nasa:NASA:https://www.youtube.com/@NASA/live"
        )

    channels = parse_channels(channels_raw)

    drive = GoogleDriveConfig(
        enabled=_env_bool("GOOGLE_DRIVE_ENABLED", False),
        client_id=_env("GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_CLIENT_SECRET"),
        refresh_token=_env("GOOGLE_REFRESH_TOKEN"),
        folder_id=_env("GOOGLE_FOLDER_ID"),
        shared_drive_id=_env("GOOGLE_SHARED_DRIVE_ID"),
        chunk_size_mb=_env_int("GOOGLE_CHUNK_SIZE_MB", 64),
    )

    webhook = WebhookConfig(
        enabled=_env_bool("WEBHOOK_ENABLED", False),
        url=_env("WEBHOOK_URL"),
        timeout_seconds=_env_int("WEBHOOK_TIMEOUT", 15),
        max_attempts=_env_int("WEBHOOK_MAX_ATTEMPTS", 10),
    )

    cfg = AppConfig(
        channels=channels,
        working_dir=_env("WORKING_DIR", "/data/working"),
        output_dir=_env("OUTPUT_DIR", "/data/archive"),
        failed_dir=_env("FAILED_DIR", "/data/failed"),
        poll_interval=_env_int("POLL_INTERVAL", 30),
        live_from_start=_env_bool("LIVE_FROM_START", True),
        wait_for_video=_env_int("WAIT_FOR_VIDEO", 300),
        recording_format=_env("RECORDING_FORMAT", "bv*[vcodec^=vp9]+ba/bv+ba/best"),
        recording_container=_env("RECORDING_CONTAINER", "mkv"),
        min_duration=_env_float("MIN_DURATION", 30.0),
        require_video=_env_bool("REQUIRE_VIDEO", True),
        require_audio=_env_bool("REQUIRE_AUDIO", True),
        decode_test=_env_bool("DECODE_TEST", True),
        google_drive=drive,
        webhook=webhook,
    )

    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    errors: list[str] = []

    if not cfg.channels:
        errors.append("No channels configured. Set CHANNELS=id:name:url")

    for ch in cfg.channels:
        if not ch.id:
            errors.append("A channel is missing an 'id'.")
        if not ch.url:
            errors.append(f"Channel '{ch.id}' is missing a 'url'.")

    if cfg.google_drive.enabled:
        if not cfg.google_drive.client_id:
            errors.append("GOOGLE_CLIENT_ID is required when GOOGLE_DRIVE_ENABLED=true")
        if not cfg.google_drive.client_secret:
            errors.append("GOOGLE_CLIENT_SECRET is required when GOOGLE_DRIVE_ENABLED=true")
        if not cfg.google_drive.refresh_token:
            errors.append("GOOGLE_REFRESH_TOKEN is required when GOOGLE_DRIVE_ENABLED=true")
        if not cfg.google_drive.folder_id:
            errors.append("GOOGLE_FOLDER_ID is required when GOOGLE_DRIVE_ENABLED=true")

    if cfg.webhook.enabled and not cfg.webhook.url:
        errors.append("WEBHOOK_URL is required when WEBHOOK_ENABLED=true")

    if cfg.poll_interval < 5:
        errors.append("POLL_INTERVAL must be at least 5 seconds")

    if errors:
        raise ConfigError(
            "Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when the configuration is invalid or incomplete."""
