"""Record written images in the case database and the evidence manifest.

The ``disk_images`` table has existed since the first schema but nothing ever
wrote to it -- an acquisition produced a folder, and that was the end of it.
This module closes that loop: a finished image gets a row, one custody event,
an audit entry, and one ``record_acquired`` artifact per segment, so it appears
in the case manifest and the SHA256SUMS file exactly like any other artifact.

Adding columns is done with ``PRAGMA table_info`` plus ``ALTER TABLE ADD COLUMN``
because :func:`desktop.core.database.init_db` only issues ``CREATE TABLE IF NOT
EXISTS`` and therefore never touches an existing database's schema.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from .. import evidence_store
from ..database import audit_log, get_db
from .base import ImageRequest, ImageResult

#: Columns this feature adds to ``disk_images``. Kept separate from the original
#: ``CREATE TABLE`` so an existing case database keeps working.
IMAGE_COLUMNS = {
    "case_id": "TEXT DEFAULT ''",
    "base_name": "TEXT DEFAULT ''",
    "source_type": "TEXT DEFAULT ''",
    "source_path": "TEXT DEFAULT ''",
    "segments": "TEXT DEFAULT ''",
    "compression": "TEXT DEFAULT ''",
    "segment_size": "INTEGER DEFAULT 0",
    "verified": "INTEGER DEFAULT 0",
    "verification_status": "TEXT DEFAULT ''",
    "completed_at": "TEXT DEFAULT ''",
    "metadata": "TEXT DEFAULT '{}'",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_image_schema(conn: sqlite3.Connection) -> list[str]:
    """Add any missing ``disk_images`` columns. Returns the names added."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(disk_images)").fetchall()}
    added = []
    for name, declaration in IMAGE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE disk_images ADD COLUMN {name} {declaration}")
            added.append(name)
    if added:
        conn.commit()
    return added


def _ensure_session(conn: sqlite3.Connection, case_id: str, session_id: Optional[str], actor: str) -> str:
    """Return ``session_id``, creating a session when the operator never ran stage 1.

    ``disk_images.session_id`` is ``NOT NULL REFERENCES forensic_sessions(id)``,
    so an image cannot be recorded without a session to hang it on.
    """
    if session_id:
        return session_id
    session_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO forensic_sessions (id, case_id, status, current_stage, operator, notes, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (session_id, case_id or "", "imaging", 3, actor or "", "created for image acquisition", now, now),
    )
    conn.commit()
    return session_id


def _source_type(request: ImageRequest, explicit: str) -> str:
    if explicit:
        return explicit
    return "device" if str(request.source_label).startswith("/dev/") else "folder"


def record_image(
    result: ImageResult,
    request: ImageRequest,
    case_id: str,
    session_id: Optional[str] = None,
    actor: str = "operator",
    source_type: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Persist a finished image: ``disk_images`` row + manifest + custody + audit."""
    owns = conn is None
    conn = conn or get_db()
    try:
        ensure_image_schema(conn)
        session = _ensure_session(conn, case_id, session_id, actor)

        if not request.verify:
            verification = "skipped"
        elif result.verified:
            verification = "verified"
        else:
            verification = "failed"
        status = "failed" if verification == "failed" else "complete"

        segments = [
            {"path": path.name, "size": path.stat().st_size, request.hash_algo: digest}
            for path, digest in result.segment_digests
        ]

        image_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO disk_images (id, session_id, image_path, image_format, hash_algorithm, hash_value,"
            " size_bytes, partition_name, status, created_at, case_id, base_name, source_type, source_path,"
            " segments, compression, segment_size, verified, verification_status, completed_at, metadata)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                image_id, session, str(result.primary), result.image_format, result.hash_algo,
                result.digest, result.total_bytes, request.source_label, status, now,
                case_id or "", request.base_name, _source_type(request, source_type),
                request.source_label, json.dumps(segments), request.compression,
                request.segment_size, 1 if result.verified else 0, verification, now,
                json.dumps(request.metadata),
            ),
        )
        conn.commit()
    finally:
        if owns:
            conn.close()

    # Manifest + custody live in the case tree, not the database.
    artifacts = []
    for segment in result.segments:
        record = evidence_store.record_acquired(
            case_id,
            request.source_label,
            segment,
            "image",
            detail=f"{result.image_format} image {result.primary.name}",
        )
        if record is not None:
            artifacts.append(record.artifact_id)

    evidence_store.log_custody(
        case_id, "IMAGE_CREATED", actor or "operator",
        f"{result.image_format} {result.primary.name} "
        f"{result.hash_algo}={result.digest[:16]}... segments={len(result.segments)} "
        f"verification={verification}",
    )
    audit_log(
        "image_created", actor or "operator", str(result.primary),
        f"format={result.image_format} segments={len(result.segments)} verification={verification}",
    )

    return {
        "image_id": image_id,
        "session_id": session,
        "status": status,
        "verification_status": verification,
        "artifacts": artifacts,
        "segments": segments,
    }


def read_images(case_id: str, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    """Images recorded for a case, newest first."""
    owns = conn is None
    conn = conn or get_db()
    try:
        ensure_image_schema(conn)
        rows = conn.execute(
            "SELECT * FROM disk_images WHERE case_id = ? ORDER BY created_at DESC",
            (case_id,),
        ).fetchall()
    finally:
        if owns:
            conn.close()
    return [dict(row) for row in rows]


__all__ = ["IMAGE_COLUMNS", "ensure_image_schema", "read_images", "record_image"]
