"""Floating assistant: a button in the corner, a drawer that talks to a model.

The transcript is the examiner's working note, so it is stored per case in the
profile (``ChatStore``) and never inside the case folder or the evidence
database. Nothing is sent anywhere until the operator presses Send, and the
drawer says which model is answering -- including when the fallback model is the
one that answered.

The UI never blocks on the network: every request runs in ``AssistantWorker``.
"""

from __future__ import annotations

import html
import threading
from typing import List, Optional

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from ...core import ai_assistant
from ...core.ai_assistant import AssistantClient, AssistantError, ChatStore, KeyStore
from .. import theme as _theme

DISCLAIMER = ("AI-generated. Verify anything before it goes into a report or a "
              "court statement.")


class AssistantWorker(QThread):
    """One request, streamed. Never touches widgets from ``run``."""

    delta = pyqtSignal(str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, client: AssistantClient, messages: List[dict], parent=None):
        super().__init__(parent)
        self._client = client
        self._messages = messages
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            answer = self._client.chat(
                self._messages,
                on_delta=self.delta.emit,
                should_stop=self._stop.is_set,
            )
        except AssistantError as exc:
            self.failed.emit(exc.user_message)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(f"Unexpected failure: {exc}")
            return
        self.finished_ok.emit(answer)


class AssistantSettingsDialog(QDialog):
    """Where the key is entered and checked. The key is never shown back."""

    def __init__(self, store: KeyStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistant settings")
        self.setMinimumWidth(520)
        self._store = store

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "DroidForenX sends the conversation to OpenRouter. No case data is "
            "attached automatically: only what you type leaves this machine.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        layout.addWidget(QLabel("OpenRouter API key"))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-or-v1-...")
        existing = store.load()
        self.key_edit.setPlaceholderText(
            f"Stored: {ai_assistant.mask_key(existing)}" if existing else "sk-or-v1-...")
        layout.addWidget(self.key_edit)

        models = QLabel("Models, in order: " + ", ".join(ai_assistant.DEFAULT_MODELS))
        models.setWordWrap(True)
        models.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(models)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_test = QPushButton("Save and test")
        self.btn_test.clicked.connect(self._save_and_test)
        row.addWidget(self.btn_test)
        self.btn_clear = QPushButton("Remove key")
        self.btn_clear.clicked.connect(self._clear)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _save_and_test(self):
        key = self.key_edit.text().strip()
        if not key:
            self.status.setText("Paste a key first.")
            return
        if not key.startswith("sk-or-"):
            self.status.setText(
                "That does not look like an OpenRouter key (they start with sk-or-).")
            return
        try:
            self._store.save(key)
        except Exception as exc:
            self.status.setText(f"Could not save the key: {exc}")
            return
        self.key_edit.clear()
        self.key_edit.setPlaceholderText(
            f"Stored: {ai_assistant.mask_key(key)}")
        self.status.setText("Saved. Checking with OpenRouter...")
        self.btn_test.setEnabled(False)
        _pump_events()

        try:
            info = AssistantClient(key).check()
        except AssistantError as exc:
            self.status.setText(exc.user_message)
            self.btn_test.setEnabled(True)
            return
        finally:
            self.btn_test.setEnabled(True)

        usage = info.get("usage")
        limit = info.get("limit_remaining")
        parts = ["Key accepted."]
        if isinstance(usage, (int, float)):
            parts.append(f"Used so far: ${usage:.2f}.")
        if isinstance(limit, (int, float)):
            parts.append(f"Remaining: ${limit:.2f}.")
        self.status.setText(" ".join(parts))

    def _clear(self):
        self._store.clear()
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("sk-or-v1-...")
        self.status.setText("Key removed. The assistant is disabled until a new one is saved.")


def _pump_events():
    """Let the status label repaint before a blocking network call."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


class AssistantDrawer(QFrame):
    """The chat panel itself."""

    closed = pyqtSignal()

    def __init__(self, parent=None, store: Optional[ChatStore] = None,
                 key_store: Optional[KeyStore] = None, client_factory=None):
        super().__init__(parent)
        self.setObjectName("assistantDrawer")
        self.setStyleSheet("""
            QFrame#assistantDrawer {
                background: #0f172a; border: 1px solid #1e293b; border-radius: 12px;
            }
        """)
        # Injectable so the tests can run the whole send/receive path against a
        # temp directory and a fake model, with no key and no network.
        self._store = store or ChatStore()
        self._keys = key_store or KeyStore()
        self._client_factory = client_factory or (lambda key: AssistantClient(key))
        self._case_id = ""
        self._messages: List[dict] = []
        self._worker: Optional[AssistantWorker] = None
        self._streaming_text = ""
        self._streaming = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Assistant")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #e2e8f0;"
                            " background: transparent; border: none;")
        header.addWidget(title)
        self.model_label = QLabel("")
        self.model_label.setStyleSheet("font-size: 11px; color: #64748b;"
                                       " background: transparent; border: none;")
        header.addWidget(self.model_label)
        header.addStretch(1)

        self.btn_settings = QToolButton()
        self.btn_settings.setText("\u2699")
        self.btn_settings.setToolTip("Assistant settings and API key")
        self.btn_settings.clicked.connect(self._open_settings)
        header.addWidget(self.btn_settings)

        self.btn_close = QToolButton()
        self.btn_close.setText("\u2715")
        self.btn_close.setToolTip("Close")
        self.btn_close.clicked.connect(self.close_drawer)
        header.addWidget(self.btn_close)
        layout.addLayout(header)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setStyleSheet(
            "QTextBrowser { background: #080c14; border: 1px solid #1e293b;"
            " border-radius: 8px; padding: 10px; color: #e2e8f0; font-size: 13px; }")
        layout.addWidget(self.transcript, 1)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask about Android forensics, APK behaviour, "
                                      "SQLite schemas...  (Enter sends, Shift+Enter "
                                      "adds a line)")
        self.input.setFixedHeight(76)
        self.input.setStyleSheet(
            "QPlainTextEdit { background: #080c14; border: 1px solid #1e293b;"
            " border-radius: 8px; padding: 8px; color: #e2e8f0; font-size: 13px; }")
        self.input.installEventFilter(self)
        layout.addWidget(self.input)

        buttons = QHBoxLayout()
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip("Delete this case's conversation")
        self.btn_clear.clicked.connect(self._clear_history)
        buttons.addWidget(self.btn_clear)
        buttons.addStretch(1)
        self.btn_send = QPushButton("Send")
        self.btn_send.setObjectName("btn-success")
        self.btn_send.clicked.connect(self._send)
        buttons.addWidget(self.btn_send)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self._stop)
        buttons.addWidget(self.btn_stop)
        layout.addLayout(buttons)

        self.footer = QLabel(DISCLAIMER)
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet("font-size: 10px; color: #64748b;"
                                  " background: transparent; border: none;")
        layout.addWidget(self.footer)

        self._load_history()
        self._render()

    # -- state -------------------------------------------------------------

    def set_case_id(self, case_id: str) -> None:
        case_id = case_id or ""
        if case_id == self._case_id:
            return
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        self._case_id = case_id
        self._streaming_text = ""
        self._load_history()
        self._render()

    def _load_history(self) -> None:
        self._messages = [m for m in self._store.load(self._case_id)
                          if m.get("role") in ("user", "assistant")]
        self._update_model_label()

    def _update_model_label(self) -> None:
        if not self._keys.load():
            self.model_label.setText("no key set")
        elif self._case_id:
            self.model_label.setText(f"case {self._case_id}")
        else:
            self.model_label.setText("no case open")

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        blocks = []
        for message in self._messages:
            meta = ""
            if message.get("role") == "assistant" and message.get("model"):
                meta = message["model"]
                if message.get("fallback"):
                    meta += " \u00b7 fallback"
                if message.get("partial"):
                    meta += " \u00b7 partial"
            blocks.append(self._bubble(message.get("role", "user"),
                                       message.get("content", ""), meta))
        if self._streaming:
            blocks.append(self._bubble("assistant", self._streaming_text or "\u2026",
                                       "streaming"))
        if not blocks:
            blocks.append(
                "<p style='color:#64748b'>Ask anything about the device, the "
                "artifacts, or Android in general. Nothing is sent until you press "
                "Send.</p>")
        # The transcript is HTML, so the CSS translation never reaches it; the
        # bubble colours are translated here instead, or light mode would draw
        # pale text on a pale panel.
        self.transcript.setHtml(_theme.map_qss("".join(blocks)))
        self.transcript.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _bubble(role: str, text: str, meta: str = "") -> str:
        is_user = role == "user"
        label = "You" if is_user else "Assistant"
        color = "#93c5fd" if is_user else "#34d399"
        body = html.escape(text).replace("\n", "<br>")
        meta_html = (f"<span style='color:#64748b;font-size:10px'> \u00b7 "
                     f"{html.escape(meta)}</span>" if meta else "")
        return (f"<div style='margin:6px 0'>"
                f"<div style='color:{color};font-size:11px;font-weight:700'>"
                f"{label}{meta_html}</div>"
                f"<div style='color:#e2e8f0'>{body}</div></div>")

    # -- actions -----------------------------------------------------------

    def _open_settings(self):
        dialog = AssistantSettingsDialog(self._keys, self)
        dialog.exec()
        self._update_model_label()

    def _clear_history(self):
        if not self._messages:
            return
        if QMessageBox.question(
                self, "Clear conversation",
                "Delete this case's conversation from this machine?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._store.clear(self._case_id)
        self._messages = []
        self._streaming_text = ""
        self._render()

    def _stop(self):
        if self._worker is not None:
            self._worker.stop()
            self.btn_stop.setEnabled(False)
            self.footer.setText("Stopping...")

    def _send(self):
        text = self.input.toPlainText().strip()
        if not text or self._streaming:
            return
        key = self._keys.load()
        if not key:
            QMessageBox.information(
                self, "No API key",
                "The assistant needs an OpenRouter key. Press the gear icon and "
                "paste one.")
            self._open_settings()
            if not self._keys.load():
                return

        self.input.clear()
        self._messages.append({"role": "user", "content": text})
        self._store.append({"role": "user", "content": text}, self._case_id)
        self._streaming = True
        self._streaming_text = ""
        self.btn_send.setEnabled(False)
        self.btn_stop.setVisible(True)
        self.btn_stop.setEnabled(True)
        self.footer.setText("Sending to OpenRouter...")
        self._render()

        payload = self._request_messages()
        self._worker = AssistantWorker(self._client_factory(key), payload, parent=self)
        self._worker.delta.connect(self._on_delta)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _request_messages(self) -> List[dict]:
        """What actually goes on the wire: a short system prompt plus the case."""
        system = ("You are an assistant inside DroidForenX, a Windows digital "
                  "forensics tool for Android devices. Answer concisely and "
                  "concretely. When you are unsure, say so. Never invent file "
                  "paths, database tables or Android APIs.")
        return [{"role": "system", "content": system}] + list(self._messages)

    def _finish_ui(self):
        self._streaming = False
        self.btn_send.setEnabled(True)
        self.btn_stop.setVisible(False)
        self.footer.setText(DISCLAIMER)

    def _on_delta(self, piece: str):
        self._streaming_text += piece
        self._render()

    def _on_finished(self, answer):
        self._messages.append({"role": "assistant",
                               "content": answer.text,
                               "model": answer.model,
                               "fallback": answer.used_fallback,
                               "partial": answer.partial})
        self._store.append({"role": "assistant", "content": answer.text,
                            "model": answer.model,
                            "fallback": answer.used_fallback,
                            "partial": answer.partial}, self._case_id)
        self._streaming_text = ""
        self._finish_ui()
        if answer.partial:
            self.footer.setText("Partial answer: the stream ended early.")
        self._render()

    def _on_failed(self, message: str):
        # Keep what arrived, if anything, so the operator is not left guessing.
        if self._streaming_text.strip():
            self._messages.append({"role": "assistant",
                                   "content": self._streaming_text + "\n\n(incomplete)"})
            self._store.append({"role": "assistant",
                                "content": self._streaming_text,
                                "partial": True}, self._case_id)
        self._streaming_text = ""
        self._finish_ui()
        self.footer.setText(message)
        self._render()

    # -- plumbing ----------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self._send()
                    return True
        return super().eventFilter(obj, event)

    def close_drawer(self):
        self.closed.emit()


class AssistantOverlay(QObject):
    """Owns the corner button and the drawer, and keeps them in place.

    Deliberately not a QWidget covering the window: a translucent overlay that
    swallows clicks is a classic way to make a working application look broken.
    The button is a child of the main window; the drawer is too, and both are
    repositioned when the window resizes.
    """

    SIZE = QSize(430, 560)
    MARGIN = 22

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window

        self.button = QToolButton(window)
        self.button.setText("\U0001F916")   # robot
        self.button.setToolTip("Ask the assistant (Ctrl+Shift+A)")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setFixedSize(52, 52)
        self.button.setStyleSheet("""
            QToolButton {
                background: #10b981; color: #ffffff; border: none;
                border-radius: 26px; font-size: 24px;
            }
            QToolButton:hover { background: #34d399; }
            QToolButton:pressed { background: #059669; }
        """)
        self.button.clicked.connect(self.toggle)

        self.drawer = AssistantDrawer(window)
        self.drawer.setFixedSize(self.SIZE)
        self.drawer.setVisible(False)
        self.drawer.closed.connect(self.hide)

        window.installEventFilter(self)
        self._reposition()

    # -- api ---------------------------------------------------------------

    def set_case_id(self, case_id: str) -> None:
        self.drawer.set_case_id(case_id)

    def toggle(self):
        self.drawer.setVisible(not self.drawer.isVisible())
        if self.drawer.isVisible():
            self._reposition()
            self.drawer.input.setFocus()

    def hide(self):
        self.drawer.setVisible(False)

    def attach_shortcut(self, window):
        from PyQt6.QtGui import QKeySequence, QShortcut
        shortcut = QShortcut(QKeySequence("Ctrl+Shift+A"), window)
        shortcut.activated.connect(self.toggle)
        return shortcut

    # -- geometry ----------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() in (QEvent.Type.Resize,
                                                    QEvent.Type.Show):
            self._reposition()
        return False

    def _reposition(self):
        rect = self._window.rect()
        margin = self.MARGIN
        # The status bar is part of the window and would sit under the button.
        bottom = margin
        status = getattr(self._window, "statusBar", None)
        if callable(status):
            bar = status()
            if bar is not None and bar.isVisible():
                bottom += bar.height()
        self.button.move(rect.width() - self.button.width() - margin,
                         rect.height() - self.button.height() - bottom)
        self.button.raise_()
        size = self.drawer.size()
        x = rect.width() - size.width() - margin
        y = rect.height() - size.height() - bottom - self.button.height() - 10
        if y < 10:
            y = 10
        if x < 10:
            x = 10
        self.drawer.move(QPoint(x, y))
        self.drawer.raise_()


__all__ = ["AssistantDrawer", "AssistantOverlay", "AssistantWorker",
           "AssistantSettingsDialog", "DISCLAIMER"]
