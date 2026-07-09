"""Property-based tests for the Edge_Agent Local_Camera_Config merge.

These tests validate ``edge/local_camera_config.py`` — specifically
``LocalCameraConfig.build_machine_configs(metadata)``, which merges the
credential-free cloud ``MachineMetadata`` with the edge-only RTSP mapping to
produce the ``machine_config`` dicts ``PipelineOrchestrator.start_pipeline()``
expects.

Feature: edge-cloud-split
"""

import os
import sys
from datetime import datetime, timezone

from hypothesis import given, settings, strategies as st
from structlog.testing import capture_logs

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from api.ingest_schemas import MachineMetadata  # noqa: E402
from edge.local_camera_config import LocalCameraConfig  # noqa: E402

# The exact machine_config shape PipelineOrchestrator.start_pipeline() expects.
EXPECTED_KEYS = {
    "machine_id",
    "rtsp_url",
    "display_name",
    "detection_zone",
    "person_confidence_threshold",
    "light_zone",
}

# The skip-warning message emitted for unmapped metadata machine IDs.
SKIP_WARNING_FRAGMENT = "No Local_Camera_Config mapping"

# ── Strategies ────────────────────────────────────────────

# Machine IDs: non-empty tokens of URL/JSON-safe characters.
MACHINE_ID_ST = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="-_"
    ),
    min_size=1,
    max_size=12,
)


def _make_metadata(draw, machine_id: str) -> MachineMetadata:
    """Build a MachineMetadata for the given machine ID with drawn fields."""
    return MachineMetadata(
        machine_id=machine_id,
        display_name=draw(st.text(min_size=0, max_size=20)),
        detection_zone=draw(st.text(min_size=0, max_size=20)),
        person_confidence_threshold=draw(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        ),
        light_zone=draw(st.one_of(st.none(), st.text(min_size=0, max_size=20))),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _rtsp_url(draw) -> str:
    """Build a valid plaintext rtsp:// URL (host required, no credentials)."""
    octet = draw(st.integers(min_value=1, max_value=254))
    port = draw(st.integers(min_value=1, max_value=65535))
    return f"rtsp://192.168.1.{octet}:{port}/Streaming/Channels/101"


@st.composite
def scenarios(draw):
    """Draw (metadata_list, mapping, mapped_ids, unmapped_ids).

    A set of unique metadata machine IDs is split into those that also have a
    Local_Camera_Config entry (mapped) and those that do not (unmapped). Extra
    local-only IDs (present in the mapping but NOT in metadata) are added to
    prove they never produce a config and are never warned about.
    """
    meta_ids = draw(st.lists(MACHINE_ID_ST, min_size=0, max_size=8, unique=True))

    metadata = []
    mapping = {}
    mapped = set()
    unmapped = set()
    for mid in meta_ids:
        metadata.append(_make_metadata(draw, mid))
        if draw(st.booleans()):
            mapping[mid] = {"rtsp_url": _rtsp_url(draw)}
            mapped.add(mid)
        else:
            unmapped.add(mid)

    # Local-only IDs: mapped cameras with no cloud metadata. They must not
    # appear in the output and must not trigger warnings (we iterate metadata).
    extra_ids = draw(st.lists(MACHINE_ID_ST, min_size=0, max_size=4, unique=True))
    for mid in extra_ids:
        if mid not in meta_ids:
            mapping[mid] = {"rtsp_url": _rtsp_url(draw)}

    return metadata, mapping, mapped, unmapped


# ── Property 27 ────────────────────────────────────────────────────
# Feature: edge-cloud-split, Property 27: Unmapped machine metadata is skipped
# and warned — for any Machine_Metadata list and Local_Camera_Config mapping,
# the Edge_Agent builds a machine_config exactly for the machine IDs present in
# BOTH the metadata and the local mapping, and every metadata machine ID with no
# local mapping is skipped (absent from output) with a logged warning.
# Validates: Requirements 7.8
@settings(max_examples=200)
@given(scenarios())
def test_unmapped_metadata_skipped_and_warned(scenario):
    metadata, mapping, mapped, unmapped = scenario

    config = LocalCameraConfig.from_mapping(mapping)

    with capture_logs() as logs:
        configs = config.build_machine_configs(metadata)

    # 1) A machine_config is produced exactly for IDs present in BOTH.
    produced_ids = [c["machine_id"] for c in configs]
    assert set(produced_ids) == mapped
    # Exactly one config per mapped machine (no duplicates/drops).
    assert len(produced_ids) == len(mapped)

    # 2) Every produced config has exactly the keys start_pipeline() expects,
    #    with values merged from the correct metadata + local RTSP entry.
    meta_by_id = {m.machine_id: m for m in metadata}
    for cfg in configs:
        assert set(cfg.keys()) == EXPECTED_KEYS
        mid = cfg["machine_id"]
        meta = meta_by_id[mid]
        assert cfg["rtsp_url"] == mapping[mid]["rtsp_url"]
        assert cfg["display_name"] == meta.display_name
        assert cfg["detection_zone"] == meta.detection_zone
        assert cfg["person_confidence_threshold"] == meta.person_confidence_threshold
        assert cfg["light_zone"] == meta.light_zone

    # 3) Unmapped metadata IDs are skipped: absent from the output entirely.
    assert set(produced_ids).isdisjoint(unmapped)

    # 4) A warning is logged for each skipped (unmapped) machine ID, identifying
    #    it, and for no other machine.
    warned_ids = [
        e.get("machine_id")
        for e in logs
        if e.get("log_level") == "warning" and SKIP_WARNING_FRAGMENT in e.get("event", "")
    ]
    assert set(warned_ids) == unmapped
    # Exactly one warning per skipped machine.
    assert len(warned_ids) == len(unmapped)
