# Configuration Reference

All application settings are defined in `config/config.yaml` (mounted read-only into `/config/config.yaml` inside the container).

Environment variables can override sensitive values without editing YAML.

---

## Configuration Sections

### `application`

| Key | Default | Description |
|-----|---------|-------------|
| `data_dir` | `/data` | Root path for runtime files and database |
| `database` | `/data/archive.db` | SQLite database file storing recording state |

---

### `youtube`

| Key | Default | Description |
|-----|---------|-------------|
| `poll_interval_seconds` | `30` | Frequency in seconds to check each channel for live status |
| `wait_for_video_seconds` | `300` | How long to wait if a live event is scheduled |
| `live_from_start` | `true` | Record from stream beginning (captures buffered rewind window) |

---

### `recording`

| Key | Default | Description |
|-----|---------|-------------|
| `working_dir` | `/data/working` | Directory for active stream downloads |
| `failed_dir` | `/data/failed` | Directory where unverified/failed files are quarantined |
| `format` | `bv*[vcodec^=vp9]+ba/bv+ba/best` | yt-dlp format selector string |
| `container` | `mkv` | Recording file container format (`mkv` recommended for streaming) |

---

### `verification`

| Key | Default | Description |
|-----|---------|-------------|
| `require_video` | `true` | Fails verification if no video track is found |
| `require_audio` | `true` | Fails verification if no audio track is found |
| `run_decode_test` | `true` | Runs an FFmpeg packet decode check across the file |
| `minimum_duration_seconds` | `30` | Minimum duration required for a recording to be considered valid |

---

### `processing`

| Key | Default | Description |
|-----|---------|-------------|
| `max_parallel_uploads` | `2` | Maximum concurrent Google Drive upload threads |

---

### `google_drive`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Whether to upload completed recordings to Google Drive |
| `credentials_file` | `/config/token.json` | Path to OAuth token JSON or Service Account key JSON |
| `folder_id` | `""` | Target destination Google Drive folder ID |
| `shared_drive_id` | `""` | ID of the Shared Drive (Google Workspace only; empty for personal Drive) |
| `chunk_size_mb` | `64` | Chunk size in megabytes for resumable uploads |

> **Channel Subfolders**: Uploads are automatically placed into subfolders named after each channel (e.g. `NASA/`, `SpaceX/`) inside your target folder.

---

### `webhook`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Whether to send webhook alerts upon upload completion |
| `url` | `""` | Webhook endpoint URL (Discord, Slack, or custom JSON receiver) |
| `timeout_seconds` | `15` | HTTP request timeout in seconds |
| `max_attempts` | `10` | Maximum delivery attempts before giving up |

---

### `cleanup`

| Key | Default | Description |
|-----|---------|-------------|
| `require_webhook` | `true` | Require successful webhook delivery before deleting local file |

Set `require_webhook: false` if you do not use webhooks and want immediate disk cleanup after Drive upload succeeds.

---

### `retry`

Exponential backoff parameters for network requests (Drive uploads, webhooks, YouTube checks):

| Key | Default | Description |
|-----|---------|-------------|
| `initial_delay_seconds` | `5.0` | Initial delay for first retry |
| `multiplier` | `2.0` | Factor multiplied on each subsequent attempt |
| `max_delay_seconds` | `300.0` | Maximum ceiling delay |
| `jitter` | `true` | Adds random ±50% jitter to prevent thundering herds |

---

### `channels`

List of YouTube channels to continuously monitor:

```yaml
channels:
  - id: nasa
    name: NASA
    url: https://www.youtube.com/@NASA/live
    enabled: true
  - id: spacex
    name: SpaceX
    url: https://www.youtube.com/@SpaceX/live
    enabled: true
```

| Key | Required | Description |
|-----|----------|-------------|
| `id` | Yes | Stable, filesystem-safe alphanumeric identifier |
| `name` | Yes | Human-friendly display name (also used as the Google Drive subfolder name) |
| `url` | Yes | Channel `/live` URL |
| `enabled` | No (default: `true`) | Easily toggle channels on/off without removing them |

---

## Environment Variable Overrides

Environment variables override values loaded from `config.yaml`:

| Variable | Overrides | Example |
|----------|-----------|---------|
| `CONFIG_PATH` | Path to config file | `/config/config.yaml` |
| `WEBHOOK_URL` | `webhook.url` | `https://discord.com/api/webhooks/...` |
| `GOOGLE_CREDENTIALS_FILE` | `google_drive.credentials_file` | `/config/token.json` |
| `GOOGLE_FOLDER_ID` | `google_drive.folder_id` | `1a2b3c4d5e...` |
| `GOOGLE_SHARED_DRIVE_ID` | `google_drive.shared_drive_id` | `0ABcDeFg...` |
| `LOG_LEVEL` | Application logging level | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TZ` | Container timezone | `UTC`, `America/New_York`, `Asia/Bangkok` |
