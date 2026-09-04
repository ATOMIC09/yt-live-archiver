# yt-live-archiver — Agent Implementation Plan

## 1. Project Objective

Build **yt-live-archiver**, a self-contained Dockerized service that continuously monitors configured YouTube channels, records livestreams, validates the resulting media, uploads verified recordings to a Google Drive shared folder, sends a detailed webhook notification, and safely removes the local recording only after all required steps have succeeded.

The application must be designed for unattended, long-running operation.

Primary priorities, in order:

1. **Do not lose recordings.**
2. **Do not treat a corrupt/incomplete recording as valid.**
3. **Do not delete a local recording until the remote copy has been verified.**
4. **Recover automatically from crashes, network failures, API failures, and host reboots.**
5. **Keep deployment independent of the host software environment.**
6. **Make operations observable and easy to troubleshoot.**

---

# 2. Deployment Philosophy

Docker is the **primary and intended deployment method**.

The host machine should only need:

- Docker Engine
- Docker Compose
- storage
- network access

The host must **not** be required to provide:

- Python
- Python packages
- yt-dlp
- FFmpeg
- ffprobe
- Google API libraries

All runtime dependencies must be contained inside the Docker image.

The application should run as **one container**.

Do not create separate containers for:

- yt-dlp
- FFmpeg
- SQLite
- Google Drive
- webhook processing
- monitoring

These are all components of the same application and lifecycle.

---

# 3. High-Level Architecture

```text
                         YouTube
                            |
                            v
                  +--------------------+
                  | Live Monitor        |
                  +----------+---------+
                             |
                        live detected
                             |
                             v
                  +--------------------+
                  | yt-dlp Recorder    |
                  +----------+---------+
                             |
                             v
                       /data/working
                             |
                        stream ends
                             |
                             v
                  +--------------------+
                  | Finalization        |
                  +----------+---------+
                             |
                             v
                  +--------------------+
                  | Media Verification |
                  | ffprobe + ffmpeg   |
                  +----------+---------+
                             |
                       valid recording
                             |
                             v
                  +--------------------+
                  | Google Drive        |
                  | Resumable Upload    |
                  +----------+---------+
                             |
                      remote verified
                             |
                             v
                  +--------------------+
                  | Webhook Notification|
                  +----------+---------+
                             |
                       all complete
                             |
                             v
                  +--------------------+
                  | Local Cleanup       |
                  +--------------------+

          Docker Container
          ┌─────────────────────────────────────────┐
          │ yt-live-archiver                        │
          │                                         │
          │ Python application                      │
          │ yt-dlp                                  │
          │ FFmpeg / ffprobe                        │
          │ SQLite                                  │
          │ Google Drive client                     │
          │ Webhook client                          │
          └─────────────────┬───────────────────────┘
                            │
                         /data
                            │
                            v
                    Persistent host storage
```

---

# 4. Container Model

The complete application runs in one container:

```text
yt-live-archiver
├── Python application
├── yt-dlp
├── FFmpeg
├── ffprobe
└── SQLite
```

The container itself should be considered disposable.

Persistent information must live outside the container.

Recommended host layout:

```text
./
├── data/
│   ├── archive.db
│   ├── working/
│   ├── failed/
│   └── metadata/
│
├── config/
│   └── config.yaml
│
└── credentials/
    └── google-credentials.json
```

Mount these into the container:

```text
./data        -> /data
./config      -> /config:ro
./credentials -> /credentials:ro
```

Do not store persistent application data inside the image filesystem.

---

# 5. Repository Structure

Use the following structure:

```text
yt-live-archiver/
├── src/
│   └── yt_live_archiver/
│       ├── __init__.py
│       ├── __main__.py
│       ├── app.py
│       │
│       ├── config.py
│       ├── logging_config.py
│       │
│       ├── models.py
│       ├── database.py
│       ├── migrations.py
│       ├── state_machine.py
│       │
│       ├── monitor.py
│       ├── recorder.py
│       ├── media.py
│       ├── processor.py
│       │
│       ├── drive.py
│       ├── uploader.py
│       ├── webhook.py
│       │
│       ├── recovery.py
│       ├── cleanup.py
│       └── utils.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── docker/
│   ├── Dockerfile
│   └── entrypoint.sh
│
├── deploy/
│   └── compose.yaml
│
├── config/
│   └── config.example.yaml
│
├── docs/
│   ├── architecture.md
│   ├── installation.md
│   ├── configuration.md
│   ├── google-drive.md
│   ├── operations.md
│   └── troubleshooting.md
│
├── .dockerignore
├── .gitignore
├── .env.example
├── compose.yaml
├── pyproject.toml
├── README.md
└── LICENSE
```

---

# 6. Container Image

The Docker image must contain all runtime dependencies.

Example base:

```dockerfile
FROM python:3.13-slim
```

Install:

- FFmpeg
- required OS libraries
- Python dependencies
- yt-dlp

The exact versions should be deliberately managed rather than depending on whatever happens to be installed on the host.

The image should expose no unnecessary network ports.

This is primarily a background worker.

---

# 7. Docker Image Requirements

The Dockerfile should:

1. Use a small maintained base image.
2. Install required system packages.
3. Install Python dependencies from the project metadata.
4. Install yt-dlp.
5. Copy only necessary application files.
6. Run as a non-root user where practical.
7. Set an explicit entrypoint.
8. Provide a usable healthcheck.
9. Avoid storing runtime state inside the image.
10. Avoid embedding secrets.

The container should be rebuildable from scratch at any time.

---

# 8. Docker Compose

Use Compose as the primary deployment interface.

Example:

```yaml
services:
  yt-live-archiver:
    image: ghcr.io/YOUR_USERNAME/yt-live-archiver:latest
    container_name: yt-live-archiver
    restart: unless-stopped

    env_file:
      - .env

    volumes:
      - ./data:/data
      - ./config:/config:ro
      - ./credentials:/credentials:ro

    environment:
      TZ: UTC
```

The exact image name must be configurable/documented.

Do not require users to build the image locally for normal deployment.

---

# 9. Persistent Storage

Use **one primary persistent ****`/data`**** mount**.

Inside `/data`:

```text
/data/
├── archive.db
├── working/
├── failed/
└── metadata/
```

Configuration and credentials should be separate read-only mounts:

```text
/config
/credentials
```

This gives the project:

- one application container
- one primary persistent data mount
- separate read-only configuration/secret mounts

No additional service-specific containers or volumes are required.

---

# 10. Configuration

Use YAML.

Example:

```yaml
application:
  data_dir: /data
  database: /data/archive.db

youtube:
  poll_interval_seconds: 30
  wait_for_video_seconds: 300
  live_from_start: true

recording:
  working_dir: /data/working
  failed_dir: /data/failed
  format: "bv*[vcodec^=vp9]+ba/bv+ba/best"
  container: mkv

verification:
  require_video: true
  require_audio: true
  run_decode_test: true
  minimum_duration_seconds: 30

processing:
  max_parallel_uploads: 2

google_drive:
  enabled: true
  shared_drive_id: ""
  folder_id: ""
  chunk_size_mb: 64

webhook:
  enabled: true
  timeout_seconds: 15
  max_attempts: 10

cleanup:
  require_webhook: true
```

All paths inside configuration should refer to container paths.

---

# 11. Secrets

Never commit secrets.

Possible sensitive values include:

- Google credentials
- OAuth tokens
- service account credentials
- webhook URLs
- YouTube cookies if ever required

Use:

```text
.env
/credentials/*
```

or Docker secrets where appropriate.

For the first version, a protected credentials bind mount is acceptable.

Example:

```text
./credentials/google-credentials.json
```

mounted as:

```text
/credentials/google-credentials.json:ro
```

Never print credentials to logs.

---

# 12. Channel Configuration

Support multiple channels:

```yaml
channels:
  - id: nasa
    name: NASA
    url: https://www.youtube.com/@NASA/live
    enabled: true

  - id: example
    name: Example
    url: https://www.youtube.com/@example/live
    enabled: true
```

Each channel should have a stable local identifier.

A YouTube video ID must be globally unique within the application's database.

Never create two recording jobs for the same video ID.

---

# 13. Database

Use SQLite.

Suggested database:

```text
/data/archive.db
```

Do not store the database inside the container filesystem.

Recommended recording fields:

```text
id
youtube_video_id
channel_id
channel_name
channel_url
youtube_url
title

status

detected_at
started_at
ended_at

local_path
local_size_bytes

duration_seconds
container
video_codec
audio_codec
width
height
fps
video_bitrate
audio_bitrate

drive_file_id
drive_folder_id
drive_size_bytes

media_verified
drive_verified
webhook_sent

recording_attempts
verification_attempts
upload_attempts
webhook_attempts

last_error
last_error_at

created_at
updated_at
```

Add indexes to frequently queried fields.

Use a unique constraint for YouTube video IDs.

---

# 14. State Machine

Implement an explicit state machine.

Normal states:

```text
DISCOVERED
RECORDING
FINALIZING
VERIFYING
VERIFIED
UPLOADING
UPLOADED
NOTIFYING
COMPLETED
```

Failure states:

```text
RECORDING_FAILED
VERIFICATION_FAILED
UPLOAD_FAILED
NOTIFICATION_FAILED
```

Every transition must be validated.

Do not scatter status strings throughout the code.

Use a central state-machine implementation.

---

# 15. State Invariants

These rules are mandatory.

### Recording

A recording must not transition to `VERIFIED` unless local media validation succeeds.

### Upload

A recording must not transition to `UPLOADED` unless the remote file has been verified.

### Notification

A recording must not transition to `COMPLETED` until notification requirements are satisfied.

### Cleanup

A local file may be deleted only when:

```python
media_verified
and drive_verified
and webhook_sent
```

If webhook delivery is configurable as optional, reflect that explicitly in the cleanup rule.

When uncertain, **keep the local file**.

---

# 16. YouTube Monitoring

The monitor should continuously watch configured channels.

Initial recommended values:

```yaml
poll_interval_seconds: 30
wait_for_video_seconds: 300
```

A 30-second polling interval is a reasonable default, but it must be configurable.

Use yt-dlp's livestream capabilities where appropriate.

Do not unnecessarily recreate yt-dlp processes if a waiting invocation can safely monitor for availability.

The monitor must:

- detect live streams
- obtain the YouTube video ID
- avoid duplicates
- create a persistent database record
- start recording
- handle offline channels without noisy failures

---

# 17. Recording

Use yt-dlp through Python's subprocess API.

Do not use `shell=True` unless necessary.

Recorder responsibilities:

- build command
- launch yt-dlp
- capture output
- record start/end timestamps
- capture exit code
- detect failures
- manage working files
- return structured results

Initial yt-dlp options should be based on the existing working configuration, including where appropriate:

```text
--wait-for-video
--live-from-start
--hls-use-mpegts
--merge-output-format mkv
--remux-video mkv
```

Also consider:

```text
--retries infinite
--fragment-retries infinite
```

Validate the current yt-dlp behavior for every option before finalizing the implementation.

Do not blindly preserve obsolete options.

---

# 18. Recording File Safety

Never write directly to the final archival filename.

Use a working path such as:

```text
/data/working/<channel_id>/<video_id>/recording.mkv
```

A working file is not considered a completed archive.

After recording stops:

```text
working
  ↓
finalization
  ↓
verification
```

Only a verified recording may proceed to upload.

Failed recordings should remain available for inspection.

---

# 19. Long-Running Stream Reliability

Design for livestreams lasting many hours.

The recorder must handle:

- long-running subprocesses
- transient HLS failures
- network interruptions
- fragment retries
- yt-dlp process failure
- FFmpeg process behavior
- host/container restarts

Do not assume every livestream ends cleanly.

The system should distinguish between:

- normal stream ending
- transient recording failure
- permanent recording failure

Where recovery is possible, retry.

---

# 20. Finalization

When yt-dlp exits:

1. Record process exit code.
2. Determine whether the stream ended normally.
3. Find the produced file.
4. Confirm it exists.
5. Confirm its size is greater than zero.
6. Ensure it is no longer being written.
7. Move/rename it into a stable processing location.
8. Transition to `FINALIZING`.
9. Begin media verification.

Never mark media verified merely because the file exists.

---

# 21. Media Verification

Use `ffprobe` and optionally FFmpeg.

Minimum checks:

### File

- exists
- readable
- size > 0
- stable size

### Container

Verify that ffprobe can parse the file.

### Streams

By default require:

```text
at least one video stream
at least one audio stream
```

Make these requirements configurable.

### Metadata

Extract:

- duration
- container
- codec
- resolution
- frame rate
- bitrate
- sample rate
- channels

### Decode test

Run:

```bash
ffmpeg -v error -i FILE -f null -
```

A non-zero exit status should fail verification.

Record useful FFmpeg errors in the database/logs.

Do not delete invalid media.

---

# 22. Verification Philosophy

The goal is not merely:

> "Can FFmpeg open the file?"

The goal is:

> "Is this file sufficiently valid to archive and safely remove the only local copy?"

Therefore the validation pipeline should be deliberately conservative.

When validation is ambiguous:

```text
KEEP FILE
```

not:

```text
DELETE FILE
```

---

# 23. Media Metadata Storage

Persist extracted metadata so that the webhook does not have to run ffprobe again.

Example:

```json
{
  "container": "matroska",
  "duration_seconds": 7320.4,
  "video": {
    "codec": "vp9",
    "width": 1920,
    "height": 1080,
    "fps": 30.0,
    "bitrate": 4500000
  },
  "audio": {
    "codec": "opus",
    "sample_rate": 48000,
    "channels": 2
  }
}
```

Store important metadata in normalized database columns.

Additional raw ffprobe JSON may optionally be stored.

---

# 24. Google Drive

Implement Google Drive as an isolated service layer.

The rest of the application should not depend directly on Google API classes.

Example conceptual interface:

```python
upload_file(...)
get_file(...)
verify_file(...)
```

The Drive layer must support a **Shared Drive** target.

Configuration:

```yaml
google_drive:
  shared_drive_id: "..."
  folder_id: "..."
```

---

# 25. Resumable Uploads

Livestream recordings can be very large.

Use resumable uploads.

Do not rely on one huge HTTP request.

Recommended configurable chunk size:

```text
64 MB
```

Support retry for:

- timeout
- network interruption
- HTTP 429
- HTTP 5xx

Use exponential backoff.

Persist the remote Drive file ID once known.

---

# 26. Drive Upload Recovery

If the application restarts during upload:

```text
UPLOADING
   ↓
startup recovery
   ↓
check remote state
   ↓
resume/retry safely
```

Do not blindly create a second remote file if the first upload may already have completed.

The database must contain enough information to determine whether an existing remote object should be reused.

---

# 27. Drive Verification

After upload:

1. Retrieve the remote file metadata.
2. Confirm the file exists.
3. Confirm the Drive file ID.
4. Compare file size with the local file.
5. Compare checksum/hash where reliably available.
6. Mark `drive_verified = true`.

Only after remote verification should the application be allowed to delete the local file.

---

# 28. Webhook

Implement a generic HTTP webhook client.

Requirements:

- POST JSON
- configurable timeout
- retry transient failures
- exponential backoff
- persistent attempt count
- idempotent logical event
- useful error logging

Initial target may be Discord-compatible webhook formatting, but internal code should not assume Discord forever.

---

# 29. Webhook Payload

Include:

### YouTube information

```text
video ID
channel
title
URL
start time
end time
duration
```

### File information

```text
filename
size
container
video codec
audio codec
resolution
FPS
duration
```

### Verification information

```text
ffprobe passed
decode test passed
remote verification passed
```

### Google Drive information

```text
Drive file ID
Drive folder ID
upload status
```

Example:

```json
{
  "event": "youtube_live_recorded",

  "youtube": {
    "video_id": "abc123",
    "channel": "NASA",
    "title": "Example Live",
    "url": "https://www.youtube.com/watch?v=abc123",
    "started_at": "2026-09-04T10:15:00Z",
    "ended_at": "2026-09-04T12:17:41Z",
    "duration_seconds": 7361
  },

  "file": {
    "name": "NASA_2026-09-04_abc123.mkv",
    "size_bytes": 1234567890,
    "container": "matroska",
    "video_codec": "vp9",
    "audio_codec": "opus",
    "width": 1920,
    "height": 1080,
    "fps": 30
  },

  "verification": {
    "ffprobe_valid": true,
    "decode_test_passed": true,
    "drive_verified": true
  },

  "google_drive": {
    "file_id": "..."
  }
}
```

Never include credentials or tokens.

---

# 30. Webhook Idempotency

A logical event should have a stable identifier.

Recommended:

```text
youtube_video_id + event_type
```

Persist:

```text
webhook_event_id
webhook_sent
webhook_attempts
webhook_last_error
```

If the process restarts, it should know whether notification has already been delivered.

---

# 31. Cleanup

Local deletion must be the final stage.

Required condition:

```python
can_delete = (
    recording.media_verified
    and recording.drive_verified
    and recording.webhook_sent
)
```

Before deleting:

1. Confirm local file still exists.
2. Confirm database state.
3. Reconfirm Drive file exists.
4. Reconfirm remote size where practical.
5. Reconfirm notification state.
6. Delete local file.
7. Confirm deletion.
8. Mark recording `COMPLETED`.

Never delete based only on:

```text
yt-dlp exited successfully
```

or:

```text
Drive upload returned success
```

---

# 32. Crash Recovery

The application must perform recovery on startup.

Inspect all non-terminal database records.

Examples:

```text
RECORDING
FINALIZING
VERIFYING
UPLOADING
NOTIFYING
```

For every record, compare:

- database state
- filesystem state
- Google Drive state
- webhook state

Then continue from the safest valid point.

Example:

```text
Database: UPLOADING
Local file: exists
Drive file: exists
Remote size: matches
Webhook: not sent

→ mark UPLOADED
→ send webhook
```

Another:

```text
Database: VERIFIED
Local file: exists
Drive file: absent

→ upload
```

Another:

```text
Database: NOTIFYING
Local file: exists
Drive file: verified
Webhook: uncertain

→ retry webhook
→ do not delete local file
```

---

# 33. Reconciliation

Periodically scan for inconsistencies.

Look for:

- orphan files
- stale states
- failed jobs
- missing local files
- missing Drive files
- uploads waiting for notification
- notifications waiting for cleanup

Do not automatically delete orphan files unless a deliberate retention rule says it is safe.

---

# 34. Failure Handling

Classify errors.

## Transient

- network timeout
- HTTP 429
- HTTP 500–599
- temporary DNS failure
- temporary Drive outage

Retry with exponential backoff.

## Permanent

- invalid Google credentials
- invalid Drive ID
- invalid configuration
- unsupported media
- missing required dependency

Log clearly and stop retrying rapidly.

Do not spin in a tight failure loop.

---

# 35. Retry Policy

Use exponential backoff.

Example:

```text
5 sec
10 sec
20 sec
40 sec
80 sec
160 sec
...
```

Cap the maximum delay.

Add jitter when appropriate.

Make retry settings configurable.

---

# 36. Concurrency

One container is sufficient, but the application should internally separate responsibilities.

Conceptually:

```text
Monitor
    |
    +---- Recording A
    |
    +---- Recording B
    |
    +---- Processor
             |
             +---- Upload A
             +---- Upload B
```

Multiple channels may record concurrently.

Limit parallel uploads:

```yaml
processing:
  max_parallel_uploads: 2
```

Do not allow unlimited memory/thread/process growth.

---

# 37. Application Lifecycle

Normal lifecycle:

```text
DISCOVERED
   ↓
RECORDING
   ↓
FINALIZING
   ↓
VERIFYING
   ↓
VERIFIED
   ↓
UPLOADING
   ↓
UPLOADED
   ↓
NOTIFYING
   ↓
COMPLETED
```

A failure must not cause silent data loss.

Example:

```text
UPLOAD_FAILED
   ↓
keep local file
   ↓
retry later
```

---

# 38. Graceful Shutdown

Handle SIGTERM.

On shutdown:

1. Stop accepting new work.
2. Preserve current state.
3. Avoid deleting anything.
4. Allow active processes to shut down cleanly where practical.
5. Leave unfinished files available for recovery.
6. Exit cleanly.

Docker's restart policy should handle application/container crashes, but application-level recovery remains responsible for the workflow state.

---

# 39. Docker Restart Policy

Use:

```yaml
restart: unless-stopped
```

This is only the outer recovery layer.

Do not depend on Docker restart behavior to recover workflow state.

SQLite + filesystem + remote verification must provide the real recovery mechanism.

---

# 40. Health Check

Implement an application health command:

```bash
yt-live-archiver --healthcheck
```

The health check should verify basic application health, such as:

- database accessible
- required directories writable
- yt-dlp executable
- FFmpeg executable
- configuration valid

Avoid making external services such as YouTube or Google Drive mandatory for every health check unless there is a strong reason.

The container may use:

```yaml
healthcheck:
  test: ["CMD", "yt-live-archiver", "--healthcheck"]
  interval: 30s
  timeout: 10s
  retries: 3
```

---

# 41. Logging

Use Python logging.

When running in Docker, log to stdout/stderr so Docker can collect the logs.

Do not require a custom log file inside the container.

Example:

```text
2026-09-04T10:15:30Z INFO  channel=nasa live_detected video_id=abc123
2026-09-04T10:15:31Z INFO  video_id=abc123 recording_started
2026-09-04T12:17:42Z INFO  video_id=abc123 recording_finished size=1.15GB
2026-09-04T12:17:45Z INFO  video_id=abc123 media_verification_passed
2026-09-04T12:18:00Z INFO  video_id=abc123 drive_upload_started
2026-09-04T12:21:30Z INFO  video_id=abc123 drive_upload_completed
2026-09-04T12:21:31Z INFO  video_id=abc123 drive_verification_passed
2026-09-04T12:21:32Z INFO  video_id=abc123 webhook_sent
2026-09-04T12:21:32Z INFO  video_id=abc123 local_file_deleted
```

Log enough context to identify failures.

Never log secrets.

---

# 42. CLI

Support:

```bash
yt-live-archiver
```

and:

```bash
yt-live-archiver --config /config/config.yaml
```

Useful commands:

```bash
yt-live-archiver --version
yt-live-archiver --check-config
yt-live-archiver --check-dependencies
yt-live-archiver --healthcheck
yt-live-archiver --recover
```

Optional future commands:

```bash
yt-live-archiver status
yt-live-archiver list
yt-live-archiver retry VIDEO_ID
```

---

# 43. Dependency Check

At startup log versions of:

```text
Python
yt-dlp
FFmpeg
ffprobe
application
```

Check that:

- database is accessible
- `/data` is writable
- `/data/working` exists
- configuration is valid
- required credentials are available

Fail clearly when essential configuration is invalid.

---

# 44. Filesystem Layout

Inside the container:

```text
/data/
├── archive.db
├── working/
│   └── <channel>/<video_id>/
│
├── failed/
│   └── <channel>/<video_id>/
│
└── metadata/
```

Files must never be silently placed in arbitrary directories.

---

# 45. Filename Convention

Recommended:

```text
{channel}_{YYYY-MM-DD}_{video_id}_{title}.mkv
```

Example:

```text
NASA_2026-09-04_abc123_NASA_Live.mkv
```

Sanitize titles.

Never trust YouTube titles as safe filesystem names.

Handle:

- slash
- backslash
- colon
- asterisk
- question mark
- quotes
- control characters
- excessive length

The video ID must remain part of the filename.

---

# 46. Time

Use UTC internally.

Store timestamps in ISO-8601 form.

Convert to local time only for presentation.

Do not rely on locale-specific parsing.

---

# 47. Security

The container should:

- run as a non-root user where practical
- use read-only configuration mounts
- use read-only credential mounts
- not expose unnecessary ports
- not run with privileged mode
- not require host Docker socket access
- not require host PID/network namespaces unless necessary

Do not require:

```yaml
privileged: true
```

Do not mount:

```text
/
```

or unnecessary host directories.

---

# 48. Testing

Use pytest.

## Unit tests

Test:

- configuration
- filename sanitization
- state transitions
- SQLite transactions
- retry logic
- cleanup conditions
- webhook payload
- metadata parsing
- duplicate detection

## Integration tests

Mock:

- yt-dlp
- FFmpeg
- Google Drive
- webhook server

Do not make normal CI depend on a real YouTube livestream.

---

# 49. Failure Injection

Explicitly test:

### Recording

- normal completion
- yt-dlp crash
- network failure
- missing output
- zero-byte output

### Media

- valid MKV
- corrupt MKV
- missing video
- missing audio
- FFmpeg decode failure

### Drive

- successful upload
- timeout
- HTTP 429
- HTTP 500
- upload interruption
- remote size mismatch

### Webhook

- success
- timeout
- HTTP 500
- HTTP 429
- permanent 4xx

### Recovery

Kill the container at each major state:

```text
RECORDING
FINALIZING
VERIFYING
UPLOADING
NOTIFYING
```

Restart the container and verify that it resumes safely.

---

# 50. Docker Build Testing

The agent must verify:

```bash
docker build .
```

Then run:

```bash
docker compose up
```

Confirm:

- application starts
- configuration is loaded
- database is created
- dependencies work
- logs appear on stdout
- `/data` persists after container recreation

Test:

```bash
docker compose down
docker compose up -d
```

and ensure database/state remains intact.

---

# 51. Persistent Data Test

Explicitly test:

```text
create database
create recording state
stop container
remove container
start new container
```

The recording state must still exist.

The application must never rely on container-local storage for recovery-critical data.

---

# 52. Container Upgrade Strategy

Users should upgrade with:

```bash
docker compose pull
docker compose up -d
```

The new container must reuse:

```text
/data
/config
/credentials
```

Application upgrades must not modify or delete persistent data unexpectedly.

Database migrations must be handled automatically and safely.

---

# 53. GHCR / GitHub Actions

The project should publish Docker images to GitHub Container Registry.

Recommended workflow:

```text
git push
    ↓
GitHub Actions
    ↓
run tests
    ↓
build image
    ↓
optional security/lint checks
    ↓
push GHCR image
```

Recommended tags:

```text
latest
1.0.0
1.0
```

For production users, documentation should recommend pinning an explicit version instead of blindly using `latest`.

---

# 54. Release Strategy

Use semantic versioning:

```text
MAJOR.MINOR.PATCH
```

Example:

```text
1.0.0
1.1.0
1.1.1
```

Publish a GitHub release with:

- changelog
- Docker image tag
- migration notes
- breaking changes

---

# 55. Installation Experience

The preferred first-run flow should be simple.

Example:

```bash
git clone https://github.com/USER/yt-live-archiver.git
cd yt-live-archiver
mkdir -p ./{data,config,credentials}
cp config/config.example.yaml ./config/config.yaml
cp .env.example ./.env
```

Configure:

```text
./config/config.yaml
./.env
./credentials/
```

Then:

```bash
docker compose up -d
```

Check:

```bash
docker compose logs -f
```

The README should document this exact workflow.

---

# 56. Optional Setup Script

A convenience script may be provided:

```text
scripts/setup.sh
```

It may:

- create directories
- copy example configuration
- validate Docker
- validate configuration
- start Compose

Do not hide important behavior inside a giant installer.

Users should still be able to understand and manually reproduce the installation.

---

# 57. Backup

Provide documentation for backing up:

```text
/data/archive.db
/config/config.yaml
/credentials/
```

The largest media files normally should not be backed up locally because their intended durable destination is Google Drive.

Do not automatically back up credentials unless explicitly configured.

---

# 58. Retention

Initial policy:

- successful local recording → delete after full verification
- failed recording → keep locally
- database history → keep
- Google Drive files → never delete automatically
- unknown/orphan files → keep until manually reviewed

Make retention policies configurable in future versions.

---

# 59. Agent Implementation Order

Implement incrementally.

## Phase 1 — Project foundation

Implement:

- Python package
- configuration
- CLI
- logging
- Dockerfile
- Compose
- dependency checks

Goal:

```text
Container starts reliably.
```

---

## Phase 2 — Database and state machine

Implement:

- SQLite
- schema
- migrations
- recording model
- transactions
- state transitions

Goal:

```text
Application can persist recording jobs safely.
```

---

## Phase 3 — YouTube recorder

Implement:

- channel monitoring
- duplicate protection
- yt-dlp subprocess
- working directory
- recording state
- retry behavior

Goal:

```text
Livestream is automatically recorded locally.
```

---

## Phase 4 — Media verification

Implement:

- ffprobe
- metadata extraction
- FFmpeg decode test
- validity checks

Goal:

```text
Application can distinguish usable recordings from broken recordings.
```

---

## Phase 5 — Google Drive

Implement:

- authentication
- Shared Drive
- target folder
- resumable upload
- retry
- remote verification

Goal:

```text
Verified recordings are uploaded safely.
```

---

## Phase 6 — Webhook

Implement:

- payload creation
- webhook client
- retries
- idempotency
- persistent status

Goal:

```text
Completed recordings produce reliable notifications.
```

---

## Phase 7 — Cleanup

Implement:

- safe deletion checks
- completed state
- deletion verification

Goal:

```text
Local storage is cleaned only after all required stages succeed.
```

---

## Phase 8 — Recovery

Implement:

- startup reconciliation
- interrupted recording handling
- interrupted upload recovery
- pending webhook recovery
- stale state detection

Goal:

```text
Container restarts require no manual recovery for normal failures.
```

---

## Phase 9 — Production Docker

Complete:

- non-root execution
- healthcheck
- persistent mounts
- security settings
- Compose documentation

Goal:

```text
The application is production-deployable as one Docker container.
```

---

## Phase 10 — CI/CD

Implement:

- pytest
- linting
- Docker build
- GitHub Actions
- GHCR publishing
- release tagging

Goal:

```text
git push → test → build → publish image
```

---

# 60. Final Acceptance Criteria

The project is complete only when all of the following are satisfied.

## Monitoring

- [ ] Multiple channels are supported.
- [ ] Offline channels can be monitored continuously.
- [ ] Live streams are detected automatically.
- [ ] Duplicate recordings are prevented.

## Recording

- [ ] yt-dlp runs inside the container.
- [ ] FFmpeg runs inside the container.
- [ ] Recordings are stored on persistent `/data`.
- [ ] Temporary files are separated from verified files.
- [ ] Recording failures do not destroy existing data.

## Media integrity

- [ ] ffprobe validation is performed.
- [ ] Required audio/video streams are checked.
- [ ] Decode testing is supported.
- [ ] Invalid recordings remain on disk.
- [ ] Valid recordings contain metadata.

## Google Drive

- [ ] Shared Drives are supported.
- [ ] Target folders are configurable.
- [ ] Large uploads use resumable transfers.
- [ ] Uploads retry after transient failures.
- [ ] Remote file existence is verified.
- [ ] Remote size/checksum is checked where practical.

## Webhook

- [ ] Detailed live metadata is included.
- [ ] Detailed file metadata is included.
- [ ] Drive information is included.
- [ ] Verification results are included.
- [ ] Webhook failures retry.
- [ ] Notification state is persistent/idempotent.

## Cleanup

- [ ] Local files are deleted only after all required checks.
- [ ] Failed recordings are preserved.
- [ ] Ambiguous states default to retaining the local file.

## Recovery

- [ ] Database state survives container replacement.
- [ ] Interrupted processing resumes.
- [ ] Interrupted uploads are recoverable.
- [ ] Pending webhook delivery resumes.
- [ ] Container restarts do not destroy recordings.

## Docker

- [ ] One application container is sufficient.
- [ ] Host Python is not required.
- [ ] Host FFmpeg is not required.
- [ ] Host yt-dlp is not required.
- [ ] Persistent data is mounted from the host.
- [ ] Configuration is mounted separately.
- [ ] Credentials are mounted read-only.
- [ ] Container runs without privileged mode.
- [ ] Healthcheck works.
- [ ] `restart: unless-stopped` is configured.

## CI/CD

- [ ] Tests run in GitHub Actions.
- [ ] Docker image builds automatically.
- [ ] Image is published to GHCR.
- [ ] Version tags are published.
- [ ] README documents deployment.

---

# 61. Critical Engineering Rules

The agent must follow these rules throughout implementation.

1. **When uncertain, keep the file.**
2. **Never delete the only local copy before remote verification.**
3. **Never equate successful subprocess termination with media integrity.**
4. **Persist state before and after important external operations.**
5. **Make retries safe and idempotent.**
6. **One channel failing must not terminate other channels.**
7. **The container filesystem is disposable.**
8. **All recovery-critical data must live in ****`/data`****.**
9. **Configuration and credentials must not be baked into the image.**
10. **Do not require the host to install application runtime dependencies.**
11. **Do not use multiple containers unless a concrete future requirement justifies them.**
12. **Do not automatically delete failed or ambiguous recordings.**
13. **Do not silently create duplicate Drive files after uncertain upload state.**
14. **Do not silently lose webhook delivery state.**
15. **Every recovery decision must favor data preservation.**

---

# 62. Definition of Done

The agent should consider the project complete when a fresh Ubuntu server with only Docker installed can execute:

```bash
git clone https://github.com/USER/yt-live-archiver.git
cd yt-live-archiver
docker compose up -d
```

after configuration, and the resulting container can autonomously:

```text
monitor YouTube
    ↓
record livestream
    ↓
finalize media
    ↓
verify media
    ↓
upload to Google Drive
    ↓
verify Drive copy
    ↓
send webhook
    ↓
delete local recording
```

while remaining safe across:

```text
network outage
YouTube error
yt-dlp failure
FFmpeg failure
Google Drive outage
webhook outage
container restart
host reboot
application crash
```

The system must preserve the local recording whenever it cannot prove that the recording has been safely archived.
