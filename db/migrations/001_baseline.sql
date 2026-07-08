-- Migration 001: Baseline schema
-- Captures current schema from database.py with performance improvements.
-- Requirements: 11.1, 11.2, 11.3

-- Enable WAL mode for concurrent reads during writes
PRAGMA journal_mode=WAL;

-- Enable foreign key constraint enforcement
PRAGMA foreign_keys=ON;

-- ============================================================
-- Tables
-- ============================================================

CREATE TABLE IF NOT EXISTS employees (
    badge_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    badge_id TEXT NOT NULL,
    machine_id TEXT NOT NULL DEFAULT 'M-01',
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    active_duration_seconds REAL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'ACTIVE',
    close_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (badge_id) REFERENCES employees(badge_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    badge_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT,
    resolved INTEGER DEFAULT 0,
    root_cause TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS machine_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (section, key)
);

-- ============================================================
-- Performance Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_sessions_machine_start ON sessions(machine_id, start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_badge ON sessions(badge_id);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved_created ON alerts(resolved, created_at);
CREATE INDEX IF NOT EXISTS idx_machine_events_machine_ts ON machine_state_events(machine_id, timestamp);
