# Database module - SQLite connection and schema (Async with aiosqlite)
import os
import aiosqlite
import sqlite3
import asyncio

from config import DB_PATH

# SQL schema definitions
_SCHEMA_SQL = """
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
"""


class Database:
    """Asynchronous SQLite database wrapper using aiosqlite."""

    def __init__(self, db_path=None):
        self._db_path = db_path or DB_PATH
        self._connection = None

    async def connect(self):
        """Create or return the existing database connection."""
        if self._connection is None:
            # Ensure the directory for the database file exists
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            self._connection = await aiosqlite.connect(self._db_path)
            self._connection.row_factory = aiosqlite.Row
            # Disable foreign key enforcement
            await self._connection.execute("PRAGMA foreign_keys = OFF")
        return self._connection

    async def execute(self, sql, params=()):
        """Execute a SQL statement asynchronously."""
        conn = await self.connect()
        cursor = await conn.execute(sql, params)
        await conn.commit()
        return cursor

    async def executemany(self, sql, params_list):
        """Execute a SQL statement against multiple parameter sets."""
        conn = await self.connect()
        cursor = await conn.executemany(sql, params_list)
        await conn.commit()
        return cursor

    async def fetch_one(self, sql, params=()):
        """Execute a query and return a single row."""
        conn = await self.connect()
        cursor = await conn.execute(sql, params)
        return await cursor.fetchone()

    async def fetch_all(self, sql, params=()):
        """Execute a query and return all rows."""
        conn = await self.connect()
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()

    async def close(self):
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def create_tables(self):
        """Create all schema tables if they don't exist."""
        conn = await self.connect()
        await conn.executescript(_SCHEMA_SQL)
        # Migration for root_cause in alerts
        try:
            await conn.execute("ALTER TABLE alerts ADD COLUMN root_cause TEXT")
            await conn.commit()
        except sqlite3.OperationalError:
            pass # Column already exists


# Module-level singleton instance
_db_instance = None

def get_database():
    """Get or create the module-level Database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance

async def init_db():
    """Initialize the database: create the file and all tables.
    Returns the Database instance for convenience.
    """
    db = get_database()
    await db.create_tables()
    return db
