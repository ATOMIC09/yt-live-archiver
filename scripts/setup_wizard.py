#!/usr/bin/env python3
"""
Interactive Setup Wizard for yt-live-archiver.

Configures:
1. Monitored YouTube Channels (supports adding multiple channels).
2. Google Drive upload (Personal Google Account OAuth vs Workspace Service Account,
   in-line OAuth flow, folder ID).
3. Discord / Slack webhook notifications.
4. Auto-writes configuration to config/config.yaml and .env.
5. Provides clear instructions on how to edit configuration after setup.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

# Import auth helper functions
try:
    from auth_gdrive import (
        _DEFAULT_PORT,
        _DEFAULT_SCOPES,
        _OAUTH_AUTH_URL,
        exchange_code_for_tokens,
        parse_code_from_input,
    )
except ImportError:
    # If executed from outside scripts/ dir
    sys.path.insert(0, str(Path(__file__).parent))
    from auth_gdrive import (
        _DEFAULT_PORT,
        _DEFAULT_SCOPES,
        _OAUTH_AUTH_URL,
        exchange_code_for_tokens,
        parse_code_from_input,
    )


def print_banner() -> None:
    print("\n" + "=" * 76)
    print("                yt-live-archiver — Interactive Setup Wizard")
    print("=" * 76)
    print("This wizard will guide you through setting up your live stream archiver.")
    print("You can press Enter to accept defaults where indicated.")
    print("=" * 76 + "\n")


def prompt_text(msg: str, default: str = "", required: bool = True) -> str:
    """Prompt user for input with an optional default value."""
    if default:
        display = f"{msg} [{default}]: "
    else:
        display = f"{msg}: "

    while True:
        try:
            val = input(display).strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup aborted.")
            sys.exit(1)

        if not val and default:
            return default
        if val or not required:
            return val
        print("  Error: This field cannot be empty. Please enter a value.")


def prompt_yes_no(msg: str, default_yes: bool = True) -> bool:
    """Prompt user for a yes/no choice."""
    options = "[Y/n]" if default_yes else "[y/N]"
    display = f"{msg} {options}: "
    while True:
        try:
            val = input(display).strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup aborted.")
            sys.exit(1)

        if not val:
            return default_yes
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        print("  Please enter 'y' for yes or 'n' for no.")


def sanitize_channel_id(name_or_url: str) -> str:
    """Derive a clean, filesystem-safe channel ID."""
    clean = re.sub(r"https?://(?:www\.)?youtube\.com/", "", name_or_url)
    clean = clean.replace("@", "").split("/")[0]
    clean = re.sub(r"[^\w\-_]", "_", clean).lower().strip("_")
    return clean or "channel"


def configure_channels() -> list[dict]:
    """Interactively prompt for one or more YouTube channels."""
    print("[1/3] YouTube Channels to Monitor")
    print("-" * 76)
    print("How to get the channel URL:")
    print("  1. Open YouTube in your browser and navigate to the channel's page.")
    print("  2. Copy the URL from your browser's address bar.")
    print("  Examples:")
    print("    - https://www.youtube.com/@NASA")
    print("    - https://www.youtube.com/@SpaceX/live")
    print("    - https://www.youtube.com/channel/UCvIn3hV597X8rN7aQ...")
    print("-" * 76)

    channels = []
    index = 1

    while True:
        print(f"\nAdding Channel #{index}:")
        url = prompt_text(f"  Channel #{index} YouTube URL (e.g. https://www.youtube.com/@NASA)")

        # Normalize URL to end with /live if not present
        clean_url = url.rstrip("/")
        if not clean_url.endswith("/live") and "/watch?" not in clean_url:
            clean_url = f"{clean_url}/live"

        default_id = sanitize_channel_id(url)
        channel_id = prompt_text("  Filesystem-safe Channel ID", default=default_id)
        channel_id = re.sub(r"[^\w\-_]", "_", channel_id).lower()

        default_name = channel_id.replace("_", " ").title()
        channel_name = prompt_text("  Display Name", default=default_name)

        channels.append({
            "id": channel_id,
            "name": channel_name,
            "url": clean_url,
            "enabled": True,
        })
        print(f"  -> Added '{channel_name}' ({clean_url})")

        add_more = prompt_yes_no("\nWould you like to add another channel?", default_yes=False)
        if not add_more:
            break
        index += 1

    print(f"\nConfigured {len(channels)} channel(s).")
    return channels


def configure_google_drive(install_dir: Path) -> dict:
    """Interactively configure Google Drive settings and credentials."""
    print("\n[2/3] Google Drive Upload")
    print("-" * 76)
    print("Do you want to automatically upload completed stream recordings to Google Drive?")
    enable_drive = prompt_yes_no("Enable Google Drive upload?", default_yes=True)

    if not enable_drive:
        return {"enabled": False}

    print("\nSelect your Google account type:")
    print("  [1] Personal Google Account (@gmail.com / OAuth 2.0) [RECOMMENDED]")
    print("      -> Uses OAuth 2.0 user login. Uploads directly to your Google Drive.")
    print("  [2] Google Workspace (Business / Enterprise / School)")
    print("      -> Uses a Service Account with Shared Drives (Team Drives).")
    choice = prompt_text("Enter choice [1 or 2]", default="1")

    config_dir = install_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    shared_drive_id = ""
    folder_id = ""

    if choice == "2":
        # Google Workspace Service Account
        print("\n--- Google Workspace Service Account Setup ---")
        print("Where to get your Service Account key:")
        print("  1. In Google Cloud Console, go to IAM & Admin > Service Accounts.")
        print("  2. Create or select your Service Account.")
        print("  3. Go to Keys tab > Add Key > Create new key > JSON.")
        print("  4. Add the Service Account email to your Shared Drive as 'Content Manager'.")

        sa_path = prompt_text("Path to Service Account JSON key file on this machine")
        p = Path(sa_path).expanduser().resolve()
        while not p.exists():
            print(f"  File not found: {p}")
            sa_path = prompt_text("Please enter a valid path to your Service Account JSON")
            p = Path(sa_path).expanduser().resolve()

        dest = config_dir / "credentials.json"
        dest.write_bytes(p.read_bytes())
        credentials_file = "/config/credentials.json"
        print(f"  -> Saved credentials to {dest}")

        print("\nHow to get your Shared Drive ID:")
        print("  Open the Shared Drive in your browser. Copy the ID from the URL:")
        print("  https://drive.google.com/drive/folders/<SHARED_DRIVE_ID>")
        shared_drive_id = prompt_text("Shared Drive ID", default="")
        folder_prompt = "Folder ID inside the Shared Drive (or press Enter for root)"
        folder_id = prompt_text(folder_prompt, default=shared_drive_id, required=False)

    else:
        # Personal Google Account (OAuth 2.0)
        print("\n--- Personal Google Drive Setup (OAuth 2.0) ---")
        print("Where to get OAuth Client Credentials:")
        print("  1. Open Google Cloud Console (https://console.cloud.google.com).")
        print("  2. Go to APIs & Services > Credentials (or Google Auth Platform > Clients).")
        print("  3. Click '+ CREATE CREDENTIALS' > 'OAuth client ID'.")
        print("  4. Select Application type: 'Desktop app', name it 'yt-live-archiver'.")
        print("  5. Download the client secret JSON, or copy the Client ID and Secret.")

        print("\nHow would you like to provide your OAuth credentials?")
        print("  [1] Path to downloaded client_secret JSON file")
        print("  [2] Paste client_secret JSON contents directly into terminal")
        print("  [3] Enter Client ID and Client Secret manually")
        auth_mode = prompt_text("Enter choice [1, 2, or 3]", default="1")

        client_id = ""
        client_secret = ""

        if auth_mode == "2":
            print("\nPaste the client_secret JSON below and press Enter,")
            print("then type 'EOF' on a new line and press Enter:")
            lines = []
            while True:
                line = input()
                if line.strip() == "EOF":
                    break
                lines.append(line)
            raw_json = "\n".join(lines).strip()
            try:
                data = json.loads(raw_json)
                app_data = data.get("installed") or data.get("web") or data
                client_id = app_data.get("client_id", "")
                client_secret = app_data.get("client_secret", "")
            except Exception as exc:
                print(f"  Error parsing JSON: {exc}. Falling back to manual entry.")

        elif auth_mode == "1":
            file_path = prompt_text("Path to downloaded client_secret JSON file")
            p = Path(file_path).expanduser().resolve()
            while not p.exists():
                print(f"  File not found: {p}")
                file_path = prompt_text("Please enter a valid path to client_secret JSON")
                p = Path(file_path).expanduser().resolve()
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                app_data = data.get("installed") or data.get("web") or data
                client_id = app_data.get("client_id", "")
                client_secret = app_data.get("client_secret", "")
            except Exception as exc:
                print(f"  Error reading credentials file: {exc}")

        if not client_id or not client_secret:
            client_id = prompt_text("OAuth Client ID")
            client_secret = prompt_text("OAuth Client Secret")

        token_out = config_dir / "token.json"
        redirect_uri = f"http://localhost:{_DEFAULT_PORT}/"

        auth_params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(_DEFAULT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{_OAUTH_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

        print("\n" + "=" * 76)
        print("AUTHORIZE GOOGLE DRIVE ACCESS")
        print("=" * 76)
        print("1. Open the following URL in any web browser:")
        print(f"\n   {auth_url}\n")
        print("2. Log in with your Google account.")
        print("3. Click 'Continue' / 'Allow'.")
        print("   (If warned 'Google hasn't verified this app',")
        print("    click 'Advanced' -> 'Go to yt-live-archiver')")
        print("\n4. After approving, your browser will redirect to:")
        print(f"   {redirect_uri}?code=...")
        print("\n[NOTE FOR REMOTE / SSH SERVERS]:")
        print("Your browser will likely display 'Unable to connect'")
        print("or 'This site can't be reached'. THAT IS NORMAL!")
        print("Simply copy the entire URL from your browser's address bar")
        print("(or the code after 'code=') and paste it below.")
        print("=" * 76)

        user_code_input = prompt_text("\nEnter redirect URL or code")
        code = parse_code_from_input(user_code_input)

        print("Exchanging authorization code with Google for tokens...")
        token_response = exchange_code_for_tokens(code, client_id, client_secret, redirect_uri)

        token_data = {
            "token": token_response.get("access_token"),
            "refresh_token": token_response.get("refresh_token"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": _DEFAULT_SCOPES,
        }
        token_out.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
        print(f"  -> Authentication successful! Saved credentials to {token_out}")
        credentials_file = "/config/token.json"

        # Folder ID prompt
        print("\nHow to get your Google Drive Folder ID:")
        print("  1. Open Google Drive (https://drive.google.com) in your browser.")
        print("  2. Open the folder where you want completed streams to be stored.")
        print("  3. Copy the ID from the browser address bar:")
        print("     https://drive.google.com/drive/folders/<FOLDER_ID>")
        print("     (Example: 1a2B3c4D5e6F7g...)")
        f_prompt = "Enter Google Drive Folder ID (or press Enter to save to Drive root)"
        folder_id = prompt_text(f_prompt, default="", required=False)

    return {
        "enabled": True,
        "credentials_file": credentials_file,
        "folder_id": folder_id,
        "shared_drive_id": shared_drive_id,
    }


def configure_webhook() -> dict:
    """Interactively configure Webhook notifications."""
    print("\n[3/3] Webhook Notifications (Discord / Slack)")
    print("-" * 76)
    print("Receive instant notifications when livestreams are detected, finish, or upload.")
    enable_webhook = prompt_yes_no("Enable Discord/Slack webhook notifications?", default_yes=True)

    if not enable_webhook:
        return {"enabled": False, "url": ""}

    print("\nHow to get a Discord Webhook URL:")
    print("  1. In Discord, right-click the text channel where you want alerts -> 'Edit Channel'.")
    print("  2. Click 'Integrations' > 'Webhooks' > 'New Webhook'.")
    print("  3. Click 'Copy Webhook URL'.")

    webhook_url = prompt_text("Enter Webhook URL (e.g. https://discord.com/api/webhooks/...)")
    return {"enabled": True, "url": webhook_url}


def write_configuration(
    install_dir: Path,
    channels: list[dict],
    drive_cfg: dict,
    webhook_cfg: dict,
) -> None:
    """Update config.yaml and .env with user-specified settings."""
    config_file = install_dir / "config" / "config.yaml"
    template_file = install_dir / "config" / "config.example.yaml"

    # Read base template or existing config
    base_text = ""
    if config_file.exists():
        base_text = config_file.read_text(encoding="utf-8")
    elif template_file.exists():
        base_text = template_file.read_text(encoding="utf-8")
    else:
        # Fallback minimal base
        base_text = """application:
  data_dir: /data
  database: /data/archive.db

youtube:
  poll_interval_seconds: 30
  wait_for_video_seconds: 300
  live_from_start: true

recording:
  working_dir: /data/working
  failed_dir: /data/failed
  format: "bv*[vcodec^=vp9]+ba/bv+ba/best"
  container: mkv

verification:
  require_video: true
  require_audio: true
  run_decode_test: true
  minimum_duration_seconds: 30

processing:
  max_parallel_uploads: 2

google_drive:
  enabled: false
  credentials_file: /config/token.json
  shared_drive_id: ""
  folder_id: ""
  chunk_size_mb: 64

webhook:
  enabled: false
  url: ""
  timeout_seconds: 15
  max_attempts: 10

cleanup:
  require_webhook: true

retry:
  initial_delay_seconds: 5.0
  max_delay_seconds: 300.0
  multiplier: 2.0
  jitter: true

channels:
"""

    # 1. Update Google Drive section
    drive_enabled_str = "true" if drive_cfg.get("enabled") else "false"
    creds_file = drive_cfg.get("credentials_file", "/config/token.json")
    folder_id = drive_cfg.get("folder_id", "")
    shared_id = drive_cfg.get("shared_drive_id", "")

    base_text = re.sub(
        r"(google_drive:\s*\n(?:[^\n]*\n)*?\s*enabled:\s*)(?:true|false)",
        rf"\g<1>{drive_enabled_str}",
        base_text,
        count=1,
    )
    base_text = re.sub(
        r"(credentials_file:\s*)[^\n]+",
        rf"\g<1>{creds_file}",
        base_text,
        count=1,
    )
    base_text = re.sub(
        r'(folder_id:\s*)[^\n]*',
        rf'\g<1>"{folder_id}"',
        base_text,
        count=1,
    )
    base_text = re.sub(
        r'(shared_drive_id:\s*)[^\n]*',
        rf'\g<1>"{shared_id}"',
        base_text,
        count=1,
    )

    # 2. Update Webhook section
    wh_enabled_str = "true" if webhook_cfg.get("enabled") else "false"
    wh_url = webhook_cfg.get("url", "")
    base_text = re.sub(
        r"(webhook:\s*\n(?:[^\n]*\n)*?\s*enabled:\s*)(?:true|false)",
        rf"\g<1>{wh_enabled_str}",
        base_text,
        count=1,
    )
    base_text = re.sub(
        r'(url:\s*)[^\n]*',
        rf'\g<1>"{wh_url}"',
        base_text,
        count=1,
    )

    # 3. Format channels block
    channels_yaml = "channels:\n"
    for ch in channels:
        channels_yaml += f"  - id: {ch['id']}\n"
        channels_yaml += f"    name: {ch['name']}\n"
        channels_yaml += f"    url: {ch['url']}\n"
        channels_yaml += "    enabled: true\n\n"

    # Replace channels section
    if re.search(r"^channels:.*", base_text, re.MULTILINE):
        base_text = re.split(r"^channels:.*", base_text, flags=re.MULTILINE)[0] + channels_yaml
    else:
        base_text = base_text + "\n\n" + channels_yaml

    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(base_text, encoding="utf-8")

    # Update .env
    env_file = install_dir / ".env"
    env_lines = []
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("WEBHOOK_URL="):
                continue
            env_lines.append(line)
    env_lines.append(f"WEBHOOK_URL={wh_url}")
    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")


def print_completion_instructions(
    install_dir: Path, channels: list[dict], drive_cfg: dict, webhook_cfg: dict
) -> None:
    """Print configuration summary and detailed guide on how to edit later."""
    print("\n" + "=" * 76)
    print("                    CONFIGURATION SUMMARY")
    print("=" * 76)
    print(f"Monitored Channels ({len(channels)}):")
    for ch in channels:
        print(f"  • {ch['name']} ({ch['id']}) -> {ch['url']}")

    if drive_cfg.get("enabled"):
        print("\nGoogle Drive Upload: ENABLED")
        print(f"  • Credentials: {drive_cfg.get('credentials_file')}")
        folder = drive_cfg.get('folder_id') or '(Root of Drive)'
        print(f"  • Destination Folder ID: {folder}")
    else:
        print("\nGoogle Drive Upload: Disabled")

    if webhook_cfg.get("enabled"):
        print(f"\nWebhook Notifications: ENABLED ({webhook_cfg.get('url')[:35]}...)")
    else:
        print("\nWebhook Notifications: Disabled")

    print("-" * 76)
    print(f"Configuration saved to: {install_dir / 'config' / 'config.yaml'}")
    print(f"Environment saved to:   {install_dir / '.env'}")
    print("=" * 76)

    print("\n" + "#" * 76)
    print("                  HOW TO EDIT SETTINGS IN THE FUTURE")
    print("#" * 76)
    print("Your configuration is saved in standard text files that you can edit anytime:\n")
    print("1. TO ADD / REMOVE YOUTUBE CHANNELS:")
    print(f"   Edit the configuration file:\n     nano {install_dir}/config/config.yaml")
    print("   Scroll to the bottom under 'channels:' and add entries:")
    print("     channels:")
    print("       - id: new_channel")
    print("         name: New Channel Name")
    print("         url: https://www.youtube.com/@ChannelHandle/live")
    print("         enabled: true\n")

    print("2. TO CHANGE GOOGLE DRIVE OR WEBHOOK SETTINGS:")
    print(f"   Open {install_dir}/config/config.yaml and adjust 'google_drive:' or 'webhook:'.\n")

    print("3. TO APPLY YOUR CHANGES:")
    print("   Whenever you edit config.yaml, restart the container:")
    print("     sudo docker compose restart")
    print("   Or if updating docker configuration:")
    print("     sudo docker compose down && sudo docker compose up -d\n")

    print("4. TO VIEW LIVE STREAM MONITORING LOGS:")
    print("     sudo docker compose logs -f")
    print("#" * 76 + "\n")


def main() -> None:
    install_dir = Path(os.environ.get("INSTALL_DIR", ".")).resolve()

    print_banner()
    channels = configure_channels()
    drive_cfg = configure_google_drive(install_dir)
    webhook_cfg = configure_webhook()

    write_configuration(install_dir, channels, drive_cfg, webhook_cfg)
    print_completion_instructions(install_dir, channels, drive_cfg, webhook_cfg)


if __name__ == "__main__":
    main()
