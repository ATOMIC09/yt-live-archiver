# yt-live-archiver Dockerfile
#
# Multi-stage build:
#   Stage 1 (builder): Install Python dependencies
#   Stage 2 (runtime): Minimal runtime image with all deps + app

# ---------------------------------------------------------------------------
# Stage 1: Builder
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only the files needed to install Python deps
COPY pyproject.toml ./
COPY src/ ./src/

# Install the package and all dependencies into a wheel directory
RUN pip install --upgrade pip \
    && pip install --no-cache-dir build \
    && pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim

LABEL org.opencontainers.image.title="yt-live-archiver"
LABEL org.opencontainers.image.description="Automated YouTube livestream archiver"
LABEL org.opencontainers.image.source="https://github.com/ATOMIC09/yt-live-archiver"
LABEL org.opencontainers.image.licenses="MIT"

# Install runtime system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    # FFmpeg (includes ffprobe)
    ffmpeg \
    # curl for healthchecks and debugging
    curl \
    # tini for proper signal handling
    tini \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp (managed via pip for easy version updates)
RUN pip install --no-cache-dir yt-dlp

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user
RUN groupadd --gid 1000 archiver \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin archiver

# Create runtime directories (will be overridden by bind mounts in production)
RUN mkdir -p /data/working /data/failed /data/metadata /config /credentials \
    && chown -R archiver:archiver /data

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Switch to non-root user
USER archiver

# Data directory (bind mount from host)
VOLUME ["/data"]

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["yt-live-archiver", "--healthcheck"]

# Use tini as PID 1 for proper signal forwarding
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]
CMD ["yt-live-archiver"]
