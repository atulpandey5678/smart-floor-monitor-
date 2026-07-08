# ──────────────────────────────────────────────────────────────────────────────
# Cologic Shop Floor Tracker — Multi-stage Production Dockerfile
# Base: Python 3.10-slim | Serves on port 8000
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.10-slim AS builder

# Install build-time system dependencies for OpenCV and compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python dependencies into a virtual environment for clean copy
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Download YOLOv8n model at build time (cached in layer)
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.10-slim AS runtime

# Install minimal runtime system dependencies for OpenCV headless
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the YOLOv8n model downloaded during build
COPY --from=builder /root/.config/Ultralytics /root/.config/Ultralytics
COPY --from=builder /build/yolov8n.pt /app/yolov8n.pt

WORKDIR /app

# Copy application source code (no secrets, no .env, no DB files)
COPY main.py config.py logging_config.py pyproject.toml requirements.txt ./
COPY api/ ./api/
COPY engine/ ./engine/
COPY cv_pipeline/ ./cv_pipeline/
COPY db/ ./db/
COPY dashboard/ ./dashboard/

# Create directory for database and backups (persistent via volume mount)
RUN mkdir -p /app/data /app/backups

# Expose the API port
EXPOSE 8000

# Health check — app must respond within 30s of start
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Entrypoint: run migrations then start the application
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
