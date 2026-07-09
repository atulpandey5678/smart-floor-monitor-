import asyncio, tempfile, os, sys
from datetime import date, datetime

from db.async_database import AsyncDatabase
from db.repository import Repository
from engine.report_engine import ReportEngine


async def main():
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "smoke.db")
    db = AsyncDatabase(db_path=db_path)
    await db.connect()
    # Minimal schema needed by the report queries
    await db.execute("""CREATE TABLE employees (badge_id TEXT PRIMARY KEY, name TEXT)""")
    await db.execute("""CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, badge_id TEXT, machine_id TEXT,
        start_time TEXT, end_time TEXT, active_duration_seconds REAL,
        state TEXT, close_reason TEXT)""")
    await db.execute("""CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, badge_id TEXT, alert_type TEXT,
        message TEXT, resolved INTEGER DEFAULT 0, root_cause TEXT, created_at TEXT,
        machine_id TEXT)""")

    today = date.today()
    st = datetime.combine(today, datetime.min.time()).replace(hour=9)
    await db.execute("INSERT INTO employees (badge_id, name) VALUES (?, ?)", ("B1", "Alice"))
    await db.execute(
        "INSERT INTO sessions (badge_id, machine_id, start_time, end_time, active_duration_seconds, state, close_reason) VALUES (?,?,?,?,?,?,?)",
        ("B1", "M-01", st.isoformat(), st.isoformat(), 3600.0, "CLOSED", "normal"),
    )
    await db.execute(
        "INSERT INTO alerts (badge_id, alert_type, message, resolved, created_at, machine_id) VALUES (?,?,?,?,?,?)",
        ("B1", "downtime", "idle", 0, st.isoformat(), "M-01"),
    )

    repo = Repository(db)
    engine = ReportEngine(repo, shift_hours=8.0)
    dr = await engine.daily_report(today)
    assert dr.total_sessions == 1, dr.total_sessions
    assert dr.total_active_hours == 1.0, dr.total_active_hours
    assert dr.machine_utilization.get("M-01") == 12.5, dr.machine_utilization
    assert dr.alerts_summary.get("downtime") == 1, dr.alerts_summary
    csv_out = engine._format_csv(dr)
    assert "Daily Report" in csv_out and "Machine Utilization" in csv_out
    # AI summary degrades gracefully with no valid key
    summary = engine.generate_ai_summary(dr)
    assert "AI Summary" in summary or "AI Shift Summary" in summary
    print("SMOKE OK:", dr.report_date, dr.total_sessions, dr.total_active_hours, dr.machine_utilization)
    await db.close()


asyncio.run(main())
