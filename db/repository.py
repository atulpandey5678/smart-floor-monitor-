"""Repository module - CRUD operations for employees, sessions, and alerts.

Uses AsyncDatabase for non-blocking database access. The Repository accepts
any object that implements the same interface (execute, fetch_one, fetch_all)
via duck typing, supporting both the synchronous Database and AsyncDatabase.
"""

import structlog
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional, List, TYPE_CHECKING, Union

from db.database import Database
from db.async_database import AsyncDatabase

if TYPE_CHECKING:  # avoid a runtime db->api layering import; only needed for hints
    from api.ingest_schemas import AlertMsg, MachineEventMsg, SessionRecordMsg

logger = structlog.get_logger(__name__)

# The system is presence-based and does not scan badges, but sessions.badge_id
# is NOT NULL with a FK to employees. Ingested sessions/alerts are tagged with
# this sentinel badge, mirroring engine.session_manager.WORKER_BADGE_ID.
INGEST_WORKER_BADGE_ID = "WORKER"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an idempotent ingest write.

    ``created`` is True when this call persisted a new record (or applied an
    upsert to an existing session), and False when the Event_ID had already been
    persisted and the request was treated as an idempotent no-op. In both cases
    ``event_id`` echoes the accepted Event_ID so the endpoint can return HTTP 200.
    """

    event_id: str
    created: bool

    @property
    def already_persisted(self) -> bool:
        return not self.created

    @property
    def status(self) -> str:
        return "created" if self.created else "already_persisted"


class Repository:
    """CRUD operations for employees, sessions, and alerts.

    Accepts either a synchronous Database or an AsyncDatabase instance.
    All methods are async and await database calls, so an AsyncDatabase
    should be provided for production use.
    """

    def __init__(self, db: Union[Database, AsyncDatabase]):
        self.db = db

    # ── Helpers ────────────────────────────────────────────────

    def _row_to_dict(self, row) -> Optional[dict]:
        """Convert a sqlite3.Row to a plain dict, or return None."""
        if row is None:
            return None
        return dict(row)

    def _rows_to_dicts(self, rows) -> List[dict]:
        """Convert a list of sqlite3.Row objects to a list of dicts."""
        return [dict(r) for r in rows]

    # ── Employees ──────────────────────────────────────────────

    async def upsert_employee(self, badge_id: str, name: str) -> dict:
        """Create or update an employee record."""
        try:
            await self.db.execute(
                "INSERT OR REPLACE INTO employees (badge_id, name) VALUES (?, ?)",
                (badge_id, name),
            )
            logger.info("Upserted employee: badge_id=%s, name=%s", badge_id, name)
            return await self.get_employee(badge_id)
        except Exception as e:
            logger.error("Failed to upsert employee %s: %s", badge_id, e)
            raise

    async def get_employee(self, badge_id: str) -> Optional[dict]:
        """Get a single employee by badge ID. Returns None if not found."""
        try:
            row = await self.db.fetch_one(
                "SELECT badge_id, name, created_at FROM employees WHERE badge_id = ?",
                (badge_id,),
            )
            return self._row_to_dict(row)
        except Exception as e:
            logger.error("Failed to get employee %s: %s", badge_id, e)
            raise

    async def get_all_employees(self, limit: Optional[int] = None, offset: int = 0) -> List[dict]:
        """Get all registered employees with optional pagination."""
        try:
            query = "SELECT badge_id, name, created_at FROM employees ORDER BY name"
            params: tuple = ()
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params = (limit, offset)
            rows = await self.db.fetch_all(query, params)
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get all employees: %s", e)
            raise

    async def count_employees(self) -> int:
        """Count total number of employees."""
        try:
            row = await self.db.fetch_one("SELECT COUNT(*) as cnt FROM employees")
            return row["cnt"] if row else 0
        except Exception as e:
            logger.error("Failed to count employees: %s", e)
            raise

    # ── Date-Range Queries ─────────────────────────────────────

    async def get_sessions_for_date(self, target_date: date) -> List[dict]:
        """Get all sessions that started on a specific calendar date."""
        try:
            day_start = datetime.combine(target_date, datetime.min.time())
            day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
            rows = await self.db.fetch_all(
                """SELECT s.id, s.badge_id, s.machine_id, s.start_time, s.end_time,
                          s.active_duration_seconds, s.active_duration_seconds as duration_seconds,
                          s.state, s.close_reason,
                          COALESCE(e.name, s.badge_id) as employee_name
                   FROM sessions s
                   LEFT JOIN employees e ON s.badge_id = e.badge_id
                   WHERE s.start_time >= ? AND s.start_time < ?
                   ORDER BY s.start_time DESC""",
                (day_start.isoformat(), day_end.isoformat()),
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get sessions for date %s: %s", target_date, e)
            raise

    async def get_alerts_for_date(self, target_date: date) -> List[dict]:
        """Get all alerts created on a specific calendar date."""
        try:
            day_start = datetime.combine(target_date, datetime.min.time())
            day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
            rows = await self.db.fetch_all(
                """SELECT a.id, a.badge_id, a.alert_type, a.message,
                          a.resolved, a.root_cause, a.created_at,
                          COALESCE(e.name, a.badge_id) as employee_name
                   FROM alerts a
                   LEFT JOIN employees e ON a.badge_id = e.badge_id
                   WHERE a.created_at >= ? AND a.created_at < ?
                   ORDER BY a.created_at DESC""",
                (day_start.isoformat(), day_end.isoformat()),
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get alerts for date %s: %s", target_date, e)
            raise

    # ── Sessions ──────────────────────────────────────────────

    async def create_session(self, badge_id: str, start_time: datetime, machine_id: str = 'M-01') -> int:
        """Create a new session record. Returns the new session ID."""
        try:
            cursor = await self.db.execute(
                """INSERT INTO sessions (badge_id, machine_id, start_time, state)
                   VALUES (?, ?, ?, ?)""",
                (badge_id, machine_id, start_time.isoformat(), "ACTIVE"),
            )
            session_id = cursor.lastrowid
            logger.info("Created session %d for badge %s at %s", session_id, badge_id, start_time)
            return session_id
        except Exception as e:
            logger.error("Failed to create session for %s: %s", badge_id, e)
            raise

    async def update_session(self, session_id: int, active_duration: float, state: str):
        """Update session's active duration and state."""
        try:
            await self.db.execute(
                """UPDATE sessions
                   SET active_duration_seconds = ?, state = ?
                   WHERE id = ?""",
                (active_duration, state, session_id),
            )
        except Exception as e:
            logger.error("Failed to update session %d: %s", session_id, e)
            raise

    async def close_session(self, session_id: int, end_time: datetime, active_duration: float, close_reason: str):
        """Close a session with end time, final duration, and reason."""
        try:
            await self.db.execute(
                """UPDATE sessions
                   SET end_time = ?, active_duration_seconds = ?,
                       state = 'CLOSED', close_reason = ?
                   WHERE id = ?""",
                (end_time.isoformat(), active_duration, close_reason, session_id),
            )
            logger.info("Closed session %d: reason=%s, duration=%.1fs", session_id, close_reason, active_duration)
        except Exception as e:
            logger.error("Failed to close session %d: %s", session_id, e)
            raise

    async def get_today_sessions(self, machine_id: Optional[str] = None, limit: Optional[int] = None, offset: int = 0) -> List[dict]:
        """Get all sessions for the current day with optional machine_id filtering and pagination."""
        try:
            today_start = datetime.combine(date.today(), datetime.min.time())
            conditions = ["s.start_time >= ?"]
            params: list = [today_start.isoformat()]

            if machine_id:
                conditions.append("s.machine_id = ?")
                params.append(machine_id)

            where_clause = " AND ".join(conditions)
            query = f"""SELECT s.id, s.badge_id, s.machine_id, s.start_time, s.end_time,
                          s.active_duration_seconds, s.active_duration_seconds as duration_seconds,
                          s.state, s.close_reason,
                          COALESCE(e.name, s.badge_id) as employee_name
                   FROM sessions s
                   LEFT JOIN employees e ON s.badge_id = e.badge_id
                   WHERE {where_clause}
                   ORDER BY s.start_time DESC"""
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            rows = await self.db.fetch_all(query, tuple(params))
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get today's sessions: %s", e)
            raise

    async def count_today_sessions(self, machine_id: Optional[str] = None) -> int:
        """Count total sessions for the current day, optionally filtered by machine_id."""
        try:
            today_start = datetime.combine(date.today(), datetime.min.time())
            conditions = ["start_time >= ?"]
            params: list = [today_start.isoformat()]

            if machine_id:
                conditions.append("machine_id = ?")
                params.append(machine_id)

            where_clause = " AND ".join(conditions)
            row = await self.db.fetch_one(
                f"SELECT COUNT(*) as cnt FROM sessions WHERE {where_clause}",
                tuple(params),
            )
            return row["cnt"] if row else 0
        except Exception as e:
            logger.error("Failed to count today's sessions: %s", e)
            raise

    async def get_history_sessions(self, days: int = 7, machine_id: Optional[str] = None, limit: Optional[int] = None, offset: int = 0) -> List[dict]:
        """Get sessions from the last N days with optional machine_id filtering and pagination."""
        try:
            start_date = datetime.combine(date.today() - timedelta(days=days), datetime.min.time())
            conditions = ["s.start_time >= ?"]
            params: list = [start_date.isoformat()]

            if machine_id:
                conditions.append("s.machine_id = ?")
                params.append(machine_id)

            where_clause = " AND ".join(conditions)
            query = f"""SELECT s.id, s.badge_id, s.machine_id, s.start_time, s.end_time,
                          s.active_duration_seconds, s.active_duration_seconds as duration_seconds,
                          s.state, s.close_reason,
                          COALESCE(e.name, s.badge_id) as employee_name
                   FROM sessions s
                   LEFT JOIN employees e ON s.badge_id = e.badge_id
                   WHERE {where_clause}
                   ORDER BY s.start_time DESC"""
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            rows = await self.db.fetch_all(query, tuple(params))
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get history sessions: %s", e)
            raise

    async def count_history_sessions(self, days: int = 7, machine_id: Optional[str] = None) -> int:
        """Count total sessions from the last N days, optionally filtered by machine_id."""
        try:
            start_date = datetime.combine(date.today() - timedelta(days=days), datetime.min.time())
            conditions = ["start_time >= ?"]
            params: list = [start_date.isoformat()]

            if machine_id:
                conditions.append("machine_id = ?")
                params.append(machine_id)

            where_clause = " AND ".join(conditions)
            row = await self.db.fetch_one(
                f"SELECT COUNT(*) as cnt FROM sessions WHERE {where_clause}",
                tuple(params),
            )
            return row["cnt"] if row else 0
        except Exception as e:
            logger.error("Failed to count history sessions: %s", e)
            raise

    async def get_active_session(self) -> Optional[dict]:
        """Get the currently active session."""
        try:
            row = await self.db.fetch_one(
                """SELECT s.id, s.badge_id, s.start_time, s.end_time,
                          s.active_duration_seconds, s.state, s.close_reason,
                          e.name as employee_name
                   FROM sessions s
                   LEFT JOIN employees e ON s.badge_id = e.badge_id
                   WHERE s.state NOT IN ('CLOSED', 'ABANDONED', 'EXCEPTION')
                   ORDER BY s.start_time DESC
                   LIMIT 1"""
            )
            return self._row_to_dict(row)
        except Exception as e:
            logger.error("Failed to get active session: %s", e)
            raise

    # ── Alerts ──────────────────────────────────────────────

    async def create_alert(self, badge_id: str, alert_type: str, message: str = None, machine_id: Optional[str] = None) -> int:
        """Create a new alert record. Returns the new alert ID."""
        try:
            cursor = await self.db.execute(
                """INSERT INTO alerts (badge_id, alert_type, message, machine_id)
                   VALUES (?, ?, ?, ?)""",
                (badge_id, alert_type, message, machine_id),
            )
            alert_id = cursor.lastrowid
            logger.info(
                "Created alert %d: badge=%s, type=%s, machine=%s",
                alert_id, badge_id, alert_type, machine_id,
            )
            return alert_id
        except Exception as e:
            logger.error("Failed to create alert for %s: %s", badge_id, e)
            raise

    async def resolve_alert(self, alert_id: int, root_cause: str = None) -> bool:
        """Mark an alert as resolved. Returns True if alert was found and updated."""
        try:
            cursor = await self.db.execute(
                "UPDATE alerts SET resolved = 1, root_cause = ? WHERE id = ? AND resolved = 0",
                (root_cause, alert_id),
            )
            resolved = cursor.rowcount > 0
            if resolved:
                logger.info("Resolved alert %d (cause: %s)", alert_id, root_cause)
            else:
                logger.warning("Alert %d not found or already resolved", alert_id)
            return resolved
        except Exception as e:
            logger.error("Failed to resolve alert %d: %s", alert_id, e)
            raise

    async def get_unresolved_alerts(self, machine_id: Optional[str] = None, resolved: Optional[bool] = None, limit: Optional[int] = None, offset: int = 0) -> List[dict]:
        """Get alerts with optional filtering by machine_id and resolved status.
        
        By default (resolved=None), returns only unresolved alerts for backward compatibility.
        If resolved=True, returns only resolved alerts.
        If resolved=False, returns only unresolved alerts.
        """
        try:
            conditions: list = []
            params: list = []

            # Default behavior: unresolved only (backward compat)
            if resolved is None:
                conditions.append("a.resolved = 0")
            elif resolved is False:
                conditions.append("a.resolved = 0")
            else:
                conditions.append("a.resolved = 1")

            if machine_id:
                conditions.append("a.machine_id = ?")
                params.append(machine_id)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            query = f"""SELECT a.id, a.badge_id, a.machine_id, a.alert_type, a.message,
                          a.resolved, a.root_cause, a.created_at, a.event_image_url,
                          e.name as employee_name
                   FROM alerts a
                   LEFT JOIN employees e ON a.badge_id = e.badge_id
                   WHERE {where_clause}
                   ORDER BY a.created_at DESC"""
            if limit is not None:
                query += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            rows = await self.db.fetch_all(query, tuple(params))
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get alerts: %s", e)
            raise

    async def count_unresolved_alerts(self, machine_id: Optional[str] = None, resolved: Optional[bool] = None) -> int:
        """Count alerts with optional filtering by machine_id and resolved status.
        
        By default (resolved=None), counts only unresolved alerts.
        """
        try:
            conditions: list = []
            params: list = []

            if resolved is None:
                conditions.append("resolved = 0")
            elif resolved is False:
                conditions.append("resolved = 0")
            else:
                conditions.append("resolved = 1")

            if machine_id:
                conditions.append("machine_id = ?")
                params.append(machine_id)

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            row = await self.db.fetch_one(
                f"SELECT COUNT(*) as cnt FROM alerts WHERE {where_clause}",
                tuple(params),
            )
            return row["cnt"] if row else 0
        except Exception as e:
            logger.error("Failed to count alerts: %s", e)
            raise

    async def get_alert(self, alert_id: int) -> Optional[dict]:
        """Get a single alert by ID. Returns None if not found."""
        try:
            row = await self.db.fetch_one(
                """SELECT id, badge_id, alert_type, message, resolved, root_cause, created_at
                   FROM alerts WHERE id = ?""",
                (alert_id,),
            )
            return self._row_to_dict(row)
        except Exception as e:
            logger.error("Failed to get alert %d: %s", alert_id, e)
            raise

    async def get_alerts_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get all alerts (resolved + unresolved) ordered by most recent, with pagination."""
        try:
            rows = await self.db.fetch_all(
                """SELECT id, badge_id, machine_id, alert_type, message, resolved,
                          root_cause, created_at, event_image_url
                   FROM alerts
                   ORDER BY created_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get alerts history: %s", e)
            raise

    # ── Machine State Events ───────────────────────────────────

    async def create_machine_state_event(
        self, machine_id: str, previous_status: str, new_status: str, timestamp: datetime
    ) -> int:
        """Insert a machine state transition event. Returns the new event ID."""
        try:
            cursor = await self.db.execute(
                """INSERT INTO machine_state_events (machine_id, previous_status, new_status, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (machine_id, previous_status, new_status, timestamp.isoformat()),
            )
            event_id = cursor.lastrowid
            logger.info(
                "Machine state event %d: %s %s → %s",
                event_id, machine_id, previous_status, new_status,
            )
            return event_id
        except Exception as e:
            logger.error("Failed to create machine state event: %s", e)
            raise

    async def get_machine_state_events(
        self, machine_id: str, date_from: Optional[date] = None, date_to: Optional[date] = None
    ) -> List[dict]:
        """Retrieve machine state events for a given machine within a date range.

        Results are ordered by timestamp descending.
        """
        try:
            conditions = ["machine_id = ?"]
            params: list = [machine_id]

            if date_from:
                day_start = datetime.combine(date_from, datetime.min.time())
                conditions.append("timestamp >= ?")
                params.append(day_start.isoformat())

            if date_to:
                day_end = datetime.combine(date_to + timedelta(days=1), datetime.min.time())
                conditions.append("timestamp < ?")
                params.append(day_end.isoformat())

            where_clause = " AND ".join(conditions)
            rows = await self.db.fetch_all(
                f"""SELECT id, machine_id, previous_status, new_status, timestamp, created_at
                    FROM machine_state_events
                    WHERE {where_clause}
                    ORDER BY timestamp DESC""",
                tuple(params),
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get machine state events for %s: %s", machine_id, e)
            raise

    # ── Idempotent Ingest (Edge → Cloud) ───────────────────────

    async def event_exists(self, event_id: str) -> bool:
        """Return True if an Event_ID has already been persisted (any kind)."""
        try:
            row = await self.db.fetch_one(
                "SELECT 1 FROM ingested_events WHERE event_id = ? LIMIT 1",
                (event_id,),
            )
            return row is not None
        except Exception as e:
            logger.error("Failed to check event existence for %s: %s", event_id, e)
            raise

    async def _claim_event(self, conn, event_id: str, machine_id: str, kind: str, produced_at: str) -> bool:
        """Insert into the idempotency ledger inside an open transaction.

        Returns True if this Event_ID was newly claimed (0 → 1 row inserted), or
        False if it was already present (ON CONFLICT DO NOTHING → 0 rows). Must be
        called on the connection yielded by ``AsyncDatabase.transaction()``.
        """
        cursor = await conn.execute(
            """INSERT INTO ingested_events (event_id, machine_id, kind, produced_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(event_id) DO NOTHING""",
            (event_id, machine_id, kind, produced_at),
        )
        return cursor.rowcount == 1

    async def ingest_session(self, msg: "SessionRecordMsg") -> IngestResult:
        """Idempotently persist a Session_Record push (open/update/close).

        Idempotent by ``event_id`` (via the ingested_events ledger) and upserted
        by ``session_uuid`` so open/update/close pushes for one session collapse
        into a single row. A ``close`` push with no matching existing session
        creates a new row marked CLOSED (orphan close, Requirement 5.7). Every row
        is tagged with the originating machine ID (Requirements 2.7, 16.2). The
        ledger claim and the domain upsert commit atomically; on duplicate the
        existing record is left untouched (Requirements 5.1, 5.6).
        """
        try:
            is_close = msg.op == "close"
            state = "CLOSED" if is_close else "ACTIVE"
            end_time = msg.end_time.isoformat() if msg.end_time else None
            close_reason = msg.close_reason if is_close else None

            async with self.db.transaction() as conn:
                claimed = await self._claim_event(
                    conn, msg.event_id, msg.machine_id, "session", msg.produced_at.isoformat()
                )
                if not claimed:
                    logger.info("Duplicate session event %s ignored", msg.event_id)
                    return IngestResult(event_id=msg.event_id, created=False)

                # Satisfy the NOT NULL + FK on sessions.badge_id (presence-based).
                await conn.execute(
                    "INSERT OR IGNORE INTO employees (badge_id, name) VALUES (?, ?)",
                    (INGEST_WORKER_BADGE_ID, "Worker"),
                )

                # Upsert the session identified by session_uuid.
                await conn.execute(
                    """INSERT INTO sessions
                           (badge_id, machine_id, start_time, end_time,
                            active_duration_seconds, state, close_reason,
                            event_id, session_uuid)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(session_uuid) DO UPDATE SET
                           machine_id = excluded.machine_id,
                           start_time = excluded.start_time,
                           end_time = excluded.end_time,
                           active_duration_seconds = excluded.active_duration_seconds,
                           state = excluded.state,
                           close_reason = excluded.close_reason,
                           event_id = excluded.event_id""",
                    (
                        INGEST_WORKER_BADGE_ID,
                        msg.machine_id,
                        msg.start_time.isoformat(),
                        end_time,
                        msg.active_duration_seconds,
                        state,
                        close_reason,
                        msg.event_id,
                        msg.session_uuid,
                    ),
                )

            logger.info(
                "Ingested session event %s (uuid=%s, op=%s, machine=%s)",
                msg.event_id, msg.session_uuid, msg.op, msg.machine_id,
            )
            return IngestResult(event_id=msg.event_id, created=True)
        except Exception as e:
            logger.error("Failed to ingest session %s: %s", msg.event_id, e)
            raise

    async def ingest_alert(self, msg: "AlertMsg", image_url: str) -> IngestResult:
        """Idempotently persist an Alert push, storing the Event_Image URL.

        Idempotent by ``event_id``: on a duplicate, no new alert row is created
        and the existing record is left unchanged (Requirements 5.1, 5.6). The
        alert is tagged with the originating machine ID (Requirements 2.7, 16.2).
        The caller uploads the image to the Object_Store and passes the resulting
        URL, which is stored on the alert row in the same transaction.
        """
        try:
            async with self.db.transaction() as conn:
                claimed = await self._claim_event(
                    conn, msg.event_id, msg.machine_id, "alert", msg.produced_at.isoformat()
                )
                if not claimed:
                    logger.info("Duplicate alert event %s ignored", msg.event_id)
                    return IngestResult(event_id=msg.event_id, created=False)

                await conn.execute(
                    """INSERT INTO alerts
                           (badge_id, alert_type, message, machine_id,
                            event_id, event_image_url, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        INGEST_WORKER_BADGE_ID,
                        msg.alert_type,
                        msg.message,
                        msg.machine_id,
                        msg.event_id,
                        image_url,
                        msg.produced_at.isoformat(),
                    ),
                )

            logger.info(
                "Ingested alert event %s (type=%s, machine=%s)",
                msg.event_id, msg.alert_type, msg.machine_id,
            )
            return IngestResult(event_id=msg.event_id, created=True)
        except Exception as e:
            logger.error("Failed to ingest alert %s: %s", msg.event_id, e)
            raise

    async def ingest_machine_event(self, msg: "MachineEventMsg") -> IngestResult:
        """Idempotently persist a Machine_Event (tower-light transition).

        Idempotent by ``event_id``; on a duplicate no new row is created and
        existing data is untouched (Requirements 5.1, 5.6). Tagged with the
        originating machine ID (Requirements 2.7, 16.2).
        """
        try:
            async with self.db.transaction() as conn:
                claimed = await self._claim_event(
                    conn, msg.event_id, msg.machine_id, "machine_event", msg.produced_at.isoformat()
                )
                if not claimed:
                    logger.info("Duplicate machine event %s ignored", msg.event_id)
                    return IngestResult(event_id=msg.event_id, created=False)

                await conn.execute(
                    """INSERT INTO machine_state_events
                           (machine_id, previous_status, new_status, timestamp, event_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        msg.machine_id,
                        msg.previous_status,
                        msg.new_status,
                        msg.produced_at.isoformat(),
                        msg.event_id,
                    ),
                )

            logger.info(
                "Ingested machine event %s (%s: %s → %s)",
                msg.event_id, msg.machine_id, msg.previous_status, msg.new_status,
            )
            return IngestResult(event_id=msg.event_id, created=True)
        except Exception as e:
            logger.error("Failed to ingest machine event %s: %s", msg.event_id, e)
            raise
