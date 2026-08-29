"""Streaming hashing and integrity verification for forensic evidence.

Hashing is a core integrity guarantee: the platform records a fingerprint of
an evidence file at registration time and can later recompute it to prove the
file was not altered. Just like the rest of the platform, this layer is honest
about what it does not know — a file that was never hashed has no recorded
digests, and verification against a missing digest reports that gap rather
than silently passing.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_ALGORITHMS = ("md5", "sha1", "sha256")


class MissingFileError(FileNotFoundError):
    pass


def hash_stream(fileobj, algorithms: Iterable[str] = SUPPORTED_ALGORITHMS, chunk_size: int = 1 << 20) -> Dict[str, str]:
    """Compute hex digests by streaming a binary file object.

    Streams in fixed-size chunks so arbitrarily large images can be hashed
    without loading them into memory.
    """
    hashers = {alg: hashlib.new(alg) for alg in algorithms}
    while True:
        chunk = fileobj.read(chunk_size)
        if not chunk:
            break
        for h in hashers.values():
            h.update(chunk)
    return {alg: hasher.hexdigest() for alg, hasher in hashers.items()}


def hash_file(path: Path, algorithms: Iterable[str] = SUPPORTED_ALGORITHMS, chunk_size: int = 1 << 20) -> Dict[str, str]:
    """Compute hex digests for a file on disk."""
    path = Path(path)
    if not path.exists():
        raise MissingFileError(f"File does not exist: {path}")
    with path.open("rb") as f:
        return hash_stream(f, algorithms=algorithms, chunk_size=chunk_size)


def hash_single(path: Path, algorithm: str = "sha256", chunk_size: int = 1 << 20) -> str:
    """Shortcut: return a single algorithm's hex digest for a file."""
    return hash_file(path, algorithms=[algorithm], chunk_size=chunk_size)[algorithm]


def verify_file(path: Path, expected: Dict[str, str], algorithms: Optional[Iterable[str]] = None,
                chunk_size: int = 1 << 20) -> Dict[str, bool]:
    """Recompute digests and compare them to the expected values.

    Returns a mapping of algorithm -> whether the recomputed digest matches.
    Only algorithms whose expected value was provided are compared; algorithms
    without a recorded expected value are reported as ``None`` (unknown), not
    as verified — integrity can only be asserted for digests that exist.
    """
    path = Path(path)
    if not path.exists():
        raise MissingFileError(f"File does not exist: {path}")

    wanted = list(expected.keys()) if algorithms is None else [a for a in algorithms if a in expected]
    if not wanted:
        # Nothing recorded to verify against — the honest answer is "unknown".
        return {}

    computed = hash_file(path, algorithms=wanted, chunk_size=chunk_size)
    result: Dict[str, bool] = {}
    for alg in expected:
        if alg in computed:
            result[alg] = (computed[alg].lower() == str(expected[alg]).lower())
    return result


def verification_passed(result: Dict[str, bool]) -> bool:
    """True only if every compared digest matched and at least one was compared."""
    return bool(result) and all(result.values())


def write_manifest(path: Path, digests: Dict[str, str], *, source: str = "", created: str = "") -> Path:
    """Write a hash manifest file next to the evidence file (``<name>.sha256`` style).

    Returns the manifest path. The manifest encodes algorithm and digest per
    line for a single file, matching the common forensic convention.
    """
    path = Path(path)
    manifest = path.with_suffix(path.suffix + ".hash")
    lines = []
    if source or created:
        lines.append(f"# source: {source}")
        lines.append(f"# created: {created}")
    for alg in SUPPORTED_ALGORITHMS:
        if alg in digests:
            lines.append(f"{alg.upper()} {digests[alg]}  {path.name}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def parse_manifest(manifest: Path) -> Dict[str, str]:
    """Read a manifest file back into {algorithm: digest}."""
    result: Dict[str, str] = {}
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        alg, _, rest = line.partition(" ")
        digest = rest.split()[0] if rest.split() else ""
        if alg.lower() in SUPPORTED_ALGORITHMS and digest:
            result[alg.lower()] = digest
    return result
