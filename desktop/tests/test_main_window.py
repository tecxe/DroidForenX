"""
Headless Qt tests for the main window chrome.

The sidebar is the one part of the UI that is always on screen, and two style
rules had quietly rendered it unreadable:

  * a selector-less ``padding`` set on a parent widget is inherited by every
    child, and ``#sidebar QLabel`` carries the section-header padding, so the
    two logo labels were handed 32 px of padding inside a 34 px box -- a 2 px
    text slot, which drew the product name as slivers;
  * ``QFrame { padding: 12px; }`` matched the footer's QLabel children as well,
    because QLabel derives from QFrame, leaving the status dot and the device
    name with a zero-height text box.

Both are invisible in code review and obvious on screen. These tests measure the
geometry that actually decides whether glyphs are painted.

Run with:  python -m unittest desktop.tests.test_main_window -v
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QLabel, QWidget

    from desktop.ui.main_window import MainWindow
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False


#: The window opens the operator's real database on construction. Tests must not
#: write to that store: an earlier revision of MainWindow deleted every report,
#: session and artifact on startup, so every suite run quietly wiped the
#: examiner's record. Point the module at a throwaway database for this file.
_restore_db_path = None
_tmp_home = None


def setUpModule():
    global _restore_db_path, _tmp_home
    from desktop.core import database

    _restore_db_path = database.DB_PATH
    _tmp_home = tempfile.mkdtemp(prefix="dfx-window-")
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
    # QSettings is global to the application identity. Without this, the tests
    # would read and write the operator's real preference store -- including the
    # new "sidebar visible" key, which would then hide the sidebar on the next
    # launch after a test toggled it.
    app.setOrganizationName("DroidForenXTests")
    app.setApplicationName("DroidForenXTests")
    return app


def _pump(app, rounds=4):
    for _ in range(rounds):
        app.processEvents()


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class SidebarRenderingTest(unittest.TestCase):
    """Every sidebar label must have room for the glyphs it is asked to draw."""

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.window = MainWindow()
        cls.window.resize(1280, 720)
        cls.window.show()
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        _pump(cls._app)

    def setUp(self):
        # Each test may resize; start from the reference geometry every time.
        self.window.resize(1280, 720)
        _pump(self._app)

    def _sidebar(self):
        return self.window.findChild(QWidget, "sidebar")

    def _sidebar_labels(self):
        return [lbl for lbl in self._sidebar().findChildren(QLabel) if lbl.text().strip()]

    def _clipped(self):
        offenders = []
        for label in self._sidebar_labels():
            needed = label.fontMetrics().height()
            available = label.contentsRect().height()
            if available < needed:
                offenders.append(f"{label.text()!r} has {available} px for {needed} px of text")
        return offenders

    def test_no_sidebar_label_is_clipped(self):
        self.assertEqual(self._clipped(), [])

    def test_the_product_name_has_room_for_its_glyphs(self):
        # The name is the shipped wordmark now, not a text label. Either way it
        # has to fit the column: a text label needs room for its font, and a
        # pixmap needs room for its pixels.
        logo = [lbl for lbl in self._sidebar_labels()
                if lbl.text().startswith("DROIDFORENX")]
        if logo:
            self.assertEqual(len(logo), 1, "the product name should be a single label")
            label = logo[0]
            self.assertGreaterEqual(label.contentsRect().height(),
                                    label.fontMetrics().height())
            return

        mark = self.window.findChild(QLabel, "logoMark")
        self.assertIsNotNone(mark, "neither a name label nor a wordmark is present")
        pixmap = mark.pixmap()
        self.assertIsNotNone(pixmap)
        self.assertGreater(mark.contentsRect().height(), 0)
        self.assertGreaterEqual(mark.contentsRect().height(), pixmap.height())

    def test_the_version_label_is_readable(self):
        version = [lbl for lbl in self._sidebar_labels() if lbl.text().startswith("v1.0")]
        self.assertEqual(len(version), 1)
        self.assertGreaterEqual(version[0].contentsRect().height(),
                                version[0].fontMetrics().height())

    def test_the_device_status_footer_is_actually_painted(self):
        # A zero-height text box is how this bug hid: the widgets existed, had
        # text, and were "visible", but drew nothing.
        for label in (self.window.footer_icon, self.window.footer_status):
            self.assertGreater(label.contentsRect().height(), 0)
            self.assertGreaterEqual(label.contentsRect().height(),
                                    label.fontMetrics().height())

    #: Smallest work area the app is expected to run in.
    SMALLEST_WORK_AREA_HEIGHT = 720

    def test_the_sidebar_asks_for_less_height_than_a_720p_desktop_leaves(self):
        """The measurement that catches the logo regression.

        The sidebar's chrome is a fixed stack, so when it asks for more height
        than it is given, QVBoxLayout takes the difference out of whichever
        widgets can shrink -- the logo labels first -- and the padding then
        leaves them a 2 px slot to paint the product name into. Before this fix
        the sidebar asked for 691 px of the 668 px a 1280x720 desktop leaves;
        it now asks for 555.
        """
        sidebar = self._sidebar()
        chrome = self.window.height() - sidebar.height()
        available = self.SMALLEST_WORK_AREA_HEIGHT - chrome
        self.assertLessEqual(
            sidebar.sizeHint().height(), available,
            f"the sidebar asks for {sidebar.sizeHint().height()} px but a "
            f"{self.SMALLEST_WORK_AREA_HEIGHT} px desktop leaves it {available} px, "
            "so its chrome will be squeezed and its labels clipped")

    def test_the_seizure_nav_item_shows_a_literal_ampersand(self):
        texts = [btn.text() for btn, _ in self.window.nav_buttons]
        seizure = [text for text in texts if "Seizure" in text]
        self.assertEqual(len(seizure), 1)
        # A lone "&" is swallowed as a mnemonic and the word loses a character.
        self.assertIn("&&", seizure[0])

    def test_navigation_selects_by_index_not_caption(self):
        # The caption above only stays safe while nothing matches on it.
        self.window._navigate_to(5)
        _pump(self._app)
        self.assertEqual(self.window.stack.currentIndex(), 5)
        checked = [idx for btn, idx in self.window.nav_buttons if btn.isChecked()]
        self.assertEqual(checked, [5])
        self.window._navigate_to(0)
        _pump(self._app)


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class SidebarVisibilityTest(unittest.TestCase):
    """Folding the navigation away, and keeping the footer inside the column.

    The footer used to be the casualty of the sidebar's fixed chrome: the nav
    list plus three section headers overran the window, the layout took the
    difference out of the bottom widget, and the device status was painted as a
    sliced line at the edge -- which reads as a rendering fault, not as an
    overflow. The navigation now scrolls, the footer labels keep their own
    compact metrics even when a device connects rewrites their style, and the
    column can be folded away from the View menu, Ctrl+B, or the grip.
    """

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.window = MainWindow()
        cls.window.resize(1280, 720)
        cls.window.show()
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.window._set_sidebar_visible(True)
        cls.window.close()
        cls.window.deleteLater()
        _pump(cls._app)

    def setUp(self):
        from PyQt6.QtCore import QSettings

        self.window._set_sidebar_visible(True)
        self.window.resize(1280, 720)
        _pump(self._app)
        # Remove the one key rather than clearing the store: clearing deletes the
        # registry key under QSettings' feet and every later write reports
        # AccessError, which is not the behaviour under test.
        settings = QSettings()
        settings.remove("ui/sidebar_visible")
        settings.sync()

    tearDown = setUp

    # -- the footer --------------------------------------------------------

    def _footer_bottom(self):
        footer = self.window.device_footer
        return footer.y() + footer.height()

    def test_the_device_footer_is_inside_the_sidebar(self):
        sidebar = self.window.sidebar
        self.assertLessEqual(
            self._footer_bottom(), sidebar.height(),
            "the footer is being pushed past the bottom edge, which is what "
            "slices its text")

    def test_a_connected_device_keeps_the_footer_compact(self):
        # This is the path that broke it: the status labels were re-styled with
        # fragment strings that dropped "padding: 0", so they fell back to the
        # section-header padding and tripled in height.
        before = self.window.device_footer.height()
        self.window._set_footer_state(True, "TECNO_KL6 (13276374A7004073)")
        _pump(self._app)

        self.assertLessEqual(self.window.device_footer.height(), before + 4)
        for label in (self.window.footer_icon, self.window.footer_status,
                      self.window.footer_model):
            self.assertGreaterEqual(label.contentsRect().height(),
                                    label.fontMetrics().height(),
                                    f"{label.text()!r} has no room for its glyphs")
        self.assertLessEqual(self._footer_bottom(), self.window.sidebar.height())

    def test_a_long_serial_is_elided_instead_of_clipped(self):
        # 24 monospace characters do not fit the 179 px the sidebar leaves the
        # footer, and the label used to paint the surplus under the window edge.
        serial = "TECNO_KL6 (13276374A7004073)"
        self.window._set_footer_state(True, serial)
        _pump(self._app)

        label = self.window.footer_model
        self.assertEqual(label.full_text(), serial)
        self.assertEqual(label.toolTip(), serial)
        self.assertIn("\u2026", label.text())
        self.assertLessEqual(
            label.fontMetrics().horizontalAdvance(label.text()),
            label.contentsRect().width() + 2,
            "the elided text still overruns the label")

    def test_the_status_text_is_readable_after_a_connect_and_a_disconnect(self):
        self.window._set_footer_state(True, "TECNO_KL6 (SERIAL)")
        _pump(self._app)
        self.assertEqual(self.window.footer_status.text(), "Connected")
        self.window._set_footer_state(False)
        _pump(self._app)
        self.assertEqual(self.window.footer_status.text(), "No device connected")
        self.assertGreaterEqual(self.window.footer_status.contentsRect().height(),
                                self.window.footer_status.fontMetrics().height())

    def test_navigation_lives_in_a_scroll_area(self):
        # A window shorter than the navigation must scroll, not compress the
        # widgets around the list.
        self.assertTrue(self.window.nav_scroll.widgetResizable())
        self.assertIsNotNone(self.window.nav_scroll.widget())
        self.assertEqual(
            self.window.nav_scroll.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_every_navigation_button_is_reachable(self):
        nav = self.window.nav_scroll
        viewport = nav.viewport().height()
        content = nav.widget().sizeHint().height()
        account = content <= viewport or nav.verticalScrollBarPolicy() != \
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        self.assertTrue(account, "the nav list cannot be reached in full")

    # -- hide and show -----------------------------------------------------

    def test_the_sidebar_can_be_hidden_and_shown(self):
        width_with_sidebar = self.window.stack.width()
        self.window._toggle_sidebar()
        _pump(self._app)

        self.assertFalse(self.window.sidebar.isVisible())
        self.assertGreater(self.window.stack.width(), width_with_sidebar)
        self.assertFalse(self.window.sidebar_action.isChecked())
        self.assertIn("hidden", self.window.sidebar_action.text())

        self.window._toggle_sidebar()
        _pump(self._app)
        self.assertTrue(self.window.sidebar.isVisible())
        self.assertEqual(self.window.stack.width(), width_with_sidebar)
        self.assertTrue(self.window.sidebar_action.isChecked())

    def test_the_menu_action_and_the_grip_stay_in_agreement(self):
        self.window.sidebar_action.setChecked(False)
        _pump(self._app)

        self.assertFalse(self.window.sidebar.isVisible())
        self.assertEqual(self.window.sidebar_grip.text(), "\u203a")
        self.assertIn("Show", self.window.sidebar_grip.toolTip())

        self.window.sidebar_action.setChecked(True)
        _pump(self._app)
        self.assertTrue(self.window.sidebar.isVisible())
        self.assertEqual(self.window.sidebar_grip.text(), "\u2039")

    def test_a_hidden_sidebar_leaves_a_visible_way_back(self):
        self.window._set_sidebar_visible(False)
        _pump(self._app)
        self.assertTrue(self.window.sidebar_grip.isVisible())
        self.assertGreater(self.window.sidebar_grip.width(), 0)

    def test_the_choice_is_remembered_for_the_next_window(self):
        from PyQt6.QtCore import QSettings

        self.window._set_sidebar_visible(False)
        _pump(self._app)
        self.assertFalse(QSettings().value("ui/sidebar_visible", True, type=bool))

        other = MainWindow()
        other.resize(1280, 720)
        other.show()
        _pump(self._app)
        try:
            self.assertFalse(other.sidebar.isVisible(),
                             "a hidden sidebar should still be hidden next launch")
        finally:
            other._set_sidebar_visible(True)
            other.close()
            other.deleteLater()
            _pump(self._app)


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class StartupPreservesTheRecordTest(unittest.TestCase):
    """Opening the window must never discard what an examiner recorded.

    v2.0 shipped a ``_close_stale_cases`` helper that ran on every launch and,
    despite its name, deleted every artifact, examination result, report,
    isolation action, acquisition profile and session, then closed every open
    case. Restarting the program therefore erased the analytical record; the
    evidence files on disk were left orphaned with nothing pointing at them.
    """

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def _seed(self):
        import uuid
        from datetime import datetime, timezone

        from desktop.core.database import get_db, init_db

        init_db()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        case_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        conn = get_db()
        conn.execute(
            "INSERT INTO cases (id, case_number, title, investigator, agency, description,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (case_id, "DFX-2026-777", "Restart regression", "ENI", "Lab",
             "", "open", now, now))
        conn.execute(
            "INSERT INTO forensic_sessions (id, case_id, status, current_stage,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (session_id, case_id, "in_progress", 4, now, now))
        conn.execute(
            "INSERT INTO evidence_artifacts (id, session_id, artifact_id, name, source,"
            " category, created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, "sms:1", "A message", "content://sms", "SMS", now))
        conn.execute(
            "INSERT INTO forensic_reports (id, session_id, title, format, case_number,"
            " status, created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), session_id, "Report", "HTML", "DFX-2026-777", "final", now))
        conn.commit()
        conn.close()
        return case_id

    def _counts(self):
        from desktop.core.database import get_db

        conn = get_db()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("cases", "forensic_sessions", "evidence_artifacts",
                          "forensic_reports")
        }
        status = conn.execute(
            "SELECT status FROM cases WHERE case_number='DFX-2026-777'").fetchone()
        conn.close()
        return counts, (status[0] if status else None)

    def test_launching_the_window_keeps_cases_sessions_artifacts_and_reports(self):
        self._seed()
        before, status_before = self._counts()

        window = MainWindow()
        try:
            _pump(self._app)
        finally:
            window.close()
            window.deleteLater()
            _pump(self._app)

        after, status_after = self._counts()
        self.assertEqual(after, before, "startup discarded the analytical record")
        self.assertEqual(status_after, "open", "startup closed an open case")


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class NavigationRouteTest(unittest.TestCase):
    """A control that looks pressable must reach a page.

    The Device Connection page shipped a "SIM Forensics" button with no handler
    at all: drawn, hoverable, inert. Nothing caught it because no test pressed
    it. These tests press the route and check where it lands.
    """

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.window = MainWindow()
        cls.window.resize(1280, 720)
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        _pump(cls._app)

    def test_the_device_page_sim_button_opens_the_sim_page(self):
        self.window.device_page.btn_sim.click()
        _pump(self._app)
        self.assertIs(self.window.stack.currentWidget(), self.window.sim_page)

    def test_every_named_route_reaches_its_own_page(self):
        routes = ("home", "case", "device", "seizure", "identify", "acquire",
                  "examine", "report", "browse", "artifacts", "sim")
        landed = {}
        for name in routes:
            self.window._navigate_by_name(name)
            _pump(self._app)
            landed[name] = self.window.stack.currentIndex()
        # An unmapped name falls back to page 0, so a duplicate here means a
        # route silently sends the operator somewhere they did not ask for.
        self.assertEqual(sorted(landed.values()), list(range(len(routes))), landed)


if __name__ == "__main__":
    unittest.main()
