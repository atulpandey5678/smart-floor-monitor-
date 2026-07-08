"""Database migration runner — applies versioned .sql migrations at startup.

Uses synchronous sqlite3 since migrations run before the async event loop starts.
Forward-only: no automatic rollback to previous versions.
"""

import hashlib
import structlog
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

logger = structlog.get_logger(__name__)

# Default migrations directory (sibling to this file)
_DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    checksum TEXT NOT NULL
);
"""

# Pattern for migration filenames: NNN_description.sql
_MIGRATION_PATTERN = re.compile(r"^(\d+)_(.+)\.sql$")


class MigrationError(Exception):
    """Raised when a migration fails, preventing application startup."""

    def __init__(self, version: int, name: str, detail: str):
        self.version = version
        self.name = name
        self.detail = detail
        super().__init__(
            f"Migration {version:03d}_{name} failed: {detail}"
        )


class MigrationRunner:
    """Applies pending SQL migrations in sequential order.

    Parameters
    ----------
    db_path : str or Path
        Path to the SQLite database file.
    migrations_dir : str or Path, optional
        Directory containing numbered .sql migration files.
        Defaults to db/migrations/ next to this module.
    """

    def __init__(
        self,
        db_path: str | Path,
        migrations_dir: Optional[str | Path] = None,
    ):
        self._db_path = str(db_path)
        self._migrations_dir = Path(migrations_dir) if migrations_dir else _DEFAULT_MIGRATIONS_DIR
        self._connection: Optional[sqlite3.Connection] = None

    # ── Public API ───────────────────────────────────────────

    def run(self) -> list[dict]:
        """Apply all pending migrations. Returns list of applied migration info.

        Raises
        ------
        MigrationError
            If any migration fails. The failed migration is rolled back,
            but previously applied migrations remain committed.
        """
        self._connect()
        self._ensure_migrations_table()

        applied_versions = self._get_applied_versions()
        pending = self._discover_pending(applied_versions)

        if not pending:
            logger.info("No pending migrations.")
            return []

        results = []
        for version, name, sql_path in pending:
            info = self._apply_migration(version, name, sql_path)
            results.append(info)
            logger.info(
                "Applied migration %03d_%s (checksum: %s)",
                version, name, info["checksum"][:8],
            )

        logger.info("All %d migration(s) applied successfully.", len(results))
        return results

    def get_applied_migrations(self) -> list[dict]:
        """Return list of already-applied migrations from the tracking table."""
        self._connect()
        self._ensure_migrations_table()
        cursor = self._connection.execute(
            "SELECT version, name, applied_at, checksum FROM _migrations ORDER BY version"
        )
        return [
            {"version": row[0], "name": row[1], "applied_at": row[2], "checksum": row[3]}
            for row in cursor.fetchall()
        ]

    def close(self):
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    # ── Private helpers ──────────────────────────────────────

    def _connect(self):
        """Open a synchronous sqlite3 connection if not already open."""
        if self._connection is None:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._connection = sqlite3.connect(self._db_path)
            self._connection.execute("PRAGMA foreign_keys = ON")

    def _ensure_migrations_table(self):
        """Create the _migrations tracking table if it does not exist."""
        self._connection.execute(_MIGRATIONS_TABLE_SQL)
        self._connection.commit()

    def _get_applied_versions(self) -> set[int]:
        """Return set of already-applied migration version numbers."""
        cursor = self._connection.execute("SELECT version FROM _migrations")
        return {row[0] for row in cursor.fetchall()}

    def _discover_pending(self, applied: set[int]) -> list[tuple[int, str, Path]]:
        """Scan migrations directory for unapplied .sql files, sorted by version."""
        if not self._migrations_dir.exists():
            logger.warning(
                "Migrations directory does not exist: %s", self._migrations_dir
            )
            return []

        pending = []
        for entry in self._migrations_dir.iterdir():
            match = _MIGRATION_PATTERN.match(entry.name)
            if match and entry.is_file():
                version = int(match.group(1))
                name = match.group(2)
                if version not in applied:
                    pending.append((version, name, entry))

        # Sort by version number to ensure sequential application
        pending.sort(key=lambda x: x[0])
        return pending

    def _compute_checksum(self, sql_path: Path) -> str:
        """Compute SHA-256 checksum of a migration file's content."""
        content = sql_path.read_bytes()
        return hashlib.sha256(content).hexdigest()

    def _apply_migration(self, version: int, name: str, sql_path: Path) -> dict:
        """Apply a single migration file within a transaction.

        If the migration fails, it is rolled back and a MigrationError is raised.
        """
        checksum = self._compute_checksum(sql_path)
        sql_content = sql_path.read_text(encoding="utf-8")

        try:
            # Execute migration within an explicit transaction
            # We use isolation_level to control commits manually
            self._connection.execute("BEGIN")
            self._connection.executescript(sql_content)
            # Record the migration in the tracking table
            self._connection.execute(
                "INSERT INTO _migrations (version, name, checksum) VALUES (?, ?, ?)",
                (version, name, checksum),
            )
            self._connection.commit()
        except Exception as exc:
            # Rollback the failed migration
            try:
                self._connection.rollback()
            except Exception:
                pass  # Connection may be in a bad state after executescript failure
            detail = f"{type(exc).__name__}: {exc}"
            logger.error(
                "Migration %03d_%s failed — rolled back. Error: %s",
                version, name, detail,
            )
            raise MigrationError(version, name, detail) from exc

        return {
            "version": version,
            "name": name,
            "checksum": checksum,
        }
