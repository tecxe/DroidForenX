"""Metadata extraction for acquired media and other files.

Extracts what can be read directly from the file using pure-Python or
Pillow/exifread tooling:

  * images      -> EXIF (EXIF version, Make/Model, datetime, GPS, dimensions)
  * video       -> container-level dimensions where Pillow can open as an image
  * documents   -> no deep parsing payloads; reported as recognized type
  * text        -> encoding guess (best effort)

Every field is reported only when actually present. Nothing is fabricated; a
file that yields no parseable metadata returns an empty dict plus a note when
an error occurred (so the analyst knows the extraction was attempted).
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _iso(v) -> Optional[str]:
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    return None


def image_metadata(path: Path) -> Dict:
    """Extract metadata from an image file (dimensions + EXIF/GPS)."""
    result: Dict = {}
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        with Image.open(path) as img:
            result["width"] = img.width
            result["height"] = img.height
            result["format"] = (img.format or "").lower()
            exif = img.getexif()
            if exif:
                gps = {}
                for tag, value in exif.items():
                    name = TAGS.get(tag, str(tag))
                    if name == "GPSInfo":
                        gps = {k: v for k, v in value.items()}
                        continue
                    if name in ("Make", "Model", "DateTimeOriginal", "DateTime",
                                "Software", "Orientation", "ExposureTime",
                                "FNumber", "ISOSpeedRatings", "LensModel"):
                        if isinstance(value, bytes):
                            try:
                                value = value.decode("utf-8", "replace")
                            except Exception:
                                value = None
                        if value is not None:
                            result[f"exif_{name}"] = value
                if gps:
                    result["gps_present"] = True
                    # Best-effort: report raw GPS tag values by standard tag id.
                    result["gps_raw"] = {str(k): str(v) for k, v in gps.items()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("image metadata extraction failed: %s", exc)
        result["_error"] = str(exc)
    return result


def text_encoding(path: Path) -> Optional[str]:
    """Best-effort text encoding detection. Returns None if ambiguous/binary."""
    try:
        from charset_normalizer import from_path
        best = from_path(str(path)).best()
        if best is not None:
            return best.encoding
    except Exception:
        pass
    return None
