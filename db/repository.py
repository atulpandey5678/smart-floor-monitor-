"""Repository module - CRUD operations for employees, sessions, and alerts."""

import logging
from datetime import datetime, date, timedelta
from typing import Optional, List

from db.database import Database

logger = logging.getLogger(__name__)


class Repository:
    """CRUD operations for employees, sessions, and alerts."""

    def __init__(self, db: Database):
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

    def upsert_employee(self, badge_id: str, name: str) -> dict:
        """Create or update an employee record.

        Uses INSERT OR REPLACE to handle both creation and updates.
        Returns the employee dict.
        """
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO employees (badge_id, name) VALUES (?, ?)",
                (badge_id, name),
            )
            logger.info("Upserted employee: badge_id=%s, name=%s", badge_id, name)
            return self.get_employee(badge_id)
        except Exception as e:
            logger.error("Failed to upsert employee %s: %s", badge_id, e)
            raise

    def get_employee(self, badge_id: str) -> Optional[dict]:
        """Get a single employee by badge ID. Returns None if not found."""
        try:
            row = self.db.fetch_one(
                "SELECT badge_id, name, created_at FROM employees WHERE badge_id = ?",
                (badge_id,),
            )
            return self._row_to_dict(row)
        except Exception as e:
            logger.error("Failed to get employee %s: %s", badge_id, e)
            raise

    def get_all_employees(self) -> List[dict]:
        """Get all registered employees."""
        try:
            rows = self.db.fetch_all(
                "SELECT badge_id, name, created_at FROM employees ORDER BY name"
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get all employees: %s", e)
            raise

    # ── Date-Range Queries ─────────────────────────────────────

    def get_sessions_for_date(self, target_date: date) -> List[dict]:
        """Get all sessions that started on a specific calendar date."""
        try:
            day_start = datetime.combine(target_date, datetime.min.time())
            day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
            rows = self.db.fetch_all(
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

    def get_alerts_for_date(self, target_date: date) -> List[dict]:
        """Get all alerts created on a specific calendar date."""
        try:
            day_start = datetime.combine(target_date, datetime.min.time())
            day_end = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
            rows = self.db.fetch_all(
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

    def create_session(self, badge_id: str, start_time: datetime, machine_id: str = 'M-01') -> int:
        """Create a new session record. Returns the new session ID."""
        try:
            cursor = self.db.execute(
                """INSERT INTO sessions (badge_id, machine_id, start_time, state)
                   VALUES (?, ?, ?, ?)""",
                (badge_id, machine_id, start_time.isoformat(), "ACTIVE"),
            )
            session_id = cursor.lastrowid
            logger.info(
                "Created session %d for badge %s at %s",
                session_id, badge_id, start_time,
            )
            return session_id
        except Exception as e:
            logger.error("Failed to create session for %s: %s", badge_id, e)
            raise

    def update_session(self, session_id: int, active_duration: float, state: str):
        """Update session's active duration and state (for in-progress updates)."""
        try:
            self.db.execute(
                """UPDATE sessions
                   SET active_duration_seconds = ?, state = ?
                   WHERE id = ?""",
                (active_duration, state, session_id),
            )
            logger.debug(
                "Updated session %d: duration=%.1fs, state=%s",
                session_id, active_duration, state,
            )
        except Exception as e:
            logger.error("Failed to update session %d: %s", session_id, e)
            raise

    def close_session(
        self,
        session_id: int,
        end_time: datetime,
        active_duration: float,
        close_reason: str,
    ):
        """Close a session with end time, final duration, and reason."""
        try:
            self.db.execute(
                """UPDATE sessions
                   SET end_time = ?, active_duration_seconds = ?,
                       state = 'CLOSED', close_reason = ?
                   WHERE id = ?""",
                (end_time.isoformat(), active_duration, close_reason, session_id),
            )
            logger.info(
                "Closed session %d: reason=%s, duration=%.1fs",
                session_id, close_reason, active_duration,
            )
        except Exception as e:
            logger.error("Failed to close session %d: %s", session_id, e)
            raise

    def get_today_sessions(self) -> List[dict]:
        """Get all sessions for the current day."""
        try:
            today_start = datetime.combine(date.today(), datetime.min.time())
            rows = self.db.fetch_all(
                """SELECT s.id, s.badge_id, s.machine_id, s.start_time, s.end_time,
                          s.active_duration_seconds, s.active_duration_seconds as duration_seconds,
                          s.state, s.close_reason,
                          COALESCE(e.name, s.badge_id) as employee_name
                   FROM sessions s
                   LEFT JOIN employees e ON s.badge_id = e.badge_id
                   WHERE s.start_time >= ?
                   ORDER BY s.start_time DESC""",
                (today_start.isoformat(),),
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get today's sessions: %s", e)
            raise

    def get_history_sessions(self, days: int = 7) -> List[dict]:
        """Get sessions from the last N days."""
        try:
            start_date = datetime.combine(
                date.today() - timedelta(days=days), datetime.min.time()
            )
            rows = self.db.fetch_all(
                """SELECT s.id, s.badge_id, s.machine_id, s.start_time, s.end_time,
                          s.active_duration_seconds, s.active_duration_seconds as duration_seconds,
                          s.state, s.close_reason,
                          COALESCE(e.name, s.badge_id) as employee_name
                   FROM sessions s
                   LEFT JOIN employees e ON s.badge_id = e.badge_id
                   WHERE s.start_time >= ?
                   ORDER BY s.start_time DESC""",
                (start_date.isoformat(),),
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get history sessions: %s", e)
            raise

    def get_active_session(self) -> Optional[dict]:
        """Get the currently active session (not in a terminal state)."""
        try:
            row = self.db.fetch_one(
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

    def create_alert(self, badge_id: str, alert_type: str, message: str = None) -> int:
        """Create a new alert record. Returns the new alert ID."""
        try:
            cursor = self.db.execute(
                """INSERT INTO alerts (badge_id, alert_type, message)
                   VALUES (?, ?, ?)""",
                (badge_id, alert_type, message),
            )
            alert_id = cursor.lastrowid
            logger.info(
                "Created alert %d: badge=%s, type=%s",
                alert_id, badge_id, alert_type,
            )
            return alert_id
        except Exception as e:
            logger.error("Failed to create alert for %s: %s", badge_id, e)
            raise

    def resolve_alert(self, alert_id: int, root_cause: str = None) -> bool:
        """Mark an alert as resolved. Returns True if alert was found and updated."""
        try:
            cursor = self.db.execute(
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

    def get_unresolved_alerts(self) -> List[dict]:
        """Get all unresolved alerts."""
        try:
            rows = self.db.fetch_all(
                """SELECT a.id, a.badge_id, a.alert_type, a.message,
                          a.resolved, a.root_cause, a.created_at, e.name as employee_name
                   FROM alerts a
                   LEFT JOIN employees e ON a.badge_id = e.badge_id
                   WHERE a.resolved = 0
                   ORDER BY a.created_at DESC"""
            )
            return self._rows_to_dicts(rows)
        except Exception as e:
            logger.error("Failed to get unresolved alerts: %s", e)
            raise

    def get_alert(self, alert_id: int) -> Optional[dict]:
        """Get a single alert by ID. Returns None if not found."""
        try:
            row = self.db.fetch_one(
                """SELECT id, badge_id, alert_type, message, resolved, root_cause, created_at
                   FROM alerts WHERE id = ?""",
                (alert_id,),
            )
            return self._row_to_dict(row)
        except Exception as e:
            logger.error("Failed to get alert %d: %s", alert_id, e)
            raise

    # ── Machine State Events ───────────────────────────────────

    def create_machine_state_event(
        self, machine_id: str, previous_status: str, new_status: str, timestamp: datetime
    ) -> int:
        """Insert a machine state transition event. Returns the new event ID."""
        try:
            cursor = self.db.execute(
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

    def get_machine_state_events(
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
            rows = self.db.fetch_all(
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
