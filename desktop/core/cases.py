"""Case records: create, edit and delete.

Case Management used to be append-only, so a typo in a case number lived
forever and a mistyped case could never be removed. This module owns the
writes so both the page and the tests exercise one implementation.

A case is not a row in one table. Sessions hang off it, and nine other tables
hang off the sessions, so deleting a case means deleting its dependants in
dependency order inside one transaction. Evidence files on disk are separate:
they are only removed when the caller asks for it, because a file that was
hashed into a manifest is not something to drop by accident.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone

from .database import get_db, audit_log

logger = logging.getLogger(__name__)

#: Tables whose rows belong to a session, and therefore to a case.
SESSION_TABLES = (
    "isolation_actions",
    "network_state",
    "device_identifications",
    "acquisition_profiles",
    "examination_results",
    "forensic_reports",
    "sim_cards",
    "disk_images",
    "evidence_artifacts",
)

#: Columns a user is allowed to edit. ``id`` and ``created_at`` are not here:
#: changing either would silently break every reference to the case.
EDITABLE_FIELDS = ("case_number", "title", "investigator", "agency",
                   "description", "status")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_cases() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_case(case_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def case_number_taken(case_number: str, exclude_id: str = "") -> bool:
    """True when another case already uses this number.

    Case numbers are what an examiner quotes in a report, so two cases sharing
    one is a real problem rather than a cosmetic one.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM cases WHERE case_number=? AND id<>?",
            (case_number.strip(), exclude_id)).fetchone()
        return row is not None
    finally:
        conn.close()


def create_case(case_number: str, title: str, investigator: str = "",
                agency: str = "", description: str = "",
                status: str = "open") -> str:
    case_id = str(uuid.uuid4())
    now = _utc_now()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO cases (id, case_number, title, investigator, agency,"
            " description, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (case_id, case_number.strip(), title.strip(), investigator.strip(),
             agency.strip(), description.strip(), status, now, now))
        conn.commit()
    finally:
        conn.close()
    audit_log("case_created", target=case_id, details=case_number)
    return case_id


def update_case(case_id: str, **fields) -> bool:
    """Update the editable fields of a case. Returns False if nothing changed."""
    changes = {key: value for key, value in fields.items() if key in EDITABLE_FIELDS}
    if not changes:
        return False
    for key in ("case_number", "title", "investigator", "agency", "status"):
        if key in changes and isinstance(changes[key], str):
            changes[key] = changes[key].strip()
    changes["updated_at"] = _utc_now()

    assignments = ", ".join(f"{key}=?" for key in changes)
    conn = get_db()
    try:
        cursor = conn.execute(
            f"UPDATE cases SET {assignments} WHERE id=?",
            (*changes.values(), case_id))
        conn.commit()
        updated = cursor.rowcount > 0
    finally:
        conn.close()
    if updated:
        audit_log("case_updated", target=case_id,
                  details=", ".join(sorted(k for k in changes if k != "updated_at")))
    return updated


def delete_case(case_id: str, delete_evidence: bool = False) -> dict:
    """Delete a case and every database row that hangs off it.

    Evidence files are left on disk unless ``delete_evidence`` is true, and the
    removal is restricted to the directory the case tree owns.
    """
    summary = {"sessions": 0, "rows": 0, "evidence_dir": "", "removed_files": False}

    conn = get_db()
    try:
        session_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM forensic_sessions WHERE case_id=?", (case_id,))
        ]
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            # Children of sim_cards first; both are keyed by the card, not the
            # session, so they cannot join the loop below.
            for table in ("sim_contacts", "sim_messages"):
                conn.execute(
                    f"DELETE FROM {table} WHERE sim_id IN "
                    f"(SELECT id FROM sim_cards WHERE session_id IN ({placeholders}))",
                    session_ids)
            for table in SESSION_TABLES:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE session_id IN ({placeholders})",
                    session_ids)
                summary["rows"] += max(cursor.rowcount, 0)
            cursor = conn.execute(
                f"DELETE FROM forensic_sessions WHERE id IN ({placeholders})",
                session_ids)
            summary["sessions"] = max(cursor.rowcount, 0)
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
        conn.commit()
    finally:
        conn.close()

    audit_log("case_deleted", target=case_id,
              details=f"sessions={summary['sessions']} rows={summary['rows']}")

    if delete_evidence:
        from . import evidence_store
        root = evidence_store.case_dir(case_id)
        base = evidence_store.EVIDENCE_ROOT
        summary["evidence_dir"] = str(root)
        try:
            # Resolve both sides before comparing: ``case_dir`` is built from
            # the evidence root and a sanitised segment, so the resolved case
            # directory must sit directly inside the resolved root.
            if root.is_dir() and root.resolve().parent == base.resolve():
                shutil.rmtree(root)
                summary["removed_files"] = True
            elif root.is_dir():
                logger.warning("Refusing to delete path outside evidence root: %s", root)
        except OSError as exc:
            logger.error("Could not delete evidence directory %s: %s", root, exc)

    return summary
