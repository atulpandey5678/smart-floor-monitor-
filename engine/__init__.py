# Engine package
#
# The orchestrator singleton helpers below are defined eagerly because they
# have no heavy dependencies. The CV/session-related re-exports
# (AntiCheatEngine, CircuitBreaker, domain models, detection-param validation)
# are exposed *lazily* via module __getattr__ (PEP 562) so that importing the
# `engine` package does NOT transitively import the CV stack (cv2, cv_pipeline,
# PipelineOrchestrator).
#
# This matters for the edge-cloud split: the Cloud_Server imports cloud-only
# modules such as `engine.report_engine` and `engine.ai_chat`, which read
# exclusively from the Database. Importing those modules must not require any
# camera or CV_Pipeline dependency (Requirement 11.1, 11.2, 11.3). Because
# `import engine.report_engine` first executes this package __init__, keeping
# the heavy re-exports lazy prevents cv2/OpenCV from being loaded on the cloud.

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


# ── Lazy re-exports (loaded only on first access) ─────────────────────────
# Maps public attribute name -> submodule that defines it. Accessing any of
# these on the `engine` package triggers a one-time import of the backing
# submodule, keeping the base package import free of CV dependencies.
_LAZY_EXPORTS = {
    "AntiCheatEngine": "engine.anti_cheat",
    "CircuitBreaker": "engine.circuit_breaker",
    "CircuitState": "engine.circuit_breaker",
    "SessionState": "engine.models",
    "FrameResult": "engine.models",
    "SessionRecord": "engine.models",
    "AlertRecord": "engine.models",
    "LiveStatus": "engine.models",
    "validate_detection_params": "engine.pipeline_orchestrator",
}


def __getattr__(name):
    """PEP 562 lazy attribute loader for heavy re-exports."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, name)


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))
