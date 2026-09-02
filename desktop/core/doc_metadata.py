"""Document Metadata Extraction — PDF XMP, DOCX XML, XLSX properties, revision history."""

import re
import json
import struct
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def extract_pdf_metadata(filepath: str) -> dict:
    """Extract PDF metadata including XMP, creator, producer, dates, pages."""
    meta = {
        "type": "PDF",
        "filename": Path(filepath).name,
        "size": 0,
        "pages": None,
        "title": "",
        "author": "",
        "creator": "",
        "producer": "",
        "created": "",
        "modified": "",
        "subject": "",
        "keywords": "",
        "xmp": {},
        "encryption": False,
    }

    try:
        meta["size"] = Path(filepath).stat().st_size
    except OSError:
        return meta

    try:
        with open(filepath, "rb") as f:
            content = f.read(500000)

        # Basic metadata from PDF trailer
        for pattern, key in [
            (rb"/Title\s*\(([^)]+)\)", "title"),
            (rb"/Author\s*\(([^)]+)\)", "author"),
            (rb"/Creator\s*\(([^)]+)\)", "creator"),
            (rb"/Producer\s*\(([^)]+)\)", "producer"),
            (rb"/Subject\s*\(([^)]+)\)", "subject"),
            (rb"/Keywords\s*\(([^)]+)\)", "keywords"),
        ]:
            m = re.search(pattern, content)
            if m:
                try:
                    meta[key] = m.group(1).decode("latin-1", errors="replace")
                except Exception:
                    pass

        # Page count
        pages_m = re.search(rb"/Type\s*/Page[^s]", content)
        if pages_m:
            meta["pages"] = len(re.findall(rb"/Type\s*/Page[^s]", content))

        # Creation/Modification dates
        for pattern, key in [
            (rb"/CreationDate\s*\(([^)]+)\)", "created"),
            (rb"/ModDate\s*\(([^)]+)\)", "modified"),
        ]:
            m = re.search(pattern, content)
            if m:
                meta[key] = m.group(1).decode("latin-1", errors="replace")

        # Encryption check
        meta["encryption"] = b"/Encrypt" in content

        # XMP metadata (XML metadata stream)
        xmp_start = content.find(b"<x:xmpmeta")
        xmp_end = content.find(b"</x:xmpmeta>")
        if xmp_start >= 0 and xmp_end > xmp_start:
            xmp_raw = content[xmp_start:xmp_end + 13].decode("utf-8", errors="replace")
            try:
                root = ET.fromstring(xmp_raw)
                ns = {
                    "dc": "http://purl.org/dc/elements/1.1/",
                    "xmp": "http://ns.adobe.com/xap/1.0/",
                    "pdf": "http://ns.adobe.com/pdf/1.3/",
                    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
                }
                for tag, prefix in [("title", "dc"), ("creator", "dc"), ("subject", "dc")]:
                    for elem in root.iter(f"{{{ns[prefix]}}}{tag}"):
                        for child in elem:
                            if child.text:
                                meta["xmp"][f"{prefix}:{tag}"] = child.text.strip()
                for tag in ["CreatorTool", "CreateDate", "ModifyDate"]:
                    for elem in root.iter(f"{{{ns['xmp']}}}{tag}"):
                        if elem.text:
                            meta["xmp"][f"xmp:{tag}"] = elem.text.strip()
            except ET.ParseError:
                pass

    except Exception as e:
        logger.debug(f"PDF metadata extraction failed: {e}")

    return meta


def extract_docx_metadata(filepath: str) -> dict:
    """Extract DOCX metadata from XML properties."""
    meta = {
        "type": "DOCX",
        "filename": Path(filepath).name,
        "size": 0,
        "title": "",
        "author": "",
        "lastModifiedBy": "",
        "created": "",
        "modified": "",
        "revision": "",
        "application": "",
        "pages": None,
        "words": None,
        "characters": None,
    }

    try:
        meta["size"] = Path(filepath).stat().st_size
    except OSError:
        return meta

    try:
        import zipfile
        with zipfile.ZipFile(filepath, "r") as z:
            # core.xml
            if "docProps/core.xml" in z.namelist():
                core = z.read("docProps/core.xml").decode("utf-8", errors="replace")
                root = ET.fromstring(core)
                ns = {"cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                      "dc": "http://purl.org/dc/elements/1.1/"}
                for tag in ["title", "subject", "creator", "description"]:
                    for elem in root.iter(f"{{{ns['dc']}}}{tag}"):
                        if elem.text:
                            meta[tag] = elem.text.strip()
                for tag in ["lastModifiedBy", "revision", "created", "modified"]:
                    for elem in root.iter(f"{{{ns['cp']}}}{tag}"):
                        if elem.text:
                            meta[tag] = elem.text.strip()

            # app.xml
            if "docProps/app.xml" in z.namelist():
                app = z.read("docProps/app.xml").decode("utf-8", errors="replace")
                root = ET.fromstring(app)
                ns = {"": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"}
                for tag in ["Application", "Company", "Template", "TotalTime",
                             "Pages", "Words", "Characters", "Lines", "Paragraphs"]:
                    for elem in root.iter(f"{{{ns['']}}}{tag}"):
                        if elem.text:
                            key = tag.lower()
                            meta[key] = elem.text.strip()

    except Exception as e:
        logger.debug(f"DOCX metadata extraction failed: {e}")

    return meta


def extract_xlsx_metadata(filepath: str) -> dict:
    """Extract XLSX metadata from XML properties."""
    meta = {
        "type": "XLSX",
        "filename": Path(filepath).name,
        "size": 0,
        "title": "",
        "author": "",
        "lastModifiedBy": "",
        "created": "",
        "modified": "",
        "revision": "",
        "application": "",
        "sheets": None,
    }

    try:
        meta["size"] = Path(filepath).stat().st_size
    except OSError:
        return meta

    try:
        import zipfile
        with zipfile.ZipFile(filepath, "r") as z:
            if "docProps/core.xml" in z.namelist():
                core = z.read("docProps/core.xml").decode("utf-8", errors="replace")
                root = ET.fromstring(core)
                ns = {"cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                      "dc": "http://purl.org/dc/elements/1.1/"}
                for tag in ["title", "creator"]:
                    for elem in root.iter(f"{{{ns['dc']}}}{tag}"):
                        if elem.text:
                            meta[tag] = elem.text.strip()
                for tag in ["lastModifiedBy", "revision", "created", "modified"]:
                    for elem in root.iter(f"{{{ns['cp']}}}{tag}"):
                        if elem.text:
                            meta[tag] = elem.text.strip()

            if "docProps/app.xml" in z.namelist():
                app = z.read("docProps/app.xml").decode("utf-8", errors="replace")
                root = ET.fromstring(app)
                ns = {"": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"}
                for tag in ["Application", "Company", "Sheets"]:
                    for elem in root.iter(f"{{{ns['']}}}{tag}"):
                        if elem.text:
                            meta[tag.lower()] = elem.text.strip()

    except Exception as e:
        logger.debug(f"XLSX metadata extraction failed: {e}")

    return meta


def extract_generic_metadata(filepath: str) -> dict:
    """Extract basic metadata for non-office document types."""
    meta = {
        "filename": Path(filepath).name,
        "size": 0,
        "extension": Path(filepath).suffix.lower(),
    }

    try:
        meta["size"] = Path(filepath).stat().st_size
    except OSError:
        return meta

    ext = meta["extension"]

    if ext == ".txt":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                sample = f.read(10000)
            meta["lines"] = sample.count("\n") + 1
            meta["words"] = len(sample.split())
            meta["chars"] = len(sample)
        except Exception:
            pass

    elif ext == ".csv":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            meta["rows"] = len(lines)
            meta["columns"] = len(lines[0].split(",")) if lines else 0
        except Exception:
            pass

    elif ext == ".json":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            meta["top_level_type"] = type(data).__name__
            if isinstance(data, list):
                meta["array_length"] = len(data)
            elif isinstance(data, dict):
                meta["keys"] = list(data.keys())[:20]
        except Exception:
            pass

    return meta


def extract_all_metadata(filepath: str) -> dict:
    """Route to correct extractor based on file extension."""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_pdf_metadata(filepath)
    elif ext == ".docx":
        return extract_docx_metadata(filepath)
    elif ext == ".xlsx":
        return extract_xlsx_metadata(filepath)
    elif ext in (".pptx",):
        try:
            import zipfile
            with zipfile.ZipFile(filepath, "r") as z:
                if "docProps/core.xml" in z.namelist():
                    core = z.read("docProps/core.xml").decode("utf-8", errors="replace")
                    root = ET.fromstring(core)
                    return {
                        "type": "PPTX", "filename": Path(filepath).name,
                        "size": Path(filepath).stat().st_size,
                    }
        except Exception:
            pass
        return {"type": "PPTX", "filename": Path(filepath).name}
    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"):
        return {"type": "Image", "filename": Path(filepath).name,
                "size": Path(filepath).stat().st_size if Path(filepath).exists() else 0}
    elif ext in (".mp4", ".3gp", ".mkv", ".avi"):
        return {"type": "Video", "filename": Path(filepath).name,
                "size": Path(filepath).stat().st_size if Path(filepath).exists() else 0}
    elif ext in (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus"):
        return {"type": "Audio", "filename": Path(filepath).name,
                "size": Path(filepath).stat().st_size if Path(filepath).exists() else 0}
    else:
        return extract_generic_metadata(filepath)
