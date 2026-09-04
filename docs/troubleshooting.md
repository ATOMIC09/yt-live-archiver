# Troubleshooting Guide

This guide covers common issues and resolutions for `yt-live-archiver`.

---

## 1. Google Drive Issues

### "The user's Drive storage quota has been exceeded" (HTTP 403)

- **Cause**: You are using a **Service Account** with a personal Google account (`@gmail.com`). Free or personal Google Drive accounts allocate 0 bytes of storage to external service accounts.
- **Fix**: Switch to **OAuth 2.0 user credentials**. Run the setup wizard (`scripts/setup.sh`) or `scripts/auth_gdrive.py` and select **Personal Google Account**. This uploads using your personal Google Drive storage.

### "Access blocked: yt-live-archiver has not completed the Google verification process"

- **Cause**: Your OAuth consent screen in Google Cloud Console is in "Testing" status, and your email address is not listed as an authorized test user.
- **Fix**:
  1. Go to [Google Cloud Console → OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent).
  2. Scroll down to the **Test users** section.
  3. Click **Add Users** and enter your Google account email address.
  4. Click **Save** and retry authorization.

### "Unable to connect" / "This site can't be reached" during authorization

- **Cause**: When authorizing on a remote headless server over SSH, Google redirects your laptop's browser to `http://localhost:8085/?code=...`. Because the local webserver is running on your remote machine (not your laptop), your laptop cannot connect to `localhost:8085`.
- **Fix**: **This is completely normal!** Do not close the tab. Look at your browser's address bar, copy the entire URL (or just the text after `code=`), and paste it into the terminal prompt.

---

## 2. Webhook Issues

### Discord returns HTTP 400 Bad Request ("Cannot send an empty message")

- **Cause**: Discord requires either a top-level text `content` or a valid `embeds` array.
- **Fix**: Make sure you are running `v1.0.1` or newer. `v1.0.1` structures the payload with rich embeds including thumbnail image and code-formatted fields.
- **Testing**: You can test your Discord webhook using curl:
  ```bash
  curl -H "Content-Type: application/json" -X POST -d '{"content":"Test from yt-live-archiver"}' "YOUR_DISCORD_WEBHOOK_URL"
  ```

---

## 3. YouTube Stream Detection & Recording Issues

### Stream is live on YouTube, but `yt-live-archiver` is not recording

- **Check 1: Was it already recorded?**
  The archiver skips streams that already exist in `data/archive.db`. Check the logs for:
  `Already have a record for this video, skipping`
  If you want to re-record it, delete the record from SQLite (see [Operations Guide](operations.md#resetting--re-testing-a-live-stream)).

- **Check 2: Channel URL format**
  Ensure the channel URL in `config/config.yaml` points to `/live`:
  ```yaml
  url: https://www.youtube.com/@ChannelName/live
  ```

- **Check 3: Scheduled stream vs Active live broadcast**
  If a stream is scheduled but has not started broadcasting video fragments, yt-dlp will wait up to `wait_for_video_seconds` (default: 300s) before timing out.

### Outdated `yt-dlp` cipher errors

YouTube frequently updates internal player APIs, which can temporarily break stream extraction in older `yt-dlp` releases.
To update `yt-dlp` inside your running container without waiting for a new image build:

```bash
sudo docker compose exec yt-live-archiver pip install --upgrade yt-dlp
sudo docker compose restart
```

---

## 4. Verification Failures

### Recordings moving to `data/failed/`

When a recording finishes, `ffprobe` validates the container, audio, and video streams. If a stream was interrupted prematurely (e.g. fewer than 30 seconds) or lacks a video track:
1. The recording is flagged as `VERIFICATION_FAILED`.
2. The file is moved to `data/failed/<channel>/<video_id>/`.
3. The archiver does **not** upload corrupt files to Google Drive.

Inspect the failure reason in logs:

```bash
sudo docker compose logs | grep "verification_failed"
```
