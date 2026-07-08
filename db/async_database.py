"""Async database module — non-blocking SQLite wrapper using aiosqlite.

Provides the same interface as the synchronous Database class but with
async/await semantics. Uses an asyncio.Semaphore to limit concurrent
operations (aiosqlite wraps a single connection in a background thread).
"""

import asyncio
import structlog
import os
from typing import Any, List, Optional, Tuple

import aiosqlite

from config import DB_PATH

logger = structlog.get_logger(__name__)


class AsyncDatabase:
    """Async SQLite database wrapper using aiosqlite.

    Uses a single aiosqlite connection with an asyncio.Semaphore(5) to
    limit concurrent operations. SQLite doesn't benefit from true connection
    pooling, but the semaphore prevents excessive queuing on the background thread.

    Supports async context manager:
        async with AsyncDatabase() as db:
            row = await db.fetch_one("SELECT ...")
    """

    def __init__(self, db_path: Optional[str] = None, max_connections: int = 5):
        self._db_path = db_path or DB_PATH
        self._semaphore = asyncio.Semaphore(max_connections)
        self._connection: Optional[aiosqlite.Connection] = None
        self._closed = False

    async def connect(self) -> aiosqlite.Connection:
        """Open the aiosqlite connection if not already open.

        Enables WAL mode and foreign key constraints on the connection.
        """
        if self._connection is None:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            self._connection = await aiosqlite.connect(self._db_path)
            self._connection.row_factory = aiosqlite.Row
            # Enable WAL mode for concurrent read/write
            await self._connection.execute("PRAGMA journal_mode=WAL")
            # Enable foreign key constraints
            await self._connection.execute("PRAGMA foreign_keys=ON")
            self._closed = False
            logger.info("AsyncDatabase connected to %s (WAL mode, FK enabled)", self._db_path)

        return self._connection

    async def execute(self, sql: str, params: Tuple = ()) -> aiosqlite.Cursor:
        """Execute a SQL statement with optional parameters and commit.

        Returns the cursor for access to lastrowid, rowcount, etc.
        """
        async with self._semaphore:
            conn = await self.connect()
            cursor = await conn.execute(sql, params)
            await conn.commit()
            return cursor

    async def executemany(self, sql: str, params_list: List[Tuple]) -> aiosqlite.Cursor:
        """Execute a SQL statement against multiple parameter sets and commit."""
        async with self._semaphore:
            conn = await self.connect()
            cursor = await conn.executemany(sql, params_list)
            await conn.commit()
            return cursor

    async def fetch_one(self, sql: str, params: Tuple = ()) -> Optional[Any]:
        """Execute a query and return the first row, or None."""
        async with self._semaphore:
            conn = await self.connect()
            cursor = await conn.execute(sql, params)
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: Tuple = ()) -> List[Any]:
        """Execute a query and return all rows."""
        async with self._semaphore:
            conn = await self.connect()
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()

    async def close(self) -> None:
        """Close the database connection."""
        if self._connection is not None and not self._closed:
            await self._connection.close()
            self._connection = None
            self._closed = True
            logger.info("AsyncDatabase connection closed")

    # ── Context Manager ────────────────────────────────────

    async def __aenter__(self) -> "AsyncDatabase":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


# ── Module-level singleton ───────────────────────────────────
_async_db_instance: Optional[AsyncDatabase] = None


def get_async_database() -> AsyncDatabase:
    """Return the module-level AsyncDatabase singleton.

    Raises RuntimeError if init_async_db() has not been called.
    """
    if _async_db_instance is None:
        raise RuntimeError(
            "AsyncDatabase not initialized. Call init_async_db() first."
        )
    return _async_db_instance


async def init_async_db(db_path: Optional[str] = None) -> AsyncDatabase:
    """Initialize the async database singleton and open the connection.

    Call this during application startup (e.g., in FastAPI lifespan).
    Returns the singleton instance.
    """
    global _async_db_instance
    if _async_db_instance is None:
        _async_db_instance = AsyncDatabase(db_path=db_path)
        await _async_db_instance.connect()
    return _async_db_instance


async def close_async_db() -> None:
    """Close the async database singleton connection.

    Call this during application shutdown.
    """
    global _async_db_instance
    if _async_db_instance is not None:
        await _async_db_instance.close()
        _async_db_instance = None
