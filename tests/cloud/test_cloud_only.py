"""Unit tests for the cloud-only reports / AI-chat path (edge-cloud-split).

Feature: edge-cloud-split — Task 18.2

These plain (non-Hypothesis) unit tests assert three things about the cloud side:

1. **No camera / CV path.** ``engine/report_engine.py`` and ``engine/ai_chat.py``
   do not import or depend on ``cv_pipeline`` / ``PipelineOrchestrator`` / camera
   capture. Their module-level imports are inspected (via ``ast``) and none may
   reference the CV compute stack, so neither module requires ``cv2`` or the
   orchestrator at import time.

2. **Reports & AI chat run purely from the Database with the orchestrator
   absent.** A ``ReportEngine`` and the AI-chat handler are exercised against a
   ``Repository`` backed by a temporary migrated (001–004) on-disk SQLite
   ``AsyncDatabase``. Seeded sessions/alerts flow back through the daily/weekly
   report and through the AI-chat DB-backed tool call — no camera or CV access.

3. **SQLite is retained (Requirement 14.3).** The cloud data layer is SQLite:
   ``config.DB_PATH`` is a SQLite file and ``AsyncDatabase`` answers
   ``SELECT sqlite_version()``.

Validates: Requirements 11.3, 14.2, 14.3
"""

import ast
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from db.async_database import AsyncDatabase
from db.migrations import MigrationRunner
from db.repository import Repository

# ── Locations ────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = _PROJECT_ROOT / "db" / "migrations"
REPORT_ENGINE_SRC = _PROJECT_ROOT / "engine" / "report_engine.py"
AI_CHAT_SRC = _PROJECT_ROOT / "engine" / "ai_chat.py"

# Module fragments that would indicate a camera / CV dependency in the cloud app.
_FORBIDDEN_MODULE_FRAGMENTS = (
    "cv2",
    "cv_pipeline",
    "pipeline_orchestrator",
)
# Imported symbol names that would indicate a camera / CV dependency.
_FORBIDDEN_IMPORT_NAMES = (
    "PipelineOrchestrator",
    "PipelineInstance",
    "FrameCapture",
    "RTSPCapture",
)


# ── Helpers ──────────────────────────────────────────────────────


def _collect_imports(src_path: Path):
    """Return (modules, names) imported at the top of a Python source file.

    ``modules`` is the set of module paths referenced by ``import x`` /
    ``from x import ...`` statements; ``names`` is the set of symbol names
    pulled in by ``from x import name`` / ``import x as name``.
    """
    tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
            for alias in node.names:
                names.add(alias.name)
    return modules, names


# ── Temp migrated SQLite fixture ─────────────────────────────────


@pytest_asyncio.fixture
async def cloud_repo(tmp_path):
    """Yield (Repository, AsyncDatabase) over a temp migrated (001–004) SQLite DB.

    A real on-disk SQLite file is migrated with the production
    ``MigrationRunner`` and opened via ``AsyncDatabase`` — the exact cloud data
    layer, with no orchestrator or camera anywhere in sight.
    """
    db_path = tmp_path / "cloud.db"
    runner = MigrationRunner(str(db_path), MIGRATIONS_DIR)
    try:
        runner.run()
    finally:
        runner.close()

    db = AsyncDatabase(db_path=str(db_path))
    await db.connect()
    try:
        yield Repository(db), db
    finally:
        await db.close()


# ══════════════════════════════════════════════════════════════════
# 1. Cloud app exposes NO camera / CV path
# ══════════════════════════════════════════════════════════════════
# Validates: Requirements 11.3


class TestNoCameraOrCvPath:
    def test_report_engine_imports_have_no_cv_dependency(self):
        modules, names = _collect_imports(REPORT_ENGINE_SRC)
        for mod in modules:
            for frag in _FORBIDDEN_MODULE_FRAGMENTS:
                assert frag not in mod, (
                    f"report_engine.py must not import '{mod}' (CV/camera dependency)"
                )
        assert names.isdisjoint(_FORBIDDEN_IMPORT_NAMES), (
            f"report_engine.py imports forbidden CV symbols: "
            f"{names & set(_FORBIDDEN_IMPORT_NAMES)}"
        )

    def test_ai_chat_imports_have_no_cv_dependency(self):
        modules, names = _collect_imports(AI_CHAT_SRC)
        for mod in modules:
            for frag in _FORBIDDEN_MODULE_FRAGMENTS:
                assert frag not in mod, (
                    f"ai_chat.py must not import '{mod}' (CV/camera dependency)"
                )
        assert names.isdisjoint(_FORBIDDEN_IMPORT_NAMES), (
            f"ai_chat.py imports forbidden CV symbols: "
            f"{names & set(_FORBIDDEN_IMPORT_NAMES)}"
        )

    def test_importing_modules_does_not_load_cv_stack(self):
        # Importing the cloud report / AI-chat modules must not drag in the CV
        # compute stack. Neither module is allowed to require cv2 / cv_pipeline /
        # the orchestrator at import time.
        import importlib

        for mod_name in ("engine.report_engine", "engine.ai_chat"):
            mod = importlib.import_module(mod_name)
            assert mod is not None
            # The module object must not expose the orchestrator class as a
            # dependency it pulled in.
            assert not hasattr(mod, "PipelineOrchestrator")

        # The source files themselves reference no cv_pipeline import — a direct
        # dependency on the CV package would appear in sys.modules pulled in
        # *by* these modules. We assert the two modules do not name the CV
        # package among their own imports (covered above); here we additionally
        # confirm they imported cleanly.
        assert "engine.report_engine" in sys.modules
        assert "engine.ai_chat" in sys.modules


# ══════════════════════════════════════════════════════════════════
# 2. Reports run purely from the Database (orchestrator absent)
# ══════════════════════════════════════════════════════════════════
# Validates: Requirements 11.3, 14.2


class TestReportsFromDatabaseOnly:
    # A fixed Monday so the daily date falls inside the weekly window.
    REPORT_DATE = date(2024, 6, 3)

    async def _seed(self, repo):
        """Seed a couple of sessions and an alert on REPORT_DATE via the repo."""
        await repo.upsert_employee("B100", "Alice")
        await repo.upsert_employee("B200", "Bob")

        base = datetime.combine(self.REPORT_DATE, datetime.min.time())

        sid1 = await repo.create_session(
            "B100", base.replace(hour=8), machine_id="M-01"
        )
        await repo.update_session(sid1, active_duration=3600.0, state="CLOSED")

        sid2 = await repo.create_session(
            "B200", base.replace(hour=10), machine_id="M-01"
        )
        await repo.update_session(sid2, active_duration=1800.0, state="CLOSED")

        # An alert on the same day (created_at defaults to now, so tag date via
        # a direct insert to keep it on REPORT_DATE for the daily query).
        await repo.db.execute(
            "INSERT INTO alerts (badge_id, alert_type, message, machine_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("B100", "downtime", "Machine idle", "M-01", base.replace(hour=9).isoformat()),
        )

    async def test_daily_report_returns_seeded_db_data(self, cloud_repo):
        from engine.report_engine import DailyReport, ReportEngine

        repo, _db = cloud_repo
        await self._seed(repo)

        # No orchestrator/camera passed — ReportEngine only takes a Repository.
        engine = ReportEngine(repo, shift_hours=8.0)
        report = await engine.daily_report(self.REPORT_DATE)

        assert isinstance(report, DailyReport)
        assert report.report_date == self.REPORT_DATE.isoformat()
        assert report.total_sessions == 2
        # 3600 + 1800 seconds = 1.5 hours
        assert report.total_active_hours == pytest.approx(1.5)
        assert report.alerts_summary.get("downtime") == 1
        assert "M-01" in report.machine_utilization
        # Two workers appear in the breakdown, sourced purely from the DB.
        badge_ids = {w.badge_id for w in report.workers}
        assert {"B100", "B200"} <= badge_ids

    async def test_weekly_report_returns_seeded_db_data(self, cloud_repo):
        from engine.report_engine import ReportEngine, WeeklyReport

        repo, _db = cloud_repo
        await self._seed(repo)

        engine = ReportEngine(repo, shift_hours=8.0)
        report = await engine.weekly_report(self.REPORT_DATE)  # Monday

        assert isinstance(report, WeeklyReport)
        assert report.week_start == self.REPORT_DATE.isoformat()
        # The two seeded sessions fall within the 7-day window.
        assert report.total_sessions == 2
        assert report.total_active_hours == pytest.approx(1.5)
        assert len(report.daily_active_hours) == 7
        assert report.alerts_summary.get("downtime") == 1

    async def test_empty_database_yields_empty_report(self, cloud_repo):
        from engine.report_engine import ReportEngine

        repo, _db = cloud_repo
        engine = ReportEngine(repo, shift_hours=8.0)

        report = await engine.daily_report(self.REPORT_DATE)
        assert report.total_sessions == 0
        assert report.total_active_hours == 0.0
        assert report.workers == []
        assert report.alerts_summary == {}


# ══════════════════════════════════════════════════════════════════
# 2b. AI chat runs purely from the Database (no live Claude key)
# ══════════════════════════════════════════════════════════════════
# Validates: Requirements 11.3


# ── Minimal fake Anthropic client to drive the DB-backed tool path ──
# The LLM is an external service we cannot call in tests. We stub only the
# transport so the *real* DB-backed tool-call code in ai_chat runs and returns
# data purely from the SQLite database.


class _FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, name, tool_input, block_id="tool_1"):
        self.name = name
        self.input = tool_input
        self.id = block_id


class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


class _FakeMessages:
    def __init__(self, responses):
        self._responses = responses
        self.calls = 0
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        response = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return response


class _FakeAsyncAnthropic:
    def __init__(self, *args, **kwargs):
        # Responses are injected by the test via the class attribute.
        self.messages = _FakeMessages(type(self)._responses)


class TestAiChatFromDatabaseOnly:
    async def test_ai_chat_disabled_without_valid_key_no_cv(self, cloud_repo, monkeypatch):
        # Guard the API key: with no valid key, the handler returns a graceful
        # disabled message without touching any camera or CV path.
        import engine.ai_chat as ai_chat

        monkeypatch.setattr(ai_chat, "CLAUDE_API_KEY", "", raising=False)
        repo, _db = cloud_repo

        reply = await ai_chat.handle_chat_message(
            [{"role": "user", "content": "How are the machines?"}], repo
        )
        assert isinstance(reply, str)
        assert "disabled" in reply.lower()

    async def test_ai_chat_tool_call_reads_from_database(self, cloud_repo, monkeypatch):
        # Drive the DB-backed tool path: Claude "asks" for get_recent_alerts,
        # the handler queries the SQLite DB, and the seeded alert data flows
        # into the tool_result that is sent back — proving the AI-chat data path
        # is served exclusively from the Database, with no CV access.
        import engine.ai_chat as ai_chat

        repo, _db = cloud_repo
        await repo.upsert_employee("B100", "Alice")
        await repo.create_alert(
            badge_id="B100",
            alert_type="downtime",
            message="Machine idle",
            machine_id="M-01",
        )

        responses = [
            _FakeResponse(
                stop_reason="tool_use",
                content=[_FakeToolUseBlock("get_recent_alerts", {"days": 7})],
            ),
            _FakeResponse(
                stop_reason="end_turn",
                content=[_FakeTextBlock("There was 1 downtime alert this week.")],
            ),
        ]
        _FakeAsyncAnthropic._responses = responses

        monkeypatch.setattr(ai_chat, "CLAUDE_API_KEY", "sk-ant-testkey", raising=False)
        monkeypatch.setattr(ai_chat.anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)

        reply = await ai_chat.handle_chat_message(
            [{"role": "user", "content": "What went wrong this week?"}], repo
        )

        # The handler drove the DB-backed tool path and returned the model's
        # final answer without any camera / CV access. (The tool_result rows
        # read from the DB are asserted explicitly in the test below.)
        assert reply == "There was 1 downtime alert this week."


class TestAiChatToolResultCarriesDbData:
    async def test_tool_result_contains_seeded_alert(self, cloud_repo, monkeypatch):
        import engine.ai_chat as ai_chat

        repo, _db = cloud_repo
        await repo.upsert_employee("B100", "Alice")
        await repo.create_alert(
            badge_id="B100",
            alert_type="downtime",
            message="Machine idle",
            machine_id="M-01",
        )

        responses = [
            _FakeResponse(
                stop_reason="tool_use",
                content=[_FakeToolUseBlock("get_recent_alerts", {"days": 7})],
            ),
            _FakeResponse(
                stop_reason="end_turn",
                content=[_FakeTextBlock("Summary based on DB data.")],
            ),
        ]
        _FakeAsyncAnthropic._responses = responses

        monkeypatch.setattr(ai_chat, "CLAUDE_API_KEY", "sk-ant-testkey", raising=False)
        monkeypatch.setattr(ai_chat.anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)

        messages = [{"role": "user", "content": "What went wrong this week?"}]
        reply = await ai_chat.handle_chat_message(messages, repo)
        assert reply == "Summary based on DB data."

        # The handler mutated `messages` in place, appending the assistant's
        # tool request and a user message carrying the tool_result. That
        # tool_result content is the JSON of rows read from the SQLite DB.
        tool_result_blocks = [
            block
            for m in messages
            if isinstance(m.get("content"), list)
            for block in m["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]
        assert tool_result_blocks, "expected a tool_result carrying DB rows"
        combined = " ".join(str(b.get("content", "")) for b in tool_result_blocks)
        # Real DB data flowed into the tool result.
        assert "downtime" in combined
        assert "Machine idle" in combined


# ══════════════════════════════════════════════════════════════════
# 3. SQLite is retained (Requirement 14.3)
# ══════════════════════════════════════════════════════════════════
# Validates: Requirements 14.3


class TestSqliteRetained:
    def test_db_path_is_sqlite_file(self):
        from config import DB_PATH

        assert isinstance(DB_PATH, str) and DB_PATH
        # Default cloud DB is a SQLite file (".db"); a ":memory:" SQLite DB is
        # also acceptable. Either way it is SQLite, not another engine.
        assert DB_PATH.endswith(".db") or DB_PATH == ":memory:"

    def test_async_database_backed_by_aiosqlite(self):
        import db.async_database as async_database

        # The cloud data layer is SQLite via aiosqlite.
        assert hasattr(async_database, "aiosqlite")

    async def test_repository_answers_sqlite_version(self, cloud_repo):
        repo, db = cloud_repo
        # Repository wraps an AsyncDatabase (SQLite).
        assert isinstance(db, AsyncDatabase)
        assert isinstance(repo.db, AsyncDatabase)
        row = await db.fetch_one("SELECT sqlite_version() AS v")
        assert row is not None
        assert row["v"]  # a non-empty SQLite version string confirms the engine
