"""RAW (dd) image writer.

Streams a source into byte-for-byte segments the way ``dd`` plus FTK Imager
would: ``evidence.001``, ``evidence.002`` ... with a single ``evidence.dd`` when
no segmentation is requested. The image digest is computed while writing, so the
source is read exactly once, and the finished image is re-read to confirm it.

The reader is never assumed to be a file -- it only has to expose
``read(size) -> bytes``. Device sources (``adb exec-out``) and local files both
satisfy that, which is what makes this module testable without hardware.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .base import (
    CHUNK_SIZE,
    ImageRequest,
    ImageResult,
    ImageWriter,
    Progress,
    emit,
    new_hash,
)


class RawImageWriter(ImageWriter):
    """Write a RAW image, optionally split into fixed-size segments."""

    format_id = "raw"
    source_kind = "stream"
    extension = "dd"

    def write(self, source, request: ImageRequest, progress: Optional[Progress] = None) -> ImageResult:
        request.dest_dir.mkdir(parents=True, exist_ok=True)

        multi = request.segment_size > 0
        total_size = int(getattr(source, "size", 0) or 0)

        # Never overwrite a previous image: an evidence file that silently
        # replaces another one destroys the chain of custody, so refuse instead
        # of opening it with "wb".
        first_name = f"{request.base_name}.001" if multi else f"{request.base_name}.dd"
        first_path = request.dest_dir / first_name
        if first_path.exists():
            raise ValueError(
                f"{first_name} already exists in {request.dest_dir}; choose a "
                "different base name so the existing image is not overwritten")

        # Fail before moving bytes when the destination cannot hold the image.
        # Only possible for sources whose size is known (a physical partition).
        if total_size:
            free = shutil.disk_usage(request.dest_dir).free
            if free < total_size:
                raise ValueError(
                    f"not enough free space in {request.dest_dir}: the source is "
                    f"{total_size / 1024 ** 3:.1f} GiB, only {free / 1024 ** 3:.1f} GiB free")

        image_digest = new_hash(request.hash_algo)
        segments: list[Path] = []
        segment_digests: list[tuple[Path, str]] = []

        index = 1
        remaining = request.segment_size if multi else 0
        # The first segment is opened lazily, on the first chunk that actually
        # arrives: an empty source should fail without ever creating -- and then
        # deleting -- a zero-byte file that looks like evidence.
        path = handle = seg_digest = None
        total_written = 0
        seg_written = 0

        try:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                offset = 0
                while offset < len(chunk):
                    take = len(chunk) - offset
                    if multi:
                        take = min(remaining, take)
                    if handle is None:
                        path, handle, seg_digest = self._open_segment(request, index, multi)
                    piece = chunk[offset:offset + take]
                    handle.write(piece)
                    image_digest.update(piece)
                    seg_digest.update(piece)
                    total_written += take
                    seg_written += take
                    offset += take

                    if multi:
                        remaining -= take
                        if remaining == 0:
                            handle.close()
                            segments.append(path)
                            segment_digests.append((path, seg_digest.hexdigest()))
                            index += 1
                            remaining = request.segment_size
                            handle = None
                            seg_written = 0
                            emit(progress, total_written, total_size, f"segment {index}")
                emit(progress, total_written, total_size, "writing")
        except BaseException:
            if handle is not None and not handle.closed:
                handle.close()
            self._cleanup(segments, path)
            raise

        if handle is not None and not handle.closed:
            handle.close()

        if total_written == 0:
            self._cleanup(segments, path)
            raise ValueError("no data was read from the source; image not written")

        if path not in segments:
            if seg_written:
                segments.append(path)
                segment_digests.append((path, seg_digest.hexdigest()))
            else:
                # The source ended exactly on a segment boundary, so the segment
                # opened for the next chunk is empty; drop it rather than leaving
                # a phantom zero-byte segment in the image.
                path.unlink(missing_ok=True)

        result = ImageResult(
            image_format=self.format_id,
            primary=segments[0],
            segments=segments,
            total_bytes=total_written,
            hash_algo=request.hash_algo,
            digest=image_digest.hexdigest(),
            segment_digests=segment_digests,
        )
        result.sidecars.append(self._write_hash_manifest(request, segment_digests))
        self._verify_if_requested(result, request)
        result.info_path = self._write_info_file(request, result)
        emit(progress, total_written, total_size or total_written, "done")
        return result

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _open_segment(request: ImageRequest, index: int, multi: bool):
        if multi:
            name = f"{request.base_name}.{index:03d}"
        else:
            name = f"{request.base_name}.dd"
        path = request.dest_dir / name
        return path, open(path, "wb"), new_hash(request.hash_algo)

    @staticmethod
    def _cleanup(segments: list, current) -> None:
        """Remove every partial output so a failed/cancelled run leaves nothing.

        The in-progress segment is not in ``segments`` until it is closed, so it
        must be removed explicitly; a cancelled acquisition used to leave one
        truncated ``.001`` behind that looked like a finished image.
        """
        for segment in segments:
            Path(segment).unlink(missing_ok=True)
        if current is not None:
            Path(current).unlink(missing_ok=True)

    @staticmethod
    def _write_hash_manifest(request: ImageRequest, digests: list) -> Path:
        """``<base>.<algo>`` listing every segment, one ``<hex>  <name>`` per line."""
        manifest = request.dest_dir / f"{request.base_name}.{request.hash_algo}"
        lines = [f"{digest}  {path.name}" for path, digest in digests]
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest

    @staticmethod
    def _write_info_file(request: ImageRequest, result: ImageResult) -> Path:
        """``<base>.imageinfo.txt`` with the image-level digest and provenance."""
        info = request.dest_dir / f"{request.base_name}.imageinfo.txt"
        lines = [
            f"format: {result.image_format}",
            f"base_name: {request.base_name}",
            f"source: {request.source_label}",
            f"hash_algo: {result.hash_algo}",
            f"image_hash: {result.digest}",
            f"verified: {str(result.verified).lower()}",
            f"total_bytes: {result.total_bytes}",
            f"segment_size: {request.segment_size}",
        ]
        for key, value in sorted(request.metadata.items()):
            lines.append(f"{key}: {value}")
        lines.append("segments:")
        for path, digest in result.segment_digests:
            lines.append(f"  {path.name}  {digest}")
        info.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return info
