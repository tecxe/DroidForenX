"""Theming for the desktop UI.

There are two layers here, and they solve different problems.

The *global* stylesheet is built from ``COLORS`` and describes chrome: the
window, sidebar, tables, buttons, inputs, tabs. It is authored once, in the
dark palette, and translated at apply time.

The *inline* styles are the ~150 ``setStyleSheet`` calls scattered across the
pages, each carrying literal hex values with no token discipline. Rather than
rewrite six hundred literals by hand -- and inevitably miss some -- the widget
setter is interposed once: every stylesheet a widget receives is recorded in
its original (dark) form and the applied form is passed through ``map_qss``.
Switching theme re-applies the recorded originals, so a widget can be themed
long after it was built, and a widget built later is themed on arrival.

Colours that are not in ``SWATCH`` pass through untouched. That is deliberate:
brand marks (WhatsApp green, Telegram blue, TikTok red) and the printed
report stylesheet must not be recoloured by a UI preference.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import QObject, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

#: Dark palette. The product's existing look; every value here is load-bearing
#: and was in the shipping build before the theme switch existed.
COLORS = {
    "bg_dark": "#0a0e17",
    "bg_card": "#111827",
    "bg_panel": "#1a2234",
    "bg_sunken": "#16202f",
    "bg_hover": "#1e293b",
    "bg_input": "#0f172a",
    "border": "#1e293b",
    "border_light": "#334155",
    "text": "#e2e8f0",
    "text_muted": "#64748b",
    "text_dim": "#475569",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_light": "#3b82f6",
    "accent": "#f59e0b",
    "accent_hover": "#d97706",
    "success": "#10b981",
    "success_bg": "rgba(16,185,129,0.12)",
    "danger": "#ef4444",
    "danger_bg": "rgba(239,68,68,0.12)",
    "warning": "#f59e0b",
    "warning_bg": "rgba(245,158,11,0.12)",
    "info": "#06b6d4",
    "info_bg": "rgba(6,182,212,0.12)",
    "stage_1": "#8b5cf6",
    "stage_2": "#3b82f6",
    "stage_3": "#10b981",
    "stage_4": "#f59e0b",
    "stage_5": "#ef4444",
}

#: Light palette, same semantic roles, designed rather than inverted. Surfaces
#: rise towards white as they gain prominence, which is the opposite direction
#: from the dark palette -- a card is #fff on a #eef2f8 page, not grey on grey.
LIGHT = {
    "bg_dark": "#eef2f8",
    "bg_card": "#ffffff",
    "bg_panel": "#ffffff",
    "bg_sunken": "#eef2f7",
    "bg_hover": "#e4eaf2",
    "bg_input": "#f3f6fa",
    "border": "#e2e8f0",
    "border_light": "#cbd5e1",
    "text": "#0f172a",
    "text_muted": "#55657a",
    "text_dim": "#94a3b8",
    "primary": "#2563eb",
    "primary_hover": "#1e40af",
    "primary_light": "#2563eb",
    "accent": "#d97706",
    "accent_hover": "#b45309",
    "success": "#059669",
    "success_bg": "rgba(5,150,105,0.10)",
    "danger": "#dc2626",
    "danger_bg": "rgba(220,38,38,0.09)",
    "warning": "#d97706",
    "warning_bg": "rgba(217,119,6,0.10)",
    "info": "#0891b2",
    "info_bg": "rgba(8,145,178,0.10)",
    "stage_1": "#7c3aed",
    "stage_2": "#2563eb",
    "stage_3": "#059669",
    "stage_4": "#d97706",
    "stage_5": "#dc2626",
}

PALETTES = {"dark": COLORS, "light": LIGHT}

STAGE_COLORS = [COLORS["stage_1"], COLORS["stage_2"], COLORS["stage_3"], COLORS["stage_4"], COLORS["stage_5"]]
STAGE_NAMES = ["Seizure & Isolation", "Identification", "Acquisition", "Examination", "Reporting"]

MAIN_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['text']};
    font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
    font-size: 13px;
}}

QMainWindow::separator {{
    background: {COLORS['border']};
    width: 1px;
}}

/* ── Sidebar ── */
#sidebar {{
    background-color: {COLORS['bg_card']};
    border-right: 1px solid {COLORS['border']};
    min-width: 220px;
    max-width: 220px;
}}
#sidebar QLabel {{
    color: {COLORS['text_muted']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 16px 16px 4px 16px;
    text-transform: uppercase;
}}
#sidebar QPushButton {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    color: {COLORS['text_muted']};
    font-size: 13px;
    font-weight: 500;
    padding: 10px 16px;
    text-align: left;
}}
#sidebar QPushButton:hover {{
    background: {COLORS['bg_hover']};
    color: {COLORS['text']};
}}
#sidebar QPushButton:checked {{
    background: rgba(245,158,11,0.08);
    color: {COLORS['accent']};
    border-left-color: {COLORS['accent']};
}}
/* The navigation column scrolls when the window is short, so the device footer
   is never squeezed to a sliced line at the bottom edge. */
#navScroll {{
    background: transparent;
    border: none;
}}
/* The grip that folds the column away. Deliberately colourless so it reads as
   part of the divider rather than as a control competing with the navigation. */
#sidebarGrip {{
    background: {COLORS['bg_card']};
    border: none;
    color: {COLORS['text_muted']};
    font-size: 12px;
    padding: 0;
}}
#sidebarGrip:hover {{
    background: {COLORS['bg_hover']};
    color: {COLORS['accent']};
}}
/* Footer labels must never inherit the section-header padding. At 16 px top and
   4 px bottom an 11 px line becomes a 31 px box, which is what pushed the device
   status past the bottom edge and sliced the glyphs in half. */
#deviceFooter QLabel {{
    padding: 0;
}}

/* ── Cards ── */
.card {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 20px;
}}
.card-header {{
    font-size: 16px;
    font-weight: 700;
    color: {COLORS['text']};
    padding-bottom: 12px;
    border-bottom: 1px solid {COLORS['border']};
    margin-bottom: 16px;
}}

/* ── Stat Cards ── */
.stat-card {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 16px;
}}
.stat-value {{
    font-size: 28px;
    font-weight: 700;
    color: {COLORS['text']};
}}
.stat-label {{
    font-size: 11px;
    font-weight: 600;
    color: {COLORS['text_muted']};
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 4px;
}}

/* ── Tables ── */
QTableWidget {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    gridline-color: {COLORS['border']};
    selection-background-color: rgba(37,99,235,0.2);
    selection-color: {COLORS['text']};
    font-size: 12px;
    alternate-background-color: {COLORS['bg_sunken']};
}}
QTableWidget::item {{
    padding: 8px 12px;
    border-bottom: 1px solid {COLORS['border']};
}}
QTableWidget::item:selected {{
    background: rgba(37,99,235,0.15);
}}
QHeaderView::section {{
    background: {COLORS['bg_sunken']};
    color: {COLORS['text_muted']};
    border: none;
    border-bottom: 2px solid {COLORS['border']};
    border-right: 1px solid {COLORS['border']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    padding: 10px 12px;
}}

/* ── Buttons ── */
QPushButton {{
    background: {COLORS['primary']};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {COLORS['primary_hover']};
}}
QPushButton:pressed {{
    background: #1e40af;
}}
QPushButton:disabled {{
    background: {COLORS['bg_hover']};
    color: {COLORS['text_dim']};
}}
#btn-danger {{
    background: {COLORS['danger']};
}}
#btn-danger:hover {{
    background: #dc2626;
}}
#btn-accent {{
    background: {COLORS['accent']};
    color: #000;
}}
#btn-accent:hover {{
    background: {COLORS['accent_hover']};
}}
#btn-success {{
    background: {COLORS['success']};
}}
#btn-success:hover {{
    background: #059669;
}}
#btn-outline {{
    background: transparent;
    border: 1px solid {COLORS['border_light']};
    color: {COLORS['text']};
}}
#btn-outline:hover {{
    background: {COLORS['bg_hover']};
    border-color: {COLORS['text_muted']};
}}

/* ── Inputs ── */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background: {COLORS['bg_input']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    color: {COLORS['text']};
    padding: 10px 12px;
    font-size: 13px;
    selection-background-color: rgba(37,99,235,0.3);
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {COLORS['primary']};
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {COLORS['text_muted']};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {COLORS['bg_panel']};
    border: 1px solid {COLORS['border']};
    color: {COLORS['text']};
    selection-background-color: {COLORS['primary']};
    outline: none;
}}

/* ── Group Box ── */
QGroupBox {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 20px;
    font-weight: 600;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 12px;
    color: {COLORS['accent']};
}}

/* ── Progress Bar ── */
QProgressBar {{
    background: {COLORS['bg_input']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['primary']}, stop:1 {COLORS['accent']});
    border-radius: 4px;
}}

/* ── ScrollBar ── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border_light']};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['text_muted']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {COLORS['border_light']};
    min-width: 30px;
    border-radius: 4px;
}}

/* ── Tab Widget ── */
QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    background: {COLORS['bg_card']};
}}
QTabBar::tab {{
    background: {COLORS['bg_sunken']};
    color: {COLORS['text_muted']};
    border: 1px solid {COLORS['border']};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background: {COLORS['bg_card']};
    color: {COLORS['accent']};
    border-bottom: 2px solid {COLORS['accent']};
}}
QTabBar::tab:hover {{
    color: {COLORS['text']};
}}

/* ── Checkboxes / radios ──
   Deliberately unstyled: the native indicator draws a real tick, and letting
   the palette drive it keeps dark mode exactly as it shipped. */
QCheckBox, QRadioButton {{
    color: {COLORS['text']};
    spacing: 8px;
}}

/* ── Labels ── */
h1 {{ font-size: 24px; font-weight: 700; color: {COLORS['text']}; }}
h2 {{ font-size: 18px; font-weight: 700; color: {COLORS['text']}; }}
h3 {{ font-size: 15px; font-weight: 600; color: {COLORS['text']}; }}
.muted {{ color: {COLORS['text_muted']}; }}
.accent {{ color: {COLORS['accent']}; }}

/* ── Tool Tips ── */
QToolTip {{
    background: {COLORS['bg_panel']};
    border: 1px solid {COLORS['border']};
    color: {COLORS['text']};
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
}}
"""

#: Dark hex -> light hex, for the inline styles that were written without
#: tokens. Single pass over the original text, so entries never chain: an
#: accent and its hover state each move one step and stay ordered.
#:
#: Anything absent is left alone on purpose. That covers brand marks
#: (#25d366 WhatsApp, #0088cc Telegram, #e4405f Instagram, #ff0050 TikTok,
#: #1877f2 Facebook, #fffc00 Snapchat, #1da1f2, #4285f4, #ff0000) and the
#: Material palette used by the printed HTML report, which is a document and
#: has no business following a UI preference.
SWATCH = {
    # surfaces, darkest to lightest
    "#080c14": "#e9eef5",
    "#0a0e17": "#eef2f8",
    "#0d1117": "#edf1f7",
    "#0f172a": "#f3f6fa",
    "#111827": "#ffffff",
    "#16202f": "#eef2f7",
    "#1a2234": "#ffffff",
    "#1a1f2e": "#f5f7fb",
    "#1e293b": "#e2e8f0",
    "#334155": "#cbd5e1",
    # tinted panels: app cards and chat surfaces keep their hue, at tint level
    "#0d1a2e": "#e8f0fa",
    "#0a1f2e": "#e7f2fa",
    "#0f1d33": "#e8f0fb",
    "#0d1f2e": "#e6f1f9",
    "#0d1f33": "#e6effa",
    "#0d262e": "#e3f5f9",
    "#0d2e15": "#e8f8ee",
    "#0d2e1c": "#e7f7ef",
    "#0d3328": "#e5f6f0",
    "#162d4a": "#dfeaf7",
    "#1a1a2e": "#efecfb",
    "#1a0d0d": "#fbeeee",
    "#1a0d2e": "#f3edfb",
    "#1e3a5f": "#dbe8f7",
    "#2e0d0d": "#fceaea",
    "#2e0d1a": "#fcebf1",
    "#2e1515": "#fbebeb",
    "#2e1a0d": "#fdf1e6",
    "#2e220d": "#fdf5e3",
    "#2e2e0d": "#faf7da",
    "#3b1515": "#f9dede",
    # text, in order of prominence
    "#e2e8f0": "#0f172a",
    "#a8b4c4": "#475569",
    "#94a3b8": "#475569",
    "#64748b": "#55657a",
    "#475569": "#94a3b8",
    # a pair of speaker labels that read distinctly from one another:
    # green for the model, both darkened until they hold on a white panel
    "#93c5fd": "#1d4ed8",
    "#34d399": "#047857",
    # accents, each darkened one step so it holds up on white
    "#2563eb": "#2563eb",  # primary already clears AA on white; kept identical
    "#3b82f6": "#2563eb",
    "#1d4ed8": "#1e40af",
    "#1e40af": "#1e3a8a",
    "#06b6d4": "#0891b2",
    "#10b981": "#059669",
    "#059669": "#047857",
    "#22c55e": "#16a34a",
    "#16a34a": "#15803d",
    "#ef4444": "#dc2626",
    "#dc2626": "#b91c1c",
    "#f59e0b": "#d97706",
    "#d97706": "#b45309",
    "#f97316": "#ea580c",
    "#8b5cf6": "#7c3aed",
    "#7c3aed": "#6d28d9",
    "#a855f7": "#9333ea",
    "#ec4899": "#db2777",
}

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")

#: Managed by ThemeManager. Read directly by map_qss so the translation works
#: even before the manager is installed (tests, or a bare widget).
_current_mode = "dark"


def map_qss(text: str, mode: str | None = None) -> str:
    """Translate a dark-authored stylesheet into the requested mode."""
    if not text:
        return text
    mode = mode or _current_mode
    if mode != "light":
        return text
    return _HEX_RE.sub(lambda m: SWATCH.get(m.group(0).lower(), m.group(0)), text)


def unmapped_colors(text: str) -> set[str]:
    """Hex values in ``text`` that have no light counterpart.

    Used by the test suite to prove the swatch table actually covers what the
    UI paints, rather than assuming it does.
    """
    return {m.group(0).lower() for m in _HEX_RE.finditer(text or "")
            if m.group(0).lower() not in SWATCH}


# ── interposition ────────────────────────────────────────────────────────────
_patched = False
_original_set = None


def _install_setter_hook() -> None:
    """Record every stylesheet and apply the translated form.

    PyQt allows replacing the class method, which is the only practical way to
    reach styles already set throughout the pages. The original text is kept on
    the widget so a theme switch can re-translate it instead of trying to
    invert an already-translated sheet.
    """
    global _patched, _original_set
    if _patched:
        return
    _original_set = QWidget.setStyleSheet

    def setStyleSheet(self, sheet):  # noqa: N802 - Qt spelling
        try:
            self._dfs_qss = sheet
        except (AttributeError, RuntimeError):
            pass
        _original_set(self, map_qss(sheet))

    QWidget.setStyleSheet = setStyleSheet
    _patched = True


_install_setter_hook()


def _source_of(widget):
    try:
        return getattr(widget, "_dfs_qss", None)
    except RuntimeError:
        return None


def _build_palette(mode: str) -> QPalette:
    c = PALETTES["light" if mode == "light" else "dark"]
    pal = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    window = QColor(c["bg_dark"])
    text = QColor(c["text"])
    pal.setColor(role.Window, window)
    pal.setColor(role.WindowText, text)
    pal.setColor(role.Base, QColor(c["bg_card"]))
    pal.setColor(role.AlternateBase, QColor(c["bg_sunken"]))
    pal.setColor(role.ToolTipBase, QColor(c["bg_panel"]))
    pal.setColor(role.ToolTipText, text)
    pal.setColor(role.Text, text)
    pal.setColor(role.Button, QColor(c["bg_card"]))
    pal.setColor(role.ButtonText, text)
    pal.setColor(role.BrightText, QColor("#ffffff"))
    pal.setColor(role.Highlight, QColor(c["primary"]))
    pal.setColor(role.HighlightedText, QColor("#ffffff"))
    pal.setColor(role.PlaceholderText, QColor(c["text_muted"]))
    pal.setColor(role.Link, QColor(c["primary_light"]))
    pal.setColor(role.Mid, QColor(c["border"]))
    pal.setColor(role.Midlight, QColor(c["border_light"]))
    pal.setColor(role.Dark, QColor(c["border_light"]))
    pal.setColor(role.Light, QColor(c["bg_panel"]))

    for disabled in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(group.Disabled, disabled, QColor(c["text_dim"]))
    return pal


def _windows_apps_use_light() -> bool | None:
    """Windows 10/11 app theme, for when Qt will not report a colour scheme.

    ``QStyleHints.colorScheme()`` returns Unknown on some platforms and in the
    offscreen mode the test suite runs under, so the registry is consulted
    before falling back to the previous look.
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
    except OSError:
        return None
    try:
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return bool(value)
    except OSError:
        return None
    finally:
        key.Close()


def system_prefers_dark() -> bool:
    """Whether the OS is currently asking for a dark UI.

    Defaults to dark when nothing can tell us, which keeps the shipping look
    for anyone on a platform that does not report a preference.
    """
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
    except Exception:
        scheme = None
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    light = _windows_apps_use_light()
    if light is None:
        return True
    return not light


class ThemeManager(QObject):
    """Owns the selected mode and re-applies it to everything alive."""

    MODES = ("dark", "light", "system")
    SETTINGS_KEY = "ui/theme_mode"

    #: Emitted with the *resolved* mode -- "dark" or "light", never "system".
    changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._mode = "dark"
        self._app = None
        self._settings = None
        self._system_watch = False

    # -- state ---------------------------------------------------------------
    @property
    def mode(self) -> str:
        """The selected mode, which may be ``system``."""
        return self._mode

    def resolved(self) -> str:
        """The mode actually being painted: dark or light."""
        if self._mode == "system":
            return "dark" if system_prefers_dark() else "light"
        return self._mode

    def is_light(self) -> bool:
        return self.resolved() == "light"

    # -- lifecycle -----------------------------------------------------------
    def install(self, app: QApplication) -> None:
        self._app = app
        try:
            self._settings = QSettings()
        except Exception:
            self._settings = None

        stored = None
        if self._settings is not None:
            stored = self._settings.value(self.SETTINGS_KEY)
        self._mode = stored if stored in self.MODES else "dark"

        if not self._system_watch:
            self._watch_system()
        self.reapply()

    def _watch_system(self) -> None:
        """Follow the OS while in system mode."""
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme)
            self._system_watch = True
        except Exception:
            self._system_watch = False

    def _on_system_scheme(self, _scheme) -> None:
        if self._mode == "system":
            self.reapply()
            self.changed.emit(self.resolved())

    # -- mutation ------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"unknown theme mode: {mode!r}")
        if mode == self._mode:
            self.reapply()
            return
        self._mode = mode
        if self._settings is not None:
            self._settings.setValue(self.SETTINGS_KEY, mode)
            self._settings.sync()
        self.reapply()
        self.changed.emit(self.resolved())

    def reapply(self) -> None:
        """Push the current mode at every widget that has a stylesheet."""
        global _current_mode
        _current_mode = self.resolved()
        app = self._app or QApplication.instance()
        if app is None:
            return
        app.setPalette(_build_palette(_current_mode))
        app.setStyleSheet(map_qss(MAIN_STYLESHEET))
        for widget in app.allWidgets():
            source = _source_of(widget)
            if source is None:
                continue
            try:
                _original_set(widget, map_qss(source))
                widget.update()
            except RuntimeError:
                continue


#: The one instance the UI talks to.
theme = ThemeManager()
