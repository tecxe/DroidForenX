"""ZIP image writer.

Packs an acquired case folder into a single ``.zip`` with a ``MANIFEST.json``
inside it. This is the logical counterpart to the RAW writer: RAW captures a
block device, ZIP captures a folder tree (the usual output of an unrooted
acquisition). Only the standard library is involved.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .base import (
    ImageRequest,
    ImageResult,
    ImageWriter,
    Progress,
    emit,
    hash_file,
    write_sidecar,
)

_COMPRESSION = {
    "none": (zipfile.ZIP_STORED, None),
    "fast": (zipfile.ZIP_DEFLATED, 1),
    "best": (zipfile.ZIP_DEFLATED, 9),
}


class ZipImageWriter(ImageWriter):
    """Write a ZIP archive of a folder."""

    format_id = "zip"
    source_kind = "folder"
    extension = "zip"

    def write(self, source, request: ImageRequest, progress: Optional[Progress] = None) -> ImageResult:
        source = Path(source)
        if not source.is_dir():
            raise ValueError(f"zip images require a folder source; {source} is not a directory")

        request.dest_dir.mkdir(parents=True, exist_ok=True)
        dest = request.dest_dir / f"{request.base_name}.{self.extension}"
        method, level = _COMPRESSION[request.compression]

        files = sorted(p for p in source.rglob("*") if p.is_file())
        total_size = sum(p.stat().st_size for p in files)

        inventory = []
        written = 0
        with zipfile.ZipFile(dest, "w", compression=method, compresslevel=level, allowZip64=True) as archive:
            for path in files:
                arcname = path.relative_to(source).as_posix()
                archive.write(path, arcname)
                size = path.stat().st_size
                inventory.append({
                    "path": arcname,
                    "size": size,
                    request.hash_algo: hash_file(path, request.hash_algo),
                })
                written += size
                emit(progress, written, total_size, f"packing {arcname}")
            archive.writestr("MANIFEST.json", self._manifest(request, source, inventory))

        digest = hash_file(dest, request.hash_algo)
        result = ImageResult(
            image_format=self.format_id,
            primary=dest,
            segments=[dest],
            total_bytes=dest.stat().st_size,
            hash_algo=request.hash_algo,
            digest=digest,
            segment_digests=[(dest, digest)],
        )
        result.sidecars.append(write_sidecar(dest, digest, request.hash_algo))
        self._verify_if_requested(result, request)
        emit(progress, total_size, total_size, "done")
        return result

    def verify(self, result: ImageResult, request: ImageRequest) -> bool:
        """A ZIP verifies if every member's CRC checks out and it hashes right."""
        try:
            with zipfile.ZipFile(result.primary) as archive:
                if archive.testzip() is not None:
                    return False
        except (zipfile.BadZipFile, OSError):
            return False
        return hash_file(result.primary, result.hash_algo) == result.digest

    @staticmethod
    def _manifest(request: ImageRequest, source: Path, inventory: list) -> str:
        document = {
            "tool": "DroidForenX",
            "format": "zip",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": request.source_label or str(source),
            "hash_algo": request.hash_algo,
            "compression": request.compression,
            "file_count": len(inventory),
            "files": inventory,
            "metadata": request.metadata,
        }
        return json.dumps(document, indent=2, sort_keys=True)
