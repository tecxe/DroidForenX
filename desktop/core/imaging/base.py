"""Shared types and helpers for the disk-image writers.

Nothing here touches a device or ADB. The writers read from a file-like reader
(``raw``) or a directory (``zip``, ``aff4``) and write to local paths, which
keeps every writer unit-testable in isolation.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

#: Hash algorithms we expose, named the way they are written to sidecars.
HASH_ALGOS = ("md5", "sha1", "sha256")

#: Compression settings shared by the container writers.
COMPRESSIONS = ("none", "fast", "best")

#: Image formats this package can write.
KNOWN_FORMATS = ("raw", "zip", "aff4")

#: Read granularity for the streaming writer.
CHUNK_SIZE = 1024 * 1024

#: ``progress(done_bytes, total_bytes, stage)``; ``total_bytes`` is 0 when the
#: source size is unknown.
Progress = Callable[[int, int, str], None]

_HASHER = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}

_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def new_hash(algo: str):
    """Return a fresh hashlib object for ``algo`` (``md5``/``sha1``/``sha256``)."""
    try:
        return _HASHER[algo]()
    except KeyError:
        raise ValueError(
            f"unsupported hash algorithm {algo!r}; expected one of "
            f"{', '.join(HASH_ALGOS)}"
        ) from None


def hash_file(path: Path, algo: str, chunk_size: int = CHUNK_SIZE) -> str:
    """Hex digest of a file, read in ``chunk_size`` blocks.

    The current acquisition code reads whole files into memory to hash them;
    disk images are far too large for that, so this streams instead.
    """
    digest = new_hash(algo)
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_base_name(name: str) -> str:
    """Validate a user-supplied image base name and return it unchanged.

    Rejects anything that would escape the destination directory or produce an
    invalid file name on Windows, rather than silently rewriting it -- a silently
    rewritten evidence file name is worse than an error.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("image base name is required")
    if _UNSAFE_NAME.search(name):
        raise ValueError(
            f"invalid character in image base name {name!r}; "
            "letters, digits, '.', '-', '_' and spaces are allowed"
        )
    if name in (".", ".."):
        raise ValueError("image base name must not be '.' or '..'")
    return name


def write_sidecar(path: Path, digest: str, algo: str) -> Path:
    """Write ``<path>.<algo>`` beside ``path`` in SHA256SUMS style.

    The line is ``<hex digest>  <file name>`` (two spaces), matching what the
    evidence store already writes elsewhere in the project.
    """
    sidecar = path.with_name(path.name + f".{algo}")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return sidecar


def noop_progress(done: int, total: int, stage: str) -> None:  # pragma: no cover
    """Default no-op progress sink."""
    return None


def emit(progress: Optional[Progress], done: int, total: int, stage: str) -> None:
    """Call ``progress`` if one was supplied."""
    if progress is not None:
        progress(done, total, stage)


@dataclass
class ImageRequest:
    """Everything needed to write one image."""

    dest_dir: Path
    base_name: str
    image_format: str
    #: Where the bytes came from, recorded for provenance (e.g.
    #: ``/dev/block/by-name/userdata`` or a case folder path).
    source_label: str = ""
    hash_algo: str = "sha256"
    compression: str = "fast"
    #: Segment size in bytes; ``0`` writes a single file.
    segment_size: int = 2 * 1024 ** 3
    #: Re-read the finished image and confirm it hashes to the same value.
    verify: bool = True
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.dest_dir = Path(self.dest_dir)
        self.base_name = safe_base_name(self.base_name)
        if self.image_format not in KNOWN_FORMATS:
            raise ValueError(
                f"unknown image format {self.image_format!r}; expected one of "
                f"{', '.join(KNOWN_FORMATS)}"
            )
        if self.hash_algo not in HASH_ALGOS:
            raise ValueError(
                f"unsupported hash algorithm {self.hash_algo!r}; expected one of "
                f"{', '.join(HASH_ALGOS)}"
            )
        if self.compression not in COMPRESSIONS:
            raise ValueError(
                f"unsupported compression {self.compression!r}; expected one of "
                f"{', '.join(COMPRESSIONS)}"
            )
        if not isinstance(self.segment_size, int) or self.segment_size < 0:
            raise ValueError("segment_size must be a non-negative integer")
        if self.image_format == "raw" and 0 < self.segment_size < CHUNK_SIZE:
            # A segment smaller than the read block would still work, but it
            # means the operator typed something wrong; fail loudly.
            raise ValueError(
                f"segment_size must be 0 or at least {CHUNK_SIZE} bytes"
            )


@dataclass
class ImageResult:
    """What a writer produced."""

    image_format: str
    primary: Path
    segments: List[Path]
    total_bytes: int
    hash_algo: str
    #: Digest of the whole image (all segments concatenated in order).
    digest: str
    verified: bool = False
    #: per-segment digests, ``[(path, hex digest), ...]``
    segment_digests: List[tuple] = field(default_factory=list)
    sidecars: List[Path] = field(default_factory=list)
    info_path: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "image_format": self.image_format,
            "primary": str(self.primary),
            "segments": [str(p) for p in self.segments],
            "total_bytes": self.total_bytes,
            "hash_algo": self.hash_algo,
            "digest": self.digest,
            "verified": self.verified,
            "segment_digests": [[str(p), h] for p, h in self.segment_digests],
            "sidecars": [str(p) for p in self.sidecars],
            "info_path": str(self.info_path) if self.info_path else None,
        }


class ImageWriter(ABC):
    """Base class for every image writer.

    ``source_kind`` tells the caller what to hand ``write``:

    * ``"stream"`` -- a file-like object with ``read(size)`` yielding bytes
    * ``"folder"`` -- a :class:`~pathlib.Path` to a directory
    """

    format_id: str = ""
    source_kind: str = ""
    extension: str = ""

    @abstractmethod
    def write(self, source, request: ImageRequest, progress: Optional[Progress] = None) -> ImageResult:
        """Write the image and return an :class:`ImageResult`."""

    def verify(self, result: ImageResult, request: ImageRequest) -> bool:
        """Re-read the finished image and confirm it matches ``result.digest``.

        The base implementation just re-hashes every segment in order, which is
        correct for RAW. Container writers override it.
        """
        digest = new_hash(result.hash_algo)
        for segment in result.segments:
            with open(segment, "rb") as handle:
                for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
                    digest.update(block)
        return digest.hexdigest() == result.digest

    def _verify_if_requested(self, result: ImageResult, request: ImageRequest) -> None:
        if request.verify:
            result.verified = self.verify(result, request)
