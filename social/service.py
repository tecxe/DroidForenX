"""Service: dispatch social-app database analysis and persist findings.

Given an artifact that holds an acquired app database, this determines which
recognized social/messaging schema it belongs to (WhatsApp today; the framework
makes Facebook/Instagram straightforward to add) and records a structured
summary on the artifact.
"""

from __future__ import annotations

import logging
from typing import Dict

from hashing.service import artifact_disk_path

from .whatsapp import extract as whatsapp_extract

logger = logging.getLogger(__name__)


class NoFileError(ValueError):
    pass


class UnsupportedAppError(ValueError):
    pass


def analyze_social_artifact(artifact, *, record: bool = True) -> Dict:
    """Analyze an artifact's file as a social/messaging database.

    Raises NoFileError if there is no stored file, or UnsupportedAppError if the
    database does not match a recognized social schema.
    """
    path = artifact_disk_path(artifact)
    if path is None:
        raise NoFileError(f"Artifact {artifact.artifact_id} has no stored file")

    pairs = [
        ("whatsapp", whatsapp_extract(path)),
    ]
    raise_unsupported = True
    for app, result in pairs:
        if getattr(result, "is_whatsapp_db", False):
            raise_unsupported = False
            summary = _persist(artifact, app, result)
            return summary

    if raise_unsupported:
        raise UnsupportedAppError(f"{artifact.name} is not a recognized social-media database")
    raise UnsupportedAppError(f"{artifact.name} not recognized")


def _persist(artifact, app: str, result) -> Dict:
    if app == "whatsapp":
        summary = {
            "app": "whatsapp",
            "is_whatsapp_db": result.is_whatsapp_db,
            "message_count": result.message_count,
            "contact_count": len(result.contacts),
            "tables_found": result.tables_found,
            "error": result.error,
            "sample_contacts": result.contacts[:20],
        }
    else:  # pragma: no cover - extension point
        summary = {"app": app}

    metadata = dict(artifact.metadata or {})
    metadata["social"] = {app: summary}
    artifact.metadata = metadata
    artifact.artifact_type = f"{app}_database"
    artifact.category = "social_media"
    artifact.save(update_fields=["metadata", "artifact_type", "category", "updated"])
    return summary
