"""Signature-based file carving.

Carves recoverable files out of a forensic image or raw unallocated space by
locating known header signatures and, where a reliable end-marker exists,
recovering the complete file between header and footer.

This layer only ever writes files that it actually located a signature for. A
region with no recognizable signature yields nothing — we never invent a carve.

Supported families (header -> footer):
  * JPEG          FFD8FF          -> FFD9
  * PNG           89504E47        -> IEND chunk (AE426082 trailer)
  * GIF           GIF87a/GIF89a   -> trailer 3B
  * PDF           %PDF            -> %%EOF (best effort, may trail)
  * ZIP/APK/OOXML PK.. -> EOCD (best effort)
  * SQLite        SQLite format 3
  * BMP           424D
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

CARVABLE_SIGNATURES = {
    "JPEG": {"header": b"\xff\xd8\xff", "footer": b"\xff\xd9", "ext": ".jpg"},
    "PNG": {"header": b"\x89PNG\r\n\x1a\n", "footer": b"IEND\xaeB`\x82", "ext": ".png"},
    "GIF": {"header": b"GIF8", "footer": b"\x3b", "ext": ".gif"},
    "PDF": {"header": b"%PDF", "footer": b"%%EOF", "ext": ".pdf"},
    "ZIP": {"header": b"PK\x03\x04", "footer": b"PK\x05\x06", "ext": ".zip"},
    "SQLite": {"header": b"SQLite format 3\x00", "footer": None, "ext": ".db"},
    "BMP": {"header": b"BM", "footer": None, "ext": ".bmp"},
}


@dataclass
class CarvedFile:
    kind: str
    extension: str
    start: int
    end: int
    size: int
    status: str  # "recovered" if footer found, "truncated" otherwise
    output_name: str = ""

    @property
    def recovered_fully(self) -> bool:
        return self.status == "recovered"


def _find(data: bytes, needle: bytes, start: int) -> int:
    return data.find(needle, start)


def carve_from_memory(data: bytes, min_size: int = 512, max_sequential: Optional[int] = None) -> List[CarvedFile]:
    """Carve recoverable files from an in-memory blob.

    Scans for each header signature, then attempts to find that file type's
    footer. Files smaller than ``min_size`` are skipped as likely false
    positives. Returns CarvedFile records describing offsets (not content).
    """
    results: List[CarvedFile] = []
    length = len(data)

    # Scan once, tracking the earliest header per kind to avoid overlapping.
    for kind, spec in CARVABLE_SIGNATURES.items():
        header = spec["header"]
        footer = spec["footer"]
        ext = spec["ext"]
        pos = 0
        while True:
            start = _find(data, header, pos)
            if start == -1:
                break
            # Guard: a header at the very end with nothing after is not viable.
            end = length
            status = "truncated"
            if footer is not None:
                fi = _find(data, footer, start + len(header))
                if fi != -1:
                    end = fi + len(footer)
                    status = "recovered"
                else:
                    # No footer found; keep header open through the end but flag
                    # it as truncated so the analyst knows it may be partial.
                    end = length
                    status = "truncated"
            size = end - start
            if size >= min_size:
                results.append(CarvedFile(
                    kind=kind, extension=ext, start=start, end=end,
                    size=size, status=status,
                ))
            pos = end  # advance past this candidate to avoid re-matching inside
    results.sort(key=lambda c: c.start)
    return results


def carve_file(path: Path, out_dir: Path, min_size: int = 512, chunk_size: int = 1 << 20) -> List[CarvedFile]:
    """Carve a file/blob on disk, writing recovered blocks into ``out_dir``.

    Streams the source in chunks, collecting header/footer hits, then writes
    each recovered region to disk. Returns the CarvedFile records (which include
    a matching output filename via ``output_name``).
    """
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return []

    with path.open("rb") as f:
        blob = f.read()

    carved = carve_from_memory(blob, min_size=min_size)
    written = []
    for i, c in enumerate(carved):
        content = blob[c.start:c.end]
        fname = f"carve_{c.kind.lower()}_{i:04d}{c.extension}"
        (out_dir / fname).write_bytes(content)
        c.output_name = fname
        written.append(c)
    return written
