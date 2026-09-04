# Configuration Reference

All configuration lives in `/config/config.yaml` (mounted read-only).
Sensitive values can be overridden via environment variables.

## application

| Key | Default | Description |
|-----|---------|-------------|
| `data_dir` | `/data` | Root data directory |
| `database` | `/data/archive.db` | SQLite database path |

## youtube

| Key | Default | Description |
|-----|---------|-------------|
| `poll_interval_seconds` | `30` | How often to check each channel |
| `wait_for_video_seconds` | `300` | Time to wait for a scheduled stream to start |
| `live_from_start` | `true` | Record from the very beginning of the stream |

## recording

| Key | Default | Description |
|-----|---------|-------------|
| `working_dir` | `/data/working` | Directory for in-progress recordings |
| `failed_dir` | `/data/failed` | Directory for failed recordings (kept for inspection) |
| `format` | `bv*[vcodec^=vp9]+ba/bv+ba/best` | yt-dlp format selection string |
| `container` | `mkv` | Output container format |

## verification

| Key | Default | Description |
|-----|---------|-------------|
| `require_video` | `true` | Fail verification if no video stream |
| `require_audio` | `true` | Fail verification if no audio stream |
| `run_decode_test` | `true` | Run FFmpeg decode test (recommended) |
| `minimum_duration_seconds` | `30` | Minimum acceptable recording duration |

## processing

| Key | Default | Description |
|-----|---------|-------------|
| `max_parallel_uploads` | `2` | Maximum concurrent Drive uploads |

## google_drive

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable Google Drive upload |
| `credentials_file` | `/credentials/google-credentials.json` | Service account JSON path |
| `shared_drive_id` | `""` | Shared Drive ID (empty = personal Drive) |
| `folder_id` | `""` | Target folder ID (required if enabled) |
| `chunk_size_mb` | `64` | Upload chunk size in MB |

## webhook

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable webhook notifications |
| `url` | `""` | Webhook URL (required if enabled) |
| `timeout_seconds` | `15` | Request timeout |
| `max_attempts` | `10` | Maximum delivery attempts |

Can also be set via `WEBHOOK_URL` environment variable.

## cleanup

| Key | Default | Description |
|-----|---------|-------------|
| `require_webhook` | `true` | Require webhook delivery before deleting local file |

Set to `false` if you don't use webhooks and still want automatic cleanup.

## retry

| Key | Default | Description |
|-----|---------|-------------|
| `initial_delay_seconds` | `5.0` | First retry delay |
| `max_delay_seconds` | `300.0` | Maximum retry delay |
| `multiplier` | `2.0` | Delay multiplier per retry |
| `jitter` | `true` | Add random jitter |

## channels

List of channels to monitor.

| Key | Required | Description |
|-----|----------|-------------|
| `id` | Yes | Short, stable, filesystem-safe identifier |
| `name` | Yes | Human-readable channel name |
| `url` | Yes | YouTube channel `/live` URL |
| `enabled` | No (default: `true`) | Enable/disable monitoring |

```yaml
channels:
  - id: nasa
    name: NASA
    url: https://www.youtube.com/@NASA/live
    enabled: true
```

## Environment Variables

| Variable | Overrides |
|----------|-----------|
| `CONFIG_PATH` | Config file path |
| `WEBHOOK_URL` | `webhook.url` |
| `GOOGLE_CREDENTIALS_FILE` | `google_drive.credentials_file` |
| `GOOGLE_SHARED_DRIVE_ID` | `google_drive.shared_drive_id` |
| `GOOGLE_FOLDER_ID` | `google_drive.folder_id` |
| `LOG_LEVEL` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `TZ` | Container timezone |
