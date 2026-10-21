"""Disk-image creation for DroidForenX.

The operator picks an output container and the tool writes it, the way FTK
Imager's *Create Disk Image* works. Two containers are supported, both with the
Python standard library alone -- there is no ``libewf`` or other native
dependency to bundle:

    raw        byte-for-byte segments (``.001``, ``.002`` ...) plus hash sidecars
    zip        a compressed archive of an acquired case folder
    zipstream  a compressed archive repacked from the device's logical tar
               stream, so an unrooted handset's storage can still be written
               as one browsable, hashed container

Writers never talk to a device. A *stream* writer (RAW, ZIP-stream) is handed a
file-like reader, a *folder* writer (ZIP) is handed a directory path, and both
write to local paths only. That keeps the whole module testable without ADB; the
device side lives in :mod:`desktop.core.imaging.sources`.
"""
from __future__ import annotations

from pathlib import Path

from . import sources
from .base import (
    COMPRESSIONS,
    HASH_ALGOS,
    KNOWN_FORMATS,
    ImageRequest,
    ImageResult,
    ImageWriter,
    hash_file,
    new_hash,
    write_sidecar,
)
from .raw import RawImageWriter
from .zip import ZipImageWriter
from .zipstream import ZipStreamImageWriter

#: format id -> writer class. Declaration order is the order the UI presents them.
WRITERS: dict[str, type[ImageWriter]] = {
    RawImageWriter.format_id: RawImageWriter,
    ZipImageWriter.format_id: ZipImageWriter,
    ZipStreamImageWriter.format_id: ZipStreamImageWriter,
}

#: Human labels for the format picker.
FORMAT_LABELS = {
    "raw": "Raw (dd) - byte-for-byte segments",
    "zip": "ZIP - archived case folder",
    "zipstream": "ZIP - compressed archive of device files",
}

#: Which source each format accepts, for the UI to grey out the wrong pair.
FORMAT_SOURCE_KIND = {fmt: writer.source_kind for fmt, writer in WRITERS.items()}

#: For streaming formats, which kind of stream they require ("" means any).
FORMAT_STREAM_KIND = {fmt: writer.stream_kind for fmt, writer in WRITERS.items()}


def writer_for(image_format: str) -> ImageWriter:
    """Instantiate the writer for ``image_format`` or raise ``ValueError``."""
    try:
        return WRITERS[image_format]()
    except KeyError:
        raise ValueError(
            f"unknown image format {image_format!r}; expected one of "
            f"{', '.join(sorted(WRITERS))}"
        ) from None


def create_image(source, request: ImageRequest, progress=None) -> ImageResult:
    """Write an image of ``source`` per ``request``.

    ::

        ``source`` is a file-like reader for the stream formats (``raw``,
        ``zipstream``) or a directory path for the folder format (``zip``).
    """
    writer = writer_for(request.image_format)
    if writer.source_kind == "folder":
        source = Path(source)
    return writer.write(source, request, progress=progress)


__all__ = [
    "COMPRESSIONS",
    "FORMAT_LABELS",
    "FORMAT_SOURCE_KIND",
    "FORMAT_STREAM_KIND",
    "HASH_ALGOS",
    "KNOWN_FORMATS",
    "WRITERS",
    "ImageRequest",
    "ImageResult",
    "ImageWriter",
    "RawImageWriter",
    "ZipImageWriter",
    "ZipStreamImageWriter",
    "create_image",
    "hash_file",
    "new_hash",
    "sources",
    "writer_for",
    "write_sidecar",
]
