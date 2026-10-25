"""Headless tests for the floating assistant.

The network is not involved: a fake client stands in for OpenRouter, and a fake
key store stands in for DPAPI, so the parts that can actually go wrong in the UI
are exercised directly:

* a send with no key must never reach the network, and must not eat the text;
* a finished reply is written to the transcript of the *case that asked*, not to
  whichever case happens to be open when the signal lands;
* switching cases switches notebooks;
* text is rendered as text -- an answer containing markup cannot inject HTML
  into the transcript;
* a fallback answer says it came from the fallback;
* the corner button stays in the corner when the window resizes.

Run with:  python -m unittest desktop.tests.test_assistant_drawer -v
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication, QMainWindow
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False

from desktop.core.ai_assistant import Answer, ChatStore
from desktop.ui.widgets.assistant_drawer import AssistantDrawer, AssistantOverlay


class FakeKeyStore:
    def __init__(self, key: str = ""):
        self.key = key
        self.cleared = False

    def load(self):
        return self.key

    def save(self, key):
        self.key = key

    def clear(self):
        self.key = ""
        self.cleared = True


class FakeClient:
    """Answers immediately, so the worker thread finishes in milliseconds."""

    def __init__(self, text="a concise answer", model="google/gemma-3-4b-it",
                 failures=None, partial=False):
        self.text = text
        self.model = model
        self.failures = failures or []
        self.partial = partial
        self.seen = []

    def chat(self, messages, on_delta=None, should_stop=None):
        self.seen.append(messages)
        if on_delta:
            on_delta(self.text)
        return Answer(self.text, self.model, {}, self.failures, self.partial)


def _pump(app, ms=250):
    app.processEvents()
    app.thread().msleep(ms)
    app.processEvents()


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class DrawerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self._tmp.name))
        self.keys = FakeKeyStore("sk-or-v1-test")
        self.client = FakeClient()
        self.drawer = AssistantDrawer(
            store=self.store, key_store=self.keys,
            client_factory=lambda key: self.client)

    def tearDown(self):
        worker = getattr(self.drawer, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(3000)
        self.drawer.deleteLater()
        self._app.processEvents()
        self._tmp.cleanup()

    def _send_and_wait(self, text: str):
        self.drawer.input.setPlainText(text)
        self.drawer._send()
        worker = getattr(self.drawer, "_worker", None)
        self.assertIsNotNone(worker, "a worker should have been started")
        worker.wait(5000)
        _pump(self._app, 150)

    # -- key handling ------------------------------------------------------

    def test_no_key_is_reported_and_the_text_is_kept(self):
        self.keys.key = ""
        self.drawer.input.setPlainText("what does a SIM store?")
        with mock.patch("desktop.ui.widgets.assistant_drawer.QMessageBox.information") as info, \
                mock.patch.object(self.drawer, "_open_settings") as settings:
            self.drawer._send()

        info.assert_called_once()
        settings.assert_called_once()
        self.assertEqual(self.drawer.input.toPlainText(), "what does a SIM store?")
        self.assertIsNone(getattr(self.drawer, "_worker", None))
        self.assertEqual(self.store.load(""), [])

    def test_no_key_message_names_the_problem(self):
        self.keys.key = ""
        self.drawer.input.setPlainText("hello")
        with mock.patch("desktop.ui.widgets.assistant_drawer.QMessageBox.information") as info, \
                mock.patch.object(self.drawer, "_open_settings"):
            self.drawer._send()
        title = info.call_args[0][1]
        self.assertIn("key", title.lower())

    # -- the round trip ----------------------------------------------------

    def test_reply_lands_in_the_case_that_asked(self):
        self.drawer.set_case_id("CASE-A")
        self._send_and_wait("what is in a SIM?")

        messages = self.store.load("CASE-A")
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "what is in a SIM?")
        self.assertEqual(messages[1]["content"], "a concise answer")
        self.assertEqual(messages[1]["model"], "google/gemma-3-4b-it")
        self.assertEqual(self.store.load("CASE-B"), [])

    def test_switching_case_switches_transcript(self):
        self.store.append({"role": "user", "content": "about A"}, case_id="A")
        self.store.append({"role": "user", "content": "about B"}, case_id="B")

        self.drawer.set_case_id("A")
        self.assertEqual([m["content"] for m in self.drawer._messages], ["about A"])

        self.drawer.set_case_id("B")
        self.assertEqual([m["content"] for m in self.drawer._messages], ["about B"])

    def test_a_case_with_no_history_starts_empty_even_after_another_case(self):
        self.drawer.set_case_id("A")
        self._send_and_wait("first")
        self.drawer.set_case_id("FRESH")
        self.assertEqual(self.drawer._messages, [])

    def test_the_system_prompt_is_sent_and_not_stored(self):
        self.drawer.set_case_id("CASE-P")
        self._send_and_wait("hi")
        sent = self.client.seen[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[1]["content"], "hi")
        stored = self.store.load("CASE-P")
        self.assertFalse([m for m in stored if m["role"] == "system"])

    def test_send_is_disabled_while_streaming(self):
        self.drawer.set_case_id("CASE-S")
        self.drawer._streaming = True
        self.drawer.input.setPlainText("second question")
        self.drawer._send()
        self.assertIsNone(getattr(self.drawer, "_worker", None))
        self.assertEqual(self.drawer.input.toPlainText(), "second question")

    def test_fallback_is_labelled_in_the_transcript(self):
        self.client = FakeClient(failures=[("google/gemma-3-4b-it", "rate_limit")],
                                 model="meta-llama/llama-3.1-8b-instruct")
        self.drawer._client_factory = lambda key: self.client
        self.drawer.set_case_id("CASE-F")
        self._send_and_wait("hi")
        rendered = self.drawer.transcript.toHtml()
        self.assertIn("fallback", rendered)

    def test_answer_markup_cannot_become_html(self):
        self.client = FakeClient(text="<script>alert(1)</script>")
        self.drawer._client_factory = lambda key: self.client
        self.drawer.set_case_id("CASE-X")
        self._send_and_wait("hi")
        rendered = self.drawer.transcript.toHtml()
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script", rendered)

    def test_clear_removes_only_this_cases_history(self):
        self.drawer.set_case_id("A")
        self._send_and_wait("a question")
        self.store.append({"role": "user", "content": "other"}, case_id="B")

        with mock.patch(
                "desktop.ui.widgets.assistant_drawer.QMessageBox.question",
                return_value=__import__("PyQt6.QtWidgets", fromlist=["QMessageBox"])
                .QMessageBox.StandardButton.Yes):
            self.drawer._clear_history()

        self.assertEqual(self.store.load("A"), [])
        self.assertEqual(len(self.store.load("B")), 1)
        self.assertEqual(self.drawer._messages, [])

    def test_a_failure_keeps_whatever_arrived_and_says_incomplete(self):
        class FailingClient(FakeClient):
            def chat(self, messages, on_delta=None, should_stop=None):
                if on_delta:
                    on_delta("half a thought")
                from desktop.core.ai_assistant import AssistantError, NETWORK
                raise AssistantError(NETWORK, "reset")

        self.drawer._client_factory = lambda key: FailingClient()
        self.drawer.set_case_id("CASE-D")
        self.drawer.input.setPlainText("hi")
        self.drawer._send()
        self.drawer._worker.wait(5000)
        _pump(self._app, 150)

        stored = self.store.load("CASE-D")
        self.assertEqual(stored[-1]["content"], "half a thought")
        self.assertTrue(stored[-1]["partial"])
        self.assertIn("incomplete", self.drawer.transcript.toHtml())


class BubbleTest(unittest.TestCase):
    def test_roles_are_labelled(self):
        user = AssistantDrawer._bubble("user", "hello")
        assistant = AssistantDrawer._bubble("assistant", "hi")
        self.assertIn("You", user)
        self.assertIn("Assistant", assistant)

    def test_newlines_become_breaks(self):
        self.assertIn("<br>", AssistantDrawer._bubble("assistant", "a\nb"))

    def test_meta_is_escaped(self):
        html_text = AssistantDrawer._bubble("assistant", "x", "<b>bad</b>")
        self.assertNotIn("<b>bad</b>", html_text)


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class OverlayGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_button_sits_in_the_bottom_right_and_follows_a_resize(self):
        window = QMainWindow()
        window.resize(1200, 800)
        overlay = AssistantOverlay(window)
        window.show()
        _pump(self._app, 100)

        button = overlay.button
        self.assertGreater(button.x(), window.width() / 2)
        self.assertGreater(button.y(), window.height() / 2)

        window.resize(900, 600)
        _pump(self._app, 100)
        self.assertLessEqual(button.x() + button.width(), window.width())
        self.assertLessEqual(button.y() + button.height(), window.height())
        self.assertGreater(button.x(), window.width() / 2,
                           "the button must stay anchored to the right edge")
        window.close()

    def test_drawer_opens_and_closes_without_a_sidebar_entry(self):
        window = QMainWindow()
        window.resize(1200, 800)
        overlay = AssistantOverlay(window)
        window.show()
        _pump(self._app, 100)

        self.assertFalse(overlay.drawer.isVisible())
        overlay.toggle()
        self.assertTrue(overlay.drawer.isVisible())
        overlay.toggle()
        self.assertFalse(overlay.drawer.isVisible())
        window.close()


if __name__ == "__main__":
    unittest.main()
