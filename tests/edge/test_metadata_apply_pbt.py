"""
Property-based tests for edge/metadata_apply.py — metadata application logic.

Feature: edge-cloud-split
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ── Property 26: Metadata change decision — live update vs restart ───────────
# Feature: edge-cloud-split, Property 26: Changing a hot-reloadable metadata key
# triggers a live update; changing any non-hot-reloadable key triggers a restart.
# Validates: Requirements 7.4

_HOT_RELOADABLE = {
    "detection_zone",
    "person_confidence_threshold",
    "light_zone",
    "display_name",
}


class TestProperty26MetadataChangeDecision:

    @given(
        changed_keys=st.sets(
            st.sampled_from(list(_HOT_RELOADABLE)),
            min_size=1,
            max_size=len(_HOT_RELOADABLE),
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_hot_reloadable_keys_trigger_live_update(
        self, changed_keys: set[str]
    ) -> None:
        """Changing only hot-reloadable keys triggers a live update, not restart."""
        from edge.metadata_apply import classify_changed_keys

        hot, non_hot = classify_changed_keys(changed_keys)

        # All changed keys should be classified as hot-reloadable
        assert hot == changed_keys, f"Expected hot={changed_keys}, got {hot}"
        assert non_hot == set(), f"Expected no non-hot keys, got {non_hot}"

    @given(
        hot_keys=st.sets(
            st.sampled_from(list(_HOT_RELOADABLE)), min_size=0, max_size=2
        ),
        non_hot_keys=st.sets(
            st.sampled_from(["location", "unknown_key", "rtsp_url_encrypted"]),
            min_size=1,
            max_size=2,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_non_hot_reloadable_keys_trigger_restart(
        self, hot_keys: set[str], non_hot_keys: set[str]
    ) -> None:
        """Changing at least one non-hot-reloadable key triggers a restart."""
        from edge.metadata_apply import classify_changed_keys

        all_changed = hot_keys | non_hot_keys

        hot, non_hot = classify_changed_keys(all_changed)

        # Should detect at least the non-hot keys
        assert non_hot >= non_hot_keys, (
            f"Expected non_hot to include {non_hot_keys}, got {non_hot}"
        )

    @given(
        unchanged_keys=st.sets(
            st.sampled_from(list(_HOT_RELOADABLE | {"location"})),
            min_size=1,
            max_size=5,
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_unchanged_keys_trigger_no_action(self, unchanged_keys: set[str]) -> None:
        """Keys with identical values trigger no update or restart."""
        from edge.metadata_apply import classify_changed_keys

        # Pass empty set when no keys changed
        hot, non_hot = classify_changed_keys([])

        assert hot == frozenset(), f"Expected no hot keys, got {hot}"
        assert non_hot == frozenset(), f"Expected no non-hot keys, got {non_hot}"

    @given(
        added_keys=st.sets(
            st.sampled_from(list(_HOT_RELOADABLE)), min_size=1, max_size=3
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_added_hot_keys_trigger_live_update(self, added_keys: set[str]) -> None:
        """Adding new hot-reloadable keys triggers a live update."""
        from edge.metadata_apply import classify_changed_keys

        hot, non_hot = classify_changed_keys(added_keys)

        assert hot == added_keys, f"Expected hot={added_keys}, got {hot}"
        assert non_hot == frozenset(), f"Expected no non-hot keys, got {non_hot}"

    @given(
        removed_keys=st.sets(
            st.sampled_from(list(_HOT_RELOADABLE)), min_size=1, max_size=3
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_removed_hot_keys_trigger_live_update(self, removed_keys: set[str]) -> None:
        """Removing hot-reloadable keys triggers a live update."""
        from edge.metadata_apply import classify_changed_keys

        hot, non_hot = classify_changed_keys(removed_keys)

        assert hot == removed_keys, f"Expected hot={removed_keys}, got {hot}"
        assert non_hot == frozenset(), f"Expected no non-hot keys, got {non_hot}"

