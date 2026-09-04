"""
Structured logging configuration.

Logs to stdout in a consistent format suitable for Docker log collection.
Format: ISO-8601 timestamp  LEVEL  logger_name  message  [key=value ...]
"""

from __future__ import annotations

import logging
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class _ContextFilter(logging.Filter):
    """Adds contextual key=value pairs appended by StructuredLogger.bind()."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "_extra_ctx"):
            record._extra_ctx = {}  # type: ignore[attr-defined]
        return True


def _format_ctx(record: logging.LogRecord) -> str:
    ctx = getattr(record, "_extra_ctx", {})
    if not ctx:
        return ""
    parts = " ".join(f"{k}={v}" for k, v in ctx.items())
    return "  " + parts


class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        ctx = _format_ctx(record)
        return base + ctx


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging to stdout with the structured formatter."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    handler.setFormatter(_StructuredFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    for name in ("googleapiclient", "google.auth", "urllib3", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Bound logger helper
# ---------------------------------------------------------------------------


class BoundLogger:
    """A wrapper around a standard Logger that carries extra context fields.

    Usage:
        log = BoundLogger(logging.getLogger(__name__), video_id="abc123")
        log.info("recording_started")
        log.error("upload_failed", error=str(e))
    """

    def __init__(self, logger: logging.Logger, **ctx: object) -> None:
        self._logger = logger
        self._ctx = dict(ctx)

    def bind(self, **extra: object) -> "BoundLogger":
        """Return a new BoundLogger with additional context."""
        merged = {**self._ctx, **extra}
        return BoundLogger(self._logger, **merged)

    def _log(self, level: int, msg: str, **extra: object) -> None:
        ctx = {**self._ctx, **extra}
        self._logger.log(level, msg, extra={"_extra_ctx": ctx})

    def debug(self, msg: str, **extra: object) -> None:
        self._log(logging.DEBUG, msg, **extra)

    def info(self, msg: str, **extra: object) -> None:
        self._log(logging.INFO, msg, **extra)

    def warning(self, msg: str, **extra: object) -> None:
        self._log(logging.WARNING, msg, **extra)

    def error(self, msg: str, **extra: object) -> None:
        self._log(logging.ERROR, msg, **extra)

    def exception(self, msg: str, **extra: object) -> None:
        ctx = {**self._ctx, **extra}
        self._logger.exception(msg, extra={"_extra_ctx": ctx})


def get_logger(name: str, **ctx: object) -> BoundLogger:
    """Get a BoundLogger with optional initial context."""
    return BoundLogger(logging.getLogger(name), **ctx)
