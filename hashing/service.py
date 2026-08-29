"""Service functions tying streaming hashing to the evidence model.

These helpers hash an artifact's stored file, record authoritative digests in
``HashRecord``, copy the fast-lookup digests onto the artifact, write a
standalone hash manifest next to the evidence file, and verify integrity.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, Optional

from django.utils import timezone

from evidence.storage import evidence_root

from .core import (
    SUPPORTED_ALGORITHMS,
    MissingFileError,
    hash_file,
    parse_manifest,
    verification_passed,
    verify_file,
    write_manifest,
)
from .models import HashRecord

logger = logging.getLogger(__name__)

# Fast-copy field names on Artifact, keyed by algorithm.
_ARTIFACT_FIELD = {
    "md5": "md5",
    "sha1": None,
    "sha256": "sha256",
    "sha512": "sha512",
}


def artifact_disk_path(artifact) -> Optional[Path]:
    """Resolve the on-disk path of an artifact, or None if not stored."""
    if not artifact.storage_path:
        return None
    p = Path(artifact.storage_path)
    if not p.is_absolute():
        p = evidence_root() / p
    if p.exists():
        return p
    logger.info("artifact storage path missing: %s", p)
    return None


def hash_artifact(
    artifact,
    algorithms: Iterable[str] = ("md5", "sha1", "sha256", "sha512"),
    write_manifest_file: bool = True,
) -> Dict[str, str]:
    """Hash the artifact's stored file and persist the results.

    Returns the computed digests. Raises MissingFileError if the artifact has
    no stored file. The artifact's fast-copy fields and a manifest file are
    updated, and authoritative HashRecord rows are written.
    """
    path = artifact_disk_path(artifact)
    if path is None:
        raise MissingFileError(f"Artifact {artifact.artifact_id} has no stored file")

    digests = hash_file(path, algorithms=algorithms)
    size = path.stat().st_size

    # Authoritative records (upsert).
    for alg in algorithms:
        if alg not in digests:
            continue
        HashRecord.objects.update_or_create(
            artifact=artifact,
            algorithm=alg,
            defaults={
                "digest": digests[alg],
                "file_path": str(path),
                "size_bytes": size,
            },
        )

    # Fast-copy fields.
    for alg, field in _ARTIFACT_FIELD.items():
        if field and alg in digests:
            setattr(artifact, field, digests[alg])
    artifact.size_bytes = artifact.size_bytes or size
    artifact.save(update_fields=[f for f in _ARTIFACT_FIELD.values() if f] + ["size_bytes", "updated"])

    if write_manifest_file:
        write_manifest(
            path,
            digests,
            source=artifact.source,
            created=timezone.now().isoformat(),
        )
    return digests


def verify_artifact(artifact, algorithms: Optional[Iterable[str]] = None) -> Dict[str, bool]:
    """Recompute digests for an artifact and compare against recorded values.

    Returns {algorithm: True/False}. Algorithms with no recorded digest are
    omitted (unknown), never reported as verified.
    """
    records = {r.algorithm: r.digest for r in HashRecord.objects.filter(artifact=artifact)}
    if not records:
        # Fall back to the artifact's fast-copy fields for comparison.
        records = {alg: getattr(artifact, field, "")
                   for alg, field in _ARTIFACT_FIELD.items() if field and getattr(artifact, field, "")}

    path = artifact_disk_path(artifact)
    if path is None:
        return {}

    result = verify_file(path, records, algorithms=algorithms)
    now = timezone.now()
    ok = verification_passed(result)
    # Record verification outcome on HashRecords we compared.
    for alg, matched in result.items():
        try:
            rec = HashRecord.objects.get(artifact=artifact, algorithm=alg)
            rec.verified_at = now
            rec.verified_ok = bool(matched)
            rec.save(update_fields=["verified_at", "verified_ok"])
        except HashRecord.DoesNotExist:
            pass
    return result


def artifact_verifies(artifact) -> bool:
    """Convenience wrapper returning True only if the artifact's file matches
    every recorded digest it has (and has at least one)."""
    return verification_passed(verify_artifact(artifact))


def write_artifact_manifest(artifact) -> Path:
    """Write/refresh a standalone hash manifest next to the artifact file."""
    path = artifact_disk_path(artifact)
    if path is None:
        raise MissingFileError(f"Artifact {artifact.artifact_id} has no stored file")
    digests = {rec.algorithm: rec.digest for rec in HashRecord.objects.filter(artifact=artifact)}
    return write_manifest(path, digests, source=artifact.source, created=timezone.now().isoformat())
