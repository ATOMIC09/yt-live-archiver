# Troubleshooting

## Container won't start

### Check logs
```bash
docker compose logs yt-live-archiver
```

### Config validation
```bash
docker compose run --rm yt-live-archiver yt-live-archiver --check-config
```

### Dependency check
```bash
docker compose run --rm yt-live-archiver yt-live-archiver --check-deps
```

---

## "Configuration file not found"

Make sure your config is mounted correctly:
```yaml
volumes:
  - /srv/yt-live-archiver/config:/config:ro
```

And the file exists:
```bash
ls /srv/yt-live-archiver/config/config.yaml
```

---

## "No channels configured"

Check your `config.yaml` has at least one channel under `channels:`:
```yaml
channels:
  - id: nasa
    name: NASA
    url: https://www.youtube.com/@NASA/live
    enabled: true
```

---

## Channel not detecting live streams

1. Check if the channel is actually live by visiting the URL in a browser
2. Check yt-dlp can access the channel:
   ```bash
   docker compose exec yt-live-archiver yt-dlp --no-warnings --quiet --dump-json --no-playlist "https://www.youtube.com/@NASA/live"
   ```
3. Check for rate limiting — increase `poll_interval_seconds`

---

## Google Drive upload fails

### "credentials file not found"
```bash
ls /srv/yt-live-archiver/credentials/google-credentials.json
```

Ensure the credentials are mounted:
```yaml
volumes:
  - /srv/yt-live-archiver/credentials:/credentials:ro
```

### "permission denied" on Drive
- The service account email must have Editor/Contributor access to the target folder
- See [Google Drive setup guide](google-drive.md)

### Upload stuck / slow
- Check network connectivity from the container
- Try reducing `chunk_size_mb` to 16 for unstable connections

---

## Webhook not sending

1. Check `webhook.enabled: true` and `webhook.url` is set
2. Test the URL manually:
   ```bash
   curl -X POST "YOUR_WEBHOOK_URL" -H "Content-Type: application/json" -d '{"test": true}'
   ```
3. Check logs for `webhook_retryable_error` or `webhook_permanent_failure`

---

## Recording stuck in UPLOADING after restart

The recovery system should handle this automatically. If needed:
```bash
docker compose exec yt-live-archiver yt-live-archiver --recover
```

---

## Disk full

Failed recordings accumulate in `/data/failed/`. They are never auto-deleted.

```bash
# See what's there
du -sh /srv/yt-live-archiver/data/failed/

# Manually remove old failed recordings (after inspection)
rm -rf /srv/yt-live-archiver/data/failed/channelname/
```

---

## Increasing log verbosity

Set in your `.env`:
```
LOG_LEVEL=DEBUG
```

Then restart:
```bash
docker compose up -d
```

---

## Getting help

1. Check the logs: `docker compose logs -f`
2. Check the database: `sqlite3 /srv/yt-live-archiver/data/archive.db "SELECT * FROM recordings ORDER BY updated_at DESC LIMIT 5;"`
3. Open an issue at https://github.com/ATOMIC09/yt-live-archiver/issues
