"""
Configuration loader and validator.

Reads /config/config.yaml (or the path provided via --config).
Environment variables can override certain settings via .env / Docker env.
All container-internal paths are used directly from the config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Sub-configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ApplicationConfig:
    data_dir: str = "/data"
    database: str = "/data/archive.db"


@dataclass
class YouTubeConfig:
    poll_interval_seconds: int = 30
    wait_for_video_seconds: int = 300
    live_from_start: bool = True


@dataclass
class RecordingConfig:
    working_dir: str = "/data/working"
    failed_dir: str = "/data/failed"
    format: str = "bv*[vcodec^=vp9]+ba/bv+ba/best"
    container: str = "mkv"


@dataclass
class VerificationConfig:
    require_video: bool = True
    require_audio: bool = True
    run_decode_test: bool = True
    minimum_duration_seconds: float = 30.0


@dataclass
class ProcessingConfig:
    max_parallel_uploads: int = 2


@dataclass
class GoogleDriveConfig:
    enabled: bool = True
    credentials_file: str = "/credentials/google-credentials.json"
    shared_drive_id: str = ""
    folder_id: str = ""
    chunk_size_mb: int = 64


@dataclass
class WebhookConfig:
    enabled: bool = True
    url: str = ""
    timeout_seconds: int = 15
    max_attempts: int = 10


@dataclass
class CleanupConfig:
    require_webhook: bool = True


@dataclass
class ChannelConfig:
    id: str = ""
    name: str = ""
    url: str = ""
    enabled: bool = True


@dataclass
class RetryConfig:
    initial_delay_seconds: float = 5.0
    max_delay_seconds: float = 300.0
    multiplier: float = 2.0
    jitter: bool = True


@dataclass
class AppConfig:
    application: ApplicationConfig = field(default_factory=ApplicationConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    google_drive: GoogleDriveConfig = field(default_factory=GoogleDriveConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    channels: list[ChannelConfig] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = "/config/config.yaml"


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load and validate configuration from a YAML file.

    Falls back to environment variables for the config path.
    Raises ConfigError for missing or invalid configuration.
    """
    path = config_path or os.environ.get("CONFIG_PATH", _DEFAULT_CONFIG_PATH)

    config_file = Path(path)
    if not config_file.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse configuration YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Configuration file must be a YAML mapping at the top level")

    cfg = _parse_config(raw)
    _apply_env_overrides(cfg)
    _validate(cfg)
    return cfg


def _parse_config(raw: dict) -> AppConfig:
    """Parse raw YAML dict into AppConfig dataclass tree."""
    cfg = AppConfig()

    if "application" in raw:
        a = raw["application"]
        cfg.application.data_dir = a.get("data_dir", cfg.application.data_dir)
        cfg.application.database = a.get("database", cfg.application.database)

    if "youtube" in raw:
        y = raw["youtube"]
        cfg.youtube.poll_interval_seconds = int(
            y.get("poll_interval_seconds", cfg.youtube.poll_interval_seconds)
        )
        cfg.youtube.wait_for_video_seconds = int(
            y.get("wait_for_video_seconds", cfg.youtube.wait_for_video_seconds)
        )
        cfg.youtube.live_from_start = bool(
            y.get("live_from_start", cfg.youtube.live_from_start)
        )

    if "recording" in raw:
        r = raw["recording"]
        cfg.recording.working_dir = r.get("working_dir", cfg.recording.working_dir)
        cfg.recording.failed_dir = r.get("failed_dir", cfg.recording.failed_dir)
        cfg.recording.format = r.get("format", cfg.recording.format)
        cfg.recording.container = r.get("container", cfg.recording.container)

    if "verification" in raw:
        v = raw["verification"]
        cfg.verification.require_video = bool(
            v.get("require_video", cfg.verification.require_video)
        )
        cfg.verification.require_audio = bool(
            v.get("require_audio", cfg.verification.require_audio)
        )
        cfg.verification.run_decode_test = bool(
            v.get("run_decode_test", cfg.verification.run_decode_test)
        )
        cfg.verification.minimum_duration_seconds = float(
            v.get("minimum_duration_seconds", cfg.verification.minimum_duration_seconds)
        )

    if "processing" in raw:
        p = raw["processing"]
        cfg.processing.max_parallel_uploads = int(
            p.get("max_parallel_uploads", cfg.processing.max_parallel_uploads)
        )

    if "google_drive" in raw:
        gd = raw["google_drive"]
        cfg.google_drive.enabled = bool(gd.get("enabled", cfg.google_drive.enabled))
        cfg.google_drive.credentials_file = gd.get(
            "credentials_file", cfg.google_drive.credentials_file
        )
        cfg.google_drive.shared_drive_id = gd.get(
            "shared_drive_id", cfg.google_drive.shared_drive_id
        )
        cfg.google_drive.folder_id = gd.get("folder_id", cfg.google_drive.folder_id)
        cfg.google_drive.chunk_size_mb = int(
            gd.get("chunk_size_mb", cfg.google_drive.chunk_size_mb)
        )

    if "webhook" in raw:
        wh = raw["webhook"]
        cfg.webhook.enabled = bool(wh.get("enabled", cfg.webhook.enabled))
        cfg.webhook.url = wh.get("url", cfg.webhook.url)
        cfg.webhook.timeout_seconds = int(
            wh.get("timeout_seconds", cfg.webhook.timeout_seconds)
        )
        cfg.webhook.max_attempts = int(wh.get("max_attempts", cfg.webhook.max_attempts))

    if "cleanup" in raw:
        cl = raw["cleanup"]
        cfg.cleanup.require_webhook = bool(
            cl.get("require_webhook", cfg.cleanup.require_webhook)
        )

    if "retry" in raw:
        rt = raw["retry"]
        cfg.retry.initial_delay_seconds = float(
            rt.get("initial_delay_seconds", cfg.retry.initial_delay_seconds)
        )
        cfg.retry.max_delay_seconds = float(
            rt.get("max_delay_seconds", cfg.retry.max_delay_seconds)
        )
        cfg.retry.multiplier = float(rt.get("multiplier", cfg.retry.multiplier))
        cfg.retry.jitter = bool(rt.get("jitter", cfg.retry.jitter))

    if raw.get("channels"):
        for ch in raw["channels"]:
            cfg.channels.append(
                ChannelConfig(
                    id=ch.get("id", ""),
                    name=ch.get("name", ""),
                    url=ch.get("url", ""),
                    enabled=bool(ch.get("enabled", True)),
                )
            )

    return cfg


def _apply_env_overrides(cfg: AppConfig) -> None:
    """Apply environment variable overrides.

    Environment variables take precedence over the config file.
    Secrets (webhook URL, credentials path) can be injected this way.
    """
    if url := os.environ.get("WEBHOOK_URL"):
        cfg.webhook.url = url
    if creds := os.environ.get("GOOGLE_CREDENTIALS_FILE"):
        cfg.google_drive.credentials_file = creds
    if drive_id := os.environ.get("GOOGLE_SHARED_DRIVE_ID"):
        cfg.google_drive.shared_drive_id = drive_id
    if folder_id := os.environ.get("GOOGLE_FOLDER_ID"):
        cfg.google_drive.folder_id = folder_id


def _validate(cfg: AppConfig) -> None:
    """Validate the loaded configuration and raise ConfigError for problems."""
    errors: list[str] = []

    if not cfg.channels:
        errors.append("No channels configured. Add at least one channel under 'channels:'")

    for ch in cfg.channels:
        if not ch.id:
            errors.append("A channel is missing a required 'id' field")
        if not ch.url:
            errors.append(f"Channel '{ch.id}' is missing a required 'url' field")

    if cfg.google_drive.enabled:
        if not cfg.google_drive.folder_id:
            errors.append(
                "google_drive.folder_id is required when google_drive.enabled=true"
            )

    if cfg.webhook.enabled and not cfg.webhook.url:
        errors.append("webhook.url is required when webhook.enabled=true")

    if cfg.youtube.poll_interval_seconds < 5:
        errors.append("youtube.poll_interval_seconds must be at least 5")

    if errors:
        msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ConfigError(msg)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when configuration is missing, invalid, or cannot be loaded."""
