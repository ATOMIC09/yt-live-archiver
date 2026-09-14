# yt-live-archiver Dockerfile
#
# Multi-stage build:
#   Stage 1 (builder): Install Python package and dependencies
#   Stage 2 (runtime): Minimal image with system deps + app

# ---------------------------------------------------------------------------
# Stage 1: Builder
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --upgrade pip --quiet \
    && pip install --no-cache-dir --prefix=/install .

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim

LABEL org.opencontainers.image.title="yt-live-archiver"
LABEL org.opencontainers.image.description="Automated YouTube livestream archiver"
LABEL org.opencontainers.image.source="https://github.com/ATOMIC09/yt-live-archiver"
LABEL org.opencontainers.image.licenses="MIT"

# Runtime system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp via pip (easy version pinning / updates)
RUN pip install --no-cache-dir yt-dlp

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Run as non-root user
RUN useradd --system --no-create-home --shell /bin/false archiver
USER archiver

# Data volume (recordings go here)
VOLUME ["/data"]

# Default environment
ENV LOG_LEVEL=INFO \
    TZ=UTC \
    WORKING_DIR=/data/working \
    OUTPUT_DIR=/data/archive \
    FAILED_DIR=/data/failed \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD yt-live-archiver --healthcheck

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["yt-live-archiver"]
