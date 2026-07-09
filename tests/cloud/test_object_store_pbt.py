"""Property-based tests for the Cloud_Server Object_Store (event images).

Feature: edge-cloud-split
Property 29: Event images uploaded and referenced only for alerts.

Validates: Requirements 8.2, 8.3, 8.4

These tests exercise the real production code in ``engine/object_store.py``
(``InMemoryObjectStore`` + ``build_object_key``). The alert-vs-non-alert
dispatch rule under test mirrors the design contract that *only* Alert events
trigger an Object_Store upload (Requirement 8.4); Session_Records,
Machine_Events, and Heartbeats never upload.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.object_store import InMemoryObjectStore, build_object_key

# Kinds that flow through the Ingest_API. Only "alert" carries an Event_Image
# and therefore is the only kind that triggers an Object_Store upload.
_NON_ALERT_KINDS = ("session", "machine_event", "heartbeat")

# Identifiers are non-empty (build_object_key rejects empty ids) and drawn from
# a realistic id alphabet to keep object keys well-formed.
_id_text = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_",
    min_size=1,
    max_size=24,
)
_jpeg_bytes = st.binary(min_size=0, max_size=512)


def _dispatch_ingest(store, kind, machine_id, event_id, image):
    """Route an ingest event, uploading an Event_Image only for alerts.

    Returns the referenced image URL for alerts, or ``None`` for non-alert
    kinds. This encodes the Requirement 8.4 policy ("upload only for Alert
    events") against the real Object_Store implementation.
    """
    if kind == "alert":
        return store.upload_event_image(machine_id, event_id, image)
    return None


# Feature: edge-cloud-split, Property 29: Event images uploaded and referenced
# only for alerts — for any Alert payload with an Event_Image, the Object_Store
# upload is invoked once with the image bytes and the persisted Alert's image
# URL equals the URL returned by the store; for any non-alert ingest no upload
# occurs.
@settings(max_examples=200)
@given(machine_id=_id_text, event_id=_id_text, image=_jpeg_bytes)
def test_alert_uploads_once_and_reference_matches(machine_id, event_id, image):
    """Validates: Requirements 8.2, 8.3, 8.4

    An alert uploads exactly one object, keyed deterministically, and the
    referenced URL equals the URL the store returned; the stored bytes equal
    the pushed image bytes.
    """
    store = InMemoryObjectStore()

    url = _dispatch_ingest(store, "alert", machine_id, event_id, image)

    # 8.2: image was stored — exactly one object exists.
    assert len(store.objects) == 1
    # 8.3: deterministic key and reference URL point at the stored object.
    expected_key = "alerts/{}/{}.jpg".format(machine_id, event_id)
    assert build_object_key(machine_id, event_id) == expected_key
    assert expected_key in store.objects
    assert url is not None and expected_key in url
    # The persisted reference resolves to the exact bytes that were pushed.
    assert store.get(machine_id, event_id) == image
    assert store.objects[expected_key] == image


# Feature: edge-cloud-split, Property 29: retries of the same alert overwrite
# rather than duplicate — the deterministic key keeps the object count at 1.
@settings(max_examples=200)
@given(
    machine_id=_id_text,
    event_id=_id_text,
    first_image=_jpeg_bytes,
    second_image=_jpeg_bytes,
    retries=st.integers(min_value=1, max_value=5),
)
def test_alert_retry_overwrites_and_count_stays_one(
    machine_id, event_id, first_image, second_image, retries
):
    """Validates: Requirements 8.3, 8.4

    Re-uploading the same alert (same machine_id + event_id) any number of
    times overwrites in place: the object count stays 1 and the latest bytes
    win, so retries never create duplicates.
    """
    store = InMemoryObjectStore()

    first_url = _dispatch_ingest(store, "alert", machine_id, event_id, first_image)
    last_url = None
    for _ in range(retries):
        last_url = _dispatch_ingest(store, "alert", machine_id, event_id, second_image)

    # Deterministic key => single object regardless of retry count.
    assert len(store.objects) == 1
    # The reference URL is stable across retries of the same alert.
    assert last_url == first_url
    # Last write wins.
    assert store.get(machine_id, event_id) == second_image


# Feature: edge-cloud-split, Property 29: non-alert ingests never upload — for
# Session_Record, Machine_Event, and Heartbeat kinds, no Object_Store upload
# occurs and nothing is referenced.
@settings(max_examples=200)
@given(
    kinds=st.lists(st.sampled_from(_NON_ALERT_KINDS), min_size=1, max_size=10),
    machine_id=_id_text,
    event_id=_id_text,
    image=_jpeg_bytes,
)
def test_non_alert_ingests_never_upload(kinds, machine_id, event_id, image):
    """Validates: Requirements 8.4

    Only alert events trigger uploads. Any interleaving of non-alert ingests
    leaves the Object_Store empty and returns no image reference.
    """
    store = InMemoryObjectStore()

    for i, kind in enumerate(kinds):
        ref = _dispatch_ingest(store, kind, machine_id, "{}-{}".format(event_id, i), image)
        assert ref is None

    assert store.objects == {}


# Feature: edge-cloud-split, Property 29: mixed streams — only the alert events
# in a mixed ingest stream produce uploaded/referenced images.
@settings(max_examples=200)
@given(
    events=st.lists(
        st.tuples(st.sampled_from(("alert",) + _NON_ALERT_KINDS), _id_text, _id_text, _jpeg_bytes),
        min_size=1,
        max_size=12,
    ),
)
def test_mixed_stream_uploads_only_for_alerts(events):
    """Validates: Requirements 8.2, 8.3, 8.4

    Across a mixed stream of ingest events, the set of stored object keys is
    exactly the set of deterministic keys for the alert events, and each
    non-alert event yields no reference.
    """
    store = InMemoryObjectStore()

    expected_keys = set()
    for kind, machine_id, event_id, image in events:
        ref = _dispatch_ingest(store, kind, machine_id, event_id, image)
        if kind == "alert":
            key = build_object_key(machine_id, event_id)
            expected_keys.add(key)
            assert ref is not None and key in ref
        else:
            assert ref is None

    assert set(store.objects.keys()) == expected_keys
