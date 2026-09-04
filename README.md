# yt-live-archiver

Automated, resilient YouTube livestream archiver with Google Drive sync and rich Discord notifications.

[![CI](https://github.com/ATOMIC09/yt-live-archiver/actions/workflows/ci.yml/badge.svg)](https://github.com/ATOMIC09/yt-live-archiver/actions/workflows/ci.yml)
[![Docker Image](https://img.shields.io/badge/ghcr.io-yt--live--archiver-blue?logo=docker)](https://github.com/ATOMIC09/yt-live-archiver/pkgs/container/yt-live-archiver)
[![Release](https://img.shields.io/github/v/release/ATOMIC09/yt-live-archiver?color=green)](https://github.com/ATOMIC09/yt-live-archiver/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

**yt-live-archiver** is a self-contained, unattended Docker daemon that continuously monitors your favorite YouTube channels, records livestreams using `yt-dlp`, validates media integrity via `ffprobe`/`ffmpeg`, uploads recordings into channel-specific folders on Google Drive, sends rich Discord embed alerts, and safely deletes local files only when all steps succeed.

---

## ✨ Features

- 🎥 **Multi-Channel Monitoring** — Continuously polls YouTube channels with configurable intervals.
- ⚡ **Resilient Stream Recording** — Powered by `yt-dlp` with HLS recovery, retry logic, and live-from-start capture.
- 🔍 **Strict Media Verification** — `ffprobe` stream validation and `ffmpeg` decode integrity testing before upload.
- 📁 **Organized Google Drive Sync** — Automatically creates subfolders per channel name (e.g. `NASA/`, `SpaceX/`) with resumable chunked transfers.
- 🔑 **Universal Account Support** — Works with **Personal Google Accounts (OAuth 2.0)** and **Google Workspace (Service Accounts & Shared Drives)**.
- 🏷️ **Clean Filenames** — Files are saved cleanly using the sanitized stream title (`{title}.mkv`).
- 🔔 **Rich Discord Webhook Embeds** — Sleek notification cards featuring high-res video preview thumbnails and code-formatted stream metrics.
- 🛡️ **Zero-Data-Loss Guarantee** — Never deletes a local recording until the remote file exists and its size is verified.
- 🔄 **Self-Healing Crash Recovery** — Automatically reconciles in-flight jobs after server reboots or container restarts.
- 🐳 **Docker-First** — Pure single-container setup; no host Python, FFmpeg, or yt-dlp dependencies needed.

---

## ⚡ Quick Start (60 Seconds)

The easiest way to get started on any Linux server (e.g. Ubuntu / Debian) is using the interactive setup wizard:

```bash
# One-liner automated setup (works with or without sudo):
sudo bash -c "$(curl -sSL https://raw.githubusercontent.com/ATOMIC09/yt-live-archiver/master/scripts/setup.sh)"

# Or if you already cloned the repository:
sudo bash scripts/setup.sh
```

The interactive wizard will:
1. Create all necessary data and configuration directories.
2. Interactively add your YouTube channels.
3. Automatically configure Google Drive (Personal Account OAuth 2.0 or Workspace Service Account).
4. Configure your Discord or Slack webhook URL.
5. Launch the Docker container in the background.

---

## 🐳 Manual Setup (Docker Compose)

If you prefer to configure everything manually:

### 1. Clone the repository

```bash
git clone https://github.com/ATOMIC09/yt-live-archiver.git
cd yt-live-archiver
```

### 2. Create host directories

```bash
mkdir -p ./{data,config}
mkdir -p ./data/{working,failed,metadata}
```

### 3. Set up configuration

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
  credentials_file: /config/token.json  # OAuth token or Service Account JSON
  folder_id: "your_target_folder_id"   # Destination folder in Google Drive

webhook:
  enabled: true
  url: "https://discord.com/api/webhooks/YOUR/WEBHOOK/URL"
```

### 4. Authorize Google Drive

- **Personal Google Account**: Run `python scripts/auth_gdrive.py` to generate `/config/token.json`.
- **Google Workspace**: Place your `service-account.json` into `./config/token.json` and grant the service account Editor access to your Drive folder.

*(See [Google Drive Setup Guide](docs/google-drive.md) for detailed step-by-step instructions.)*

### 5. Launch

```bash
docker compose up -d
docker compose logs -f
```

---

## 🔔 Webhook Preview

When a live stream concludes, `yt-live-archiver` posts a Discord embed card:

```
┌──────────────────────────────────────────────────────────┐
│ 🔴 Live Video from the International Space Station      │
│ https://www.youtube.com/watch?v=M3HKLzjvKPc              │
├──────────────────────────────────────────────────────────┤
│ Channel      Duration     File Size                      │
│ `NASA`       `02:15:30`   `2.4 GB`                       │
│                                                          │
│ Resolution                Codecs                         │
│ `1920x1080 @ 30fps`       `vp9 / opus`                   │
│                                                          │
│ Started At                Ended At                       │
│ `2026-09-04 17:13:00 UTC` `2026-09-04 19:28:30 UTC`     │
│                                                          │
│ Google Drive                                             │
│ [Open in Google Drive](https://drive.google.com/...)     │
├──────────────────────────────────────────────────────────┤
│ [                     VIDEO THUMBNAIL                  ] │
│ yt-live-archiver                                         │
└──────────────────────────────────────────────────────────┘
```

---

## 🛠️ Operations & Maintenance

### View live logs

```bash
sudo docker compose logs -f
```

### Add or remove channels

Edit `config/config.yaml` and restart the container:

```bash
sudo nano config/config.yaml
sudo docker compose restart
```

### Update to latest version

```bash
sudo docker compose pull
sudo docker compose up -d
```

### Re-test a stream

To force `yt-live-archiver` to re-record an active live stream that was previously archived:

```bash
# Delete the channel's past recordings from the SQLite database
sudo docker compose exec yt-live-archiver python -c "
import sqlite3
conn = sqlite3.connect('/data/archive.db')
conn.execute(\"DELETE FROM recordings WHERE channel_id = 'nasa'\")
conn.commit()
print('Reset complete')
"
sudo docker compose restart
```

---

## 📚 Documentation

- [Installation Guide](docs/installation.md) — Automated script & manual installation walkthroughs.
- [Google Drive Setup](docs/google-drive.md) — Personal Account OAuth 2.0 & Workspace Service Account setups.
- [Configuration Reference](docs/configuration.md) — Complete `config.yaml` schema and environment variables.
- [Operations & Monitoring](docs/operations.md) — Maintenance, database queries, backups, and disk management.
- [Troubleshooting](docs/troubleshooting.md) — Common error resolution (OAuth consent, quotas, permissions).
- [Architecture](docs/architecture.md) — System architecture, state machine, and data safety guarantees.

---

## 📄 License

MIT License — Copyright (c) 2026 ATOMIC09. See [LICENSE](LICENSE) for details.
