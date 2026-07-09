"""Property-based tests for the credential-free cloud machine metadata read path.

# Feature: edge-cloud-split, Property 21: Cloud never persists or exposes camera
# credentials — for any ingest payload processed by the Cloud_Server and any
# Machine_Metadata response it serializes, no stored value and no serialized
# output field contains a camera password or RTSP credential.

Validates: Requirements 13.2, 13.3

Strategy
--------
Machines are registered through the authoritative ``MachineRegistry`` with real
RTSP URLs carrying arbitrary usernames/passwords/hosts (encrypted at rest). The
``CloudMachineRegistry`` read path (``get_metadata`` / ``list_metadata`` /
``is_registered``) is then exercised against a temporary on-disk SQLite database
migrated with the real migrations (001-004). We assert that:

  * the returned ``MachineMetadata`` carries no credential-bearing field names,
  * no serialized metadata value contains the secret RTSP URL, username, or
    password, and
  * every SELECT issued by the cloud read path never references the
    ``rtsp_url_encrypted`` column.
"""

import asyncio
import os
import sys
import tempfile

# Ensure a valid Fernet key is present before importing the registry module so
# RTSP encryption/decryption is exercised deterministically.
if not os.getenv("FERNET_KEY"):
    from cryptography.fernet import Fernet

    os.environ["FERNET_KEY"] = Fernet.generate_key().decode()

# Ensure project root is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from db.async_database import AsyncDatabase
from db.migrations import MigrationRunner
from engine.machine_registry import (
    CloudMachineRegistry,
    MachineRegistry,
    decrypt_rtsp_url,
)

# Column that stores encrypted RTSP credentials — must NEVER be referenced by
# the cloud read path (Requirements 13.2, 13.3).
FORBIDDEN_COLUMN = "rtsp_url_encrypted"

# Substrings in a metadata field name that would indicate a credential leak.
CREDENTIAL_FIELD_MARKERS = ("rtsp", "username", "password", "credential", "secret")


# ── Recording database wrapper ───────────────────────────────────────────────


class _RecordingDB(AsyncDatabase):
    """AsyncDatabase that records every SQL statement it executes.

    Lets the property assert that the cloud read path never references the
    encrypted-credential column.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_sql: list[str] = []

    async def fetch_one(self, sql, params=()):
        self.executed_sql.append(sql)
        return await super().fetch_one(sql, params)

    async def fetch_all(self, sql, params=()):
        self.executed_sql.append(sql)
        return await super().fetch_all(sql, params)

    async def execute(self, sql, params=()):
        self.executed_sql.append(sql)
        return await super().execute(sql, params)


# ── Module-scoped migrated database file ─────────────────────────────────────


@pytest.fixture(scope="module")
def migrated_db_path():
    """Create a temporary SQLite DB migrated with the real migrations 001-004."""
    tmp_dir = tempfile.mkdtemp(prefix="cloud_registry_pbt_")
    db_path = os.path.join(tmp_dir, "cloud.db")

    runner = MigrationRunner(db_path)
    runner.run()
    runner.close()

    yield db_path

    # Cleanup: remove the DB file and any WAL/SHM side files, then the dir.
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass


# ── Generators for arbitrary credentials / hosts ─────────────────────────────

# machine_id: alphanumeric + hyphen, 1-20 chars (per validate_machine_id).
_machine_id = st.from_regex(r"\A[a-zA-Z0-9\-]{1,20}\Z")

# Distinctive credential components: a fixed marker prefix plus arbitrary
# alphanumeric tail. The marker guarantees the generated secret is meaningfully
# distinct from credential-free metadata (which is derived from machine_id and a
# fixed display prefix), so a substring hit unambiguously signals a real leak
# rather than a coincidental collision.
_alnum = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=6,
    max_size=14,
)
_username = _alnum.map(lambda s: "usr" + s)
_password = _alnum.map(lambda s: "pwd" + s)
_host = st.from_regex(r"\A[a-z0-9]{2,10}(\.[a-z0-9]{2,6}){0,3}\Z")
_port = st.integers(min_value=1, max_value=65535)
_path = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=0, max_size=10
)


def _build_rtsp_url(username: str, password: str, host: str, port: int, path: str) -> str:
    return f"rtsp://{username}:{password}@{host}:{port}/{path}"


async def _register_and_read(
    db_path: str,
    machine_id: str,
    display_name: str,
    detection_zone: str,
    threshold: float,
    light_zone,
    rtsp_url: str,
):
    """Register a machine with credentials, then exercise the cloud read path.

    Returns ``(metadata, metadata_list, is_registered, cloud_sql)`` where
    ``cloud_sql`` is the list of SQL statements issued by the cloud read path.
    """
    db = _RecordingDB(db_path=db_path)
    await db.connect()
    try:
        registry = MachineRegistry(db)
        await registry.register(
            {
                "machine_id": machine_id,
                "display_name": display_name,
                "rtsp_url": rtsp_url,
                "detection_zone": detection_zone,
                "person_confidence_threshold": threshold,
                "light_zone": light_zone,
            }
        )

        # Sanity: the credential really is stored (encrypted) and recoverable —
        # i.e. we are genuinely testing a machine that HAS a secret.
        stored = await registry.get_decrypted_url(machine_id)
        assert stored == rtsp_url

        cloud = CloudMachineRegistry(db)

        # Only record SQL issued by the cloud read path.
        db.executed_sql.clear()
        metadata = await cloud.get_metadata(machine_id)
        metadata_list = await cloud.list_metadata(status=None)
        is_registered = await cloud.is_registered(machine_id)
        cloud_sql = list(db.executed_sql)

        return metadata, metadata_list, is_registered, cloud_sql
    finally:
        # Clean the row so repeated Hypothesis examples don't collide on the
        # unique machine_id, then close the connection.
        try:
            await db.execute(
                "DELETE FROM machines WHERE machine_id = ?", (machine_id,)
            )
        except Exception:
            pass
        await db.close()


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    machine_id=_machine_id,
    username=_username,
    password=_password,
    host=_host,
    port=_port,
    path=_path,
    threshold=st.floats(min_value=0.0, max_value=1.0),
    has_light_zone=st.booleans(),
)
def test_cloud_never_persists_or_exposes_credentials(
    migrated_db_path,
    machine_id,
    username,
    password,
    host,
    port,
    path,
    threshold,
    has_light_zone,
):
    """Property 21: the cloud metadata read path never exposes credentials."""
    rtsp_url = _build_rtsp_url(username, password, host, port, path)

    # Credential-free metadata values, deliberately independent of the secrets.
    display_name = f"Machine {machine_id}"
    detection_zone = "(0.0, 0.0, 1.0, 1.0)"
    light_zone = "(0.4, 0.1, 0.6, 0.3)" if has_light_zone else None

    metadata, metadata_list, is_registered, cloud_sql = asyncio.run(
        _register_and_read(
            migrated_db_path,
            machine_id,
            display_name,
            detection_zone,
            threshold,
            light_zone,
            rtsp_url,
        )
    )

    # The machine is known to the registration lookup used by ingest.
    assert is_registered is True

    # get_metadata and list_metadata both return the machine.
    assert metadata is not None
    matching = [m for m in metadata_list if m.machine_id == machine_id]
    assert len(matching) == 1

    secrets = (rtsp_url, username, password)

    for model in (metadata, matching[0]):
        # 1. No credential-bearing field NAMES on the serialized model.
        dumped = model.model_dump()
        for field_name in dumped:
            lowered = field_name.lower()
            assert not any(
                marker in lowered for marker in CREDENTIAL_FIELD_MARKERS
            ), f"credential-bearing field name exposed: {field_name}"

        # 2. No serialized VALUE contains a secret (RTSP URL / username / password).
        serialized = model.model_dump_json()
        for secret in secrets:
            assert secret not in serialized, (
                "credential value leaked into serialized MachineMetadata"
            )

    # 3. No SELECT issued by the cloud read path references the encrypted column.
    for sql in cloud_sql:
        assert FORBIDDEN_COLUMN not in sql, (
            f"cloud read path referenced forbidden column in SQL: {sql}"
        )
    # Sanity: the cloud path actually issued queries.
    assert cloud_sql, "expected the cloud read path to issue at least one query"
