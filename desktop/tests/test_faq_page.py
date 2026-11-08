"""Tests for the FAQs page: it exists, it answers, and it stays honest."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from desktop.ui.pages.faq_page import FaqPage, FAQS
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


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class FaqPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()
        cls.page = FaqPage()
        cls.page.resize(900, 700)
        cls.page.show()
        cls._app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.page.deleteLater()
        cls._app.processEvents()

    def test_the_faq_list_is_substantial(self):
        self.assertGreaterEqual(len(FAQS), 12)

    def test_every_answer_is_real_text(self):
        for question, answer in FAQS:
            self.assertTrue(question.strip())
            self.assertTrue(len(answer.strip()) > 80)

    def test_the_ai_assistant_question_is_answered(self):
        questions = [q for q, _ in FAQS]
        self.assertTrue(any("AI assistant" in q for q in questions))

    def test_android_forensics_and_developer_mode_are_covered(self):
        questions = [q for q, _ in FAQS]
        self.assertTrue(any("Android forensics work" in q for q in questions))
        self.assertTrue(any("Developer Mode" in q for q in questions))
        self.assertTrue(any("USB debugging" in q for q in questions))

    def test_the_toolbox_carries_every_question(self):
        self.assertEqual(len(self.page.faq_items), len(FAQS))
        for (ask, body), (question, answer) in zip(self.page.faq_items, FAQS):
            self.assertIn(question[:20], ask.text())
            self.assertIn(answer[:20], body.text())


if __name__ == "__main__":
    unittest.main()
