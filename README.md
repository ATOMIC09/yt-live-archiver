# yt-live-archiver

Automated, resilient YouTube livestream archiver with Google Drive sync and Discord notifications.

[![CI](https://github.com/ATOMIC09/yt-live-archiver/actions/workflows/ci.yml/badge.svg)](https://github.com/ATOMIC09/yt-live-archiver/actions/workflows/ci.yml)
[![Docker Image](https://img.shields.io/badge/ghcr.io-yt--live--archiver-blue?logo=docker)](https://github.com/ATOMIC09/yt-live-archiver/pkgs/container/yt-live-archiver)
[![Release](https://img.shields.io/github/v/release/ATOMIC09/yt-live-archiver?color=green)](https://github.com/ATOMIC09/yt-live-archiver/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ Features

- 🎥 **Multi-Channel Monitoring** — Polls YouTube channels at a configurable interval.
- ⚡ **Resilient Recording** — yt-dlp with `--retries infinite` and automatic segment merging when a stream restarts mid-session.
- 🔍 **Media Verification** — ffprobe stream validation + ffmpeg null-decode test before upload.
- 📁 **Google Drive Sync** — Resumable chunked upload into per-channel subfolders.
- 🔔 **Discord Webhook Embeds** — Rich notification card with thumbnail, duration, codecs, and Drive link.
- 🛡️ **Zero-Data-Loss** — Local file is only deleted after Drive upload is verified.
- 🔄 **Startup Recovery** — On restart, orphaned working files from previous runs are automatically picked up and processed through the full pipeline.
- 🐳 **Config-free** — Everything is driven by environment variables. No YAML files, no volume mounts for configuration.

---

## 🚀 Quick Start

### Plain Docker

```bash
docker run -d \
  --name yt-live-archiver \
  --restart unless-stopped \
  -v /your/nas/path:/data \
  -e CHANNELS="nasa:NASA:https://www.youtube.com/@NASA/live" \
  -e WEBHOOK_ENABLED=true \
  -e WEBHOOK_URL="https://discord.com/api/webhooks/YOUR/WEBHOOK" \
  -e GOOGLE_DRIVE_ENABLED=true \
  -e GOOGLE_CLIENT_ID="your_client_id" \
  -e GOOGLE_CLIENT_SECRET="your_client_secret" \
  -e GOOGLE_REFRESH_TOKEN="your_refresh_token" \
  -e GOOGLE_FOLDER_ID="your_folder_id" \
  ghcr.io/atomic09/yt-live-archiver:latest
```

### Using an env file

```bash
cp .env.example .env
# Edit .env with your values
docker run -d --name yt-live-archiver --restart unless-stopped \
  -v /your/nas/path:/data \
  --env-file .env \
  ghcr.io/atomic09/yt-live-archiver:latest
```

---

## 🔑 Google Drive Setup (One-Time)

No scripts needed. Get your refresh token in ~2 minutes:

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth 2.0 Client ID** → choose **Desktop app** → create.
3. Copy the **Client ID** and **Client Secret**.
4. Go to [OAuth Playground](https://developers.google.com/oauthplayground).
5. Click ⚙️ (top right) → **Use your own OAuth credentials** → paste Client ID & Secret.
6. In the scope box, type `https://www.googleapis.com/auth/drive` → **Authorize APIs**.
7. Click **Exchange authorization code for tokens** → copy the **Refresh token**.
8. Find your Drive folder ID from the URL: `https://drive.google.com/drive/folders/`**`THIS_PART`**

Set these four values as environment variables and you're done — no file mounts needed.

---

## ⚙️ Environment Variables

### Required

| Variable | Example | Description |
|---|---|---|
| `CHANNELS` | `nasa:NASA:https://youtube.com/@NASA/live` | Comma-separated `id:Name:url` entries |

### Google Drive (when `GOOGLE_DRIVE_ENABLED=true`)

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth 2.0 Client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth 2.0 Client Secret |
| `GOOGLE_REFRESH_TOKEN` | Refresh token from OAuth Playground |
| `GOOGLE_FOLDER_ID` | Target Drive folder ID |
| `GOOGLE_SHARED_DRIVE_ID` | Shared Drive ID (Workspace only, optional) |
| `GOOGLE_CHUNK_SIZE_MB` | Upload chunk size, default `64` |

### Webhook / Discord (when `WEBHOOK_ENABLED=true`)

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_URL` | | Discord / Slack / custom endpoint |
| `WEBHOOK_TIMEOUT` | `15` | Request timeout (seconds) |
| `WEBHOOK_MAX_ATTEMPTS` | `10` | Max delivery retries |

### Storage

| Variable | Default | Description |
|---|---|---|
| `WORKING_DIR` | `/data/working` | Temp dir during recording |
| `OUTPUT_DIR` | `/data/archive` | Final destination |
| `FAILED_DIR` | `/data/failed` | Failed recordings for inspection |

### Recording & Verification

| Variable | Default | Description |
|---|---|---|
| `POLL_INTERVAL` | `30` | Seconds between channel polls (min 5) |
| `LIVE_FROM_START` | `true` | Record from beginning of stream |
| `WAIT_FOR_VIDEO` | `300` | Wait for scheduled stream to start |
| `RECORDING_FORMAT` | `bv*[vcodec^=vp9]+ba/bv+ba/best` | yt-dlp format string |
| `RECORDING_CONTAINER` | `mkv` | Output container |
| `MIN_DURATION` | `30` | Reject recordings shorter than this (seconds) |
| `REQUIRE_VIDEO` | `true` | Fail if no video stream found |
| `REQUIRE_AUDIO` | `true` | Fail if no audio stream found |
| `DECODE_TEST` | `true` | ffmpeg null-decode integrity test |
| `LOG_LEVEL` | `INFO` | `DEBUG / INFO / WARNING / ERROR` |

---

## 🏠 TrueNAS Custom App

In **TrueNAS SCALE → Apps → Discover → Custom App**:

- **Image**: `ghcr.io/atomic09/yt-live-archiver:latest`
- **Environment Variables**: Add each variable from the table above.
- **Storage**:
  - Add a **Host Path** volume → your NAS dataset → mount path `/data`
  - *(No other volume mounts needed — credentials are passed as ENV vars)*
- **Restart Policy**: `Unless Stopped`

---

## 🔄 Recovery Behavior

| Scenario | What happens |
|---|---|
| 📶 Network drops mid-stream | yt-dlp retries automatically (`--retries infinite`). App does nothing. |
| 💥 Container restarts while stream is live | Monitor re-detects the stream → new recording in the same working dir → **segments are auto-merged** at the end. |
| 💀 Container restarts after stream ended | **Startup orphan scan** finds the leftover file and runs it through verify → upload → webhook → delete. |

---

## 🛠️ Operations

### View logs
```bash
docker logs -f yt-live-archiver
```

### Check configuration
```bash
docker run --rm --env-file .env ghcr.io/atomic09/yt-live-archiver:latest --check-config
```

### Check dependencies
```bash
docker run --rm ghcr.io/atomic09/yt-live-archiver:latest --check-deps
```

### Update to latest
```bash
docker pull ghcr.io/atomic09/yt-live-archiver:latest
docker stop yt-live-archiver && docker rm yt-live-archiver
# Re-run your docker run command
```

---

## 📄 License

MIT License — Copyright (c) 2026 ATOMIC09. See [LICENSE](LICENSE) for details.
