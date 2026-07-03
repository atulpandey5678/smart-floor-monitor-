# Database module - SQLite connection and schema
import os
import sqlite3
import threading

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
    """Thread-safe SQLite database wrapper.

    Uses a threading.Lock to serialize access and check_same_thread=False
    to allow the connection to be shared across threads.
    """

    def __init__(self, db_path=None):
        self._db_path = db_path or DB_PATH
        self._lock = threading.Lock()
        self._connection = None

    def connect(self):
        """Create or return the existing database connection."""
        if self._connection is None:
            # Ensure the directory for the database file exists
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            self._connection = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._connection.row_factory = sqlite3.Row
            # Disable foreign key enforcement (allow sessions without registered employees)
            self._connection.execute("PRAGMA foreign_keys = OFF")
        return self._connection

    @property
    def connection(self):
        """Property access to the underlying connection."""
        return self.connect()

    @property
    def lock(self):
        """Expose the lock for external use if needed."""
        return self._lock

    def execute(self, sql, params=()):
        """Execute a SQL statement with thread-safe locking."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor

    def executemany(self, sql, params_list):
        """Execute a SQL statement against multiple parameter sets."""
        with self._lock:
            conn = self.connect()
            cursor = conn.executemany(sql, params_list)
            conn.commit()
            return cursor

    def fetch_one(self, sql, params=()):
        """Execute a query and return a single row."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(sql, params)
            return cursor.fetchone()

    def fetch_all(self, sql, params=()):
        """Execute a query and return all rows."""
        with self._lock:
            conn = self.connect()
            cursor = conn.execute(sql, params)
            return cursor.fetchall()

    def close(self):
        """Close the database connection."""
        with self._lock:
            if self._connection:
                self._connection.close()
                self._connection = None

    def create_tables(self):
        """Create all schema tables if they don't exist."""
        with self._lock:
            conn = self.connect()
            conn.executescript(_SCHEMA_SQL)
            # Migration for root_cause in alerts
            try:
                conn.execute("ALTER TABLE alerts ADD COLUMN root_cause TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass # Column already exists


# Module-level singleton instance
_db_instance = None
_instance_lock = threading.Lock()


def get_database():
    """Get or create the module-level Database singleton."""
    global _db_instance
    if _db_instance is None:
        with _instance_lock:
            if _db_instance is None:
                _db_instance = Database()
    return _db_instance


def init_db():
    """Initialize the database: create the file and all tables.

    Returns the Database instance for convenience.
    """
    db = get_database()
    db.create_tables()
    return db


def get_connection():
    """Return the thread-safe SQLite connection.

    Ensures the database is initialized before returning.
    """
    db = get_database()
    return db.connect()
