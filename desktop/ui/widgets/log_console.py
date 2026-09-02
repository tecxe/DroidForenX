"""Log console widget for real-time forensic output."""
from PyQt6.QtWidgets import QPlainTextEdit, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from datetime import datetime


class LogConsole(QWidget):
    """Real-time forensic log console with clear and copy."""
    cleared = pyqtSignal()

    def __init__(self, title: str = "Console Output", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel(f"● {title}")
        lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #10b981; background: transparent; border: none;")
        header.addWidget(lbl)
        header.addStretch()

        btn_clear = QPushButton("Clear")
        btn_clear.setObjectName("btn-outline")
        btn_clear.setFixedSize(60, 26)
        btn_clear.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        btn_clear.clicked.connect(self.clear)
        header.addWidget(btn_clear)

        layout.addLayout(header)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)
        self.console.setFont(QFont("Cascadia Code, Consolas, monospace", 11))
        self.console.setStyleSheet("""
            QPlainTextEdit {
                background: #0a0e17;
                color: #a8b4c4;
                border: none;
                border-top: 1px solid #1e293b;
                padding: 8px 12px;
                selection-background-color: rgba(37,99,235,0.3);
            }
        """)
        layout.addWidget(self.console)

    def log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        colors = {
            "info": "#a8b4c4",
            "success": "#10b981",
            "warning": "#f59e0b",
            "error": "#ef4444",
            "debug": "#64748b",
        }
        color = colors.get(level, "#a8b4c4")
        self.console.appendHtml(
            f'<span style="color:#475569">[{timestamp}]</span> '
            f'<span style="color:{color}">{message}</span>'
        )
        self.console.moveCursor(QTextCursor.MoveOperation.End)

    def log_cmd(self, command: str):
        self.log(f"$ {command}", "debug")

    def log_ok(self, message: str):
        self.log(f"✓ {message}", "success")

    def log_fail(self, message: str):
        self.log(f"✗ {message}", "error")

    def log_warn(self, message: str):
        self.log(f"⚠ {message}", "warning")

    def clear(self):
        self.console.clear()
        self.cleared.emit()
