"""Tests for file type detection and metadata analysis."""

import io
import tempfile
from pathlib import Path

from django.test import TestCase

from cases.models import Case, Investigator
from evidence.services import register_artifact, register_evidence

from .magic import detect_type, signature_for
from .metadata import image_metadata
from .service import NoFileError, analyze_artifact, analyze_bytes_prefix


def make_tmp(name, data: bytes) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / name
    p.write_bytes(data)
    return p


def tiny_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


def make_artifact(storage: Path):
    inv = Investigator.objects.create(name="Ada")
    case = Case.objects.create(case_number="CASE-AN-1", lead_investigator=inv)
    ev = register_evidence(case, title="analysis container")
    return register_artifact(ev, name=storage.name, source="test", storage_path=str(storage))


class MagicDetectTest(TestCase):
    def test_png(self):
        data = tiny_png()
        self.assertEqual(signature_for(data).mime, "image/png")

    def test_jpeg(self):
        self.assertEqual(signature_for(b"\xff\xd8\xff\xe0rest").mime, "image/jpeg")

    def test_pdf(self):
        self.assertEqual(signature_for(b"%PDF-1.4\n").mime, "application/pdf")

    def test_sqlite(self):
        self.assertEqual(signature_for(b"SQLite format 3\x00").mime, "application/x-sqlite3")

    def test_zip_apk(self):
        zip_sig = b"PK\x03\x04...."
        self.assertEqual(signature_for(zip_sig).mime, "application/zip")
        self.assertEqual(signature_for(zip_sig).extension, ".zip")

    def test_unknown_returns_none(self):
        self.assertIsNone(signature_for(b"\x00\x01\x02\x03\x04"))
        self.assertIsNone(signature_for(b""))

    def test_detect_type_missing_file(self):
        self.assertIsNone(detect_type(Path(tempfile.gettempdir()) / "nope-xyz.bin"))

    def test_analyze_bytes_prefix(self):
        r = analyze_bytes_prefix(b"\x89PNG\r\n\x1a\nmore")
        self.assertEqual(r["mime_type"], "image/png")


class MetadataTest(TestCase):
    def test_png_dimensions(self):
        p = make_tmp("pic.png", tiny_png())
        meta = image_metadata(p)
        self.assertEqual(meta.get("width"), 4)
        self.assertEqual(meta.get("height"), 2)


class AnalyzeArtifactTest(TestCase):
    def test_png_analysis_sets_mime_and_dimensions(self):
        p = make_tmp("pic.png", tiny_png())
        art = make_artifact(p)
        result = analyze_artifact(art)
        art.refresh_from_db()
        self.assertEqual(art.mime_type, "image/png")
        self.assertEqual(art.extension, ".png")
        self.assertEqual(art.metadata["image"]["width"], 4)
        self.assertEqual(result["mime_type"], "image/png")

    def test_analyze_pdf_no_metadata_error(self):
        p = make_tmp("doc.pdf", b"%PDF-1.4\n% some pdf bytes")
        art = make_artifact(p)
        result = analyze_artifact(art)
        self.assertEqual(art.mime_type, "application/pdf")
        self.assertNotIn("_error", result)

    def test_no_file_raises(self):
        inv = Investigator.objects.create(name="Ada")
        case = Case.objects.create(case_number="CASE-AN2", lead_investigator=inv)
        ev = register_evidence(case, title="c")
        art = register_artifact(ev, name="nf", source="s", storage_path="")
        with self.assertRaises(NoFileError):
            analyze_artifact(art)
