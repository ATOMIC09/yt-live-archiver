# Installation Guide

## Prerequisites

The host machine only needs:

- **Docker Engine** (20.10+)
- **Docker Compose** (v2+)
- Persistent storage (for `/srv/yt-live-archiver`)
- Network access to YouTube and Google

## Step-by-Step

### 1. Clone the repository

```bash
git clone https://github.com/ATOMIC09/yt-live-archiver.git
cd yt-live-archiver
```

### 2. Create host directories

```bash
sudo mkdir -p /srv/yt-live-archiver/{data,config,credentials}
sudo chown -R $USER:$USER /srv/yt-live-archiver
```

### 3. Copy example files

```bash
cp config/config.example.yaml /srv/yt-live-archiver/config/config.yaml
cp .env.example .env
```

### 4. Configure channels and services

Edit `/srv/yt-live-archiver/config/config.yaml`:

```yaml
channels:
  - id: nasa
    name: NASA
    url: https://www.youtube.com/@NASA/live
    enabled: true
```

Add your Google Drive folder ID and webhook URL. See:
- [Configuration Reference](configuration.md)
- [Google Drive Setup](google-drive.md)

### 5. Add Google credentials

```bash
cp /path/to/service-account.json /srv/yt-live-archiver/credentials/google-credentials.json
chmod 600 /srv/yt-live-archiver/credentials/google-credentials.json
```

### 6. Start the container

```bash
docker compose up -d
```

### 7. Verify it's running

```bash
docker compose logs -f
docker compose ps
```

You should see log lines like:
```
2026-09-04T10:00:00Z  INFO   app  app_starting  version=1.0.0
2026-09-04T10:00:01Z  INFO   monitor  monitor_loop_started  channels=1
```

## Upgrading

```bash
docker compose pull
docker compose up -d
```

The database and all recording state are preserved across upgrades.

## Uninstalling

```bash
docker compose down
```

Your data in `/srv/yt-live-archiver/data/` is not deleted.

## Optional: Setup Script

```bash
bash scripts/setup.sh
```

The script creates directories, copies example files, and starts Compose.
