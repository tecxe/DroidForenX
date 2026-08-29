"""Service: persist SQLite analysis results onto an artifact."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

from hashing.service import artifact_disk_path

from .analyzer import analyze

logger = logging.getLogger(__name__)


class NoFileError(ValueError):
    pass


class NotSqliteError(ValueError):
    pass


def analyze_artifact_database(artifact, *, record: bool = True) -> Dict:
    """Analyze an artifact's file as a SQLite database and persist results.

    Raises NoFileError if there is no stored file, or NotSqliteError if the file
    is not a SQLite database (so callers can route non-databases elsewhere).
    """
    path = artifact_disk_path(artifact)
    if path is None:
        raise NoFileError(f"Artifact {artifact.artifact_id} has no stored file")

    result = analyze(path)
    if not result.is_sqlite:
        raise NotSqliteError(f"{artifact.name} is not a SQLite database")

    summary = {
        "is_sqlite": True,
        "error": result.error,
        "tables": [
            {
                "name": t.name,
                "row_count": t.row_count,
                "error": t.error,
                "columns": [c["name"] for c in t.columns],
            }
            for t in result.tables
        ],
        "deleted_assessment": _deleted_dict(result.deleted),
    }

    if record and artifact:
        metadata = dict(artifact.metadata or {})
        metadata["sqlite"] = summary
        artifact.metadata = metadata
        artifact.artifact_type = "sqlite_database"
        artifact.save(update_fields=["metadata", "artifact_type", "updated"])

    return summary


def _deleted_dict(d) -> Dict:
    if d is None:
        return {"header_valid": False, "note": "not assessed"}
    return {
        "header_valid": d.header_valid,
        "freelist_count": d.freelist_count,
        "pages_with_remains": d.pages_with_remains,
        "total_freeblock_bytes": d.total_freeblock_bytes,
        "has_deleted_remains": d.has_deleted_remains,
        "page_size": d.page_size,
        "file_size_bytes": d.file_size_bytes,
    }
