# Installation Guide

`yt-live-archiver` is packaged as a Docker container with zero host dependencies. You do **not** need Python, FFmpeg, or yt-dlp installed on your host system.

---

## Prerequisites

- **Docker Engine** (20.10 or newer)
- **Docker Compose** (v2.0 or newer)
- Persistent storage for recordings
- Outbound internet access to YouTube and Google Drive

---

## Method 1: Interactive Setup Wizard (Recommended)

The automated setup script prepares directory structures, asks you for channels and services interactively, configures Google Drive and webhooks, and boots Docker.

### Automated Setup (New Installation)

Run this one-liner in your target installation directory:

```bash
bash -c "$(curl -sSL https://raw.githubusercontent.com/ATOMIC09/yt-live-archiver/master/scripts/setup.sh)"
```

### From a Cloned Repository

If you have already cloned the repository locally:

```bash
bash scripts/setup.sh
```

> **Note**: If installing into a root-owned system directory (like `/opt/stacks`), prefix with `sudo` (e.g. `sudo bash scripts/setup.sh` or `sudo bash -c "$(curl ...)"`).

The script will guide you step-by-step through:
1. Adding one or more YouTube channels.
2. Setting up Google Drive (Personal Google Account OAuth 2.0 or Workspace Service Account).
3. Setting up Discord or Slack webhook alerts.
4. Launching the container.

---

## Method 2: Manual Installation via Docker Compose

### 1. Clone the repository

```bash
git clone https://github.com/ATOMIC09/yt-live-archiver.git
cd yt-live-archiver
```

### 2. Create host directories

The container runs as non-root UID 1000:

```bash
mkdir -p ./{data,config}
mkdir -p ./data/{working,failed,metadata}
sudo chown -R 1000:1000 ./data
```

### 3. Prepare configuration files

```bash
cp config/config.example.yaml ./config/config.yaml
cp .env.example .env
```

Edit `config/config.yaml` to specify your channels:

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

google_drive:
  enabled: true
  credentials_file: /config/token.json
  folder_id: "YOUR_GOOGLE_DRIVE_FOLDER_ID"

webhook:
  enabled: true
  url: "https://discord.com/api/webhooks/..."
```

### 4. Configure Google Drive credentials

- **For Personal Google Accounts**: Run `python scripts/auth_gdrive.py` and save output to `config/token.json`.
- **For Google Workspace**: Copy your Service Account JSON key to `config/token.json`.

*(See the [Google Drive Setup Guide](google-drive.md) for full instructions.)*

### 5. Launch the container

```bash
docker compose up -d
```

### 6. Verify operation

View real-time application logs:

```bash
docker compose logs -f
```

Check container health:

```bash
docker compose ps
```
