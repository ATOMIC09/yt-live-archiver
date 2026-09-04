#!/usr/bin/env python3
"""
Google Drive OAuth 2.0 Token Generator for yt-live-archiver.

Generates an authorized user `token.json` file for personal Google Drive accounts
that works seamlessly on both local machines and remote headless servers (over SSH).

Usage:
    python auth_gdrive.py
    python auth_gdrive.py --credentials client_secret.json --output /config/token.json
    python auth_gdrive.py --client-id YOUR_ID --client-secret YOUR_SECRET
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DEFAULT_SCOPES = ["https://www.googleapis.com/auth/drive"]
_DEFAULT_PORT = 8085


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to capture Google's redirect callback."""

    received_code: str | None = None
    received_error: str | None = None

    def log_message(self, format: str, *args) -> None:
        """Suppress default HTTP server logging."""
        pass

    def do_GET(self) -> None:
        """Parse query string for auth code or error."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _OAuthCallbackHandler.received_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;padding-top:50px;'>"
                b"<h2 style='color:#2e7d32;'>Authentication Successful!</h2>"
                b"<p>You can close this browser tab and return to your terminal.</p>"
                b"</body></html>"
            )
        elif "error" in params:
            _OAuthCallbackHandler.received_error = params["error"][0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<html><body style='font-family:sans-serif;text-align:center;padding-top:50px;'>"
                f"<h2 style='color:#c62828;'>Authentication Failed: {params['error'][0]}</h2>"
                f"</body></html>".encode()
            )
        else:
            self.send_response(404)
            self.end_headers()


def extract_client_credentials(
    creds_path: str | None, client_id: str | None, client_secret: str | None
) -> tuple[str, str]:
    """Retrieve client_id and client_secret from file or prompt."""
    if client_id and client_secret:
        return client_id.strip(), client_secret.strip()

    if creds_path:
        p = Path(creds_path)
        if not p.exists():
            print(f"Error: Credentials file not found: {creds_path}", file=sys.stderr)
            sys.exit(1)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)

        # Google Cloud Console downloads either {"installed": {...}} or {"web": {...}}
        app_data = data.get("installed") or data.get("web") or data
        cid = app_data.get("client_id", "")
        csec = app_data.get("client_secret", "")
        if cid and csec:
            return cid.strip(), csec.strip()

    print("\n--- Google OAuth 2.0 Credentials Setup ---")
    prompt = "Enter path to client_secret JSON file (or press Enter to input manually): "
    path_input = input(prompt).strip().strip('"\'')
    if path_input:
        return extract_client_credentials(path_input, None, None)

    cid = input("Enter OAuth Client ID: ").strip()
    csec = input("Enter OAuth Client Secret: ").strip()

    if not cid or not csec:
        print("Error: Both Client ID and Client Secret are required.", file=sys.stderr)
        sys.exit(1)

    return cid, csec


def exchange_code_for_tokens(
    code: str, client_id: str, client_secret: str, redirect_uri: str
) -> dict:
    """Exchange authorization code for access and refresh tokens."""
    data = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        _OAUTH_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"\nError exchanging code for tokens: HTTP {exc.code}\n{err_body}", file=sys.stderr)
        sys.exit(1)


def parse_code_from_input(user_input: str) -> str:
    """Extract auth code whether user pastes the whole redirect URL or just the code."""
    user_input = user_input.strip().strip('"\'')
    if "code=" in user_input:
        parsed = urllib.parse.urlparse(user_input)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            return params["code"][0]
    return user_input


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Authenticate Google Drive OAuth 2.0 on headless servers or local machines."
    )
    parser.add_argument(
        "--credentials",
        "-c",
        help="Path to downloaded OAuth client secrets JSON (e.g. client_secret.json)",
    )
    parser.add_argument("--client-id", help="OAuth Client ID")
    parser.add_argument("--client-secret", help="OAuth Client Secret")
    parser.add_argument(
        "--output",
        "-o",
        default="token.json",
        help="Output path for the authorized token file (default: token.json)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=_DEFAULT_PORT,
        help=f"Local port for redirect URI callback (default: {_DEFAULT_PORT})",
    )
    args = parser.parse_args()

    client_id, client_secret = extract_client_credentials(
        args.credentials, args.client_id, args.client_secret
    )

    redirect_uri = f"http://localhost:{args.port}/"

    # Start local background HTTP server to catch redirect if reachable
    server: HTTPServer | None = None
    server_thread: threading.Thread | None = None
    try:
        server = HTTPServer(("0.0.0.0", args.port), _OAuthCallbackHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
    except Exception:
        # Port might be in use or unavailable; headless copy-paste fallback will still work
        pass

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_DEFAULT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{_OAUTH_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    print("\n" + "=" * 72)
    print("Google Drive OAuth 2.0 Authorization")
    print("=" * 72)
    print("\n1. Open the following URL in any web browser:")
    print(f"\n   {auth_url}\n")
    print("2. Log in with your Google account.")
    print("3. Click 'Continue' / 'Allow'.")
    print("   (If warned 'Google hasn't verified this app',")
    print("    click 'Advanced' -> 'Go to yt-live-archiver')\n")
    print("4. After approving, your browser will redirect to:")
    print(f"   {redirect_uri}?code=...\n")
    print("--- [HEADLESS / REMOTE SERVER NOTE] ---")
    print("If you are running this over SSH on a remote server, the browser will likely say")
    print("'This site can't be reached' or 'Unable to connect' at the end. THIS IS NORMAL!")
    print("Simply COPY the full URL from your browser's address bar (or just the code)")
    print("and PASTE it below.\n" + "=" * 72)

    # Try opening browser automatically if display is available
    try:
        if os.environ.get("DISPLAY") or sys.platform in ("win32", "darwin"):
            webbrowser.open(auth_url)
    except Exception:
        pass

    auth_code = None

    # Wait for either local callback or manual terminal input
    prompt_msg = "\nEnter redirect URL or code: "
    try:
        user_response = input(prompt_msg).strip()
        if user_response:
            auth_code = parse_code_from_input(user_response)
    except (KeyboardInterrupt, EOFError):
        print("\nAborted by user.")
        if server:
            server.shutdown()
        sys.exit(1)

    # Check if HTTP callback arrived if user didn't paste
    if not auth_code and _OAuthCallbackHandler.received_code:
        auth_code = _OAuthCallbackHandler.received_code

    if server:
        server.shutdown()

    if not auth_code:
        print("Error: No authorization code received.", file=sys.stderr)
        sys.exit(1)

    print("\nExchanging code for tokens with Google...")
    token_response = exchange_code_for_tokens(
        auth_code, client_id, client_secret, redirect_uri
    )

    access_token = token_response.get("access_token")
    refresh_token = token_response.get("refresh_token")

    if not refresh_token:
        print(
            "\nWarning: Google did not return a refresh_token.\n"
            "This happens if consent was already given previously. Try re-running the script.\n"
            "Proceeding with available tokens.",
            file=sys.stderr,
        )

    token_data = {
        "token": access_token,
        "refresh_token": refresh_token,
        "token_uri": _OAUTH_TOKEN_URL,
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": _DEFAULT_SCOPES,
    }

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    print("\n" + "=" * 72)
    print(f"SUCCESS! Saved OAuth credentials to:\n  {out_path}")
    print("=" * 72)
    print("\nNext steps:")
    print("1. Place or mount this file into your container config directory")
    print("   (e.g. /config/token.json)")
    print("2. In your config.yaml, configure:")
    print("   google_drive:")
    print("     enabled: true")
    print(f"     credentials_file: {args.output}")
    print('     folder_id: "YOUR_FOLDER_ID"  # Folder in your Google Drive')
    print('     shared_drive_id: ""          # Leave empty for personal drive')
    print("=" * 72 + "\n")


if __name__ == "__main__":
    main()
