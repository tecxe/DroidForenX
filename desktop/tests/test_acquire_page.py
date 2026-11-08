"""Geometry tests for the acquisition page's results card.

The card used to render as a tall empty rectangle with a scrollbar and no rows.
The cause was not the table: the card carried ``QFrame { padding: 16px; ... }``,
and a widget style sheet is inherited by every descendant that matches the
selector. ``QTableWidget`` derives from ``QFrame``, so the table picked up the
card's 16 px padding as its own frame, its viewport collapsed to fifteen pixels,
and the rows were painted into a space that could not show them.

These tests measure the table rather than the string, because the string looked
perfectly reasonable.

Run with:  python -m unittest desktop.tests.test_acquire_page -v
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QFrame

    from desktop.ui.pages.acquire_page import AcquirePage
    from desktop.ui.theme import MAIN_STYLESHEET
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False


_restore_db_path = None
_tmp_home = None

ROWS = [
    ("Contacts", "412", "1.2 MB"),
    ("SMS Messages", "9,120", "7.4 MB"),
    ("Call Log", "1,318", "0.4 MB"),
    ("Media Files", "2,204", "12.9 GB"),
    ("TOTAL FILES", "13,054", "13.0 GB"),
]


def setUpModule():
    global _restore_db_path, _tmp_home
    from desktop.core import database

    _restore_db_path = database.DB_PATH
    _tmp_home = tempfile.mkdtemp(prefix="dfx-acquire-")
    database.DB_PATH = Path(_tmp_home) / "droidforenx.db"


def tearDownModule():
    from desktop.core import database

    if _restore_db_path is not None:
        database.DB_PATH = _restore_db_path
    if _tmp_home:
        shutil.rmtree(_tmp_home, ignore_errors=True)


def _make_qapp():
    if not _QT_IMPORT:
        return None
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("DroidForenXTests")
    app.setApplicationName("DroidForenXTests")
    return app


def _pump(app, rounds=4):
    for _ in range(rounds):
        app.processEvents()


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class ResultsCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = AcquirePage(lambda: None)
        cls.page.setStyleSheet(MAIN_STYLESHEET)
        cls.page.resize(1200, 900)
        cls.page.show()
        _pump(cls._app)

        cls.page.results_table.load_data(ROWS)
        cls.page.results_frame.setVisible(True)
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.page.deleteLater()
        _pump(cls._app)

    def test_the_table_is_not_given_the_cards_padding(self):
        # frameWidth is where a stylesheet padding on a QAbstractScrollArea ends
        # up. Sixteen pixels of it is the regression; one pixel is the border the
        # table asks for itself.
        table = self.page.results_table
        self.assertLessEqual(
            table.frameWidth(), 2,
            "the results table is inheriting the card's padding as its frame")

    def test_the_viewport_has_room_for_the_rows(self):
        table = self.page.results_table
        row_height = table.rowHeight(0) if table.rowCount() else 30
        self.assertGreater(table.viewport().height(), row_height,
                           "the rows are painted into a fifteen-pixel slot")

    def test_every_row_is_reachable(self):
        table = self.page.results_table
        self.assertEqual(table.rowCount(), len(ROWS))
        for index in range(len(ROWS)):
            self.assertEqual(table.item(index, 0).text(), ROWS[index][0])

    def test_no_scrollbar_is_needed_for_a_handful_of_rows(self):
        table = self.page.results_table
        self.assertFalse(table.horizontalScrollBar().isVisible(),
                         "a horizontal scrollbar means the columns do not fit")
        self.assertFalse(table.verticalScrollBar().isVisible(),
                         "five rows should fit in the card, not scroll")

    def test_the_card_is_scoped_to_its_own_object_name(self):
        frame = self.page.results_frame
        self.assertEqual(frame.objectName(), "resultsFrame")
        self.assertIn("#resultsFrame", frame.styleSheet())
        self.assertNotRegex(frame.styleSheet(), r"(?m)^\s*QFrame\s*\{")

    def test_the_card_grows_with_its_content(self):
        # A QFrame with a table inside must not be shorter than the table plus
        # its own padding; that is what produced the clipped strip.
        frame = self.page.results_frame
        table = self.page.results_table
        self.assertGreaterEqual(frame.height(), table.height() + 20)

    def test_the_table_has_a_floor_height(self):
        table = self.page.results_table
        self.assertGreaterEqual(table.minimumHeight(), 200)
        self.assertGreater(table.maximumHeight(), table.minimumHeight())

    def test_the_card_starts_hidden(self):
        fresh = AcquirePage(lambda: None)
        try:
            self.assertFalse(fresh.results_frame.isVisible())
        finally:
            fresh.deleteLater()
            _pump(self._app)
@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class ScrcpyEyeTest(unittest.TestCase):
    """The God's eye button - scrcpy screen mirror/control in the acquire phase."""

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = AcquirePage(lambda: None)
        cls.page.setStyleSheet(MAIN_STYLESHEET)
        cls.page.resize(1200, 900)
        cls.page.show()
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.page.deleteLater()
        _pump(cls._app)

    def test_the_scrcpy_button_is_named_and_visible(self):
        btn = self.page.btn_scrcpy
        self.assertTrue(btn.isVisible())
        self.assertIn("God's eye", btn.text())
        self.assertTrue(bool(btn.toolTip().strip()))

    def _row_layouts(self):
        from PyQt6.QtWidgets import QHBoxLayout
        layouts = []
        for layout in self.page.findChildren(QHBoxLayout):
            widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
            if self.page.btn_create_image in widgets:
                layouts.append((layout, widgets))
        return layouts

    def test_the_scrcpy_button_sits_in_the_action_row(self):
        rows = self._row_layouts()
        self.assertTrue(len(rows) >= 1, "the create-image button must live in a row layout")
        _row_layout, widgets = rows[0]
        self.assertIn(self.page.btn_scrcpy, widgets)

    @unittest.skipUnless(shutil.which("scrcpy") is not None, "scrcpy is not installed")
    def test_the_scrcpy_binary_is_located(self):
        found = self.page._find_scrcpy()
        self.assertTrue(found.endswith("scrcpy") or found.endswith("scrcpy.exe"))

    def test_start_scrcpy_without_a_binary_reports_and_does_not_crash(self):
        original = self.page._find_scrcpy
        self.page._find_scrcpy = lambda: ""
        try:
            self.page._serial = "TESTSERIAL0000"
            self.page._start_scrcpy()
            text = self.page.console.console.toPlainText()
            self.assertIn("scrcpy not found", text)
            self.assertIsNone(getattr(self.page, "_scrcpy_proc", None))
        finally:
            self.page._find_scrcpy = original
