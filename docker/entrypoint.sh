#!/bin/sh
# entrypoint.sh — container entrypoint for yt-live-archiver
#
# Ensures required directories exist and execs the given command.
# This script runs as the non-root 'archiver' user.

set -e

# Ensure persistent data directories exist (they should already via Dockerfile,
# but the bind mount may replace them)
mkdir -p /data/working /data/failed /data/metadata

# Exec the command passed in (e.g., yt-live-archiver)
exec "$@"
