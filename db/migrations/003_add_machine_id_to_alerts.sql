-- Migration 003: Add machine_id column to alerts table
-- Enables filtering alerts by originating machine station.
-- Requirements: 3.3, 3.4

ALTER TABLE alerts ADD COLUMN machine_id TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_alerts_machine_id ON alerts(machine_id);
