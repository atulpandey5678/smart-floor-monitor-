# Engine package
from engine.anti_cheat import AntiCheatEngine
from engine.circuit_breaker import CircuitBreaker, CircuitState
from engine.models import SessionState, FrameResult, SessionRecord, AlertRecord, LiveStatus
from engine.pipeline_orchestrator import validate_detection_params

# ── Module-level orchestrator singleton (set by main.py at startup) ───────
_orchestrator_instance = None


def set_orchestrator(orchestrator):
    """Store the PipelineOrchestrator instance for cross-module access.

    Called by main.py after creating the orchestrator. Other modules
    (api/server.py, api/health.py) use get_orchestrator() to retrieve it
    without circular imports.
    """
    global _orchestrator_instance
    _orchestrator_instance = orchestrator


def get_orchestrator():
    """Return the PipelineOrchestrator instance, or None if not yet set."""
    return _orchestrator_instance
