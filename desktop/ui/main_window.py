"""Main application window — professional Cellebrite-style sidebar + page stack."""
from PyQt6.QtWidgets import (QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QStackedWidget, QFrame, QSplitter,
                               QStatusBar, QMenuBar, QMenu, QMessageBox, QFileDialog,
                               QScrollArea, QToolButton, QSizePolicy)
from PyQt6.QtCore import Qt, QSize, QSettings
from PyQt6.QtGui import QIcon, QFont, QAction, QActionGroup

from .theme import MAIN_STYLESHEET, theme
from . import branding
from .pages.home_page import HomePage
from .pages.case_page import CasePage
from .pages.device_page import DevicePage
from .pages.seizure_page import SeizurePage
from .pages.identify_page import IdentifyPage
from .pages.acquire_page import AcquirePage
from .pages.examine_page import ExaminePage
from .pages.report_page import ReportPage
from .pages.browse_page import BrowsePage
from .pages.artifacts_page import ArtifactsPage
from .pages.sim_page import SimForensicsPage
from ..core.adb_manager import list_devices, get_device_info
from ..core.database import get_db, init_db, audit_log
from .. import __version__


def _app_version() -> str:
    """Version shown in the chrome, from the package, never hardcoded.

    The sidebar used to say 2.0.16 long after main.py moved on, so every release
    shipped a window that advertised the wrong build. ``applicationVersion()``
    is not used here: Qt seeds it from the executable's version resource, so an
    embedded Python answers with its own build number until main.py overwrites
    it, and a window built without main.py (tests, tooling) would label itself
    3.14.x.
    """
    return __version__


class _ElidedLabel(QLabel):
    """A label that shortens itself in the middle instead of clipping.

    The device footer shows ``MODEL (serial)`` in a 179 px column. A TECNO serial
    is 24 monospace characters, about 192 px, so a plain ``QLabel`` cut the last
    characters mid-glyph -- which reads as a broken render rather than as an
    abbreviation. Keeping the full string on the label would elide at whatever
    width existed before the layout ran (a resize after the fact does not
    re-elide text), so the text is elided on every resize instead.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""

    def set_full_text(self, text: str) -> None:
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        if not self._full_text:
            self.setText("")
            return
        width = max(self.width() - 2, 40)
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, width))


class MainWindow(QMainWindow):
    #: Preferred working size. Used as an upper bound, never as a hard floor,
    #: so the window still opens on laptops smaller than this.
    PREFERRED_SIZE = (1440, 900)
    MIN_SIZE = (1280, 800)

    def __init__(self):
        super().__init__()
        init_db()
        self.setWindowTitle("DroidForenX — Professional Digital Forensics")
        self.setWindowIcon(branding.app_icon())
        self._fit_to_screen()
        self.setStyleSheet(MAIN_STYLESHEET)

        self._device_serial = ""
        self._device_info = None
        self._current_case_id = ""
        self._current_session_id = ""
        self._settings = None
        self._sidebar_restored = False

        self._setup_ui()
        self._setup_menu()
        self._connect_signals()
        self._restore_sidebar_state()

        self._refresh_stats()
        audit_log("application_started", details="DroidForenX desktop launched")

        # Instant scan on startup
        self._auto_scan_devices()

    def _fit_to_screen(self) -> None:
        """Size the window to the display it actually opened on.

        The previous hardcoded setMinimumSize(1280, 800) / resize(1440, 900)
        assumed a large monitor. On a 1280x720 laptop the minimum height alone
        exceeded the screen and the default width overflowed it, so the window
        opened larger than the desktop and its right-hand content was clipped
        with no way to shrink it back.

        Clamp both the minimum and the preferred size to the available screen
        geometry. Pages carry their own scroll areas, so a smaller window is
        usable rather than broken.
        """
        pref_w, pref_h = self.PREFERRED_SIZE
        min_w, min_h = self.MIN_SIZE

        avail = None
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
        except Exception:
            # No usable screen info (headless, exotic WM) -- fall back to the
            # old behaviour rather than refusing to open.
            avail = None

        if avail is None:
            avail_w, avail_h = pref_w, pref_h
        else:
            avail_w, avail_h = avail.width(), avail.height()

        self.setMinimumSize(min(min_w, avail_w), min(min_h, avail_h))
        self.resize(min(pref_w, avail_w), min(pref_h, avail_h))

        # Centre the window in the available area so the clipped-edges look
        # does not depend on where the WM happens to place it.
        if avail is not None:
            frame = self.frameGeometry()
            frame.moveCenter(avail.center())
            self.move(frame.topLeft())

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Sidebar ──
        # Stored on self so the View menu can hide it. The navigation lives in a
        # scroll area: eleven entries plus three section headers overrun a 800 px
        # window, and the layout used to pay for that by squeezing the device
        # footer until its text was sliced in half at the bottom edge.
        self.sidebar = sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # Logo
        # The padding belongs to the frame alone. A selector-less rule is
        # inherited by every child, and #sidebar QLabel already carries the
        # section-header padding, so the two labels ended up with 32 px of
        # vertical padding inside a 34 px box: a 2 px text slot, and the
        # product name rendered as slivers. Padding declared on the labels
        # themselves outranks both sources.
        logo_frame = QWidget()
        logo_frame.setObjectName("logoFrame")
        logo_frame.setStyleSheet("#logoFrame { padding: 16px; border-bottom: 1px solid #1e293b; }")
        logo_layout = QVBoxLayout(logo_frame)
        logo_layout.setContentsMargins(16, 14, 16, 12)
        logo_layout.setSpacing(4)

        # The sidebar is 220 px wide with 16 px margins, so the label has 188 px
        # to work with; 168 plus the plate's 9 px padding on each side leaves the
        # artwork a little air and keeps the label from forcing the column wider.
        logo_pixmap = branding.logo(168)
        if logo_pixmap is not None:
            self.logo_label = QLabel()
            self.logo_label.setObjectName("logoMark")
            self.logo_label.setPixmap(logo_pixmap)
            # The artwork is light lettering on transparency, so it needs a dark
            # surface to sit on. A plate behind it reads as a brand badge in both
            # themes, and keeps the pale cyan legible on the light theme's white
            # sidebar, where it would otherwise wash out.
            self.logo_label.setStyleSheet(
                "border: none; padding: 7px 9px; border-radius: 8px;"
                " background: #0b1220;")
            logo_layout.addWidget(self.logo_label)
        else:
            # Text fallback: a missing PNG is a packaging fault, not a reason to
            # ship a sidebar with a hole in it.
            logo_text = QLabel("DROIDFORENX")
            logo_text.setStyleSheet(
                "font-size: 16px; font-weight: 800; color: #f59e0b; letter-spacing: 2px; "
                "border: none; padding: 0; background: transparent;")
            logo_layout.addWidget(logo_text)

        version = QLabel(f"v{_app_version()} Professional")
        version.setStyleSheet(
            "font-size: 10px; color: #475569; letter-spacing: 1px; "
            "border: none; padding: 0; background: transparent;")
        logo_layout.addWidget(version)

        sidebar_layout.addWidget(logo_frame)

        # Nav sections
        nav_items = [
            ("WORKFLOW", [
                ("  Dashboard", 0),
                ("  Case Management", 1),
                ("  Device Connection", 2),
            ]),
            ("FORENSIC STAGES", [
                ("  1. Seizure && Isolation", 3),
                ("  2. Identification", 4),
                ("  3. Acquisition", 5),
                ("  4. Examination", 6),
                ("  5. Reporting", 7),
            ]),
            ("INVESTIGATION", [
                ("  File Browser", 8),
                ("  Artifact Viewer", 9),
                ("  SIM Forensics", 10),
            ]),
        ]

        self.nav_scroll = QScrollArea()
        self.nav_scroll.setObjectName("navScroll")
        self.nav_scroll.setWidgetResizable(True)
        self.nav_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.nav_scroll.setStyleSheet(
            "QScrollArea#navScroll { background: transparent; border: none; }"
            "QScrollArea#navScroll > QWidget > QWidget { background: transparent; }")

        nav_host = QWidget()
        nav_host.setObjectName("navHost")
        nav_layout = QVBoxLayout(nav_host)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)

        self.nav_buttons = []
        for section_title, items in nav_items:
            section_label = QLabel(section_title)
            nav_layout.addWidget(section_label)
            for text, index in items:
                btn = QPushButton(text)
                btn.setCheckable(True)
                btn.clicked.connect(lambda checked, idx=index: self._navigate_to(idx))
                nav_layout.addWidget(btn)
                self.nav_buttons.append((btn, index))

        nav_layout.addStretch()
        self.nav_scroll.setWidget(nav_host)
        sidebar_layout.addWidget(self.nav_scroll, 1)

        # Device status footer
        # Same trap as the logo: "QFrame { padding: 12px; }" also matches the
        # QLabel children, because QLabel derives from QFrame. Combined with the
        # #sidebar QLabel header padding that is 44 px on a 24 px label, so the
        # status dot and the device name painted into nothing at all.
        self.device_footer = QFrame()
        self.device_footer.setObjectName("deviceFooter")
        self.device_footer.setStyleSheet("#deviceFooter { border-top: 1px solid #1e293b; padding: 12px; }")
        footer_layout = QVBoxLayout(self.device_footer)
        footer_layout.setSpacing(4)
        self.footer_icon = QLabel("●")
        self.footer_icon.setStyleSheet(
            "font-size: 10px; color: #ef4444; border: none; padding: 0; background: transparent;")
        footer_layout.addWidget(self.footer_icon)
        self.footer_status = QLabel("No device connected")
        self.footer_status.setStyleSheet(
            "font-size: 11px; color: #64748b; border: none; padding: 0; background: transparent;")
        footer_layout.addWidget(self.footer_status)
        self.footer_model = _ElidedLabel("")
        self.footer_model.setStyleSheet(
            "font-size: 10px; color: #475569; font-family: 'Cascadia Code', monospace; "
            "border: none; padding: 0; background: transparent;")
        footer_layout.addWidget(self.footer_model)
        sidebar_layout.addWidget(self.device_footer)

        main_layout.addWidget(sidebar)

        # A 12 px grip between the column and the content is the one element
        # that stays on screen in both states, so folding the sidebar away can
        # never leave the operator without a way to bring it back.
        self.sidebar_grip = QToolButton()
        self.sidebar_grip.setObjectName("sidebarGrip")
        self.sidebar_grip.setText("\u2039")
        self.sidebar_grip.setToolTip("Hide the sidebar (Ctrl+B)")
        self.sidebar_grip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sidebar_grip.setFixedWidth(12)
        self.sidebar_grip.setSizePolicy(QSizePolicy.Policy.Fixed,
                                        QSizePolicy.Policy.Expanding)
        self.sidebar_grip.clicked.connect(self._toggle_sidebar)
        main_layout.addWidget(self.sidebar_grip)

        # ── Content Area ──
        self.stack = QStackedWidget()

        # Initialize pages
        self.home_page = HomePage()
        self.case_page = CasePage(get_db)
        self.device_page = DevicePage()
        self.seizure_page = SeizurePage(get_db)
        self.identify_page = IdentifyPage(get_db)
        self.acquire_page = AcquirePage(get_db)
        self.examine_page = ExaminePage()
        self.report_page = ReportPage(get_db)
        self.browse_page = BrowsePage()
        self.artifacts_page = ArtifactsPage()
        self.sim_page = SimForensicsPage()

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.case_page)
        self.stack.addWidget(self.device_page)
        self.stack.addWidget(self.seizure_page)
        self.stack.addWidget(self.identify_page)
        self.stack.addWidget(self.acquire_page)
        self.stack.addWidget(self.examine_page)
        self.stack.addWidget(self.report_page)
        self.stack.addWidget(self.browse_page)
        self.stack.addWidget(self.artifacts_page)
        self.stack.addWidget(self.sim_page)

        main_layout.addWidget(self.stack)

        # Status bar
        self.statusBar().setStyleSheet("background: #111827; color: #64748b; border-top: 1px solid #1e293b; font-size: 11px;")
        self.statusBar().showMessage(f"Ready — DroidForenX v{_app_version()} Professional")

        self._navigate_to(0)

    def _setup_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar { background: #111827; color: #e2e8f0; border-bottom: 1px solid #1e293b; padding: 2px; }
            QMenuBar::item { padding: 6px 12px; }
            QMenuBar::item:selected { background: #1e293b; }
            QMenu { background: #1a2234; border: 1px solid #1e293b; color: #e2e8f0; }
            QMenu::item { padding: 8px 30px; }
            QMenu::item:selected { background: #2563eb; color: #ffffff; }
        """)

        file_menu = menubar.addMenu("File")
        new_case = QAction("New Case", self)
        new_case.setShortcut("Ctrl+N")
        new_case.triggered.connect(lambda: self._navigate_to(1))
        file_menu.addAction(new_case)

        open_case = QAction("Open Case...", self)
        open_case.setShortcut("Ctrl+O")
        open_case.triggered.connect(lambda: self._navigate_to(1))
        file_menu.addAction(open_case)

        file_menu.addSeparator()

        export_action = QAction("Export Report...", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(lambda: self._navigate_to(7))
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menubar.addMenu("Tools")

        scan_action = QAction("Scan for Devices", self)
        scan_action.setShortcut("Ctrl+D")
        scan_action.triggered.connect(self._auto_scan_devices)
        tools_menu.addAction(scan_action)

        isolate_action = QAction("Isolate Device", self)
        isolate_action.triggered.connect(lambda: self._navigate_to(3))
        tools_menu.addAction(isolate_action)

        view_menu = menubar.addMenu("View")

        # Theme choice. "Follow System" is a real mode, not a convenience that
        # resolves once at startup: Qt reports colour-scheme changes while the
        # application is running, and the manager re-paints on that signal.
        self._theme_actions = {}
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for key, label, shortcut in (
            ("dark", "Dark Theme", "Ctrl+1"),
            ("light", "Light Theme", "Ctrl+2"),
            ("system", "Follow System Theme", "Ctrl+3"),
        ):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(theme.mode == key)
            action.setShortcut(shortcut)
            action.triggered.connect(lambda _checked=False, mode=key: theme.set_mode(mode))
            theme_group.addAction(action)
            view_menu.addAction(action)
            self._theme_actions[key] = action

        theme.changed.connect(self._sync_theme_actions)

        view_menu.addSeparator()

        # The sidebar is 220 px of navigation over a workflow the examiner
        # already knows after a day with the tool. Being able to fold it away is
        # the difference between a 1366-wide laptop that shows a table and one
        # that shows half of it.
        self.sidebar_action = QAction("Sidebar", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(True)
        self.sidebar_action.setShortcut("Ctrl+B")
        self.sidebar_action.setStatusTip("Show or hide the navigation sidebar")
        self.sidebar_action.toggled.connect(self._set_sidebar_visible)
        view_menu.addAction(self.sidebar_action)

        help_menu = menubar.addMenu("Help")
        about_action = QAction("About DroidForenX", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _sync_theme_actions(self, _resolved: str = "") -> None:
        """Keep the menu ticked on the selected mode.

        Deliberately keyed off ``theme.mode`` and not the resolved value, so
        System stays ticked while it happens to look dark.
        """
        for key, action in getattr(self, "_theme_actions", {}).items():
            action.setChecked(theme.mode == key)

    # -- sidebar ------------------------------------------------------------

    def _set_sidebar_visible(self, visible: bool = True) -> None:
        """Show or hide the navigation column, and remember the choice.

        ``blockSignals`` around the checkbox keeps the menu tick and the button
        that called this from chasing each other, and the tick is always derived
        from the column's real state rather than from whichever control was
        clicked.
        """
        visible = bool(visible)
        self.sidebar.setVisible(visible)

        action = getattr(self, "sidebar_action", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)
            action.setText("Sidebar" if visible else "Sidebar  (hidden)")

        toggle = getattr(self, "sidebar_grip", None)
        if toggle is not None:
            # The arrow always points at where the column will go.
            toggle.setText("\u2039" if visible else "\u203a")
            toggle.setToolTip("Hide the sidebar (Ctrl+B)" if visible
                              else "Show the sidebar (Ctrl+B)")

        settings = self._ui_settings()
        if settings is not None:
            try:
                settings.setValue("ui/sidebar_visible", visible)
                # Flush now: the value is a UI preference, and losing it because
                # the process was killed before QSettings' own flush would be a
                # silly way to reopen with the wrong layout.
                settings.sync()
                if settings.status() != QSettings.Status.NoError:
                    # The store went away underneath us (a cleared key, a policy
                    # change). Drop the handle so the next call builds a fresh
                    # one instead of failing silently forever.
                    self._settings = None
            except Exception:
                pass

        # Without this line a hidden sidebar looks like a bug: there is no
        # button left on screen to bring it back, only a menu the operator has
        # no reason to open.
        if not visible:
            self.statusBar().showMessage(
                "Sidebar hidden \u2014 Ctrl+B, or View \u25b8 Sidebar, to bring it back.",
                6000)

    def _toggle_sidebar(self) -> None:
        self._set_sidebar_visible(not self.sidebar.isVisible())

    #: Complete style strings, not fragments. ``setStyleSheet`` *replaces* the
    #: previous rule set, so the trimmed versions used to silently drop
    #: ``padding: 0`` and let the labels fall back to ``#sidebar QLabel``'s
    #: 16 px/4 px header padding -- a 31 px box for an 11 px line, which is what
    #: pushed the footer out of the sidebar and sliced the text in half.
    _FOOTER_DOT_STYLE = ("font-size: 10px; border: none; padding: 0; "
                         "background: transparent; color: {color};")
    _FOOTER_TEXT_STYLE = ("font-size: 11px; border: none; padding: 0; "
                          "background: transparent; color: {color};")

    def _set_footer_state(self, connected: bool, detail: str = "") -> None:
        self.footer_icon.setStyleSheet(
            self._FOOTER_DOT_STYLE.format(color="#10b981" if connected else "#ef4444"))
        self.footer_status.setStyleSheet(
            self._FOOTER_TEXT_STYLE.format(color="#10b981" if connected else "#64748b"))
        self.footer_status.setText("Connected" if connected else "No device connected")
        self.footer_model.set_full_text(detail)

    def _ui_settings(self):
        """``QSettings``, or ``None`` when the platform offers no store.

        Created lazily so a headless test or a locked-down profile cannot stop
        the window from opening.
        """
        if self._settings is None:
            try:
                self._settings = QSettings()
            except Exception:
                self._settings = False
        return self._settings or None

    def _restore_sidebar_state(self) -> None:
        """Apply the remembered visibility, defaulting to shown once."""
        if self._sidebar_restored:
            return
        self._sidebar_restored = True
        settings = self._ui_settings()
        visible = True
        if settings is not None:
            try:
                visible = settings.value("ui/sidebar_visible", True, type=bool)
            except Exception:
                visible = True
        if not visible:
            self._set_sidebar_visible(False)

    def _connect_signals(self):
        self.home_page.new_case_requested.connect(lambda: self._navigate_to(1))
        self.home_page.navigate.connect(self._navigate_by_name)

        self.case_page.case_selected.connect(self._on_case_selected)
        self.case_page.navigate.connect(self._navigate_by_name)

        self.device_page.device_connected.connect(self._on_device_connected)
        self.device_page.navigate.connect(self._navigate_by_name)

        self.seizure_page.session_created.connect(self._on_session_created)
        self.seizure_page.navigate.connect(self._navigate_by_name)

        self.identify_page.navigate.connect(self._navigate_by_name)
        self.acquire_page.navigate.connect(self._navigate_by_name)
        self.examine_page.navigate.connect(self._navigate_by_name)
        self.report_page.navigate.connect(self._navigate_by_name)

    def _navigate_to(self, index: int):
        self.stack.setCurrentIndex(index)
        for btn, idx in self.nav_buttons:
            btn.setChecked(idx == index)

        if index == 0:
            self._refresh_stats()

        page_names = {0: "Dashboard", 1: "Case Management", 2: "Device Connection",
                      3: "Seizure & Isolation", 4: "Identification", 5: "Acquisition",
                      6: "Examination", 7: "Reporting", 8: "File Browser", 9: "Artifact Viewer",
                      10: "SIM Forensics"}
        self.statusBar().showMessage(f"DroidForenX — {page_names.get(index, 'Unknown')}")

    def _navigate_by_name(self, name: str):
        mapping = {"home": 0, "case": 1, "device": 2, "seizure": 3, "identify": 4,
                    "acquire": 5, "examine": 6, "report": 7, "browse": 8, "artifacts": 9,
                    "sim": 10}
        self._navigate_to(mapping.get(name, 0))

    def _auto_scan_devices(self):
        """Instant synchronous scan — push device state to all pages."""
        from .widgets.device_status_bar import _scan_devices_cached
        devices = _scan_devices_cached()
        self._on_auto_scan_done(devices)

    def _on_auto_scan_done(self, devices):
        if devices:
            dev = devices[0]
            serial = dev.get("serial", "")
            model = dev.get("model", "") or dev.get("device", "") or "Unknown"
            if serial != self._device_serial:
                self._device_serial = serial
                try:
                    self._device_info = get_device_info(serial)
                except Exception:
                    self._device_info = None
                self._set_footer_state(True, f"{model} ({serial})")
                self.statusBar().showMessage(f"Device: {model} ({serial})")
                audit_log("device_connected", target=serial, details=model)
        else:
            if self._device_serial:
                self._device_serial = ""
                self._device_info = None
                self._set_footer_state(False)

        # Push device state to all pages
        self._push_device_to_pages()

    def _push_device_to_pages(self):
        """Push current device serial to all forensic pages."""
        s = self._device_serial
        self.seizure_page.set_device(s)
        self.identify_page.set_device(s)
        self.acquire_page.set_device(s, self._current_case_id, self._current_session_id)
        self.examine_page.set_device(s)
        self.artifacts_page.set_session(self._current_session_id)
        self.sim_page.set_device(s)
        self.sim_page.set_session(self._current_session_id)

    def _on_device_connected(self, device_info: dict):
        self._device_serial = device_info["serial"]
        model = device_info.get("model", "Unknown")
        self._set_footer_state(True, f"{model} ({self._device_serial})")
        self.statusBar().showMessage(f"Device connected: {model} ({self._device_serial})")
        audit_log("device_connected", target=self._device_serial, details=model)
        self._push_device_to_pages()

    def _on_case_selected(self, case_id: str):
        self._current_case_id = case_id
        conn = get_db()
        case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        conn.close()
        if case:
            self.acquire_page.set_device(self._device_serial, case_id, self._current_session_id)
            self.report_page.set_session(self._current_session_id, case["case_number"])

    def _on_session_created(self, session_id: str):
        self._current_session_id = session_id
        self.report_page.set_session(session_id)
        # The artifact viewer writes findings into the active session; hand it
        # the new id as soon as the session exists, not only on next connect.
        self.artifacts_page.set_session(session_id)
        self.sim_page.set_session(session_id)

    def _refresh_stats(self):
        from ..core.adb_manager import list_devices
        conn = get_db()
        cases = conn.execute("SELECT COUNT(*) FROM cases WHERE status='open'").fetchone()[0]
        reports = conn.execute("SELECT COUNT(*) FROM forensic_reports").fetchone()[0]
        artifacts = 0
        if self._current_case_id:
            artifacts = conn.execute(
                "SELECT COUNT(*) FROM evidence_artifacts WHERE session_id IN "
                "(SELECT id FROM forensic_sessions WHERE case_id=?)",
                (self._current_case_id,)).fetchone()[0]
        conn.close()
        devices = len(list_devices())
        self.home_page.refresh(case_count=cases, device_count=devices, artifact_count=artifacts, report_count=reports)

    def _close_stale_cases(self):
        """Retired.

        This used to run on every launch and, despite its name, deleted every
        artifact, examination result, report, isolation action, acquisition
        profile and session in the database, then marked all open cases closed.
        The evidence files on disk survived; the analytical record did not.

        It shipped in v2.0 because a clean slate is convenient while developing,
        but a forensic tool must never discard the record because the program
        was restarted, and anything left open by a crash is exactly what an
        examiner needs to see. Startup now touches nothing.
        """
        return None

    def _show_about(self):
        text = (
            f"<h2>DroidForenX v{_app_version()}</h2>"
            "<p>Professional Digital Forensics</p>"
            "<p>Standalone mobile device forensic tool for law enforcement "
            "and forensic investigators.</p>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>5-Stage Forensic Workflow</li>"
            "<li>Logical, File System & Physical Acquisition</li>"
            "<li>11+ Examination Modules</li>"
            "<li>Court-Ready PDF/HTML Reporting</li>"
            "<li>SIM Card Forensics</li>"
            "<li>Chain of Custody Documentation</li>"
            "<li>Device Isolation & Evidence Preservation</li>"
            "</ul>"
            "<p>© 2026 DroidForenX</p>")
        mark = branding.pixmap("icon.png")
        if mark is None:
            QMessageBox.about(self, "About DroidForenX", text)
            return
        QMessageBox.about(self, "About DroidForenX", text,
                          mark.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))

