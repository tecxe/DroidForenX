"""The device card must be a card, not three labels wearing a border.

The card's style sheet was ``QFrame { ... padding: 16px; }``. A widget style
sheet applies to every descendant the selector matches, and ``QLabel`` derives
from ``QFrame``, so the model name and the serial number each carried 16 px of
padding as well: a one-line card became a 150 px box with the text floating in
the middle of it.

The test builds the card the page builds, and measures it.

Run with:  python -m unittest desktop.tests.test_device_page -v
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QFrame, QLabel

    from desktop.ui.pages.device_page import DevicePage
    from desktop.ui.theme import MAIN_STYLESHEET
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False


DETECTED = {
    "serial": "13276374A7004073",
    "model": "TECNO_KL6",
    "device": "TECNO-KL6",
}


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


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class DeviceCardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = DevicePage()
        cls.page.setStyleSheet(MAIN_STYLESHEET)
        cls.page.resize(1100, 700)
        cls.page.show()
        _pump(cls._app)
        cls.page._on_devices_found([DETECTED])
        _pump(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.page.deleteLater()
        _pump(cls._app)

    def _card(self):
        layout = self.page.device_grid
        self.assertGreater(layout.count(), 0, "no card was built for the device")
        card = layout.itemAt(0).widget()
        self.assertIsInstance(card, QFrame)
        return card

    def test_the_card_is_scoped_to_its_own_object_name(self):
        sheet = self._card().styleSheet()
        self.assertEqual(self._card().objectName(), "deviceCard")
        self.assertIn("QFrame#deviceCard", sheet)
        self.assertNotIn("QFrame {", sheet,
                         "a bare type selector will style the labels inside too")

    def test_the_card_is_one_line_tall(self):
        # Name, serial and the button row, plus 12 px of margin top and bottom.
        self.assertLess(self._card().height(), 110,
                        "the card is carrying the padding of its own labels")

    def test_the_text_inside_keeps_its_own_box(self):
        for label in self._card().findChildren(QLabel):
            if not label.text():
                continue
            self.assertGreaterEqual(label.contentsRect().height(),
                                    label.fontMetrics().height(),
                                    f"{label.text()!r} has no room for its glyphs")
            self.assertEqual(label.frameWidth(), 0,
                             f"{label.text()!r} grew a frame from the card's border")


if __name__ == "__main__":
    unittest.main()
