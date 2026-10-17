"""
Tests for the light/dark/system theming layer.

The interesting failure mode here is not a crash. It is a control that keeps a
hardcoded near-black surface, or white text on a page that stopped being black,
and ships looking broken. So the central test is a coverage guard: every hex
literal anywhere under ``desktop/ui`` must either have a light counterpart or
appear in the explicit allowlist of colours that must not be translated (brand
marks and the printed report stylesheet).

The rest pin the parts that are easy to break while editing the palette:
contrast, dark-mode identity, and that a theme switch re-translates the
*recorded* stylesheet rather than an already-translated one.

Run with:  python -m unittest desktop.tests.test_theme -v
"""

import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False

from desktop.ui import theme

UI_ROOT = Path(__file__).resolve().parents[1] / "ui"
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")

#: Colours that must survive a theme switch untouched.
#:
#: Two groups. Brand marks: a WhatsApp badge has to stay WhatsApp green. And
#: the palette of the printed HTML report -- that is a document a court reads,
#: not chrome, so it does not follow a UI preference.
ALLOW_UNMAPPED = {
    # brand marks (app icon badges)
    "#25d366", "#1da851", "#0088cc", "#006ba3", "#1da1f2", "#1877f2",
    "#4285f4", "#e4405f", "#ff0050", "#fffc00", "#ff0000", "#ea580c",
    # printed report document palette
    "#f8f9ff", "#e8eaf6", "#c5cae9", "#1a237e", "#283593", "#37474f",
    "#546e7a", "#e0e0e0", "#f5f5f5", "#f8f9fa", "#fff3e0", "#ff9800",
    "#4caf50", "#c62828", "#e65100",
    # literal white, used as text on coloured buttons in both modes
    "#ffffff",
}


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def _restore_dark():
    theme.theme._mode = "dark"
    theme._current_mode = "dark"


class SwatchCoverageTest(unittest.TestCase):
    """Every colour the UI paints has a defined light counterpart."""

    def test_every_ui_colour_has_a_light_counterpart(self):
        missing = {}
        for path in UI_ROOT.rglob("*.py"):
            if path.name == "theme.py":
                continue
            for match in HEX_RE.finditer(path.read_text(encoding="utf-8")):
                color = match.group(0).lower()
                if color not in theme.SWATCH and color not in ALLOW_UNMAPPED:
                    missing.setdefault(color, set()).add(path.name)

        detail = "\n".join(
            f"  {color} used in {', '.join(sorted(files))}"
            for color, files in sorted(missing.items())
        )
        self.assertEqual(
            missing, {},
            "these colours have no light counterpart and would stay dark in "
            "light mode; add them to theme.SWATCH or to ALLOW_UNMAPPED with a "
            f"reason:\n{detail}",
        )

    def test_global_stylesheet_is_fully_covered(self):
        self.assertEqual(theme.unmapped_colors(theme.MAIN_STYLESHEET), set())

    def test_swatch_values_are_valid_hex(self):
        for key, value in theme.SWATCH.items():
            self.assertRegex(key, r"^#[0-9a-f]{6}$")
            self.assertRegex(value, r"^#[0-9a-f]{6}$")

    def test_no_light_value_is_a_near_black_surface(self):
        """A translation must never produce another dark surface."""
        dark_surfaces = {"#0a0e17", "#080c14", "#0d1117", "#111827",
                         "#1a2234", "#16202f", "#1e293b", "#162d4a"}
        self.assertEqual(set(theme.SWATCH.values()) & dark_surfaces, set())


class MappingTest(unittest.TestCase):
    def test_dark_is_an_identity(self):
        """Dark mode must be byte-identical to the shipped stylesheet."""
        for key in theme.SWATCH:
            sheet = f"color: {key}; background: {key};"
            self.assertEqual(theme.map_qss(sheet, mode="dark"), sheet)

    def test_light_translates_surfaces_and_text(self):
        translated = theme.map_qss(
            "background: #0a0e17; color: #e2e8f0; border: 1px solid #1e293b",
            mode="light",
        )
        self.assertEqual(
            translated,
            "background: #eef2f8; color: #0f172a; border: 1px solid #e2e8f0",
        )

    def test_brand_marks_are_never_recoloured(self):
        for brand in ("#25d366", "#0088cc", "#e4405f", "#ff0050", "#fffc00",
                      "#1877f2", "#1da1f2", "#4285f4", "#ff0000"):
            sheet = f"color: {brand}"
            self.assertEqual(theme.map_qss(sheet, mode="light"), sheet, brand)

    def test_report_document_palette_is_untouched(self):
        """The printed report is a document, not chrome."""
        for color in ("#f8f9ff", "#e8eaf6", "#37474f", "#546e7a", "#fff3e0"):
            sheet = f"background: {color}"
            self.assertEqual(theme.map_qss(sheet, mode="light"), sheet, color)

    def test_translation_is_case_insensitive(self):
        self.assertEqual(theme.map_qss("color: #0A0E17", mode="light"),
                         "color: #eef2f8")

    def test_empty_input_is_safe(self):
        self.assertEqual(theme.map_qss("", mode="light"), "")
        self.assertEqual(theme.map_qss(None, mode="light"), None)


class ContrastTest(unittest.TestCase):
    """The light palette has to be readable, not just light."""

    def test_body_text_is_aaa_on_both_surfaces(self):
        for surface in ("bg_dark", "bg_card", "bg_input"):
            ratio = contrast(theme.LIGHT["text"], theme.LIGHT[surface])
            self.assertGreaterEqual(
                ratio, 7.0, f"body text on {surface} is {ratio:.2f}:1")

    def test_muted_text_clears_aa(self):
        for surface in ("bg_dark", "bg_card"):
            ratio = contrast(theme.LIGHT["text_muted"], theme.LIGHT[surface])
            self.assertGreaterEqual(
                ratio, 4.5, f"muted text on {surface} is {ratio:.2f}:1")

    def test_tertiary_text_is_no_worse_than_dark_mode(self):
        """Dim text is deliberately faint; it just must not regress."""
        light = contrast(theme.LIGHT["text_dim"], theme.LIGHT["bg_card"])
        dark = contrast(theme.COLORS["text_dim"], theme.COLORS["bg_card"])
        self.assertGreaterEqual(round(light, 2), round(dark, 2))

    def test_accents_are_readable_on_white(self):
        for name in ("primary", "accent", "success", "danger", "info",
                     "stage_1", "stage_2", "stage_3", "stage_4", "stage_5"):
            ratio = contrast(theme.LIGHT[name], "#ffffff")
            self.assertGreaterEqual(
                ratio, 3.0, f"{name} ({theme.LIGHT[name]}) is {ratio:.2f}:1")


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.manager = theme.ThemeManager()

    def tearDown(self):
        _restore_dark()

    def test_defaults_to_dark(self):
        self.assertEqual(self.manager.mode, "dark")
        self.assertEqual(self.manager.resolved(), "dark")
        self.assertFalse(self.manager.is_light())

    def test_switches_mode(self):
        self.manager.set_mode("light")
        self.assertEqual(self.manager.mode, "light")
        self.assertEqual(self.manager.resolved(), "light")
        self.assertTrue(self.manager.is_light())

    def test_rejects_an_unknown_mode(self):
        with self.assertRaises(ValueError):
            self.manager.set_mode("solarized")

    def test_system_mode_resolves_to_a_real_mode(self):
        self.manager.set_mode("system")
        self.assertEqual(self.manager.mode, "system")
        self.assertIn(self.manager.resolved(), ("dark", "light"))

    def test_mode_is_persisted(self):
        class FakeSettings:
            def __init__(self):
                self.values = {}

            def value(self, key, default=None):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

            def sync(self):
                pass

        settings = FakeSettings()
        self.manager._settings = settings
        self.manager.set_mode("light")
        self.assertEqual(settings.values[theme.ThemeManager.SETTINGS_KEY], "light")

    def test_changed_reports_the_resolved_mode(self):
        seen = []
        self.manager.changed.connect(seen.append)
        self.manager.set_mode("light")
        self.assertEqual(seen, ["light"])

        seen.clear()
        self.manager.set_mode("system")
        self.assertEqual(len(seen), 1)
        self.assertIn(seen[0], ("dark", "light"))


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class WidgetThemingTest(unittest.TestCase):
    """The setter hook: styles recorded once, translated on every switch."""

    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        _restore_dark()
        theme.theme._app = self._app
        self.widget = QLabel("value")
        self.source = "color: #e2e8f0; background: #111827;"
        theme.theme.set_mode("dark")
        self.widget.setStyleSheet(self.source)

    def tearDown(self):
        _restore_dark()
        theme.theme._app = None

    def test_stylesheet_is_recorded(self):
        self.assertEqual(self.widget._dfs_qss, self.source)

    def test_dark_mode_applies_the_source_unchanged(self):
        self.assertEqual(self.widget.styleSheet(), self.source)

    def test_switching_to_light_translates(self):
        theme.theme.set_mode("light")
        self.assertEqual(self.widget.styleSheet(),
                         "color: #0f172a; background: #ffffff;")

    def test_switching_back_restores_the_source(self):
        theme.theme.set_mode("light")
        theme.theme.set_mode("dark")
        self.assertEqual(self.widget.styleSheet(), self.source)
        self.assertEqual(self.widget._dfs_qss, self.source)

    def test_repeated_switches_do_not_drift(self):
        """Guards against translating an already-translated sheet.

        The swatch is not idempotent by design (#f59e0b -> #d97706 -> #b45309),
        so a naive re-apply would keep darkening on every switch. Re-applying
        the *recorded* source is what keeps this stable.
        """
        theme.theme.set_mode("light")
        first = self.widget.styleSheet()
        for _ in range(5):
            theme.theme.set_mode("dark")
            theme.theme.set_mode("light")
        self.assertEqual(self.widget.styleSheet(), first)

    def test_widget_created_after_a_switch_is_themed_immediately(self):
        theme.theme.set_mode("light")
        late = QPushButton("later")
        late.setStyleSheet("color: #94a3b8;")
        self.assertEqual(late.styleSheet(), "color: #475569;")

    def test_theme_change_reaches_widgets_already_built(self):
        theme.theme.set_mode("light")
        theme.theme.set_mode("dark")
        theme.theme.set_mode("light")
        self.assertIn("#ffffff", self.widget.styleSheet())


if __name__ == "__main__":
    unittest.main()
