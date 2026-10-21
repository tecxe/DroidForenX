"""Streamed ZIP writer for a logical device source.

The ``zip`` writer packs a local folder that already exists on the examiner's
disk. That cannot help an unrooted handset: the only thing ADB will give us is
a live tar stream of ``/sdcard``. This writer consumes that tar as it arrives
and repacks every regular file into one browsable ``.zip`` without ever staging
the tree on disk first, which is what makes a compressed internal-storage
acquisition possible at all.

What it is and is not:

* It is a *logical* archive of the files the device exposed through the tar
  stream, with a per-file hash inventory inside ``MANIFEST.json``.
* It is not a byte-for-byte image of a block device. There is no partition
  table, no unallocated space, no deleted data. The RAW writer still owns that
  job wherever the device allows block reads.

Nothing here needs a device, so the whole writer is testable with a tar built
in memory.
"""
from __future__ import annotations

import json
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

from .base import (
    ImageRequest,
    ImageResult,
    ImageWriter,
    Progress,
    emit,
    hash_file,
    new_hash,
    write_sidecar,
)

_COMPRESSION = {
    "none": (zipfile.ZIP_STORED, None),
    "fast": (zipfile.ZIP_DEFLATED, 1),
    "best": (zipfile.ZIP_DEFLATED, 9),
}

#: Read size for copying one tar member into the archive.
COPY_CHUNK = 1024 * 1024


class ZipStreamImageWriter(ImageWriter):
    """Repack a tar stream into a compressed ZIP archive."""

    format_id = "zipstream"
    source_kind = "stream"
    #: Only the /sdcard logical tar can be repacked; a raw partition stream is
    #: not a tar and must stay RAW.
    stream_kind = "logical"
    extension = "zip"

    def write(self, source, request: ImageRequest, progress: Optional[Progress] = None) -> ImageResult:
        request.dest_dir.mkdir(parents=True, exist_ok=True)
        dest = request.dest_dir / f"{request.base_name}.{self.extension}"
        if dest.exists():
            raise ValueError(
                f"{dest.name} already exists in {request.dest_dir}; choose a "
                "different base name so the existing image is not overwritten")

        method, level = _COMPRESSION[request.compression]
        inventory: list = []
        skipped: list = []
        used_names: set = set()
        total_read = 0

        try:
            with zipfile.ZipFile(dest, "w", compression=method, compresslevel=level,
                                 allowZip64=True) as archive:
                with tarfile.open(fileobj=source, mode="r|") as tar:
                    for member in tar:
                        name = self._safe_name(member.name)
                        if not name:
                            continue
                        if member.isdir():
                            self._add_directory(archive, name, member, used_names)
                            continue
                        if not member.isfile():
                            skipped.append({
                                "path": name,
                                "reason": "symlink" if member.issym() else
                                          "hardlink" if member.islink() else
                                          "special file",
                                "target": member.linkname or "",
                            })
                            continue

                        streaming = tar.extractfile(member)
                        if streaming is None:
                            skipped.append({"path": name, "reason": "unreadable"})
                            continue

                        member_name = self._unique(name, used_names)
                        digest = new_hash(request.hash_algo)
                        info = zipfile.ZipInfo(member_name, date_time=self._date_time(member))
                        info.compress_type = method
                        if level is not None:
                            info._compresslevel = level
                        info.file_size = max(0, int(member.size or 0))
                        written = 0
                        with archive.open(info, "w", force_zip64=True) as outgoing:
                            for block in iter(lambda: streaming.read(COPY_CHUNK), b""):
                                outgoing.write(block)
                                digest.update(block)
                                written += len(block)
                                total_read += len(block)
                                emit(progress, total_read, 0, f"packing {member_name}")
                        inventory.append({
                            "path": member_name,
                            "size": written,
                            request.hash_algo: digest.hexdigest(),
                        })

                archive.writestr(
                    self._manifest_name(used_names),
                    self._manifest(request, inventory, skipped),
                )
        except BaseException:
            # A half-written container is not evidence.
            dest.unlink(missing_ok=True)
            raise

        if not inventory and not skipped:
            dest.unlink(missing_ok=True)
            raise ValueError("no data was read from the source; image not written")

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
        emit(progress, total_read, total_read, "done")
        return result

    def verify(self, result: ImageResult, request: ImageRequest) -> bool:
        """A streamed ZIP verifies if its CRCs check out and it hashes right."""
        try:
            with zipfile.ZipFile(result.primary) as archive:
                if archive.testzip() is not None:
                    return False
        except (zipfile.BadZipFile, OSError):
            return False
        return hash_file(result.primary, result.hash_algo) == result.digest

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _safe_name(name: str) -> str:
        """Normalise a tar member name into a zip entry name."""
        parts = [part for part in PurePosixPath(name.replace("\\", "/")).parts
                 if part not in ("", ".", "/")]
        if not parts:
            return ""
        if any(part == ".." for part in parts):
            # Never let a member escape the archive root.
            parts = ["_unsafe"] + [p for p in parts if p != ".."]
        return "/".join(parts)

    @staticmethod
    def _date_time(member: tarfile.TarInfo) -> tuple:
        try:
            value = time.localtime(member.mtime)
            year = max(1980, value.tm_year)
            return (year, value.tm_mon, value.tm_mday,
                    value.tm_hour, value.tm_min, value.tm_sec)
        except (OverflowError, OSError, ValueError):
            return (1980, 1, 1, 0, 0, 0)

    @staticmethod
    def _add_directory(archive: zipfile.ZipFile, name: str, member: tarfile.TarInfo,
                       used_names: set) -> None:
        entry = name.rstrip("/") + "/"
        if entry in used_names:
            return
        used_names.add(entry)
        info = zipfile.ZipInfo(entry, date_time=ZipStreamImageWriter._date_time(member))
        info.external_attr = (0o40755 << 16) | 0x10
        archive.writestr(info, b"")

    @staticmethod
    def _unique(name: str, used_names: set) -> str:
        if name not in used_names:
            used_names.add(name)
            return name
        stem, dot, suffix = name.rpartition(".")
        base = stem if dot else name
        extension = f".{suffix}" if dot else ""
        counter = 2
        while f"{base} ({counter}){extension}" in used_names:
            counter += 1
        unique = f"{base} ({counter}){extension}"
        used_names.add(unique)
        return unique

    @staticmethod
    def _manifest_name(used_names: set) -> str:
        name = "MANIFEST.json"
        counter = 2
        while name in used_names:
            name = f"MANIFEST ({counter}).json"
            counter += 1
        used_names.add(name)
        return name

    @staticmethod
    def _manifest(request: ImageRequest, inventory: list, skipped: list) -> str:
        document = {
            "tool": "DroidForenX",
            "format": "zipstream",
            "container": "zip",
            "source_kind": "logical",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": request.source_label,
            "hash_algo": request.hash_algo,
            "compression": request.compression,
            "note": ("Logical archive repacked from the device tar stream. "
                     "Contains the files exposed through /sdcard; no block-level "
                     "or deleted data."),
            "file_count": len(inventory),
            "total_bytes": sum(entry.get("size", 0) for entry in inventory),
            "files": inventory,
            "skipped": skipped,
            "metadata": request.metadata,
        }
        return json.dumps(document, indent=2, sort_keys=True)
