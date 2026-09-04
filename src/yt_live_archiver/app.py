"""
Main application entry point and CLI.

Commands:
  yt-live-archiver                   Start the archiver (default)
  yt-live-archiver --config PATH     Use a specific config file
  yt-live-archiver --healthcheck     Health check (exits 0 = healthy)
  yt-live-archiver --check-config    Validate configuration
  yt-live-archiver --check-deps      Check required dependencies
  yt-live-archiver --recover         Run startup recovery and exit
  yt-live-archiver --version         Print version
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from yt_live_archiver import __version__
from yt_live_archiver.config import AppConfig, ConfigError, load_config
from yt_live_archiver.database import Database
from yt_live_archiver.logging_config import get_logger, setup_logging
from yt_live_archiver.migrations import run_migrations
from yt_live_archiver.models import RecordingStatus
from yt_live_archiver.monitor import MonitorLoop
from yt_live_archiver.processor import Processor
from yt_live_archiver.recorder import Recorder
from yt_live_archiver.recovery import RecoveryManager


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dependency check helpers
# ---------------------------------------------------------------------------


def _check_executable(name: str) -> tuple[bool, str]:
    """Return (found, version_string)."""
    path = shutil.which(name)
    if path is None:
        return False, ""
    try:
        result = subprocess.run(
            [name, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = (result.stdout or result.stderr).strip().splitlines()[0]
        return True, version
    except Exception:
        return True, "(version unknown)"


def check_dependencies() -> bool:
    """Check all required external binaries. Returns True if all present."""
    all_ok = True
    for binary in ("yt-dlp", "ffmpeg", "ffprobe"):
        found, version = _check_executable(binary)
        if found:
            logger.info(f"dependency_ok: {binary} {version}")
        else:
            logger.error(f"dependency_missing: {binary}")
            all_ok = False
    return all_ok


def log_versions() -> None:
    """Log versions of Python, yt-dlp, ffmpeg, ffprobe, and the application."""
    logger.info(
        "app_starting",
        version=__version__,
        python=sys.version.split()[0],
    )
    for binary in ("yt-dlp", "ffmpeg", "ffprobe"):
        found, version = _check_executable(binary)
        if found:
            logger.info(f"{binary}_version", version=version)
        else:
            logger.warning(f"{binary}_not_found")


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


def run_healthcheck(config: AppConfig) -> int:
    """Run healthcheck. Returns 0 if healthy, 1 if unhealthy."""
    ok = True

    # Check database accessible
    db_path = config.application.database
    try:
        db = Database(db_path)
        db.get_all_with_status(RecordingStatus.DISCOVERED)
        logger.info("healthcheck_db_ok", path=db_path)
    except Exception as exc:
        logger.error("healthcheck_db_fail", error=str(exc))
        ok = False

    # Check data directory writable
    data_dir = Path(config.application.data_dir)
    test_file = data_dir / ".healthcheck"
    try:
        test_file.write_text("ok")
        test_file.unlink()
        logger.info("healthcheck_data_dir_ok", path=str(data_dir))
    except Exception as exc:
        logger.error("healthcheck_data_dir_fail", error=str(exc))
        ok = False

    # Check working dir exists
    working_dir = Path(config.recording.working_dir)
    if not working_dir.exists():
        try:
            working_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.error("healthcheck_working_dir_fail", error=str(exc))
            ok = False

    # Check dependencies
    if not check_dependencies():
        ok = False

    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


class Application:
    """Main application class."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.application.database)
        self._stop_event = asyncio.Event()
        self._recorder = Recorder(config)
        self._processor = Processor(config, self.db)
        self._monitor = MonitorLoop(config, self.db)
        self._recovery = RecoveryManager(config, self.db)
        self._log = get_logger(__name__)

    async def run(self) -> None:
        """Main run loop."""
        self._log.info("application_started")

        # Ensure required directories exist
        for d in [
            self.config.application.data_dir,
            self.config.recording.working_dir,
            self.config.recording.failed_dir,
        ]:
            Path(d).mkdir(parents=True, exist_ok=True)

        # Run startup recovery
        await self._run_recovery()

        # Start the monitor loop
        try:
            await self._monitor.run(self._on_live_detected)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log.error("monitor_loop_crashed", error=str(exc))
            raise

        self._log.info("application_stopped")

    async def _run_recovery(self) -> None:
        """Run startup reconciliation and reprocess any recovered recordings."""
        results = await asyncio.get_event_loop().run_in_executor(
            None, self._recovery.reconcile_all
        )

        tasks = []
        for result in results:
            if result.action in {"re_verify", "upload", "webhook", "cleanup"}:
                self._log.info(
                    "recovery_reprocessing",
                    video_id=result.recording.youtube_video_id,
                    action=result.action,
                )
                task = asyncio.create_task(
                    self._processor.reprocess_recording(result.recording),
                    name=f"recover-{result.recording.youtube_video_id}",
                )
                tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _on_live_detected(self, recording) -> None:
        """Callback when monitor detects a new live stream."""
        self._log.info(
            "live_detected_starting_record",
            video_id=recording.youtube_video_id,
            channel=recording.channel_id,
        )

        # Update status to RECORDING
        from yt_live_archiver.state_machine import state_machine
        state_machine.transition(recording, RecordingStatus.RECORDING)
        recording.recording_attempts += 1
        self.db.update_recording(recording)

        # Run recorder in executor (blocking)
        result = await asyncio.get_event_loop().run_in_executor(
            None, self._recorder.record, recording
        )

        # Process result
        await self._processor.handle_recording_result(recording, result)

    def stop(self) -> None:
        """Signal the application to stop gracefully."""
        self._log.info("shutdown_requested")
        self._monitor.stop()
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------


def _install_signal_handlers(app: Application, loop: asyncio.AbstractEventLoop) -> None:
    """Install SIGTERM and SIGINT handlers for graceful shutdown."""
    def _handle_signal(sig_name: str) -> None:
        logger.info(f"signal_received signal={sig_name}")
        app.stop()

    for sig, name in [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")]:
        try:
            loop.add_signal_handler(sig, lambda n=name: _handle_signal(n))
        except (NotImplementedError, AttributeError):
            # Windows doesn't support add_signal_handler for all signals
            signal.signal(sig, lambda s, f, n=name: _handle_signal(n))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="yt-live-archiver",
        description="Automated YouTube livestream archiver with Google Drive upload",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("CONFIG_PATH", "/config/config.yaml"),
        help="Path to config YAML file (default: /config/config.yaml)",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument(
        "--healthcheck",
        action="store_true",
        help="Run health check and exit (0=healthy)",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit",
    )
    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check required dependencies and exit",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Run startup recovery once and exit",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.version:
        print(f"yt-live-archiver {__version__}")
        sys.exit(0)

    if args.check_deps:
        ok = check_dependencies()
        sys.exit(0 if ok else 1)

    # Load configuration
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.check_config:
        print("Configuration is valid.")
        print(f"  Channels: {len(config.channels)}")
        for ch in config.channels:
            status = "enabled" if ch.enabled else "disabled"
            print(f"    - {ch.id} ({ch.name}): {ch.url} [{status}]")
        print(f"  Google Drive: {'enabled' if config.google_drive.enabled else 'disabled'}")
        print(f"  Webhook: {'enabled' if config.webhook.enabled else 'disabled'}")
        sys.exit(0)

    if args.healthcheck:
        exit_code = run_healthcheck(config)
        sys.exit(exit_code)

    # Run migrations
    try:
        run_migrations(config.application.database)
    except Exception as exc:
        logger.error(f"Database migration failed: {exc}")
        sys.exit(1)

    log_versions()

    if args.recover:
        # One-shot recovery
        db = Database(config.application.database)
        recovery = RecoveryManager(config, db)
        results = recovery.reconcile_all()
        print(f"Recovery complete. Processed {len(results)} records.")
        sys.exit(0)

    # Normal operation
    app = Application(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    _install_signal_handlers(app, loop)

    try:
        loop.run_until_complete(app.run())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    except Exception as exc:
        logger.error("application_crashed", error=str(exc))
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
