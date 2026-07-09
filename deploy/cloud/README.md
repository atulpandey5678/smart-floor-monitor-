# Cloud_Server — GCP Compute Engine Deployment Guide

## Overview

The Cloud_Server is the FastAPI process that runs on GCP Compute Engine. It
receives durable events pushed by on-site Edge_Agents, stores them in SQLite
(WAL mode), exposes the staff dashboard, and serves the Ingest_API.

It **does not** require a camera, CV stack, or RTSP access of any kind.

---

## Prerequisites

- Docker Engine ≥ 24 and Docker Compose v2
- Python 3.11+ (for running migrations outside Docker)
- A GCP Compute Engine instance (Debian/Ubuntu recommended)
- A GCS bucket for alert event images (optional; falls back to in-memory store)

---

## Environment Variables

Copy and fill in `.env.prod.example` → `.env.prod` (git-excluded):

```
cp .env.prod.example .env.prod
nano .env.prod
```

See `.env.prod.example` for all required and optional variables.

### Required secrets

| Variable | Description |
|---|---|
| `SECRET_KEY` | Session cookie signing key (staff login). Generate with `openssl rand -hex 32`. |
| `INGEST_API_KEY` | Long-lived key the Edge_Agent sends on every ingest request. Generate with `openssl rand -hex 32`. **Share only with the Edge_Agent operator.** |

### GCS (optional — alert event images)

| Variable | Description |
|---|---|
| `GCS_BUCKET` | GCS bucket name for alert event images. Leave empty to use the in-memory fallback. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path inside the container to a GCP service-account JSON key. Mount the file via `volumes`. |

---

## Running Database Migrations

Migrations run automatically at server startup via `db/migrations.py`. To run
them manually (e.g. for a blue/green deployment):

```bash
# From the project root
DB_PATH=/path/to/tracker.db python -c "
from db.migrations import MigrationRunner
r = MigrationRunner('/path/to/tracker.db')
applied = r.run()
print('Applied:', applied)
r.close()
"
```

---

## Starting the Cloud_Server

```bash
# From the project root
docker compose -f deploy/cloud/docker-compose.prod.yml up -d

# Tail logs
docker compose -f deploy/cloud/docker-compose.prod.yml logs -f
```

The server listens on port `8000`. Put an HTTPS reverse-proxy (nginx, Caddy,
Cloud Run Load Balancer) in front so the Edge_Agent can reach it over HTTPS
(required by Requirement 13.5).

---

## Confirming SQLite WAL mode

WAL mode is enabled automatically in `db/async_database.py` (`PRAGMA journal_mode=WAL`).
To verify on a running instance:

```bash
sqlite3 /path/to/tracker.db "PRAGMA journal_mode;"
# expected output: wal
```

SQLite is the retained database engine for this deployment (Requirements 14.2, 14.3).

---

## Ingest_API Endpoint Reference

All ingest endpoints are under `/api/ingest/` and require the `X-Ingest-Key`
header set to `INGEST_API_KEY`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/ingest/session` | Session record push |
| POST | `/api/ingest/alert` | Alert push with event image |
| POST | `/api/ingest/status` | Heartbeat / live status |
| POST | `/api/ingest/machine-event` | Machine tower-light event |
| POST | `/api/ingest/snapshot` | Snapshot thumbnail (JPEG body) |
| GET  | `/api/ingest/machines` | Pull credential-free machine metadata |

---

## Security Notes

- Never commit `.env.prod`, `camera_config*.json`, or GCP service-account keys.
- `INGEST_API_KEY` and `SECRET_KEY` must be different values.
- The Edge_Agent must reach the Cloud_Server over HTTPS (Requirement 13.5).
- Staff dashboard endpoints use session-cookie auth (via `SECRET_KEY`).
- Ingest endpoints reject staff cookies; staff endpoints reject the ingest key.
