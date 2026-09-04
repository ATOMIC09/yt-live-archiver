# yt-live-archiver

Automated YouTube livestream archiver with Google Drive upload and webhook notifications.

**yt-live-archiver** continuously monitors configured YouTube channels, records livestreams using `yt-dlp`, validates recordings with `ffprobe`/`ffmpeg`, uploads to Google Drive with resumable transfers, sends a configurable webhook notification, and safely deletes the local copy only after all steps have been verified.

[![CI](https://github.com/ATOMIC09/yt-live-archiver/actions/workflows/ci.yml/badge.svg)](https://github.com/ATOMIC09/yt-live-archiver/actions/workflows/ci.yml)
[![Docker Image](https://ghcr.io/atomic09/yt-live-archiver)](https://github.com/ATOMIC09/yt-live-archiver/pkgs/container/yt-live-archiver)

---

## Features

- 🎥 **Automatic detection** — polls channels every 30 seconds (configurable)
- 📼 **Live recording** — uses `yt-dlp` with HLS, retries, and fragment recovery
- 🔍 **Media verification** — `ffprobe` + FFmpeg decode test before upload
- ☁️ **Google Drive upload** — resumable transfers with retry and verification
- 🔔 **Webhook notifications** — Discord/Slack/custom with retry and idempotency
- 🛡️ **Data safety** — never deletes local copy until remote is verified
- ♻️ **Crash recovery** — resumes automatically after container restart
- 🐳 **Docker-first** — one container, no host dependencies required

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/ATOMIC09/yt-live-archiver.git
cd yt-live-archiver
```

### 2. Create host directories

```bash
mkdir -p ./{data,config,credentials}
```

### 3. Configure

```bash
cp config/config.example.yaml ./config/config.yaml
cp .env.example .env
```

Edit `./config/config.yaml`:

```yaml
channels:
  - id: nasa
    name: NASA
    url: https://www.youtube.com/@NASA/live
    enabled: true

google_drive:
  enabled: true
  folder_id: "your_google_drive_folder_id"

webhook:
  enabled: true
  url: "https://discord.com/api/webhooks/YOUR_WEBHOOK"
```

Edit `.env` with your webhook URL and any overrides.

### 4. Add Google Credentials

Place your service account credentials file:

```bash
cp /path/to/google-credentials.json ./credentials/google-credentials.json
```

See [Google Drive setup guide](docs/google-drive.md) for instructions.

### 5. Start

```bash
docker compose up -d
```

### 6. Check logs

```bash
docker compose logs -f
```

---

## Requirements (host)

- Docker Engine
- Docker Compose
- Persistent storage
- Network access

**The host does NOT need:** Python, yt-dlp, FFmpeg, or any Python packages.

---

## Architecture

```
YouTube
   │
   ▼
Live Monitor (poll every 30s)
   │
   ▼
yt-dlp Recorder ──── /data/working/
   │
   ▼
Media Verification (ffprobe + ffmpeg)
   │
   ▼
Google Drive Upload (resumable)
   │
   ▼
Remote Verification (size check)
   │
   ▼
Webhook Notification
   │
   ▼
Local Cleanup ──── /data/ (file deleted)
```

All state is persisted in `/data/archive.db` (SQLite). The container is disposable.

---

## Data Safety Guarantee

A local recording is **never deleted** unless all of the following are confirmed:

1. ✅ `ffprobe` + decode test passed
2. ✅ Google Drive file exists with matching size
3. ✅ Webhook notification delivered (configurable)

When any condition is uncertain → **keep the file**.

---

## Upgrading

```bash
docker compose pull
docker compose up -d
```

The new container automatically picks up your existing `/data/archive.db` and any interrupted jobs.

---

## Documentation

- [Installation guide](docs/installation.md)
- [Configuration reference](docs/configuration.md)
- [Google Drive setup](docs/google-drive.md)
- [Architecture](docs/architecture.md)
- [Operations](docs/operations.md)
- [Troubleshooting](docs/troubleshooting.md)

---

## License

MIT License — see [LICENSE](LICENSE)
