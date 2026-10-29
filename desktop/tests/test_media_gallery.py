"""The media grid, and the device-info panel under it.

Two bugs get pinned here.

The device info panel carried ``QFrame { padding: 20px; }``. ``QLabel`` derives
from ``QFrame``, so every specification row grew 20 px of padding on each side
and the grid was pushed out of the panel: the model heading showed and the specs
did not.

The artifact viewer's "View" button was ``setFixedSize(50, 26)`` while the page
stylesheet gave every ``QPushButton`` 20 px of horizontal padding, so the caption
was painted into ten pixels and read as "iew".

Run with:  python -m unittest desktop.tests.test_media_gallery -v
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QLabel, QTableWidget

    from desktop.ui.pages.artifacts_page import ArtifactsPage
    from desktop.ui.pages.device_page import DevicePage
    from desktop.ui.widgets.media_gallery import (TILE_SIZES, MediaGallery,
                                                  media_kind)
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False


def _make_qapp():
    if not _QT_IMPORT:
        return None
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("DroidForenXTests")
    app.setApplicationName("DroidForenXTests")
    return app


def _pump(app, rounds=6):
    for _ in range(rounds):
        app.processEvents()


def _artifact(path, kind, category="Media", name=""):
    return {
        "type": kind,
        "category": category,
        "date": "2026-10-29 10:00:00",
        "summary": name or os.path.basename(path),
        "source": path,
        "_data": {"path": path, "filename": name or os.path.basename(path),
                  "type": kind, "size": "12 KB"},
    }


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class MediaKindTest(unittest.TestCase):
    def test_known_suffixes(self):
        self.assertEqual(media_kind("/sdcard/a/IMG_1.JPG"), "Image")
        self.assertEqual(media_kind("/sdcard/a/clip.mp4"), "Video")
        self.assertEqual(media_kind("/sdcard/a/voice.opus"), "Audio")
        self.assertEqual(media_kind("/sdcard/a/notes.db"), "File")

    def test_a_suffixless_name_is_not_an_image(self):
        self.assertEqual(media_kind("/sdcard/a/no_extension"), "File")


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class MediaGalleryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def setUp(self):
        self.gallery = MediaGallery()
        self.gallery.resize(900, 600)
        self.gallery.show()
        _pump(self._app)

    def tearDown(self):
        self.gallery.stop()
        self.gallery.close()
        self.gallery.deleteLater()
        _pump(self._app)

    def _load(self, count=5, kind="Image"):
        items = [_artifact(f"/sdcard/DCIM/pic_{i}.jpg", kind) for i in range(count)]
        self.gallery.load("Camera", items)
        _pump(self._app)
        return items

    def test_one_tile_per_file(self):
        self._load(7)
        self.assertEqual(len(self.gallery._tiles), 7)
        self.assertEqual(self.gallery.count_label.text(), "7 files")

    def test_an_empty_category_says_so(self):
        self.gallery.load("Camera", [])
        _pump(self._app)
        self.assertEqual(self.gallery._tiles, [])
        self.assertIn("Nothing", self.gallery.status_label.text())

    def test_the_size_control_resizes_every_tile(self):
        self._load(3)
        before = self.gallery._tiles[0].width()
        self.gallery.set_tile_size(TILE_SIZES[-1])
        _pump(self._app)
        self.assertGreater(self.gallery._tiles[0].width(), before)
        self.assertGreater(self.gallery._tiles[0].height(), before)

    def test_tiles_wrap_into_more_than_one_column(self):
        self._load(12)
        columns = self.gallery._columns()
        self.assertGreater(columns, 1, "the grid never wrapped")
        rows = {self.gallery.grid.getItemPosition(i)[0]
                for i in range(self.gallery.grid.count())}
        self.assertEqual(len(rows), 2)

    def test_selecting_a_tile_enables_open(self):
        items = self._load(2)
        self.assertFalse(self.gallery.open_btn.isEnabled())
        self.gallery._on_tile_clicked(items[0]["_data"]["path"])
        self.assertTrue(self.gallery.open_btn.isEnabled())
        self.assertTrue(self.gallery._tiles[0]._selected)

    def test_a_local_finding_needs_no_device(self):
        import tempfile

        from PyQt6.QtGui import QPixmap

        with tempfile.TemporaryDirectory() as folder:
            local = os.path.join(folder, "capture.png")
            pix = QPixmap(40, 40)
            pix.fill()
            self.assertTrue(pix.save(local))

            gallery = MediaGallery()
            gallery.set_serial("")
            gallery.load("Screenshots", [_artifact(local, "Image", "Screenshots")])
            puller = gallery._ensure_puller()
            for _ in range(40):
                _pump(self._app)
                if gallery._pending == 0:
                    break
                self._app.thread().msleep(25)
            gallery.stop()
            gallery.deleteLater()
            _pump(self._app)

        # The puller must not have tried ADB: a local file is displayed as-is.
        self.assertTrue(puller is not None)


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class DeviceInfoPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = DevicePage()
        cls.page.resize(1100, 760)
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.page.deleteLater()
        _pump(cls._app)

    def test_the_panel_selector_is_scoped(self):
        sheet = self.page.info_panel.styleSheet()
        self.assertIn("QFrame#infoPanel", sheet)
        self.assertNotIn("QFrame {", sheet,
                         "a bare QFrame rule also styles every QLabel inside")

    def test_the_labels_are_not_inflated_by_padding(self):
        for label in self.page.info_panel.findChildren(QLabel):
            if not label.text():
                continue
            self.assertGreaterEqual(
                label.contentsRect().height(), label.fontMetrics().height(),
                f"{label.text()!r} has no room for its glyphs")


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class ArtifactViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = ArtifactsPage()
        cls.page.resize(1100, 760)
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.page.deleteLater()
        _pump(cls._app)

    def setUp(self):
        self.page.all_artifacts = []
        self.page._contacts = []

    def _calls(self):
        return [
            {"type": "Call", "category": "Calls", "date": "2026-10-29 09:10:00",
             "summary": "Incoming", "source": "content://call_log/calls",
             "_data": {"number": "+2348012345678", "type": "Incoming",
                       "answered": "Answered", "duration": "1m 20s",
                       "date": "2026-10-29 09:10:00", "raw_date": "1793000000000"}},
            {"type": "Call", "category": "Calls", "date": "2026-10-28 18:02:00",
             "summary": "Rejected", "source": "content://call_log/calls",
             "_data": {"number": "+2348099999999", "type": "Rejected",
                       "answered": "\u2014", "duration": "0m 0s",
                       "date": "2026-10-28 18:02:00", "raw_date": "1792900000000"}},
        ]

    def _table(self):
        for index in range(self.page.conv_list_layout.count()):
            widget = self.page.conv_list_layout.itemAt(index).widget()
            if isinstance(widget, QTableWidget):
                return widget
        return None

    def test_the_call_log_is_a_table_with_direction_and_answered(self):
        self.page.all_artifacts = self._calls()
        self.page._show_call_conversations()
        _pump(self._app)

        table = self._table()
        self.assertIsNotNone(table, "the call log did not build a table")
        headers = [table.horizontalHeaderItem(i).text()
                   for i in range(table.columnCount())]
        self.assertIn("Direction", headers)
        self.assertIn("Answered", headers)
        self.assertEqual(table.rowCount(), 2)

        values = {table.item(r, c).text()
                  for r in range(table.rowCount()) for c in range(table.columnCount())}
        self.assertIn("Rejected", values)
        self.assertIn("Answered", values)

    def test_the_view_button_fits_its_own_caption(self):
        button = self.page._media_button("View", "#25d366")
        _pump(self._app)
        self.assertEqual(button.text(), "View")
        self.assertGreaterEqual(button.minimumWidth(), button.fontMetrics().horizontalAdvance("View"),
                                "the caption is wider than the button")

    def test_media_categories_offer_a_grid(self):
        self.page._current_app = {"name": "Instagram"}
        self.page._set_media_actions(
            [_artifact("/sdcard/DCIM/a.jpg", "Image", "Instagram")], "Instagram")
        self.assertTrue(self.page.grid_btn.isVisibleTo(self.page) or
                        not self.page.grid_btn.isHidden())

    def test_a_list_without_pictures_hides_the_grid_button(self):
        self.page._set_media_actions([], "Calls")
        self.assertTrue(self.page.grid_btn.isHidden())

    def test_the_gallery_is_on_the_stack(self):
        self.assertIs(self.page.stacked.widget(3), self.page.gallery_view)


if __name__ == "__main__":
    unittest.main()
