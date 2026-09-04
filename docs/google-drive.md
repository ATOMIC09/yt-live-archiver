# Google Drive Setup Guide

`yt-live-archiver` supports two authentication methods for Google Drive:

| Method | Best For | Advantages |
|--------|----------|------------|
| **OAuth 2.0 (User Login)** | Personal Google Accounts (`@gmail.com`) | Uses your personal storage quota, no domain needed |
| **Service Account** | Google Workspace / Enterprise / Shared Drives | Fully automated credentials file, team drives |

---

## Method 1: Personal Google Account (OAuth 2.0) [Recommended]

Personal Google accounts cannot share folders with service accounts due to quota restrictions. Instead, use **OAuth 2.0 user credentials**.

### Step 1: Create a Google Cloud Project & Enable Drive API

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing project).
3. Navigate to **APIs & Services → Library**.
4. Search for **Google Drive API** and click **Enable**.

### Step 2: Configure the OAuth Consent Screen

1. In the left sidebar, navigate to **APIs & Services → OAuth consent screen**.
2. If prompted, select User Type **External** and click **Create**.
3. Fill in basic required information:
   - **App name**: `yt-live-archiver`
   - **User support email**: Your Gmail address
   - **Developer contact information**: Your Gmail address
4. Click **Save and Continue** through Scopes (no extra scopes needed here).
5. On the **Test users** page, click **Add Users** and add your Gmail address.
   *(This ensures you can authorize the app while it is in testing mode).*
6. Click **Save and Continue** and finish.

### Step 3: Create OAuth Client Credentials

1. Navigate to **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. Under **Application type**, select **Desktop app**.
4. Name it `yt-live-archiver-cli` and click **Create**.
5. Click **Download JSON** on the confirmation dialog.

### Step 4: Authorize and Generate Token

You can run the authorization flow on your local machine or directly on a remote server over SSH:

#### Option A: Using the Interactive Setup Wizard

If running `scripts/setup.sh`, choose **Personal Google Account**. The wizard allows you to:
- Provide the path to your downloaded `client_secret.json`.
- Paste the raw JSON directly into the terminal (convenient when SSH'd into a remote server).
- Or enter your Client ID and Client Secret manually.

#### Option B: Using `auth_gdrive.py` Directly

```bash
# Using a downloaded client_secret.json
python scripts/auth_gdrive.py --credentials client_secret.json --output config/token.json

# Or by passing ID and Secret directly
python scripts/auth_gdrive.py --client-id "YOUR_CLIENT_ID" --client-secret "YOUR_SECRET" --output config/token.json
```

#### Headless / Remote Server Authorization Flow:

1. The script will print an authorization URL:
   ```text
   https://accounts.google.com/o/oauth2/v2/auth?...
   ```
2. Open that URL in any browser on your laptop/desktop.
3. Sign in with your Google account.
4. If you see *"Google hasn't verified this app"*, click **Advanced → Go to yt-live-archiver (unsafe)** and click **Allow**.
5. Your browser will redirect to `http://localhost:8085/?code=...`.
   - On a remote headless server, the browser will say *"Unable to connect"* or *"This site can't be reached"*. **This is completely normal!**
6. Copy the **entire URL** from your browser address bar and paste it back into your terminal prompt.
7. The script saves your credentials to `config/token.json`.

---

## Method 2: Google Workspace (Service Account)

For Google Workspace organizations and Shared Drives (Team Drives).

### Step 1: Create a Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Navigate to **IAM & Admin → Service Accounts**.
3. Click **Create Service Account**, give it a name (e.g., `yt-live-archiver`), and click **Done**.

### Step 2: Generate Key

1. Click on the newly created service account.
2. Select the **Keys** tab → **Add Key → Create new key**.
3. Select **JSON** and save the file as `config/token.json`.

### Step 3: Grant Access to Your Folder or Shared Drive

1. Copy the service account's email address (e.g., `yt-live-archiver@project-id.iam.gserviceaccount.com`).
2. Open Google Drive in your browser.
3. Right-click your destination folder or Shared Drive → **Share** (or **Manage Members**).
4. Add the service account email address with **Editor** or **Contributor** access.

---

## Finding Your Target Folder ID

Open the target destination folder in Google Drive. The folder ID is the last segment of the URL:

```text
https://drive.google.com/drive/folders/1a2b3c4d5e6f7g8h9i0jKlMnOp
                                       └──────────┬──────────┘
                                             FOLDER ID
```

Add this ID to your `config/config.yaml`:

```yaml
google_drive:
  enabled: true
  credentials_file: /config/token.json
  folder_id: "1a2b3c4d5e6f7g8h9i0jKlMnOp"
  shared_drive_id: ""  # Leave empty for personal Drive; provide ID if using Shared Drive
```

---

## Automatic Channel Subfolders

In `v1.0.1`, `yt-live-archiver` automatically organizes uploads by channel name:

```text
Target Google Drive Folder/
├── NASA/
│   ├── Live Video from the International Space Station.mkv
│   └── Artemis Mission Coverage.mkv
├── SpaceX/
│   └── Starship Flight Test.mkv
└── ...
```

- If the channel folder does not exist, the archiver automatically creates it.
- Folder IDs are cached in memory to minimize API queries.
- File uploads use resumable transfers with automatic retry on network drops.
