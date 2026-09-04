# Operations & Maintenance Guide

This guide covers operational tasks, database queries, resetting recordings, and disk maintenance for `yt-live-archiver`.

---

## 📊 Monitoring & Logs

### View real-time logs

```bash
cd /opt/stacks/yt-live-archiver
sudo docker compose logs -f
```

Filter logs for a specific channel or video:

```bash
# Filter logs for NASA
sudo docker compose logs -f | grep "nasa"

# Filter logs for errors only
sudo docker compose logs -f | grep "ERROR"
```

### Check container health

```bash
sudo docker compose ps
```

The healthcheck command runs every 30 seconds internally verifying that the daemon loop and SQLite database are healthy.

---

## 🗄️ SQLite Database Operations

All persistent state (recording IDs, stream metadata, verification status, Drive IDs, webhook status) is stored in SQLite at `/data/archive.db` (`./data/archive.db` on your host).

### Inspect existing recordings

You can inspect the database directly using Python through Docker without installing tools on the host:

```bash
# List all recordings
sudo docker compose exec yt-live-archiver python -c "
import sqlite3
conn = sqlite3.connect('/data/archive.db')
for row in conn.execute('SELECT id, youtube_video_id, channel_id, status, title FROM recordings ORDER BY id DESC LIMIT 10'):
    print(row)
"
```

Or using `sqlite3` CLI if installed on your host:

```bash
sqlite3 data/archive.db "SELECT id, youtube_video_id, channel_id, status, created_at FROM recordings ORDER BY id DESC LIMIT 10;"
```

### Resetting / Re-testing a Live Stream

Because the archiver prevents duplicate recordings of the same stream, it checks `archive.db` and skips streams that are already marked as recorded or discovered.

To force `yt-live-archiver` to re-record an active live stream:

#### 1. Delete the record from SQLite:

```bash
sudo docker compose exec yt-live-archiver python -c "
import sqlite3
conn = sqlite3.connect('/data/archive.db')
deleted = conn.execute(\"DELETE FROM recordings WHERE channel_id = 'nasa'\").rowcount
conn.commit()
print(f'Removed {deleted} record(s)')
"
```

#### 2. Clean leftover working files and restart:

```bash
sudo rm -rf data/working/*
sudo docker compose restart
sudo docker compose logs -f
```

### Complete Fresh Start (Database Reset)

To reset all state and start completely fresh:

```bash
sudo docker compose down
sudo rm -f data/archive.db
sudo rm -rf data/working/*
sudo docker compose up -d
```

*(On startup, the app automatically runs migrations and creates a fresh `archive.db`.)*

---

## 💾 Disk Space Management

### Directory layout

- `data/working/`: Temporary directory where active streams are downloaded. Files here are **automatically deleted** once uploaded to Google Drive and confirmed.
- `data/failed/`: Quarantined recordings that failed media verification (corrupt streams, missing audio/video tracks). These are retained for manual inspection.
- `data/archive.db`: SQLite database file (typically a few megabytes).

### Checking disk usage

```bash
# Check working directory usage
du -sh ./data/working/

# Check failed directory usage
du -sh ./data/failed/
```

### Cleaning failed files

Files in `data/failed/` do not automatically delete so you can inspect them if needed:

```bash
# Safely remove all failed recordings older than 7 days
find ./data/failed -type f -mtime +7 -delete
```

---

## 🔄 Updating `yt-live-archiver`

When a new version is released:

```bash
cd /opt/stacks/yt-live-archiver
sudo docker compose pull
sudo docker compose up -d
```

Your `/data/archive.db` and `/config` volumes remain intact across updates.

---

## 🛡️ Backup & Recovery

To back up your configuration and recording database:

```bash
BACKUP_DIR="/backup/yt-archiver-$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Backup database
cp ./data/archive.db "$BACKUP_DIR/"

# Backup configuration & credentials
cp -r ./config "$BACKUP_DIR/"
cp ./.env "$BACKUP_DIR/" 2>/dev/null || true
```
