"""The artifact workspace skeleton: structure tree, inspector, landing card.

The viewer's value is in its reading order: where the evidence lives, what a
record says, what it is. These tests pin the parts an examiner sees before any
data arrives - the landing card that replaces an empty centre pane, the tree's
named collapse control, the inspector's explainer - and the light-mode repaint
that a hand-painted brush needs, since item colours are not stylesheets and the
theme manager cannot translate them after the fact.

Run with:  python -m unittest desktop.tests.test_workspace_layout -v
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QFrame, QPushButton

    from desktop.ui.theme import SWATCH, theme
    from desktop.ui.widgets.structure_tree import COL_COUNT, COL_NAME, StructureTree
    from desktop.ui.widgets.properties_pane import DEFAULT_EMPTY, PropertiesPane
    from desktop.ui.pages.artifacts_page import ArtifactsPage
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False


def _make_qapp():
    return QApplication.instance() or QApplication([])


def _pump(app, rounds=3):
    for _ in range(rounds):
        app.processEvents()


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class StructureTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.tree = StructureTree()
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.tree.deleteLater()
        _pump(cls._app)

    def tearDown(self):
        theme.set_mode("dark")
        _pump(self._app)

    def test_the_collapse_control_says_what_it_does(self):
        # It used to be a bare en dash, which read as a broken minus sign.
        self.assertEqual(self.tree.collapse_btn.text(), "Collapse")
        self.assertTrue(self.tree.collapse_btn.toolTip())

    def test_counts_land_on_the_leaf_and_in_the_footer(self):
        self.tree.set_counts({"SMS": 12, "Calls": 3, "WhatsApp": 0})
        self.assertEqual(self.tree._leaves["SMS"].text(COL_COUNT), "12")
        self.assertEqual(self.tree._leaves["Calls"].text(COL_COUNT), "3")
        self.assertEqual(self.tree._leaves["WhatsApp"].text(COL_COUNT), "0")
        footer = self.tree.footer.text()
        self.assertIn("15 items", footer)
        self.assertIn("2 of", footer)

    def test_an_empty_count_at_a_section_still_adds_up(self):
        self.tree.set_counts({})
        self.assertIn("No artifact loaded", self.tree.footer.text())

    def test_choosing_a_leaf_emits_its_category(self):
        chosen = []
        self.tree.category_selected.connect(chosen.append)
        self.tree._on_item_clicked(self.tree._leaves["SMS"])
        self.assertEqual(chosen, ["SMS"])

    def test_a_light_switch_repaints_the_count_brush(self):
        self.tree.set_counts({"SMS": 12})
        leaf = self.tree._leaves["SMS"]
        dark = leaf.foreground(COL_COUNT).color().name()
        theme.set_mode("light")
        _pump(self._app)
        light = leaf.foreground(COL_COUNT).color().name()
        theme.set_mode("dark")
        self.assertNotEqual(light, dark)
        self.assertEqual(light, SWATCH["#60a5fa"].lower())


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class PropertiesPaneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.pane = PropertiesPane()
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.pane.deleteLater()
        _pump(cls._app)

    def setUp(self):
        self.pane.clear()
        _pump(self._app)

    def tearDown(self):
        theme.set_mode("dark")
        _pump(self._app)

    def test_it_opens_on_an_explainer_not_a_void(self):
        self.assertEqual(self.pane.stack.currentIndex(), 0)
        self.assertEqual(self.pane.empty_body.text(), DEFAULT_EMPTY)

    def test_a_category_summary_shows_its_count_in_the_header(self):
        self.pane.show_summary("WhatsApp", "WhatsApp", 12, media=3)
        _pump(self._app)
        self.assertEqual(self.pane.stack.currentIndex(), 1)
        self.assertEqual(self.pane.header_note.text(), "12 items")
        self.assertGreaterEqual(self.pane.table.topLevelItemCount(), 1)

    def test_an_item_reports_how_many_fields_it_carries(self):
        self.pane.show_item({
            "type": "Message", "category": "SMS", "summary": "Hello",
            "_data": {"address": "+233 20 123 4567", "body": "Hello"},
        })
        _pump(self._app)
        self.assertEqual(self.pane.stack.currentIndex(), 1)
        self.assertEqual(self.pane.header_note.text(), "5 fields")
        text = self.pane.table.topLevelItem(0).child(0).text(0)
        self.assertEqual(text, "Type")

    def test_a_record_without_metadata_says_so(self):
        self.pane.show_item({"type": "", "_data": {}})
        _pump(self._app)
        self.assertEqual(self.pane.header_note.text(), "No metadata")

    def test_clear_names_the_next_step(self):
        self.pane.clear("Select a category in the structure, then an item.")
        _pump(self._app)
        self.assertEqual(self.pane.stack.currentIndex(), 0)
        self.assertEqual(self.pane.empty_body.text(),
                         "Select a category in the structure, then an item.")
        self.assertEqual(self.pane.header_note.text(), "")

    def test_a_light_switch_repaints_a_value_brush(self):
        self.pane.show_item({"type": "Contact", "_data": {"name": "Kofi"}})
        _pump(self._app)
        row = self.pane.table.topLevelItem(0).child(0)
        dark = row.foreground(1).color().name()
        theme.set_mode("light")
        _pump(self._app)
        row = self.pane.table.topLevelItem(0).child(0)
        light = row.foreground(1).color().name()
        theme.set_mode("dark")
        self.assertNotEqual(light, dark)
        self.assertEqual(light, SWATCH["#e2e8f0"].lower())


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class OverviewCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = ArtifactsPage()
        cls.page.resize(1200, 760)
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.page.deleteLater()
        _pump(cls._app)

    def setUp(self):
        self.page.all_artifacts = []
        self.page.stacked.setCurrentIndex(0)
        _pump(self._app)

    def test_the_landing_pane_is_a_card_not_two_paragraphs(self):
        landing = self.page.stacked.widget(0)
        self.assertIsNotNone(landing.findChild(QFrame, "overviewCard"))

    def test_the_card_offers_the_categories_most_cases_start_with(self):
        labels = {button.text() for button in
                  self.page.stacked.widget(0).findChildren(QPushButton)}
        for label in ("WhatsApp", "Text Messages", "Call Log", "Media",
                      "Documents", "Contacts"):
            self.assertIn(label, labels)

    def test_a_shortcut_opens_the_category_it_names(self):
        buttons = {button.text(): button for button in
                   self.page.stacked.widget(0).findChildren(QPushButton)}
        buttons["Call Log"].click()
        _pump(self._app)
        self.assertEqual(self.page.stacked.currentIndex(), 1)
        self.assertEqual(self.page._current_app["category"], "Calls")


if __name__ == "__main__":
    unittest.main()
