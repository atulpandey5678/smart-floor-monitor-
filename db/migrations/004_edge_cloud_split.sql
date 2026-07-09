-- Migration 004: Edge-Cloud Split
-- Adds idempotency (Event_ID), session identity (session_uuid), event image
-- references, and a global idempotency ledger to support the cloud Ingest_API.
-- All changes are additive/forward-only so existing sessions/alerts data is
-- preserved. A future site_id can be added without altering existing semantics.
-- Requirements: 15.3, 15.4, 16.3

-- ============================================================
-- Idempotency + session identity on sessions
-- ============================================================
ALTER TABLE sessions ADD COLUMN event_id TEXT;
ALTER TABLE sessions ADD COLUMN session_uuid TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_uuid ON sessions(session_uuid);

-- ============================================================
-- Idempotency + event image reference on alerts
-- (alerts.machine_id was added in migration 003)
-- ============================================================
ALTER TABLE alerts ADD COLUMN event_id TEXT;
ALTER TABLE alerts ADD COLUMN event_image_url TEXT;

-- ============================================================
-- Idempotency on machine tower-light events
-- (machine_state_events.machine_id exists from migration 001)
-- ============================================================
ALTER TABLE machine_state_events ADD COLUMN event_id TEXT;

-- ============================================================
-- Global idempotency ledger (fast dedup across all event kinds)
-- ============================================================
CREATE TABLE IF NOT EXISTS ingested_events (
    event_id     TEXT PRIMARY KEY,
    machine_id   TEXT NOT NULL,
    kind         TEXT NOT NULL,          -- session | alert | machine_event
    produced_at  TEXT NOT NULL,
    received_at  TEXT NOT NULL DEFAULT (datetime('now'))
    -- site_id TEXT  (future: add without altering existing semantics)
);

CREATE INDEX IF NOT EXISTS idx_ingested_events_machine ON ingested_events(machine_id);
