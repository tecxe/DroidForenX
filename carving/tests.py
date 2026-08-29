"""Tests for file carving and deleted-data recovery statuses."""

import tempfile
from pathlib import Path

from django.test import TestCase

from cases.models import Case, Investigator
from evidence.models import AcquisitionStatus, EvidenceCategory, EvidenceClassification
from evidence.services import register_artifact, register_evidence

from .engine import carve_file, carve_from_memory
from .service import NoFileError, carve_artifact


def tiny_image_blob() -> bytes:
    """A blob containing one complete JPEG and one truncated PNG."""
    jpeg = b"\xff\xd8\xff\xe0" + (b"\x00\x11\x22" * 500) + b"\x00\xff\xd9"
    png_head = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
    png_truncated = png_head + (b"\xab\xcd" * 300)  # no IEND trailer -> truncated
    return jpeg + b"\x00\x00\x00" + png_truncated + b"\x00\x00\x00\x00"


def make_artifact(path: Path):
    inv = Investigator.objects.create(name="Ada")
    case = Case.objects.create(case_number="CASE-CV-1", lead_investigator=inv)
    ev = register_evidence(case, title="carve container")
    return register_artifact(ev, name=path.name, source="test", storage_path=str(path))


class CarveEngineTest(TestCase):
    def test_carves_jpeg_fully_and_png_truncated(self):
        blob = tiny_image_blob()
        results = carve_from_memory(blob, min_size=100)
        kinds = {r.kind for r in results}
        self.assertIn("JPEG", kinds)
        self.assertIn("PNG", kinds)
        jpeg = [r for r in results if r.kind == "JPEG"][0]
        png = [r for r in results if r.kind == "PNG"][0]
        self.assertTrue(jpeg.recovered_fully)
        self.assertFalse(png.recovered_fully)
        self.assertEqual(jpeg.extension, ".jpg")

    def test_writes_files_to_disk(self):
        blob = tiny_image_blob()
        src = Path(tempfile.mkdtemp()) / "img.bin"
        src.write_bytes(blob)
        out = Path(tempfile.mkdtemp()) / "out"
        carved = carve_file(src, out, min_size=100)
        self.assertTrue(carved)
        written = list(out.glob("carve_*"))
        self.assertEqual(len(written), len(carved))

    def test_no_signature_nothing_carved(self):
        blob = b"\xde\xad\xbe\xef" * 1000
        self.assertEqual(carve_from_memory(blob, min_size=100), [])

    def test_missing_file_empty(self):
        out = Path(tempfile.mkdtemp()) / "o"
        self.assertEqual(carve_file(Path(tempfile.mkdtemp()) / "nope.bin", out), [])


class CarveServiceTest(TestCase):
    def test_carve_artifact_registers_statuses(self):
        src = Path(tempfile.mkdtemp()) / "img.bin"
        src.write_bytes(tiny_image_blob())
        art = make_artifact(src)
        summary = carve_artifact(art, min_size=100)
        self.assertGreater(summary["carved_total"], 0)
        self.assertGreaterEqual(summary["recovered_fully"], 1)
        self.assertGreaterEqual(summary["truncated"], 1)

        child = art.evidence.artifacts.exclude(pk=art.pk).get(name__startswith="carve_jpeg")
        self.assertEqual(child.status, AcquisitionStatus.DELETED_RECOVERABLE)
        self.assertEqual(child.classification, EvidenceClassification.CARVED)
        self.assertEqual(child.category, EvidenceCategory.CARVED_FILE)
        self.assertEqual(child.recovery_status, "RECOVERED")

        png_child = art.evidence.artifacts.exclude(pk=art.pk).get(name__startswith="carve_png")
        self.assertEqual(png_child.recovery_status, "PARTIALLY RECOVERED")

    def test_no_file_raises(self):
        inv = Investigator.objects.create(name="Ada")
        case = Case.objects.create(case_number="CASE-CV2", lead_investigator=inv)
        ev = register_evidence(case, title="c")
        art = register_artifact(ev, name="nf", source="s", storage_path="")
        with self.assertRaises(NoFileError):
            carve_artifact(art)
