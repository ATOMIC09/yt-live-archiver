#!/usr/bin/env bash
# scripts/setup.sh — Interactive setup wizard for yt-live-archiver
#
# Creates host directories, runs the interactive configuration wizard
# (channels, Google Drive OAuth/Service Account, Webhooks), and starts Compose.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-.}"

echo "=== yt-live-archiver setup ==="
echo "Install directory: $INSTALL_DIR"
echo ""

# 1. Prerequisites check
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH."
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "ERROR: Docker Compose is not available."
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is required to run the interactive setup wizard."
    echo "Please install python3 (e.g. 'sudo apt update && sudo apt install -y python3') and re-run."
    exit 1
fi

# Check write permissions
if [ -d "$INSTALL_DIR" ] && [ ! -w "$INSTALL_DIR" ]; then
    echo "ERROR: Cannot write to $INSTALL_DIR (Permission denied)."
    echo "Please re-run with sudo:"
    echo "  sudo bash -c \"\$(curl -sSL https://raw.githubusercontent.com/ATOMIC09/yt-live-archiver/master/scripts/setup.sh)\""
    echo "Or if already in the repository directory:"
    echo "  sudo bash scripts/setup.sh"
    exit 1
fi

echo "[1/4] Creating directories..."
mkdir -p "$INSTALL_DIR"/{data,config,scripts}
mkdir -p "$INSTALL_DIR"/data/{working,failed,metadata}

# The Docker container runs as UID 1000. Give it ownership of the data dir.
if [ "$(id -u)" = "0" ]; then
    chown -R 1000:1000 "$INSTALL_DIR/data"
fi

echo "[2/4] Preparing setup scripts and template files..."

GITHUB_RAW_BASE="https://raw.githubusercontent.com/ATOMIC09/yt-live-archiver/master"
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
fi

# Copy or download config.example.yaml
if [ ! -f "$INSTALL_DIR/config/config.example.yaml" ]; then
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../config/config.example.yaml" ]; then
        cp "$SCRIPT_DIR/../config/config.example.yaml" "$INSTALL_DIR/config/config.example.yaml"
    else
        wget -q "$GITHUB_RAW_BASE/config/config.example.yaml" -O "$INSTALL_DIR/config/config.example.yaml" || \
        curl -s "$GITHUB_RAW_BASE/config/config.example.yaml" -o "$INSTALL_DIR/config/config.example.yaml"
    fi
fi

# Copy or download .env.example
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../.env.example" ]; then
        cp "$SCRIPT_DIR/../.env.example" "$INSTALL_DIR/.env"
    else
        wget -q "$GITHUB_RAW_BASE/.env.example" -O "$INSTALL_DIR/.env" || \
        curl -s "$GITHUB_RAW_BASE/.env.example" -o "$INSTALL_DIR/.env"
    fi
fi

# Copy or download compose.yaml
if [ ! -f "$INSTALL_DIR/compose.yaml" ]; then
    if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../deploy/compose.yaml" ]; then
        cp "$SCRIPT_DIR/../deploy/compose.yaml" "$INSTALL_DIR/compose.yaml"
    else
        wget -q "$GITHUB_RAW_BASE/deploy/compose.yaml" -O "$INSTALL_DIR/compose.yaml" || \
        curl -s "$GITHUB_RAW_BASE/deploy/compose.yaml" -o "$INSTALL_DIR/compose.yaml"
    fi
    echo "  Created $INSTALL_DIR/compose.yaml"
fi

# Ensure auth_gdrive.py and setup_wizard.py are available in $INSTALL_DIR/scripts
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/auth_gdrive.py" ]; then
    cp "$SCRIPT_DIR/auth_gdrive.py" "$INSTALL_DIR/scripts/auth_gdrive.py"
    cp "$SCRIPT_DIR/setup_wizard.py" "$INSTALL_DIR/scripts/setup_wizard.py"
else
    wget -q "$GITHUB_RAW_BASE/scripts/auth_gdrive.py" -O "$INSTALL_DIR/scripts/auth_gdrive.py" || \
    curl -s "$GITHUB_RAW_BASE/scripts/auth_gdrive.py" -o "$INSTALL_DIR/scripts/auth_gdrive.py"
    wget -q "$GITHUB_RAW_BASE/scripts/setup_wizard.py" -O "$INSTALL_DIR/scripts/setup_wizard.py" || \
    curl -s "$GITHUB_RAW_BASE/scripts/setup_wizard.py" -o "$INSTALL_DIR/scripts/setup_wizard.py"
fi

chmod +x "$INSTALL_DIR"/scripts/*.py

echo "[3/4] Launching Interactive Setup Wizard..."
echo ""

# Run interactive wizard (using /dev/tty if stdin is piped via curl)
export INSTALL_DIR
if [ -t 0 ]; then
    python3 "$INSTALL_DIR/scripts/setup_wizard.py"
else
    python3 "$INSTALL_DIR/scripts/setup_wizard.py" < /dev/tty
fi

echo "[4/4] Finalizing setup..."
echo ""

# Prompt to launch container
REPLY=""
if [ -t 0 ]; then
    read -r -p "Start the container now with 'docker compose up -d'? [Y/n] " REPLY
else
    read -r -p "Start the container now with 'docker compose up -d'? [Y/n] " REPLY < /dev/tty
fi

if [[ ! "$REPLY" =~ ^[Nn]$ ]]; then
    echo "Starting container..."
    (cd "$INSTALL_DIR" && docker compose up -d)
    echo ""
    echo "Container started successfully!"
    echo "To view live logs, run:"
    echo "  cd $INSTALL_DIR && sudo docker compose logs -f"
else
    echo "Setup complete. To start the application later, run:"
    echo "  cd $INSTALL_DIR && sudo docker compose up -d"
fi
echo ""
