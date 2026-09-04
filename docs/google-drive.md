# Google Drive Setup

## Overview

yt-live-archiver uses a **Service Account** for Google Drive authentication.
This allows unattended operation without user login.

## Steps

### 1. Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Google Drive API**:
   - Navigate to **APIs & Services → Library**
   - Search for "Google Drive API"
   - Click **Enable**

### 2. Create a Service Account

1. Navigate to **IAM & Admin → Service Accounts**
2. Click **Create Service Account**
3. Give it a name (e.g., `yt-live-archiver`)
4. Click **Create and Continue**
5. Skip role assignment for now
6. Click **Done**

### 3. Download credentials

1. Click on the newly created service account
2. Navigate to the **Keys** tab
3. Click **Add Key → Create new key**
4. Choose **JSON**
5. Save the downloaded file as `google-credentials.json`

### 4. Grant access to your Drive folder

The service account has its own identity (an email like `yt-live-archiver@your-project.iam.gserviceaccount.com`).

**For a regular Google Drive folder:**
1. Open Google Drive
2. Right-click your target folder → **Share**
3. Add the service account email with **Editor** access

**For a Shared Drive:**
1. Open the Shared Drive
2. Go to **Manage Members**
3. Add the service account email with **Contributor** or higher access

### 5. Find your folder ID

The folder ID is in the URL when you open the folder in Google Drive:

```
https://drive.google.com/drive/folders/FOLDER_ID_IS_HERE
```

For a Shared Drive, the drive ID is also in the URL.

### 6. Update configuration

```yaml
google_drive:
  enabled: true
  credentials_file: /credentials/google-credentials.json
  shared_drive_id: ""        # Leave empty for personal Drive
  folder_id: "your_folder_id_here"
  chunk_size_mb: 64
```

### 7. Place credentials

```bash
cp google-credentials.json ./credentials/google-credentials.json
chmod 600 ./credentials/google-credentials.json
```

## Security Notes

- The credentials file is mounted **read-only** in the container
- Never commit `google-credentials.json` to version control
- The file is covered by `.gitignore`
- The service account only needs access to the specific folder, not all of Drive

## Verifying Setup

After starting the container:

```bash
docker compose logs -f | grep drive
```

You should see `drive_upload_starting` when a recording completes.
