#!/usr/bin/env bash
# scripts/setup.sh — Optional setup helper for yt-live-archiver
#
# Creates host directories, copies example files, and optionally starts Compose.
# You can also run the steps manually — see docs/installation.md

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-.}"

echo "=== yt-live-archiver setup ==="
echo "Install directory: $INSTALL_DIR"
echo ""

# Check Docker is available
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "ERROR: Docker Compose is not available"
    exit 1
fi

echo "[1/4] Creating directories..."
mkdir -p "$INSTALL_DIR"/{data,config}
mkdir -p "$INSTALL_DIR"/data/{working,failed,metadata}

# The Docker container runs as UID 1000. Give it ownership of the data dir.
if [ "$(id -u)" = "0" ]; then
    chown -R 1000:1000 "$INSTALL_DIR/data"
fi

echo "[2/4] Copying example files..."

GITHUB_RAW_BASE="https://raw.githubusercontent.com/ATOMIC09/yt-live-archiver/master"

# Copy or download config.yaml
if [ ! -f "$INSTALL_DIR/config/config.yaml" ]; then
    if [ -f "$(dirname "$0")/../config/config.example.yaml" ]; then
        cp "$(dirname "$0")/../config/config.example.yaml" "$INSTALL_DIR/config/config.yaml"
    else
        wget -q "$GITHUB_RAW_BASE/config/config.example.yaml" -O "$INSTALL_DIR/config/config.yaml" || curl -s "$GITHUB_RAW_BASE/config/config.example.yaml" -o "$INSTALL_DIR/config/config.yaml"
    fi
    echo "  Created $INSTALL_DIR/config/config.yaml"
else
    echo "  config.yaml already exists, skipping"
fi

# Copy or download .env
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$(dirname "$0")/../.env.example" ]; then
        cp "$(dirname "$0")/../.env.example" "$INSTALL_DIR/.env"
    else
        wget -q "$GITHUB_RAW_BASE/.env.example" -O "$INSTALL_DIR/.env" || curl -s "$GITHUB_RAW_BASE/.env.example" -o "$INSTALL_DIR/.env"
    fi
    echo "  Created .env"
else
    echo "  .env already exists, skipping"
fi

# Copy or download compose.yaml
if [ ! -f "$INSTALL_DIR/compose.yaml" ]; then
    if [ -f "$(dirname "$0")/../deploy/compose.yaml" ]; then
        cp "$(dirname "$0")/../deploy/compose.yaml" "$INSTALL_DIR/compose.yaml"
    else
        wget -q "$GITHUB_RAW_BASE/deploy/compose.yaml" -O "$INSTALL_DIR/compose.yaml" || curl -s "$GITHUB_RAW_BASE/deploy/compose.yaml" -o "$INSTALL_DIR/compose.yaml"
    fi
    echo "  Created compose.yaml"
else
    echo "  compose.yaml already exists, skipping"
fi

echo "[3/4] Setup complete!"
echo ""
echo "Next steps:"
echo "  1. REQUIRED: Edit $INSTALL_DIR/config/config.yaml and uncomment/add at least one channel."
echo "  2. OPTIONAL: If using Google Drive, edit config.yaml to enable it and place credentials in $INSTALL_DIR/config/token.json (or credentials.json)"
echo "  3. OPTIONAL: If using webhooks, edit $INSTALL_DIR/.env to set WEBHOOK_URL"
echo "  4. Start the app: docker compose up -d"
echo ""

read -r -p "Start the container now? (Make sure you edit config.yaml first!) [y/N] " REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    echo "[4/4] Starting container..."
    docker compose up -d
    echo ""
    echo "Container started. View logs with:"
    echo "  docker compose logs -f"
    echo ""
    echo "NOTE: If it is restarting, you likely need to configure a channel in config.yaml!"
fi
