"""Monolith → Edge-Cloud split migration (Requirements 15.1–15.4).

This script performs the one-time migration from the single-process monolith to
the split architecture. It is **idempotent** and **safe to re-run**: every step
either performs its change or detects the change has already been made and does
nothing.

What it does
------------
1. **Split camera credentials out of the cloud registry.**
   Reads the existing ``machines`` table (machine metadata + the Fernet-encrypted
   ``rtsp_url_encrypted``) and produces the Edge_Agent's git-excluded
   ``Local_Camera_Config`` JSON file keyed by ``machine_id``. The RTSP URL is
   written in its **encrypted** Fernet form — the exact ciphertext already stored
   in the DB — because the Edge_Agent's ``edge.camera_crypto`` uses the *same*
   Fernet key format and can decrypt it (``rtsp_url_encrypted`` entries are a
   first-class shape in ``edge.local_camera_config``). No plaintext credential is
   ever written by this script and no credential is emitted to logs (only
   redacted URLs). Optionally (``--emit-plaintext``) the URL can be decrypted and
   written as plaintext ``rtsp_url`` instead, for operators who do not share the
   Fernet key with the edge host.

   The cloud ``machines`` table keeps only credential-free metadata on its read
   path: the cloud ``CloudMachineRegistry`` selects a fixed metadata-only column
   set and never reads ``rtsp_url_encrypted`` (Requirements 13.2, 13.3). This
   migration therefore imports the existing station configs into the cloud
   registry as ``Machine_Metadata`` (they are already in the ``machines`` table)
   while moving the secrets to the edge config (Requirements 15.1, 15.2). By
   default the at-rest ciphertext column is left untouched (non-destructive); pass
   ``--scrub-cloud-credentials`` to additionally blank it in the cloud DB.

2. **Preserve and tag existing Session_Records and Alerts.**
   Existing ``sessions``, ``alerts`` and ``machine_state_events`` rows are never
   deleted or rewritten. Migration 004 already added ``event_id`` /
   ``session_uuid`` (sessions), ``event_id`` / ``event_image_url`` (alerts) and
   ``event_id`` (machine_state_events). This script only **backfills the
   machine-ID tag** where it is missing (NULL or empty), using ``--default-machine-id``
   (config ``MACHINE_ID``, default ``M-01``). ``event_id`` is intentionally left
   ``NULL`` on pre-existing rows: historical records were produced by the
   monolith and have no Edge_Agent-assigned Event_ID (Requirement 15.4 — the
   schema is *extended* with Event_ID; historical rows simply carry none).

Usage
-----
    python -m scripts.migrate_monolith_to_split \
        --db tracker.db \
        --camera-config camera_config.json \
        --default-machine-id M-01

    # decrypt to plaintext instead of copying the ciphertext:
    python -m scripts.migrate_monolith_to_split --emit-plaintext

    # also blank the (unused) at-rest ciphertext column in the cloud DB:
    python -m scripts.migrate_monolith_to_split --scrub-cloud-credentials

Re-running the script is a no-op once the split has been applied.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure the project root is importable when run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import structlog  # noqa: E402

from db.migrations import MigrationRunner  # noqa: E402
from edge.camera_crypto import decrypt_rtsp_url, redact_rtsp_url  # noqa: E402

logger = structlog.get_logger(__name__)

# Tables whose machine-ID tag is backfilled when missing.
_TAGGED_TABLES = ("sessions", "alerts", "machine_state_events")


@dataclass
class MigrationSummary:
    """Structured, credential-free result of a migration run."""

    camera_config_path: str
    exported_machine_ids: List[str] = field(default_factory=list)
    skipped_machine_ids: List[str] = field(default_factory=list)
    backfilled_tags: Dict[str, int] = field(default_factory=dict)
    scrubbed_cloud_credentials: int = 0
    migrations_applied: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "camera_config_path": self.camera_config_path,
            "exported_machine_ids": sorted(self.exported_machine_ids),
            "skipped_machine_ids": sorted(self.skipped_machine_ids),
            "backfilled_tags": dict(self.backfilled_tags),
            "scrubbed_cloud_credentials": self.scrubbed_cloud_credentials,
            "migrations_applied": self.migrations_applied,
        }


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a synchronous sqlite3 connection with row access by name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def ensure_schema(db_path: str) -> int:
    """Apply any pending migrations (incl. 004) so the split schema exists.

    Idempotent: the migration runner skips already-applied migrations. Returns
    the number of migrations applied on this run (0 if already up to date).
    """
    runner = MigrationRunner(db_path)
    try:
        applied = runner.run()
    finally:
        runner.close()
    return len(applied)


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


def export_local_camera_config(
    conn: sqlite3.Connection,
    camera_config_path: str,
    *,
    emit_plaintext: bool = False,
) -> MigrationSummary:
    """Build the Edge_Agent Local_Camera_Config from the ``machines`` table.

    Merges into any existing config file (idempotent, does not clobber
    manually-added machines). The RTSP URL is written encrypted by default
    (same Fernet key format the edge understands) or as plaintext when
    ``emit_plaintext`` is set. Machines whose stored ciphertext cannot be
    decrypted are skipped and reported (never fatal).
    """
    summary = MigrationSummary(camera_config_path=camera_config_path)

    # Load an existing config so re-runs and manual entries are preserved.
    existing: Dict[str, Any] = {}
    if os.path.isfile(camera_config_path):
        try:
            with open(camera_config_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                existing = loaded
            else:
                logger.warning(
                    "Existing camera config is not a JSON object — starting fresh",
                    path=camera_config_path,
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Could not read existing camera config — starting fresh",
                path=camera_config_path,
                error=str(exc),
            )

    rows = conn.execute(
        "SELECT machine_id, rtsp_url_encrypted FROM machines ORDER BY machine_id"
    ).fetchall()

    merged: Dict[str, Any] = dict(existing)
    for row in rows:
        machine_id = row["machine_id"]
        encrypted = row["rtsp_url_encrypted"]
        if not encrypted:
            logger.warning(
                "Machine has no stored RTSP ciphertext — skipping",
                machine_id=machine_id,
            )
            summary.skipped_machine_ids.append(machine_id)
            continue

        # Validate we can decrypt (proves the edge can too, same key format).
        try:
            plaintext = decrypt_rtsp_url(encrypted)
        except Exception as exc:  # noqa: BLE001 — surface as skip, never crash
            logger.warning(
                "Could not decrypt stored RTSP URL — skipping machine",
                machine_id=machine_id,
                error=type(exc).__name__,
            )
            summary.skipped_machine_ids.append(machine_id)
            continue

        if emit_plaintext:
            entry = {"rtsp_url": plaintext}
        else:
            entry = {"rtsp_url_encrypted": encrypted}

        merged[machine_id] = entry
        summary.exported_machine_ids.append(machine_id)
        logger.info(
            "Exported camera credentials to Local_Camera_Config",
            machine_id=machine_id,
            rtsp_url=redact_rtsp_url(plaintext),
            form="plaintext" if emit_plaintext else "encrypted",
        )

    # Write atomically (temp file + replace) to avoid a truncated config on crash.
    out_dir = os.path.dirname(os.path.abspath(camera_config_path))
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = f"{camera_config_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, camera_config_path)

    logger.info(
        "Local_Camera_Config written",
        path=camera_config_path,
        machine_count=len(merged),
        exported=len(summary.exported_machine_ids),
        skipped=len(summary.skipped_machine_ids),
    )
    return summary


def backfill_machine_tags(
    conn: sqlite3.Connection, default_machine_id: str
) -> Dict[str, int]:
    """Backfill machine-ID tagging on existing records where it is missing.

    Only rows with a NULL or empty ``machine_id`` are updated, so re-running is
    a no-op. ``event_id`` is deliberately left untouched (NULL) on pre-existing
    rows — historical monolith records have no Edge_Agent Event_ID.
    """
    counts: Dict[str, int] = {}
    for table in _TAGGED_TABLES:
        cols = _table_columns(conn, table)
        if "machine_id" not in cols:
            counts[table] = 0
            continue
        cur = conn.execute(
            f"UPDATE {table} SET machine_id = ? "
            "WHERE machine_id IS NULL OR TRIM(machine_id) = ''",
            (default_machine_id,),
        )
        counts[table] = cur.rowcount if cur.rowcount is not None else 0
        logger.info(
            "Backfilled machine-ID tag",
            table=table,
            rows_tagged=counts[table],
            default_machine_id=default_machine_id,
        )
    return counts


def scrub_cloud_credentials(conn: sqlite3.Connection) -> int:
    """Blank the at-rest RTSP ciphertext column in the cloud ``machines`` table.

    Optional and destructive to the credential column only (metadata is
    untouched). The cloud read path (``CloudMachineRegistry``) never reads this
    column, so blanking it is purely defence-in-depth for Requirement 15.2.
    Idempotent: already-blank rows are not counted again.
    """
    cols = _table_columns(conn, "machines")
    if "rtsp_url_encrypted" not in cols:
        return 0
    cur = conn.execute(
        "UPDATE machines SET rtsp_url_encrypted = '' "
        "WHERE rtsp_url_encrypted IS NOT NULL AND rtsp_url_encrypted != ''"
    )
    scrubbed = cur.rowcount if cur.rowcount is not None else 0
    if scrubbed:
        logger.info("Scrubbed at-rest RTSP ciphertext from cloud DB", rows=scrubbed)
    return scrubbed


def run_migration(
    db_path: str,
    camera_config_path: str,
    default_machine_id: str = "M-01",
    *,
    emit_plaintext: bool = False,
    scrub: bool = False,
    apply_schema: bool = True,
) -> MigrationSummary:
    """Run the full idempotent monolith→split migration.

    Steps: (1) ensure schema (migration 004), (2) export Local_Camera_Config,
    (3) backfill machine-ID tags, (4) optionally scrub cloud credentials. The
    DB mutations run in a single transaction that rolls back on any error.
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Database not found: {db_path!r}")

    migrations_applied = 0
    if apply_schema:
        migrations_applied = ensure_schema(db_path)

    conn = _connect(db_path)
    try:
        # Camera-config export reads only; write the edge file first so a later
        # DB failure never leaves credentials only half-extracted.
        summary = export_local_camera_config(
            conn, camera_config_path, emit_plaintext=emit_plaintext
        )
        summary.migrations_applied = migrations_applied

        # DB mutations atomically.
        conn.execute("BEGIN")
        try:
            summary.backfilled_tags = backfill_machine_tags(conn, default_machine_id)
            if scrub:
                summary.scrubbed_cloud_credentials = scrub_cloud_credentials(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    logger.info("Monolith→split migration complete", **summary.as_dict())
    return summary


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate the monolith DB to the edge-cloud split.",
    )
    # Import config defaults lazily so importing this module never requires .env.
    try:
        from config import DB_PATH as _DEFAULT_DB
        from config import MACHINE_ID as _DEFAULT_MID
    except Exception:  # noqa: BLE001
        _DEFAULT_DB, _DEFAULT_MID = "tracker.db", "M-01"
    from edge.local_camera_config import DEFAULT_CONFIG_PATH as _DEFAULT_CFG

    parser.add_argument("--db", default=_DEFAULT_DB, help="Path to the (cloud) SQLite DB.")
    parser.add_argument(
        "--camera-config",
        default=_DEFAULT_CFG,
        help="Output path for the Edge_Agent Local_Camera_Config JSON.",
    )
    parser.add_argument(
        "--default-machine-id",
        default=_DEFAULT_MID,
        help="Machine ID used to backfill untagged historical records.",
    )
    parser.add_argument(
        "--emit-plaintext",
        action="store_true",
        help="Decrypt and write plaintext rtsp_url instead of the ciphertext.",
    )
    parser.add_argument(
        "--scrub-cloud-credentials",
        action="store_true",
        help="Also blank the (unused) at-rest RTSP ciphertext column in the cloud DB.",
    )
    parser.add_argument(
        "--no-apply-schema",
        action="store_true",
        help="Do not run pending migrations first (assume schema is ready).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        summary = run_migration(
            db_path=args.db,
            camera_config_path=args.camera_config,
            default_machine_id=args.default_machine_id,
            emit_plaintext=args.emit_plaintext,
            scrub=args.scrub_cloud_credentials,
            apply_schema=not args.no_apply_schema,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Migration failed", error=str(exc))
        print(f"ERROR: migration failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
