# Operations Guide

## Common Commands

```bash
# View logs
docker compose logs -f

# Check status
docker compose ps

# Health check
docker compose exec yt-live-archiver yt-live-archiver --healthcheck

# Validate configuration
docker compose exec yt-live-archiver yt-live-archiver --check-config

# Check dependencies
docker compose exec yt-live-archiver yt-live-archiver --check-deps

# Manual recovery run
docker compose exec yt-live-archiver yt-live-archiver --recover

# Restart
docker compose restart yt-live-archiver

# Upgrade
docker compose pull && docker compose up -d
```

## Understanding Logs

Log format:
```
TIMESTAMP  LEVEL  LOGGER  MESSAGE  [key=value ...]
```

Key log events:

| Event | Meaning |
|-------|---------|
| `live_detected` | New live stream found |
| `recording_starting` | yt-dlp process starting |
| `recording_finished` | yt-dlp process exited |
| `media_verification_passed` | ffprobe + decode test OK |
| `drive_upload_starting` | Upload beginning |
| `drive_upload_completed` | Upload finished |
| `drive_verification_passed` | Remote size confirmed |
| `webhook_sent` | Notification delivered |
| `local_file_deleted` | Local file removed |

## Recording States

| State | Description |
|-------|-------------|
| `DISCOVERED` | Live stream detected, not yet recording |
| `RECORDING` | yt-dlp is running |
| `FINALIZING` | yt-dlp exited, checking output file |
| `VERIFYING` | ffprobe + decode test running |
| `VERIFIED` | Media is valid, ready to upload |
| `UPLOADING` | Drive upload in progress |
| `UPLOADED` | Drive upload complete and verified |
| `NOTIFYING` | Webhook delivery in progress |
| `COMPLETED` | All done, local file deleted |
| `RECORDING_FAILED` | yt-dlp failed or no output |
| `VERIFICATION_FAILED` | Media validation failed |
| `UPLOAD_FAILED` | Drive upload failed |
| `NOTIFICATION_FAILED` | Webhook exhausted all attempts |

## Failed Recordings

Failed recordings are preserved in `/data/failed/<channel>/<video_id>/`.

They are never deleted automatically.

To inspect:
```bash
ls ./data/failed/
```

## Database

The SQLite database at `/data/archive.db` contains all recording history.

```bash
# View all recordings
sqlite3 ./data/archive.db "SELECT youtube_video_id, channel_id, status, created_at FROM recordings ORDER BY created_at DESC LIMIT 20;"
```

## Backup

Back up these files:

```bash
# Database (contains all recording history and state)
cp ./data/archive.db /backup/archive.db.$(date +%Y%m%d)

# Configuration
cp ./config/config.yaml /backup/

# Credentials (keep secure)
cp ./credentials/ /backup/credentials/ -r
```

## Disk Usage

Monitor disk usage for `/data/working`:
```bash
du -sh ./data/working/
du -sh ./data/failed/
```

Files in `working/` are deleted after successful upload. Files in `failed/` accumulate until manually removed.
