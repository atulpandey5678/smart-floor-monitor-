"""Edge metadata application — hot-reload vs. restart decision.

When the Sync_Client detects changed cloud ``MachineMetadata`` for a running
machine station, the Edge_Agent must apply the change to the corresponding CV
pipeline. Requirement 7.4 draws the line:

    "apply the updated detection parameters ... without restarting the pipeline
     where the parameter supports live update, and by restarting the pipeline
     otherwise."

This module owns that decision. Given the previously-applied metadata and the
newly-pulled metadata (or a precomputed diff), it:

1. Diffs the *config-relevant* fields, ignoring identity/bookkeeping fields
   (``machine_id`` identifies the station; ``updated_at`` is only a change
   marker and never a substantive change on its own).
2. Classifies each changed key as hot-reloadable or not, using
   :data:`HOT_RELOADABLE_KEYS`.
3. If **every** changed key is hot-reloadable, applies the new values live via
   ``PipelineOrchestrator.update_pipeline_config(machine_id, updates)`` — the
   running pipeline loop picks the new values up on its next frame iteration,
   with no restart.
4. If **any** changed key is not hot-reloadable, restarts the pipeline via
   ``PipelineOrchestrator.restart_pipeline(machine_id)`` so the change takes
   effect through a clean pipeline reconstruction.

### Hot-reloadable classification (:data:`HOT_RELOADABLE_KEYS`)

The orchestrator's per-frame loop re-reads ``instance.machine_config`` every
iteration and re-applies detection parameters live. The following
``MachineMetadata`` fields are picked up that way and therefore hot-reloadable:

- ``person_confidence_threshold`` — re-read into the detector each frame.
- ``light_zone`` — re-read into the light detector each frame.
- ``detection_zone`` — a detection parameter carried in ``machine_config`` and
  read live alongside the others.
- ``display_name`` — cosmetic metadata that does not alter pipeline
  construction, so it never requires a restart.

Any *other* changed key (present now or added to ``MachineMetadata`` in the
future) is treated as non-hot-reloadable by default and forces a restart. This
is a deliberately safe default: an unrecognized change is assumed to affect
pipeline construction unless we know otherwise.

### Validation fallback

``update_pipeline_config`` validates detection parameters
(``validate_detection_params``) before applying them and returns
``(False, message)`` on failure — for example, a zone value whose shape the
validator rejects, or a machine that is not currently running. When a live
update is attempted but rejected, this module falls back to a pipeline restart
rather than silently dropping the change, so the new metadata is always applied
one way or another.

Requirements: 7.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Optional, Tuple

import structlog

from api.ingest_schemas import MachineMetadata

logger = structlog.get_logger(__name__)


# ── Classification ────────────────────────────────────────────────

#: Machine_Metadata keys the running pipeline loop can pick up live via
#: ``PipelineOrchestrator.update_pipeline_config`` without a restart.
HOT_RELOADABLE_KEYS: FrozenSet[str] = frozenset(
    {
        "detection_zone",
        "person_confidence_threshold",
        "light_zone",
        "display_name",
    }
)

#: Identity/bookkeeping fields that never constitute a substantive change on
#: their own: ``machine_id`` identifies the station and ``updated_at`` is only a
#: freshness marker bumped on every edit.
_NON_CONFIG_KEYS: FrozenSet[str] = frozenset({"machine_id", "updated_at"})

#: The config-relevant fields compared when diffing two ``MachineMetadata``
#: values: every declared field minus the identity/bookkeeping ones.
_CONFIG_KEYS: Tuple[str, ...] = tuple(
    name
    for name in MachineMetadata.model_fields  # type: ignore[attr-defined]
    if name not in _NON_CONFIG_KEYS
)


# ── Result ────────────────────────────────────────────────────────

# Applied-action outcomes.
ACTION_NONE = "none"
ACTION_HOT_RELOAD = "hot_reload"
ACTION_RESTART = "restart"


@dataclass(frozen=True)
class MetadataApplyResult:
    """Outcome of applying a metadata change to one machine's pipeline.

    Attributes:
        machine_id: The machine whose metadata was evaluated.
        changed_keys: Config-relevant keys that differed between old and new.
        action: One of :data:`ACTION_NONE`, :data:`ACTION_HOT_RELOAD`, or
            :data:`ACTION_RESTART`.
        applied_keys: The keys handed to ``update_pipeline_config`` on a live
            update (empty for restart/none).
        restarted: True when the pipeline was restarted.
        success: True when the chosen action completed successfully.
        detail: Human-readable explanation (empty on clean success).
    """

    machine_id: str
    changed_keys: FrozenSet[str] = field(default_factory=frozenset)
    action: str = ACTION_NONE
    applied_keys: FrozenSet[str] = field(default_factory=frozenset)
    restarted: bool = False
    success: bool = True
    detail: str = ""


# ── Pure diffing / classification ─────────────────────────────────


def diff_metadata(
    old: Optional[MachineMetadata], new: MachineMetadata
) -> Dict[str, Any]:
    """Return the config-relevant fields whose value changed from old to new.

    ``machine_id`` and ``updated_at`` are ignored. When ``old`` is ``None``
    (no previously-applied metadata), an empty diff is returned: applying the
    very first metadata is the bootstrap's job (it builds the initial
    ``machine_config`` and starts the pipeline), not a change to reconcile here.

    Args:
        old: The previously-applied metadata, or ``None``.
        new: The newly-pulled metadata.

    Returns:
        Mapping of changed key -> new value.
    """
    if old is None:
        return {}

    changed: Dict[str, Any] = {}
    for key in _CONFIG_KEYS:
        old_value = getattr(old, key, None)
        new_value = getattr(new, key, None)
        if old_value != new_value:
            changed[key] = new_value
    return changed


def classify_changed_keys(
    changed_keys: Iterable[str],
) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """Split changed keys into (hot_reloadable, requires_restart) subsets."""
    keys = set(changed_keys)
    hot = frozenset(k for k in keys if k in HOT_RELOADABLE_KEYS)
    cold = frozenset(k for k in keys if k not in HOT_RELOADABLE_KEYS)
    return hot, cold


# ── Application ────────────────────────────────────────────────────


class MetadataApplier:
    """Applies detected ``MachineMetadata`` changes to running pipelines.

    Wraps a ``PipelineOrchestrator`` (duck-typed: only ``update_pipeline_config``
    and ``restart_pipeline`` are used) and decides, per changed key, whether to
    hot-reload or restart per Requirement 7.4.
    """

    def __init__(self, orchestrator: Any):
        self._orchestrator = orchestrator

    def apply_change(
        self, old: Optional[MachineMetadata], new: MachineMetadata
    ) -> MetadataApplyResult:
        """Apply the diff between ``old`` and ``new`` metadata for one machine.

        Args:
            old: The previously-applied metadata, or ``None`` (no-op diff).
            new: The newly-pulled metadata.

        Returns:
            A :class:`MetadataApplyResult` describing the action taken.
        """
        changed = diff_metadata(old, new)
        return self.apply_changed_values(new.machine_id, changed)

    def apply_changed_values(
        self, machine_id: str, changed: Mapping[str, Any]
    ) -> MetadataApplyResult:
        """Apply an already-computed diff (changed key -> new value).

        Useful when the caller has computed the diff itself. Applies the same
        hot-reload-vs-restart decision as :meth:`apply_change`.
        """
        changed_keys = frozenset(changed.keys())

        # Nothing substantive changed → no action.
        if not changed_keys:
            logger.debug(
                "Metadata unchanged — no pipeline action", machine_id=machine_id
            )
            return MetadataApplyResult(
                machine_id=machine_id,
                changed_keys=changed_keys,
                action=ACTION_NONE,
                success=True,
            )

        hot_keys, cold_keys = classify_changed_keys(changed_keys)

        # Any non-hot-reloadable change forces a restart.
        if cold_keys:
            logger.info(
                "Metadata change requires pipeline restart",
                machine_id=machine_id,
                changed_keys=sorted(changed_keys),
                non_hot_reloadable=sorted(cold_keys),
            )
            return self._restart(
                machine_id,
                changed_keys,
                detail=(
                    "restart: non-hot-reloadable key(s) changed: "
                    + ", ".join(sorted(cold_keys))
                ),
            )

        # Every changed key is hot-reloadable → attempt a live update.
        updates = {key: changed[key] for key in hot_keys}
        ok, error_msg = self._orchestrator.update_pipeline_config(
            machine_id, updates
        )
        if ok:
            logger.info(
                "Metadata change hot-reloaded (no restart)",
                machine_id=machine_id,
                applied_keys=sorted(hot_keys),
            )
            return MetadataApplyResult(
                machine_id=machine_id,
                changed_keys=changed_keys,
                action=ACTION_HOT_RELOAD,
                applied_keys=hot_keys,
                restarted=False,
                success=True,
            )

        # Live update rejected (validation failure or pipeline missing):
        # fall back to a restart so the change is not lost.
        logger.warning(
            "Live update rejected — falling back to pipeline restart",
            machine_id=machine_id,
            applied_keys=sorted(hot_keys),
            reason=error_msg,
        )
        return self._restart(
            machine_id,
            changed_keys,
            detail=f"restart: live update rejected ({error_msg})",
        )

    # ── Internal ──────────────────────────────────────────
    def _restart(
        self, machine_id: str, changed_keys: FrozenSet[str], detail: str
    ) -> MetadataApplyResult:
        """Restart the pipeline and wrap the outcome in a result."""
        restarted = bool(self._orchestrator.restart_pipeline(machine_id))
        if not restarted:
            detail = f"{detail}; restart failed (pipeline not found?)"
        return MetadataApplyResult(
            machine_id=machine_id,
            changed_keys=changed_keys,
            action=ACTION_RESTART,
            applied_keys=frozenset(),
            restarted=restarted,
            success=restarted,
            detail=detail,
        )
