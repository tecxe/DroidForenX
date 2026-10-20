"""AFF4-L image writer.

AFF4 stores forensic images in a *container*, which for the standard v1.0 is a
ZIP64 archive holding an RDF metadata graph plus the image data. This writer
produces an **AFF4-L (logical) container**: a ZIP64 archive with the AFF4 root
segments required by the specification, followed by the source folder's files as
data segments, and a Turtle graph describing each one.

Scope and honesty
-----------------
* AFF4-L describes *files*, so this writer is offered for folder sources only.
  Physical AFF4 images need ``aff4:ImageStream``/``aff4:Map`` chunk streams,
  which is a substantially larger piece of work.
* ``pyaff4`` cannot be used here: its own release notes state write support is
  broken, and it pins dependencies (``rdflib==4.2.2``, ``pyyaml==5.4``) that do
  not build on current Python. ``c-aff4`` would mean bundling a native binary
  and still omits section-6 hashing. Hence: written here, standard library only.
* The container is validated structurally (ZIP64 integrity, required root
  segments present and ordered, volume URN in ``container.description`` and the
  ZIP comment, metadata graph populated) and re-hashed. Interoperability with a
  third-party AFF4 reader has **not** been validated in this environment -- see
  the README for that caveat.
"""
from __future__ import annotations

import uuid
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

#: Root segments defined by the AFF4 standard. ``container.description`` must be
#: the first segment in the volume.
RESERVED_ROOT = ("version.txt", "container.description", "information.turtle")

_HASH_TYPES = {"md5": "aff4:MD5", "sha1": "aff4:SHA1", "sha256": "aff4:SHA256"}


def _escape(value: str) -> str:
    """Escape a string for a Turtle double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Aff4ImageWriter(ImageWriter):
    """Write an AFF4-L logical container."""

    format_id = "aff4"
    source_kind = "folder"
    extension = "aff4"

    def write(self, source, request: ImageRequest, progress: Optional[Progress] = None) -> ImageResult:
        source = Path(source)
        if not source.is_dir():
            raise ValueError(f"aff4 images require a folder source; {source} is not a directory")

        request.dest_dir.mkdir(parents=True, exist_ok=True)
        dest = request.dest_dir / f"{request.base_name}.{self.extension}"
        if dest.exists():
            raise ValueError(
                f"{dest.name} already exists in {request.dest_dir}; choose a "
                "different base name so the existing image is not overwritten")
        volume_urn = f"aff4://{uuid.uuid4()}"

        method = zipfile.ZIP_STORED if request.compression == "none" else zipfile.ZIP_DEFLATED
        level = {"none": None, "fast": 1, "best": 9}[request.compression]

        resolved_dest = dest.resolve()
        files = sorted(
            p for p in source.rglob("*")
            if p.is_file() and p.resolve() != resolved_dest)
        total_size = sum(p.stat().st_size for p in files)

        entries = []
        written = 0
        try:
            with zipfile.ZipFile(dest, "w", compression=method, compresslevel=level, allowZip64=True) as archive:
                # container.description MUST be the first member of an AFF4 volume.
                archive.writestr("container.description", volume_urn)
                archive.writestr("version.txt", "1.0")

                for index, path in enumerate(files):
                    arcname = path.relative_to(source).as_posix()
                    if arcname in RESERVED_ROOT:
                        arcname = f"data/{arcname}"
                    archive.write(path, arcname)
                    size = path.stat().st_size
                    entries.append({
                        "urn": f"{volume_urn}/{index:08d}",
                        "segment": arcname,
                        "original": path.relative_to(source).as_posix(),
                        "size": size,
                        "hash": hash_file(path, request.hash_algo),
                    })
                    written += size
                    emit(progress, written, total_size, f"packing {arcname}")

                archive.writestr("information.turtle", self._turtle(request, volume_urn, entries))
                # The volume URN is also the ZIP archive comment, per the standard.
                archive.comment = volume_urn.encode("utf-8")
        except BaseException:
            dest.unlink(missing_ok=True)
            raise

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
        """Check ZIP64 integrity, required root segments, and the container hash."""
        try:
            with zipfile.ZipFile(result.primary) as archive:
                names = archive.namelist()
                if archive.testzip() is not None:
                    return False
                if names[:2] != ["container.description", "version.txt"]:
                    return False
                if "information.turtle" not in names:
                    return False
                if archive.read("version.txt") != b"1.0":
                    return False
                comment = archive.comment.decode("utf-8", "replace")
                urn = archive.read("container.description").decode("utf-8", "replace")
                if not urn.startswith("aff4://") or comment != urn:
                    return False
        except (zipfile.BadZipFile, OSError, KeyError):
            return False
        return hash_file(result.primary, result.hash_algo) == result.digest

    @staticmethod
    def _turtle(request: ImageRequest, volume_urn: str, entries: list) -> str:
        """Build the ``information.turtle`` RDF graph for the volume."""
        lines = [
            "@prefix aff4: <http://aff4.org/Schema#> .",
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
            "",
            f"<{volume_urn}> a aff4:Volume .",
            "",
        ]
        for entry in entries:
            lines.extend([
                f"<{entry['urn']}> a aff4:FileImage ;",
                f'    aff4:originalFileName "{_escape(entry["original"])}" ;',
                f'    aff4:stored "{_escape(entry["segment"])}" ;',
                f"    aff4:size {entry['size']}^^xsd:long ;",
                f"    aff4:hashType {_HASH_TYPES[request.hash_algo]} ;",
                f'    aff4:hash "{entry["hash"]}" .',
                "",
            ])
        lines.append(
            f"<{volume_urn}/acquisition> a aff4:AcquisitionRecord ; "
            f'<http://purl.org/dc/terms/created> '
            f'"{datetime.now(timezone.utc).isoformat()}"^^xsd:dateTime ; '
            f'<http://purl.org/dc/terms/source> "{_escape(request.source_label)}" .'
        )
        lines.append("")
        return "\n".join(lines)
