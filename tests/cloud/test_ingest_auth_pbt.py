"""Property-based tests for Ingest_API key authentication.

# Feature: edge-cloud-split, Property 19: Ingest rejects missing key and staff
#          cookies. For any ingest request that lacks a valid Ingest_API_Key --
#          including one authenticated only by a valid Staff_User session
#          cookie -- the Cloud_Server responds HTTP 401 and persists no data.

Validates: Requirements 3.3, 3.5

These tests exercise ``api.ingest_auth.verify_ingest_key`` directly against a
fake Starlette ``Request`` built from Hypothesis-generated headers. The
configured key used by the dependency (``INGEST_API_KEY``, imported into the
``api.ingest_auth`` module namespace) is monkeypatched to a fixed known value
for the duration of each test.
"""

from typing import Dict

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from api import ingest_auth
from api.ingest_auth import verify_ingest_key

# A fixed, known-good configured Ingest_API_Key for the tests. Generated
# wrong keys are constrained to differ from this value.
CONFIGURED_KEY = "s3cr3t-ingest-key-value-0123456789"

# Minimum iterations mandated by the design for property tests.
_PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _make_request(headers: Dict[str, str]) -> Request:
    """Build a minimal fake Starlette ``Request`` carrying ``headers``.

    Starlette expects raw headers in the ASGI scope as a list of
    ``(name, value)`` byte tuples with lowercased names.
    """
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in headers.items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/ingest/session",
        "headers": raw_headers,
    }
    return Request(scope)


def _assert_rejected(request: Request) -> None:
    """Assert ``verify_ingest_key`` rejects ``request`` with HTTP 401."""
    with pytest.raises(HTTPException) as exc_info:
        verify_ingest_key(request)
    assert exc_info.value.status_code == 401


# Header-safe alphabet: printable ASCII excluding control chars. Real HTTP
# header values are limited to latin-1 bytes, so generators are constrained to
# characters that can legally appear in a header without whitespace-strip edge
# cases.
_HEADER_CHARS = st.characters(min_codepoint=33, max_codepoint=126)

# Arbitrary "wrong key" strings: any non-empty header-safe text. Individual
# tests additionally `assume` the drawn value differs from the configured key.
wrong_keys = st.text(alphabet=_HEADER_CHARS, min_size=1, max_size=64)

# Arbitrary staff-cookie payloads simulating a Staff_User session cookie.
cookie_values = st.text(alphabet=_HEADER_CHARS, min_size=1, max_size=128)


@given(wrong_key=wrong_keys)
@_PBT_SETTINGS
def test_rejects_wrong_key_in_header(monkeypatch, wrong_key):
    """A wrong X-Ingest-Key is rejected with HTTP 401."""
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    assume(wrong_key.strip() != CONFIGURED_KEY)
    request = _make_request({"X-Ingest-Key": wrong_key})
    _assert_rejected(request)


@given(wrong_key=wrong_keys)
@_PBT_SETTINGS
def test_rejects_wrong_key_in_bearer(monkeypatch, wrong_key):
    """A wrong Authorization: Bearer key is rejected with HTTP 401."""
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    assume(wrong_key.strip() != CONFIGURED_KEY)
    request = _make_request({"Authorization": f"Bearer {wrong_key}"})
    _assert_rejected(request)


@given(data=st.data())
@_PBT_SETTINGS
def test_rejects_missing_key(monkeypatch, data):
    """A request with no ingest key at all is rejected with HTTP 401."""
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    # Optionally include arbitrary unrelated headers, but never an ingest key.
    extra = data.draw(
        st.dictionaries(
            keys=st.sampled_from(["Accept", "Content-Type", "User-Agent"]),
            values=st.text(alphabet=_HEADER_CHARS, min_size=0, max_size=32),
            max_size=3,
        )
    )
    request = _make_request(extra)
    _assert_rejected(request)


@given(cookie=cookie_values)
@_PBT_SETTINGS
def test_rejects_staff_cookie_only(monkeypatch, cookie):
    """A request authenticated only by a Staff_User session cookie is rejected.

    Validates Requirement 3.5: staff session cookies are never accepted as
    ingest authentication.
    """
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    request = _make_request({"Cookie": f"session={cookie}"})
    _assert_rejected(request)


@given(cookie=cookie_values, wrong_key=wrong_keys)
@_PBT_SETTINGS
def test_rejects_staff_cookie_with_wrong_key(monkeypatch, cookie, wrong_key):
    """A staff cookie combined with an invalid ingest key is still rejected."""
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    assume(wrong_key.strip() != CONFIGURED_KEY)
    request = _make_request(
        {"Cookie": f"session={cookie}", "X-Ingest-Key": wrong_key}
    )
    _assert_rejected(request)


@given(cookie=cookie_values)
@_PBT_SETTINGS
def test_accepts_correct_key_even_with_staff_cookie(monkeypatch, cookie):
    """A correct X-Ingest-Key is accepted regardless of any staff cookie."""
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    request = _make_request(
        {"Cookie": f"session={cookie}", "X-Ingest-Key": CONFIGURED_KEY}
    )
    # Should not raise.
    assert verify_ingest_key(request) is None


@given(cookie=cookie_values)
@_PBT_SETTINGS
def test_accepts_correct_bearer_key(monkeypatch, cookie):
    """A correct Authorization: Bearer key is accepted."""
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", CONFIGURED_KEY)
    request = _make_request(
        {"Cookie": f"session={cookie}", "Authorization": f"Bearer {CONFIGURED_KEY}"}
    )
    assert verify_ingest_key(request) is None
