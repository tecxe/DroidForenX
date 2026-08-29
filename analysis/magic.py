"""Pure-Python file signature (magic bytes) detection.

Identifies a file's type by reading the first bytes and matching against known
signatures. No native ``libmagic`` dependency is required, which keeps the
forensic workstation portable. The detector is honest about uncertainty: an
unrecognized file yields ``None``, never a guessed label.

Signatures are ordered from most specific to least specific for a given family
so that e.g. a JPEG is not mistaken for something generic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Signature:
    mime: str
    extension: str
    bytes: bytes                          # signature (prefix bytes)
    description: str = ""
    offset: int = 0                        # bytes to skip before matching


# (mime, ext, signature_bytes, [+offset, ]description)
_SIGNATURES: List[Signature] = [
    # Images
    Signature("image/jpeg", ".jpg", b"\xff\xd8\xff", "JPEG image (start of image)"),
    Signature("image/png", ".png", b"\x89PNG\r\n\x1a\n", "PNG image"),
    Signature("image/gif", ".gif", b"GIF87a", "GIF image (87a)"),
    Signature("image/gif", ".gif", b"GIF89a", "GIF image (89a)"),
    Signature("image/bmp", ".bmp", b"BM", "Windows bitmap"),
    Signature("image/tiff", ".tif", b"II*\x00", "TIFF image (little-endian)"),
    Signature("image/tiff", ".tif", b"MM\x00*", "TIFF image (big-endian)"),
    Signature("image/webp", ".webp", b"WEBP", "WebP image", offset=8),
    Signature("image/heic", ".heic", b"\x00\x00\x00\x18ftypheic", "HEIC image"),
    Signature("image/heif", ".heif", b"\x00\x00\x00\x18ftypheif", "HEIF image"),
    Signature("image/avif", ".avif", b"\x00\x00\x00\x18ftypavif", "AVIF image"),
    # Archives / containers
    Signature("application/zip", ".zip", b"PK\x03\x04", "ZIP archive"),
    Signature("application/x-7z-compressed", ".7z", b"7z\xbc\xaf\x27\x1c", "7-Zip archive"),
    Signature("application/x-rar-compressed", ".rar", b"Rar!\x1a\x07", "RAR archive"),
    Signature("application/x-tar", ".tar", b"ustar\x00", "POSIX tar archive", offset=257),
    Signature("application/gzip", ".gz", b"\x1f\x8b\x08", "gzip compressed"),
    Signature("application/x-bzip2", ".bz2", b"BZh", "bzip2 compressed"),
    Signature("application/x-xz", ".xz", b"\xfd7zXZ\x00", "xz compressed"),
    Signature("application/vnd.android.package-archive", ".apk", b"PK\x03\x04", "Android APK (ZIP)"),
    # Documents
    Signature("application/pdf", ".pdf", b"%PDF", "PDF document"),
    Signature("application/msword", ".doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE compound document"),
    Signature("application/vnd.ms-excel", ".xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE compound document"),
    # Audio / video
    Signature("audio/mpeg", ".mp3", b"ID3", "MP3 audio (ID3 tag)"),
    Signature("audio/mpeg", ".mp3", b"\xff\xfb", "MP3 audio (frame sync)"),
    Signature("video/mp4", ".mp4", b"\x00\x00\x00\x18ftyp", "MP4 video"),
    Signature("audio/mp4", ".m4a", b"\x00\x00\x00\x18ftypM4A", "MP4 audio (M4A)"),
    Signature("video/x-matroska", ".mkv", b"\x1aE\xdf\xa3", "Matroska video"),
    Signature("video/webm", ".webm", b"\x1aE\xdf\xa3", "WebM (Matroska) video"),
    Signature("video/quicktime", ".mov", b"ftyp", "QuickTime MOV", offset=4),
    # Messaging / databases
    Signature("application/x-sqlite3", ".db", b"SQLite format 3\x00", "SQLite 3 database"),
    Signature("application/octet-stream", ".vcf", b"BEGIN:VCARD", "vCard"),
    # Executables / scripts
    Signature("application/x-msdownload", ".exe", b"MZ", "PE/Windows executable"),
    Signature("application/x-executable", ".elf", b"\x7fELF", "ELF executable"),
]


def signature_for(data: bytes) -> Optional[Signature]:
    """Find the first matching signature for a byte prefix (already read)."""
    for sig in _SIGNATURES:
        if len(data) >= sig.offset + len(sig.bytes) and \
           data[sig.offset:sig.offset + len(sig.bytes)] == sig.bytes:
            return sig
    return None


def detect_type(path: Path, read_size: int = 512) -> Optional[Signature]:
    """Detect a file's type from its leading bytes."""
    path = Path(path)
    if not path.exists():
        return None
    with path.open("rb") as f:
        head = f.read(read_size)
    return signature_for(head)
