"""Analysis service: associate detected type and metadata with an artifact.

``analyze_artifact`` performs type detection (magic bytes) and metadata
extraction on the artifact's stored file, then writes the results back onto
the artifact in an auditable, structured way. Analysis is deterministic and
records what was actually found.

If the artifact has no stored file, analysis reports that honestly rather than
guessing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from hashing.service import artifact_disk_path

from .magic import detect_type, signature_for
from .metadata import image_metadata, text_encoding

logger = logging.getLogger(__name__)


class NoFileError(ValueError):
    pass


def analyze_bytes_prefix(data: bytes) -> Optional[Dict]:
    """Detect a file type from an in-memory byte prefix (used by tests/CLI)."""
    sig = signature_for(data)
    if sig is None:
        return None
    return {"mime_type": sig.mime, "extension": sig.extension, "description": sig.description}


def analyze_artifact(artifact, *, record=True) -> Dict:
    """Run type detection + metadata analysis on an artifact's stored file.

    Updates ``artifact.mime_type``, ``artifact.extension`` and merges findings
    into ``artifact.metadata``. Returns a dict summarising what was found.
    Raises NoFileError if the artifact has no stored file.
    """
    path = artifact_disk_path(artifact)
    if path is None:
        raise NoFileError(f"Artifact {artifact.artifact_id} has no stored file")

    result: Dict = {}
    sig = detect_type(path)
    if sig is not None:
        result["mime_type"] = sig.mime
        result["extension"] = sig.extension
        result["type_description"] = sig.description

    # Metadata extraction for known media types.
    if sig is not None and sig.mime.startswith("image/"):
        result["metadata"] = image_metadata(path)
    elif sig is not None and sig.mime.startswith("text/"):
        enc = text_encoding(path)
        if enc:
            result["encoding"] = enc

    if record:
        _apply_to_artifact(artifact, result)
    return result


def _apply_to_artifact(artifact, result: Dict) -> None:
    if "mime_type" in result:
        artifact.mime_type = result["mime_type"]
        artifact.extension = artifact.extension or result.get("extension", "")
    metadata = dict(artifact.metadata or {})
    # Persist the analysis summary under a stable key.
    summary = {
        "mime_type": result.get("mime_type"),
        "extension": result.get("extension"),
        "type_description": result.get("type_description"),
        "encoding": result.get("encoding"),
    }
    summary = {k: v for k, v in summary.items() if v is not None}
    if "metadata" in result:
        metadata["image"] = result["metadata"]
    metadata["analysis"] = summary
    artifact.metadata = metadata
    artifact.save(update_fields=["mime_type", "extension", "metadata", "updated"])
