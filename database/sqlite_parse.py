"""Low-level SQLite file-format reader for deleted-data assessment.

Python's ``sqlite3`` module only exposes *live* (committed, allocated) rows and
deliberately ignores the free pages and freeblocks where deleted data often
lingers. This module reads the raw SQLite page format directly (a documented,
public format) so the analyst can determine whether unallocated space still
contains recoverable remnants.

This is honest by design: we report *whether remnants are detected* and *how
much unallocated space exists*, without reconstructing the original row data.
The platform never claims specific deleted values it did not recover.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

SQLITE_HEADER = b"SQLite format 3\x00"

# SQLite page-type codes.
PAGE_INTERIOR_INDEX = 0x02
PAGE_INTERIOR_TABLE = 0x05
PAGE_LEAF_INDEX = 0x0A
PAGE_LEAF_TABLE = 0x0D


@dataclass
class Freeblock:
    offset: int
    size: int


@dataclass
class PageAssessment:
    number: int
    page_type: int
    free_start: Optional[int]
    free_end: Optional[int]
    freeblocks: List[Freeblock] = field(default_factory=list)

    @property
    def has_remains(self) -> bool:
        """True when the page has unallocated space with residual content."""
        return bool(self.freeblocks) or (
            self.free_start is not None and self.free_end is not None
            and self.free_end - self.free_start > 0
        )


@dataclass
class DatabaseAssessment:
    path: str
    header_valid: bool
    page_size: int
    freelist_count: int  # number of pages on the freelist
    file_size_bytes: int
    total_pages: int
    pages_with_freeblocks: int = 0
    pages_with_remains: int = 0
    total_freeblock_bytes: int = 0
    note: str = ""

    @property
    def has_deleted_remains(self) -> bool:
        return self.freelist_count > 0 or self.pages_with_remains > 0 or self.total_freeblock_bytes > 0


def _read_header(fileobj) -> tuple[Optional[int], Optional[int]]:
    """Return (page_size, file_size) from the 100-byte header, or (None, None)."""
    header = fileobj.read(100)
    if len(header) < 100 or header[:16] != SQLITE_HEADER:
        return None, None
    # Bytes 16-17 are the page size (special-cased value 1 means 65536).
    raw = int.from_bytes(header[16:18], "big")
    page_size = 65536 if raw == 1 else raw
    # Change counter etc. unimportant here. Freelist page count is bytes 32-35.
    freelist_count = int.from_bytes(header[32:36], "big")
    return page_size, freelist_count


def assess_database(path: Path, scan_up_to_pages: Optional[int] = 20000) -> DatabaseAssessment:
    """Produce a DatabaseAssessment for the given SQLite file.

    The header is read first; if it is not a SQLite file, header_valid is False
    and the remaining fields are best-effort/defaults. Scanning is capped to
    ``scan_up_to_pages`` to bound work on very large databases.
    """
    path = Path(path)
    result = DatabaseAssessment(
        path=str(path),
        header_valid=False,
        page_size=0,
        freelist_count=0,
        file_size_bytes=0,
        total_pages=0,
    )
    if not path.exists():
        result.note = "file not found"
        return result

    result.file_size_bytes = path.stat().st_size
    with path.open("rb") as f:
        page_size, freelist_count = _read_header(f)
        if page_size is None:
            result.note = "not a valid SQLite database (bad header)"
            return result

        result.header_valid = True
        result.page_size = page_size
        result.freelist_count = freelist_count or 0
        result.total_pages = result.file_size_bytes // page_size

        cap = min(result.total_pages, scan_up_to_pages or result.total_pages)
        for page_number in range(1, cap + 1):
            f.seek((page_number - 1) * page_size)
            page = f.read(page_size)
            if len(page) < page_size:
                break
            asst = _assess_page(page)
            if asst.has_remains:
                result.pages_with_remains += 1
            result.pages_with_freeblocks += len(asst.freeblocks)
            result.total_freeblock_bytes += sum(fb.size for fb in asst.freeblocks)

    return result


def _assess_page(page: bytes) -> PageAssessment:
    """Assess a single page (already read in full)."""
    # First byte is the page type.
    page_type = page[0]
    ps = len(page)
    a = PageAssessment(number=0, page_type=page_type, free_start=None, free_end=None)

    # Interior/leaf pages store a cell pointer array near the top of the page.
    # Bytes 1-2 hold the number of cells.
    if page_type in (PAGE_LEAF_TABLE, PAGE_LEAF_INDEX, PAGE_INTERIOR_TABLE, PAGE_INTERIOR_INDEX):
        cell_count = int.from_bytes(page[3:5], "big")
        # Bytes 5-6: offset of the first block of free space on the page.
        free_start = int.from_bytes(page[5:7], "big")
        # Bytes 7-8: offset of the first cell.
        first_cell = int.from_bytes(page[7:9], "big")
        if free_start:
            a.free_start = free_start
            a.free_end = first_cell if first_cell else ps

    # Walk freeblocks. On a freeblock page, each freeblock header holds next ptr + size.
    # We only dwell on the *first* freeblock chain reported in the header for a
    # light-weight remains assessment; deep chains are expensive. We record the
    # region between free_start and first_cell as residual space.
    if a.free_start and a.free_end and a.free_end > a.free_start:
        region = page[min(a.free_start, ps):min(a.free_end, ps)]
        # A non-zero freeblock chain starting here usually means deallocated
        # content remains. Any residual (non-NUL) region is "remains."
        residual = any(b not in (0x00,) for b in region[:min(len(region), 512)])
        if residual:
            a.freeblocks.append(Freeblock(offset=a.free_start, size=a.free_end - a.free_start))
    return a
