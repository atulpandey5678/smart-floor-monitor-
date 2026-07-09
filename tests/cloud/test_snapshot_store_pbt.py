"""Property-based tests for Snapshot_Store.

Feature: edge-cloud-split

Covers one correctness property:
- Property 31: Snapshot last-write-wins per machine
"""

from __future__ import annotations

import threading
import time
from typing import List

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.snapshot_store import SnapshotStore


# ── Property 31: Snapshot last-write-wins per machine ────────────────────────
# Feature: edge-cloud-split, Property 31: The Snapshot_Store retains exactly
# one snapshot per machine ID, replacing any previous snapshot (last-write-wins).
# Validates: Requirements 9.4


class TestProperty31SnapshotLastWriteWins:
    """Property 31 validation: Snapshot_Store last-write-wins per machine."""

    @given(
        machine_id=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
            min_size=1,
            max_size=8,
        ),
        snapshot_count=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=150, deadline=None)
    def test_last_write_wins_replaces_previous_snapshot(
        self, machine_id: str, snapshot_count: int
    ) -> None:
        """Multiple puts for same machine_id keep only the latest snapshot."""
        store = SnapshotStore()

        # Submit multiple snapshots for the same machine
        snapshots = []
        for i in range(snapshot_count):
            data = f"snapshot-{i}".encode("utf-8")
            entry = store.put(machine_id, data)
            snapshots.append((i, data, entry))
            # Small delay to ensure received_at timestamps differ
            time.sleep(0.001)

        # Verify only one snapshot exists for the machine
        assert store.has(machine_id)
        latest = store.get(machine_id)
        assert latest is not None

        # The stored snapshot should be the last one we put
        last_index, last_data, last_entry = snapshots[-1]
        assert latest.data == last_data, (
            f"Expected last snapshot data {last_data!r}, got {latest.data!r}"
        )
        assert latest.received_at == pytest.approx(
            last_entry.received_at, abs=0.01
        ), "received_at timestamp mismatch"

    @given(
        machine_count=st.integers(min_value=2, max_value=5),
    )
    @settings(max_examples=100, deadline=None)
    def test_independent_snapshots_per_machine(self, machine_count: int) -> None:
        """Each machine_id has its own independent snapshot slot."""
        store = SnapshotStore()

        # Put one snapshot per machine
        machines = [f"M-{i:02d}" for i in range(1, machine_count + 1)]
        for machine_id in machines:
            data = f"{machine_id}-snapshot".encode("utf-8")
            store.put(machine_id, data)

        # Verify each machine has exactly its own snapshot
        for machine_id in machines:
            assert store.has(machine_id)
            entry = store.get(machine_id)
            assert entry is not None
            expected_data = f"{machine_id}-snapshot".encode("utf-8")
            assert entry.data == expected_data, (
                f"Machine {machine_id} has wrong snapshot data"
            )

    @given(
        machine_id=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
            min_size=1,
            max_size=8,
        ),
        data_size=st.integers(min_value=100, max_value=5000),
    )
    @settings(max_examples=100, deadline=None)
    def test_snapshot_data_preserved_intact(
        self, machine_id: str, data_size: int
    ) -> None:
        """The stored snapshot data matches the bytes that were put."""
        store = SnapshotStore()

        # Generate repeating pattern data
        pattern = bytes(range(256))
        data = (pattern * (data_size // 256 + 1))[:data_size]

        store.put(machine_id, data)
        entry = store.get(machine_id)

        assert entry is not None
        assert entry.data == data, "Snapshot data was corrupted"
        assert len(entry.data) == data_size

    @given(
        machine_id=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
            min_size=1,
            max_size=8,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_get_returns_none_for_nonexistent_machine(self, machine_id: str) -> None:
        """get() returns None for machines that have no snapshot."""
        store = SnapshotStore()

        # Without putting anything, get should return None
        assert not store.has(machine_id)
        assert store.get(machine_id) is None

    @given(
        machine_id=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
            min_size=1,
            max_size=8,
        ),
    )
    @settings(max_examples=100, deadline=None)
    def test_received_at_timestamp_is_monotonic(self, machine_id: str) -> None:
        """Later puts have later received_at timestamps."""
        store = SnapshotStore()

        first = store.put(machine_id, b"first")
        time.sleep(0.01)  # Ensure time passes
        second = store.put(machine_id, b"second")

        assert second.received_at > first.received_at, (
            "received_at did not increase with later put"
        )

        # The stored snapshot should be the second one
        latest = store.get(machine_id)
        assert latest is not None
        assert latest.received_at == pytest.approx(second.received_at, abs=0.01)

    def test_content_type_defaults_to_jpeg(self) -> None:
        """put() defaults content_type to image/jpeg when not specified."""
        store = SnapshotStore()
        entry = store.put("M-01", b"data")
        assert entry.content_type == "image/jpeg"

    def test_content_type_can_be_overridden(self) -> None:
        """put() respects explicit content_type argument."""
        store = SnapshotStore()
        entry = store.put("M-01", b"data", content_type="image/png")
        assert entry.content_type == "image/png"

    def test_clear_removes_all_snapshots(self) -> None:
        """clear() removes all stored snapshots."""
        store = SnapshotStore()
        store.put("M-01", b"data1")
        store.put("M-02", b"data2")
        assert store.has("M-01")
        assert store.has("M-02")

        store.clear()

        assert not store.has("M-01")
        assert not store.has("M-02")
        assert store.get("M-01") is None
        assert store.get("M-02") is None

    def test_concurrent_puts_are_thread_safe(self) -> None:
        """Multiple threads can put snapshots concurrently without data races."""
        store = SnapshotStore()
        machine_ids = [f"M-{i:02d}" for i in range(1, 6)]
        errors: List[Exception] = []

        def put_worker(machine_id: str, value: int) -> None:
            try:
                for i in range(10):
                    data = f"{machine_id}-{value}-{i}".encode("utf-8")
                    store.put(machine_id, data)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = []
        for idx, machine_id in enumerate(machine_ids):
            t = threading.Thread(target=put_worker, args=(machine_id, idx))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # No errors should have occurred
        assert not errors, f"Concurrent puts raised errors: {errors}"

        # Each machine should have exactly one snapshot
        for machine_id in machine_ids:
            assert store.has(machine_id)
            entry = store.get(machine_id)
            assert entry is not None
            # The data should belong to the machine (no cross-contamination)
            assert entry.machine_id == machine_id

    def test_empty_machine_id_raises_value_error(self) -> None:
        """put() with empty machine_id raises ValueError."""
        store = SnapshotStore()
        with pytest.raises(ValueError, match="machine_id must be a non-empty string"):
            store.put("", b"data")
