"""
Main application — streamlined for v2.

No database, no state machine, no startup migrations.

Startup sequence:
  1. Load config from ENV
  2. Ensure working/output/failed directories exist
  3. Run orphan scan (process leftover files from previous runs)
  4. Start monitor loop
  5. For each new live: spawn background task → record → pipeline

In-memory seen_ids set prevents re-recording within the same session.
On restart it's cleared, which is intentional:
  - If the stream is still live → picks it up and records again.
  - If the stream ended → orphan scan handles any leftover file.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from yt_live_archiver import __version__
from yt_live_archiver.config import AppConfig, ConfigError, load_config
from yt_live_archiver.logging_config import get_logger, setup_logging
from yt_live_archiver.models import RecordingInfo, RecordingResult
from yt_live_archiver.monitor import MonitorLoop
from yt_live_archiver.pipeline import read_title_from_file, run_pipeline
from yt_live_archiver.recorder import Recorder

logger = get_logger(__name__)

_MEDIA_EXTENSIONS = {".mkv", ".mp4", ".webm", ".ts", ".m4a", ".ogg"}


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class Application:
    """Main application orchestrator."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._seen_ids: set[str] = set()
        self._recorder = Recorder(config)
        self._monitor = MonitorLoop(config, self._seen_ids)
        self._active_tasks: set[asyncio.Task] = set()
        self._log = get_logger(__name__)

    async def run(self) -> None:
        """Application main loop."""
        self._log.info("application_started", version=__version__, channels=len(self.config.channels))

        # Ensure required directories exist
        for d in [self.config.working_dir, self.config.output_dir, self.config.failed_dir]:
            Path(d).mkdir(parents=True, exist_ok=True)

        # Process any leftover files from previous runs
        await self._scan_orphans()

        # Start monitoring
        try:
            await self._monitor.run(self._on_live_detected)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._log.error("monitor_crashed", error=str(exc))
            raise

        # Wait for in-flight tasks to finish
        if self._active_tasks:
            self._log.info("waiting_for_active_tasks", count=len(self._active_tasks))
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

        self._log.info("application_stopped")

    # ------------------------------------------------------------------
    # Orphan scan
    # ------------------------------------------------------------------

    async def _scan_orphans(self) -> None:
        """Walk WORKING_DIR for leftover files from previous runs and process them.

        Directory structure: WORKING_DIR/{channel_id}/{video_id}/
        Both channel_id and video_id are encoded in the path.
        Stream title is recovered from ffprobe embedded metadata.
        """
        working_dir = Path(self.config.working_dir)
        if not working_dir.exists():
            return

        log = self._log
        channel_by_id = {ch.id: ch for ch in self.config.channels}
        loop = asyncio.get_event_loop()
        found_any = False

        for channel_dir in sorted(working_dir.iterdir()):
            if not channel_dir.is_dir():
                continue
            for video_dir in sorted(channel_dir.iterdir()):
                if not video_dir.is_dir():
                    continue

                media_files = [
                    p for p in video_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in _MEDIA_EXTENSIONS
                ]
                if not media_files:
                    continue

                channel_id = channel_dir.name
                video_id = video_dir.name
                found_any = True

                log.info("orphan_found", channel_id=channel_id, video_id=video_id, files=len(media_files))

                # Prevent the monitor from re-recording this video
                self._seen_ids.add(video_id)

                channel = channel_by_id.get(channel_id)

                # Merge segments if needed
                if len(media_files) > 1:
                    output_path = await loop.run_in_executor(
                        None,
                        lambda files=media_files, d=video_dir: self._recorder._merge_or_pick(d, files, log),
                    )
                else:
                    output_path = media_files[0]

                # Try to read title from embedded metadata
                title = await loop.run_in_executor(None, lambda p=output_path: read_title_from_file(p))
                title = title or video_id

                now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                info = RecordingInfo(
                    video_id=video_id,
                    channel_id=channel_id,
                    channel_name=channel.name if channel else channel_id,
                    youtube_url=f"https://www.youtube.com/watch?v={video_id}",
                    title=title,
                    detected_at=now,
                )
                result = RecordingResult(
                    success=True,
                    exit_code=0,
                    output_path=output_path,
                    started_at=now,
                    ended_at=now,
                )

                await run_pipeline(info, result, self.config)

        if not found_any:
            log.info("orphan_scan_complete_nothing_found")

    # ------------------------------------------------------------------
    # Live detection
    # ------------------------------------------------------------------

    async def _on_live_detected(self, info: RecordingInfo) -> None:
        """Callback from MonitorLoop — spawn a background task per stream."""
        self._log.info(
            "live_detected_starting_record",
            video_id=info.video_id,
            channel=info.channel_id,
            title=info.title,
        )
        task = asyncio.create_task(
            self._record_and_process(info),
            name=f"record-{info.video_id}",
        )
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _record_and_process(self, info: RecordingInfo) -> None:
        """Background task: record then run the pipeline."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._recorder.record, info
            )
            await run_pipeline(info, result, self.config)
        except Exception as exc:
            self._log.error("record_and_process_crashed", error=str(exc), video_id=info.video_id)

    def stop(self) -> None:
        self._log.info("shutdown_requested")
        self._monitor.stop()


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _check_executable(name: str) -> tuple[bool, str]:
    path = shutil.which(name)
    if path is None:
        return False, ""
    try:
        result = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=10)
        version = (result.stdout or result.stderr).strip().splitlines()[0]
        return True, version
    except Exception:
        return True, "(version unknown)"


def check_dependencies() -> bool:
    """Check required external binaries. Returns True if all present."""
    all_ok = True
    for binary in ("yt-dlp", "ffmpeg", "ffprobe"):
        found, version = _check_executable(binary)
        if found:
            logger.info(f"{binary}_ok", version=version)
        else:
            logger.error(f"{binary}_missing")
            all_ok = False
    return all_ok


def log_versions() -> None:
    logger.info("app_starting", version=__version__, python=sys.version.split()[0])
    for binary in ("yt-dlp", "ffmpeg", "ffprobe"):
        found, version = _check_executable(binary)
        if found:
            logger.info(f"{binary}_version", version=version)
        else:
            logger.warning(f"{binary}_not_found")


def run_healthcheck(config: AppConfig) -> int:
    """Return 0 if healthy, 1 if unhealthy."""
    ok = True

    # Working dir writable
    for d in [config.working_dir, config.output_dir]:
        test = Path(d) / ".healthcheck"
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            test.write_text("ok")
            test.unlink()
        except Exception as exc:
            logger.error("healthcheck_dir_fail", path=d, error=str(exc))
            ok = False

    if not check_dependencies():
        ok = False

    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------


def _install_signal_handlers(app: Application, loop: asyncio.AbstractEventLoop) -> None:
    def _handle(sig_name: str) -> None:
        logger.info("signal_received", signal=sig_name)
        app.stop()

    for sig, name in [(signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")]:
        try:
            loop.add_signal_handler(sig, lambda n=name: _handle(n))
        except (NotImplementedError, AttributeError):
            signal.signal(sig, lambda s, f, n=name: _handle(n))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="yt-live-archiver",
        description="Automated YouTube livestream archiver",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("--healthcheck", action="store_true", help="Run health check (0=healthy)")
    parser.add_argument("--check-deps", action="store_true", help="Check required dependencies")
    parser.add_argument("--check-config", action="store_true", help="Validate configuration")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.version:
        print(f"yt-live-archiver {__version__}")
        sys.exit(0)

    if args.check_deps:
        sys.exit(0 if check_dependencies() else 1)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        sys.exit(1)

    if args.check_config:
        print("Configuration is valid.")
        for ch in config.channels:
            print(f"  Channel: {ch.id} ({ch.name}) → {ch.url}")
        print(f"  Google Drive: {'enabled' if config.google_drive.enabled else 'disabled'}")
        print(f"  Webhook:      {'enabled' if config.webhook.enabled else 'disabled'}")
        sys.exit(0)

    if args.healthcheck:
        sys.exit(run_healthcheck(config))

    log_versions()

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
