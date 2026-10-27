"""Tests for the Artifact View Results appendix.

The appendix makes two promises the report has to keep: every class the tool
advertises gets a panel, and every panel says where its records came from.
Those two promises are what these tests pin, along with the small parsers the
panels depend on and the renderer that turns a panel into a JPEG.

Run with:  python -m unittest desktop.tests.test_artifact_views -v
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop.report import artifact_views as views
from desktop.report import view_plate


# The checklist the tool advertises, in the order an examiner walks it.
ADVERTISED = (
    "contacts", "sms", "calls", "calendar", "browser", "app_databases", "media",
    "documents", "social", "deleted", "device", "storage", "app_data",
    "recovered", "emails", "doc_metadata", "bookmarks", "packaging",
)


class CatalogueTest(unittest.TestCase):
    def test_every_advertised_class_has_a_panel(self):
        self.assertEqual(tuple(key for key, *_ in views.CATEGORIES), ADVERTISED)

    def test_every_class_knows_where_an_acquisition_would_put_it(self):
        # A gap panel is only useful if it names the folder that is missing.
        self.assertEqual(set(views.STORE_PATHS), set(ADVERTISED))

    def test_panel_colours_are_hex(self):
        for key, _title, colour, _unit in views.CATEGORIES:
            self.assertRegex(colour, r"^#[0-9a-f]{6}$", key)


class ContentProviderParsingTest(unittest.TestCase):
    def test_literal_null_is_an_absent_value(self):
        # `content query` prints NULL for a column the row does not carry, and
        # printing that on a panel reads as a contact literally named "NULL".
        self.assertEqual(views._clean_value("NULL"), "")
        self.assertEqual(views._clean_value(" null "), "")
        self.assertEqual(views._clean_value("Aaron"), "Aaron")

    def test_rows_split_on_the_next_key_not_on_every_comma(self):
        payload = ("Row: 0 _id=1, address=+233 54 149 7566, body=Hi, there, type=1\n"
                   "Row: 1 _id=2, address=5040, body=Promo, type=1\n")
        rows = views._parse_content_rows(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["body"], "Hi, there")
        self.assertEqual(rows[1]["address"], "5040")

    def test_payload_without_rows_is_empty_not_an_error(self):
        self.assertEqual(views._parse_content_rows(""), [])
        self.assertEqual(views._parse_content_rows("error: no provider"), [])


class MissingCategoryTest(unittest.TestCase):
    def test_a_class_with_no_store_and_no_device_still_gets_a_panel(self):
        panels = views.collect(None, None, allow_live=False)
        self.assertEqual(len(panels), len(ADVERTISED))
        for panel in panels:
            self.assertEqual(panel["status"], "missing", panel["key"])
            self.assertTrue(panel["source"], panel["key"])
            self.assertIn("Not present", panel["note"])

    def test_the_gap_names_the_folder_that_would_have_held_it(self):
        panels = {p["key"]: p for p in views.collect(None, None, allow_live=False)}
        self.assertEqual(panels["browser"]["source"], "browser/")
        self.assertEqual(panels["emails"]["source"], "emails/")

    def test_summary_counts_each_state(self):
        panels = views.collect(None, None, allow_live=False)
        summary = views.summarise(panels)
        self.assertEqual(summary["total"], len(ADVERTISED))
        self.assertEqual(summary["missing"], len(ADVERTISED))
        self.assertEqual(summary["recovered"], 0)
        self.assertEqual(len(summary["missing_titles"]), len(ADVERTISED))


class StoreReaderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name)
        (self.store / "logical").mkdir()
        (self.store / "browser").mkdir()
        (self.store / "device").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, relative, payload):
        path = self.store / relative
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_contacts_are_read_from_the_store_before_the_handset(self):
        self._write("logical/contacts.json", [
            {"name": "Aaron", "phones": [{"number": "+233 54 149 7566"}], "emails": []},
            {"name": "Katura Mom", "phones": ["0545532910"], "emails": ["k@example.com"]},
        ])
        panels = {p["key"]: p for p in views.collect(self.store, None, allow_live=False)}
        contacts = panels["contacts"]
        self.assertEqual(contacts["status"], "recovered")
        self.assertEqual(contacts["count"], 2)
        self.assertEqual(contacts["rows"][0][0], "Aaron")
        self.assertEqual(contacts["rows"][0][1], "+233 54 149 7566")
        self.assertEqual(contacts["source"], "logical/contacts.json")

    def test_an_empty_store_still_degrades_to_a_gap(self):
        panels = {p["key"]: p for p in views.collect(self.store, None, allow_live=False)}
        self.assertEqual(panels["sms"]["status"], "missing")

    def test_browser_history_is_counted_from_its_folder(self):
        (self.store / "browser" / "chrome_history.json").write_text("[]", encoding="utf-8")
        (self.store / "browser" / "visits.csv").write_text("url,ts\n", encoding="utf-8")
        panel = views._store_listing(self.store, "browser", 8, "records")
        self.assertEqual(panel["count"], 2)
        self.assertEqual(panel["status"], "recovered")

    def test_packaging_reports_the_manifest_it_verified_against(self):
        self._write("ACQUISITION_MANIFEST.json", {
            "acquisition_id": "acq_1", "device_serial": "144131557N016694",
            "method": "logical", "artifacts": [{}, {}],
        })
        self._write("VERIFICATION.json", {"verified": True})
        panel = views._store_packaging(self.store, 8)
        self.assertEqual(panel["count"], 2)
        labels = [row[0] for row in panel["rows"]]
        self.assertIn("Acquisition Id", labels)

    def test_media_is_counted_only_inside_the_media_folders(self):
        media = self.store / "filesystem" / "sdcard_dcim" / "DCIM" / "Camera"
        media.mkdir(parents=True)
        (media / "IMG_1.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 40)
        (self.store / "filesystem" / "note.txt").write_text("x", encoding="utf-8")
        panel = views._store_media(self.store, 8, folder_filter=("dcim",))
        self.assertEqual(panel["count"], 1)
        self.assertEqual(panel["rows"][0][0], "IMG_1.jpg")
        self.assertEqual(len(panel["thumbs"]), 1)


class LiveListingTest(unittest.TestCase):
    """The live readers are fed a fake `ls -l` so no handset is required."""

    def _listing(self, entries):
        return [{"name": name, "size": size, "is_dir": is_dir, "date": "2026-10-26 08:00"}
                for name, size, is_dir in entries]

    def test_a_package_folder_is_shown_as_a_directory_with_its_timestamp(self):
        # `ls -l` reports 3452 bytes for a package folder; printing that as a
        # size would state something that is not the size of anything.
        with mock.patch.object(views, "_live_ls",
                               return_value=self._listing([("com.android.chrome", 3452, True)])):
            panel = views._live_media("serial", ("/sdcard/Android/data",), 8,
                                      unit="packages", layout="packages")
        self.assertEqual(panel["rows"][0], ("com.android.chrome", "directory", "2026-10-26 08:00"))

    def test_tiny_placeholder_images_are_not_used_as_thumbnails(self):
        entries = [("stub.jpg", 3400, False), ("real.jpg", 240_000, False)]
        with mock.patch.object(views, "_live_ls", return_value=self._listing(entries)), \
                mock.patch.object(views, "_live_thumbs", return_value=[Path("real.jpg")]) as thumbs:
            panel = views._live_media("serial", ("/sdcard/DCIM/Camera",), 8, thumbs=6)
        self.assertEqual(thumbs.call_args[0][1], ["/sdcard/DCIM/Camera/real.jpg"])
        self.assertEqual(panel["count"], 2)

    def test_the_largest_photographs_are_offered_first(self):
        entries = [("a.jpg", 30_000, False), ("b.jpg", 900_000, False), ("c.mp4", 5_000_000, False)]
        with mock.patch.object(views, "_live_ls", return_value=self._listing(entries)), \
                mock.patch.object(views, "_live_thumbs", return_value=[]) as thumbs:
            views._live_media("serial", ("/sdcard/DCIM/Camera",), 8, thumbs=2)
        self.assertEqual(thumbs.call_args[0][1],
                         ["/sdcard/DCIM/Camera/b.jpg", "/sdcard/DCIM/Camera/a.jpg"])

    def test_ls_accepts_the_setuid_and_sticky_bits_sdcard_uses(self):
        output = ("total 417\n"
                  "drwxrws--- 3 u0_a294 ext_data_rw 3452 2025-12-22 21:35 ai.mistral.chat\n"
                  "drwxrwx--x 2 u0_a11  ext_data_rw 3452 2025-10-11 18:13 com.android.chrome\n"
                  "-rw-rw---- 1 u0_a11  ext_data_rw 1024 2025-10-11 18:14 not_a_dir\n")
        adb = mock.Mock()
        adb.adb_shell.return_value = (True, output)
        with mock.patch.object(views, "_adb", return_value=adb):
            listing = views._live_ls("serial", "/sdcard/Android/data")
        self.assertEqual([entry["name"] for entry in listing],
                         ["ai.mistral.chat", "com.android.chrome", "not_a_dir"])
        self.assertEqual([entry["is_dir"] for entry in listing], [True, True, False])


class PlateRenderTest(unittest.TestCase):
    def _view(self, **overrides):
        view = {"title": "Call Log", "colour": "#3b82f6", "unit": "calls", "count": 588,
                "source": "content://call_log/calls", "status": "live",
                "rows": [("Liberty 0256548963", "Outgoing · 45m 24s", "2026-10-25 20:31")],
                "note": "Read live from the handset at report time; not the acquired state.",
                "stamp": "2026-10-26 20:05 UTC"}
        view.update(overrides)
        return view

    def test_render_returns_a_jpeg_and_its_pixel_size(self):
        payload, (width, height) = view_plate.render(self._view())
        self.assertEqual(payload[:2], b"\xff\xd8")
        self.assertEqual(width, 1240)
        self.assertGreater(height, 250)
        self.assertLess(height, 900)

    def test_a_panel_without_records_still_renders(self):
        payload, (_width, height) = view_plate.render(
            self._view(count=None, rows=[], note="Not present in this case."))
        self.assertTrue(payload)
        self.assertGreater(height, 200)

    def test_emoji_the_windows_fonts_cannot_draw_are_removed(self):
        # Segoe UI has no glyph for these, so Pillow draws a hollow box beside
        # a contact's real name in what looks like a finished exhibit.
        self.assertEqual(view_plate._plain("Mummy \U0001F60A 0551358352"), "Mummy  0551358352")
        self.assertEqual(view_plate._plain("\u2764\ufe0f Katura"), "Katura")

    def test_render_survives_an_undrawable_name(self):
        payload, _size = view_plate.render(
            self._view(rows=[("Mummy \U0001F60A", "Outgoing", "2026-10-26 07:49")]))
        self.assertTrue(payload)


if __name__ == "__main__":
    unittest.main()
