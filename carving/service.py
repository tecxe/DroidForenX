"""Service: carve a source file and register recovered files as artifacts.

The status engine is respected strictly:
  * a carved file whose footer was found -> DELETED_RECOVERABLE (fully recovered)
  * a carved file that was truncated (no footer) -> RECOVERED with a note, or
    PARTIAL — never reported as a complete recovery.
"""

from __future__ import annotations

import logging
from pathlib import Path

from evidence.models import AcquisitionStatus, EvidenceCategory, EvidenceClassification
from evidence.services import register_artifact

from hashing.service import artifact_disk_path
from .engine import carve_file

logger = logging.getLogger(__name__)


class NoFileError(ValueError):
    pass


def carve_artifact(artifact, *, out_dir: Path = None, min_size: int = 512) -> dict:
    """Carve an evidence artifact's stored file for recoverable data.

    Created carved files are themselves registered as Artifacts (children) under
    the same Evidence container, with status either DELETED_RECOVERABLE or
    RECOVERED (truncated). Returns a summary dict of what was carved.
    """
    src = artifact_disk_path(artifact)
    if src is None:
        raise NoFileError(f"Artifact {artifact.artifact_id} has no stored file")

    if out_dir is None:
        from evidence.storage import category_dir
        out_dir = category_dir("carved") / f"src_{artifact.artifact_id}"

    carved = carve_file(src, out_dir, min_size=min_size)

    recovered_fully = 0
    truncated = 0
    created_artifacts = []
    for c in carved:
        carve_path = out_dir / c.output_name
        status = AcquisitionStatus.DELETED_RECOVERABLE if c.recovered_fully else AcquisitionStatus.RECOVERED
        recovery_status = "RECOVERED" if c.recovered_fully else "PARTIALLY RECOVERED"
        reason = (
            "File carved from image; complete (footer located)."
            if c.recovered_fully
            else "File carved from image; footer not located (may be truncated)."
        )
        new_art = register_artifact(
            artifact.evidence,
            name=c.output_name,
            category=EvidenceCategory.CARVED_FILE,
            classification=EvidenceClassification.CARVED,
            status=status,
            source=f"carved from {artifact.name} @ offset {c.start}",
            storage_path=str(carve_path),
            size_bytes=c.size,
            recovery_status=recovery_status,
            recovery_reason=reason,
        )
        if c.recovered_fully:
            recovered_fully += 1
        else:
            truncated += 1
        created_artifacts.append(c.output_name)

    return {
        "carved_total": len(carved),
        "recovered_fully": recovered_fully,
        "truncated": truncated,
        "out_dir": str(out_dir),
        "artifacts": created_artifacts,
    }
