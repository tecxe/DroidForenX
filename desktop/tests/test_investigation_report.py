"""Tests for the investigation report PDF.

The layout is composed in two passes so the table of contents can print real
page numbers, and it folds every string into Latin-1 because the built-in core
fonts cannot encode anything wider. Both of those are easy to break silently --
the document still opens, it just lies about its own pages or raises halfway
through a case -- so they are pinned here without a display and without a PDF
library: the composed object is inspected directly, and an uncompressed copy is
searched for the text it should contain.

Run with:  python -m unittest desktop.tests.test_investigation_report -v
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from desktop.report import investigation_report as report


def case_data(**overrides):
    data = {
        "case_number": "CASE-2026-0117",
        "examiner": "Detective Sgt. Ama Boateng",
        "badge": "GH-4471",
        "org": "Cybercrime Unit",
        "generated_at": "2026-10-25 11:42 UTC",
        "serial": "144131557N016694",
        "device": {
            "manufacturer": "Samsung",
            "model": "SM-A155F",
            "device_name": "Galaxy A15",
            "android_version": "14",
            "api_level": "34",
            "security_patch": "2025-02-01",
            "encryption_status": "encrypted",
            "lock_type": "PIN",
            "storage_total_bytes": 63_700_000_000,
            "storage_free_bytes": 9_400_000_000,
            "sim_count": 2,
            "identified_at": "2026-10-23T08:14:02+00:00",
        },
        "session": {"id": "5f1c9a7e", "status": "complete",
                    "created_at": "2026-10-23T08:12:41+00:00"},
        "results": [
            {"analysis_type": "call_log", "status": "completed", "item_count": 1183,
             "tools_used": "content://call_log", "completed_at": "2026-10-23T08:58:04+00:00",
             "findings": {"calls": [{"number": f"+2335000{i:04d}", "type": "INCOMING",
                                     "duration_s": str(i)} for i in range(1183)],
                          "stats": {"total": 1183, "incoming": 512}}},
            {"analysis_type": "sms_mms", "status": "completed", "item_count": 420,
             "tools_used": "content://sms", "completed_at": "2026-10-23T09:02:11+00:00",
             "findings": {"messages": [{"address": f"+2332400{i:04d}",
                                        "body": "sample body"} for i in range(420)]}},
            {"analysis_type": "browser", "status": "failed", "item_count": 0,
             "completed_at": "2026-10-23T09:06:02+00:00",
             "findings": {"error": "permission denied reading /data/data/com.android.chrome"}},
        ],
        "audit_log": [
            {"timestamp": "2026-10-23T08:12:41+00:00", "action": "CASE_OPENED",
             "actor": "A. Boateng", "details": "Case created"},
            {"timestamp": "2026-10-23T08:20:33+00:00", "action": "IMAGE_CREATED",
             "actor": "A. Boateng", "details": "Logical image of /sdcard"},
        ],
    }
    data.update(overrides)
    return data


def compose(data):
    """Compose once, without writing, and hand back the live document."""
    pdf = report.InvestigationReport(data)
    pdf._compose()
    return pdf


class ModuleTitleTest(unittest.TestCase):
    def test_known_keys_get_readable_titles(self):
        self.assertEqual(report._title_of("sms_mms"), "SMS and MMS")
        self.assertEqual(report._title_of("gps"), "GPS and Location")
        self.assertEqual(report._title_of("call_log"), "Call Log")

    def test_an_unknown_key_falls_back_to_title_case(self):
        self.assertEqual(report._title_of("some_new_module"), "Some New Module")


class TextFoldingTest(unittest.TestCase):
    def test_typographic_characters_become_latin1(self):
        folded = report._clean("2019 \u2014 \u201cquoted\u201d \u2018x\u2019 \u2026")
        self.assertEqual(folded, '2019 - "quoted" \'x\' ...')

    def test_characters_outside_latin1_are_replaced_not_raised(self):
        folded = report._clean("Galaxy \U0001f600 A15")
        self.assertIn("Galaxy", folded)
        self.assertTrue(folded.isascii() or all(ord(c) <= 255 for c in folded))

    def test_floats_lose_binary_noise(self):
        self.assertEqual(report._clean(5.6076999999999995), "5.6077")

    def test_long_values_are_truncated_with_an_ellipsis(self):
        folded = report._clean("x" * 200, 20)
        self.assertEqual(len(folded), 20)
        self.assertTrue(folded.endswith("..."))


class RowSplitTest(unittest.TestCase):
    """How a long table is divided across sheets."""

    def test_a_short_table_stays_on_one_page(self):
        self.assertEqual(report._split_rows(0, 38, 43), [0])
        self.assertEqual(report._split_rows(5, 38, 43), [5])

    def test_every_row_is_drawn_exactly_once(self):
        for count in (39, 44, 87, 120, 121, 1000):
            sizes = report._split_rows(count, 38, 43)
            self.assertEqual(sum(sizes), count)
            self.assertTrue(all(0 < s <= 43 for s in sizes), sizes)

    def test_no_page_capacity_is_exceeded(self):
        sizes = report._split_rows(200, 38, 43)
        self.assertLessEqual(sizes[0], 38)
        self.assertTrue(all(s <= 43 for s in sizes[1:]))

    def test_the_last_sheet_is_not_a_scrap(self):
        sizes = report._split_rows(87, 38, 43)
        self.assertGreaterEqual(sizes[-1], 8, sizes)


class CompositionTest(unittest.TestCase):
    def test_front_matter_is_cover_and_contents(self):
        pdf = compose(case_data())
        # _compose() renders the whole document; the front matter is only the
        # first two sheets, and the body has to start on the sheet after them.
        self.assertEqual(pdf.front_pages, 2)
        self.assertGreater(pdf.pages_count, pdf.front_pages)
        self.assertEqual(min(entry[3] for entry in pdf.toc_entries), 1)

    def test_every_contents_entry_points_at_a_body_page(self):
        pdf = compose(case_data())
        self.assertTrue(pdf.toc_entries)
        for level, number, label, page in pdf.toc_entries:
            self.assertIn(level, (0, 1))
            self.assertGreaterEqual(page, 1, label)
            self.assertTrue(label)

    def test_sections_are_numbered_in_order_and_appendices_lettered(self):
        pdf = compose(case_data())
        numbered = [entry[1] for entry in pdf.toc_entries if entry[0] == 0]
        self.assertEqual(numbered[:8], ["1", "2", "3", "4", "5", "6", "7", "8"])
        self.assertEqual(numbered[8:], ["A", "B"])

    def test_largest_modules_come_first_in_the_findings(self):
        pdf = compose(case_data())
        modules = [m["title"] for m in pdf._modules() if m["count"]]
        self.assertEqual(modules, ["Call Log", "SMS and MMS"])

    def test_a_failed_module_still_appears_with_its_reason(self):
        pdf = compose(case_data())
        failed = [m for m in pdf._modules() if m["status"] == "failed"]
        self.assertEqual(failed[0]["title"], "Browser History")
        self.assertIn("permission denied", failed[0]["error"])

    def test_counts_add_up(self):
        pdf = compose(case_data())
        counts = pdf._counts()
        self.assertEqual(counts["total"], 1603)
        self.assertEqual(counts["modules"], 3)
        self.assertEqual(counts["failed"], 1)


class StabilityTest(unittest.TestCase):
    def test_a_second_pass_reproduces_the_first(self):
        """The contents loop stops only when a pass matches the previous one."""
        first = compose(case_data())
        second = report.InvestigationReport(case_data(), toc=first.toc_entries,
                                            total_pages=first.pages_count)
        second._compose()
        self.assertEqual(first.toc_entries, second.toc_entries)
        self.assertEqual(first.pages_count, second.pages_count)

    def test_a_long_case_number_does_not_push_the_cover_onto_a_second_page(self):
        pdf = compose(case_data(case_number="CASE-2026-0117-EXTREMELY-LONG-REFERENCE-NUMBER"))
        self.assertEqual(pdf.front_pages, 2)

    def test_an_empty_case_still_produces_a_document(self):
        pdf = compose({"case_number": "CASE-EMPTY"})
        self.assertGreaterEqual(pdf.pages_count, 3)
        self.assertIn(1, [entry[3] for entry in pdf.toc_entries])


class PlatesTest(unittest.TestCase):
    """The image appendix: plates of what the artifact viewer showed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-plates-"))
        self.photo = self.tmp / "IMG_0001.jpg"
        from PIL import Image

        Image.new("RGB", (1200, 900), (90, 120, 160)).save(self.photo, quality=85)

    def _image(self, **overrides):
        image = {
            "path": str(self.photo),
            "name": self.photo.name,
            "source": "/sdcard/DCIM/Camera/IMG_0001.jpg",
            "category": "Camera",
            "size": self.photo.stat().st_size,
            "sha256": "a" * 64,
        }
        image.update(overrides)
        return image

    def test_no_images_means_no_appendix(self):
        pdf = compose(case_data())
        self.assertEqual([entry[2] for entry in pdf.toc_entries].count("Recovered Images"), 0)

    def test_images_add_an_appendixed_section_to_the_contents(self):
        pdf = compose(case_data(images=[self._image()]))
        labels = [entry[2] for entry in pdf.toc_entries]
        self.assertIn("Recovered Images", labels)
        # It follows the module appendices, so the letter is the next one free.
        letters = [entry[1] for entry in pdf.toc_entries if entry[0] == 0]
        self.assertEqual(letters[-1], chr(65 + 2))

    def test_a_file_that_has_gone_missing_is_not_listed(self):
        pdf = compose(case_data(images=[self._image(path=str(self.tmp / "gone.jpg"))]))
        self.assertEqual([entry[2] for entry in pdf.toc_entries].count("Recovered Images"), 0)

    def test_an_undecodable_image_is_plated_with_a_reason(self):
        broken = self.tmp / "broken.jpg"
        broken.write_bytes(b"not a jpeg at all")
        pdf = compose(case_data(images=[self._image(path=str(broken), name="broken.jpg")]))
        self.assertIn("Recovered Images", [entry[2] for entry in pdf.toc_entries])
        payload, size, reason = report._preview(broken)
        self.assertIsNone(payload)
        self.assertTrue(reason)

    def test_the_preview_is_shrunk_and_re_encoded(self):
        payload, size, reason = report._preview(self.photo)
        self.assertEqual(reason, "")
        self.assertLess(max(size), 1000)
        self.assertLess(len(payload), self.photo.stat().st_size // 2)

    def test_plates_do_not_bloat_the_document(self):
        images = []
        for index in range(12):
            copy = self.tmp / f"IMG_{index:04d}.jpg"
            copy.write_bytes(self.photo.read_bytes())
            images.append(self._image(path=str(copy), name=copy.name))
        out = report.build(case_data(images=images), self.tmp / "plated.pdf")
        self.assertLess(out.stat().st_size, 4_000_000, "plates should stay a sensible size")


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-report-"))

    def test_the_file_is_a_pdf_and_the_sidecar_matches_it(self):
        out = report.build(case_data(), self.tmp / "report.pdf")
        blob = out.read_bytes()
        self.assertEqual(blob[:5], b"%PDF-")
        self.assertGreater(len(blob), 10_000)

        sidecar = out.with_suffix(".pdf.sha256")
        self.assertTrue(sidecar.exists())
        recorded = sidecar.read_text(encoding="utf-8").split()[0]
        self.assertEqual(recorded, hashlib.sha256(blob).hexdigest())

    def test_the_document_contains_its_own_case_number_and_contents(self):
        pdf = report.InvestigationReport(case_data())
        pdf.set_compression(False)
        pdf._compose()
        text = bytes(pdf.output())
        self.assertIn(b"CASE-2026-0117", text)
        self.assertIn(b"Table of contents", text)
        self.assertIn(b"Chain of Custody", text)
        self.assertIn(b"CONFIDENTIAL", text)

    def test_a_hostile_device_string_does_not_break_the_build(self):
        data = case_data(device={"manufacturer": "Sams\u00f6ng\U0001f600",
                                 "model": "SM\u2014A15F",
                                 "android_version": "14"})
        out = report.build(data, self.tmp / "hostile.pdf")
        self.assertEqual(out.read_bytes()[:5], b"%PDF-")


if __name__ == "__main__":
    unittest.main()
