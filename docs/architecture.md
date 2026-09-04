# Architecture

## Overview

yt-live-archiver is a **single Docker container** with all dependencies bundled:

```
yt-live-archiver container
├── Python 3.13
├── yt-dlp         (YouTube download)
├── FFmpeg/ffprobe (media verification)
├── SQLite         (state persistence)
└── Google Drive client (upload)
```

## Pipeline

```
YouTube (polled every 30s)
         │
         ▼
┌─────────────────────────┐
│  ChannelMonitor         │  ← Runs per channel, detects live status
│  (yt-dlp metadata mode) │
└────────────┬────────────┘
             │ live detected + DB record created
             ▼
┌─────────────────────────┐
│  Recorder               │  ← yt-dlp subprocess, writes to /data/working
│  (yt-dlp subprocess)    │
└────────────┬────────────┘
             │ yt-dlp exits (stream ended)
             ▼
┌─────────────────────────┐
│  Processor.finalize()   │  ← Check file exists, move to stable path
└────────────┬────────────┘
             ▼
┌─────────────────────────┐
│  MediaVerifier          │  ← ffprobe + FFmpeg decode test
│  (ffprobe + ffmpeg)     │
└────────────┬────────────┘
             │ media_verified = true
             ▼
┌─────────────────────────┐
│  Uploader               │  ← Resumable upload, size verification
│  (Google Drive API)     │
└────────────┬────────────┘
             │ drive_verified = true
             ▼
┌─────────────────────────┐
│  WebhookClient          │  ← HTTP POST with retry
└────────────┬────────────┘
             │ webhook_sent = true
             ▼
┌─────────────────────────┐
│  Cleanup                │  ← 7-check safety deletion
└─────────────────────────┘
```

## State Machine

All status changes go through `StateMachine.transition()`:

```
DISCOVERED → RECORDING → FINALIZING → VERIFYING → VERIFIED
                                                      │
                                               UPLOADING → UPLOADED → NOTIFYING → COMPLETED
```

Failure states: `RECORDING_FAILED`, `VERIFICATION_FAILED`, `UPLOAD_FAILED`, `NOTIFICATION_FAILED`

## Concurrency Model

```
asyncio event loop
│
├── MonitorLoop.run()
│     ├── ChannelMonitor A (run_in_executor)
│     ├── ChannelMonitor B (run_in_executor)
│     └── ...
│
├── Recording A pipeline (create_task)
│     └── All blocking ops → run_in_executor
│
├── Recording B pipeline (create_task)
│     └── ...
│
└── Upload semaphore (max_parallel_uploads=2)
```

Monitors run concurrently. Each recording pipeline runs as an asyncio Task. Blocking subprocess calls (yt-dlp, ffmpeg) run in the thread executor.

## Storage Layout

```
/data/
├── archive.db              ← All recording state (never inside container)
├── working/
│   └── <channel>/<video_id>/
│       └── <filename>.mkv  ← In-progress recordings
├── failed/
│   └── <channel>/<video_id>/
│       └── <filename>.mkv  ← Failed recordings (kept for inspection)
└── metadata/               ← Reserved for future use
```

## Recovery Model

On startup, `CrashRecovery.reconcile_all()` scans all non-terminal recordings and determines the correct re-entry point by comparing:

1. Database state
2. Filesystem state (file exists? size?)
3. Google Drive state (file exists? size match?)
4. Webhook state

Then re-enters the pipeline from the safest valid point.
