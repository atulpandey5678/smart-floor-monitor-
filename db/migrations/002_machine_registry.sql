-- Migration 002: Machine Registry Table
-- Adds the machines table for multi-machine station configuration management.
-- Requirements: 1.1

CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    rtsp_url_encrypted TEXT NOT NULL,
    detection_zone TEXT NOT NULL DEFAULT '(0.0, 0.0, 1.0, 1.0)',
    ocr_zone TEXT NOT NULL DEFAULT '{"x1": 0.30, "y1": 0.10, "x2": 0.70, "y2": 0.55}',
    person_confidence_threshold REAL NOT NULL DEFAULT 0.60,
    light_zone TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_machines_status ON machines(status);
CREATE INDEX IF NOT EXISTS idx_machines_machine_id ON machines(machine_id);
