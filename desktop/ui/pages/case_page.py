"""Case management page — create, list, view forensic cases."""
import uuid
from datetime import datetime, timezone
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QLineEdit, QTextEdit, QTableWidget, QTableWidgetItem,
                              QHeaderView, QAbstractItemView, QDialog, QFormLayout,
                              QComboBox, QMessageBox, QScrollArea, QSplitter)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from ..widgets.data_table import DataTable


class CaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Case")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.setStyleSheet("""
            QDialog { background: #0a0e17; color: #e2e8f0; }
            QLabel { font-size: 13px; }
            QLineEdit, QTextEdit, QComboBox {
                background: #0f172a; border: 1px solid #1e293b; border-radius: 6px;
                color: #e2e8f0; padding: 10px; font-size: 13px;
            }
            QLineEdit:focus, QTextEdit:focus { border-color: #2563eb; }
            QPushButton {
                background: #2563eb; color: white; border: none; border-radius: 6px;
                padding: 10px 20px; font-weight: 600;
            }
            QPushButton:hover { background: #1d4ed8; }
            #btn-cancel { background: #1e293b; color: #e2e8f0; }
            #btn-cancel:hover { background: #334155; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Create New Forensic Case")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f59e0b; border: none;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(12)

        self.case_number = QLineEdit()
        self.case_number.setPlaceholderText("e.g., CASE-2026-0001")
        form.addRow("Case Number *", self.case_number)

        self.case_title = QLineEdit()
        self.case_title.setPlaceholderText("Brief description of investigation")
        form.addRow("Title *", self.case_title)

        self.investigator = QLineEdit()
        self.investigator.setPlaceholderText("Lead investigator name")
        form.addRow("Investigator", self.investigator)

        self.agency = QLineEdit()
        self.agency.setPlaceholderText("Agency / Organization")
        form.addRow("Agency", self.agency)

        self.description = QTextEdit()
        self.description.setPlaceholderText("Case details, objectives, and notes...")
        self.description.setMaximumHeight(120)
        form.addRow("Description", self.description)

        layout.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("btn-cancel")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        btn_create = QPushButton("🔒 Create Case")
        btn_create.clicked.connect(self.accept)
        btns.addWidget(btn_create)
        layout.addLayout(btns)

    def get_data(self):
        return {
            "id": str(uuid.uuid4()),
            "case_number": self.case_number.text().strip(),
            "title": self.case_title.text().strip(),
            "investigator": self.investigator.text().strip(),
            "agency": self.agency.text().strip(),
            "description": self.description.toPlainText().strip(),
        }


class CasePage(QWidget):
    case_selected = pyqtSignal(str)  # case_id
    navigate = pyqtSignal(str)

    def __init__(self, db_get, parent=None):
        super().__init__(parent)
        self.db_get = db_get
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        # Header
        header = QHBoxLayout()
        title = QLabel("📁 Case Management")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #e2e8f0; background: transparent; border: none;")
        header.addWidget(title)
        header.addStretch()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cases...")
        self.search.setFixedWidth(250)
        self.search.textChanged.connect(self._filter_cases)
        header.addWidget(self.search)

        btn_new = QPushButton("＋ New Case")
        btn_new.setObjectName("btn-accent")
        btn_new.setFixedHeight(40)
        btn_new.clicked.connect(self._new_case)
        header.addWidget(btn_new)
        layout.addLayout(header)

        # Case table
        self.table = DataTable(["Case #", "Title", "Investigator", "Agency", "Status", "Created"])
        self.table.setMinimumHeight(300)
        self.table.cellDoubleClicked.connect(self._open_case)
        layout.addWidget(self.table)

        layout.addStretch()

    def refresh(self):
        conn = self.db_get()
        cases = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
        self._all_cases = [dict(c) for c in cases]
        conn.close()
        self._populate_table(self._all_cases)

    def _populate_table(self, cases):
        self.table.setRowCount(len(cases))
        for r, c in enumerate(cases):
            for col, key in enumerate(["case_number", "title", "investigator", "agency", "status", "created_at"]):
                val = c.get(key, "")
                if key == "created_at" and val:
                    val = val[:10]
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()

    def _filter_cases(self, text):
        text = text.lower()
        filtered = [c for c in self._all_cases if text in c.get("case_number", "").lower() or text in c.get("title", "").lower()]
        self._populate_table(filtered)

    def _new_case(self):
        dlg = CaseDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if not data["case_number"] or not data["title"]:
                QMessageBox.warning(self, "Validation", "Case number and title are required.")
                return
            conn = self.db_get()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO cases (id, case_number, title, investigator, agency, description, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (data["id"], data["case_number"], data["title"], data["investigator"], data["agency"], data["description"], "open", now, now),
            )
            conn.commit()
            conn.close()
            self.refresh()

    def _open_case(self, row, col):
        if row < 0 or row >= len(self._all_cases):
            return
        case = self._all_cases[row]
        self._current_case_id = case["id"]
        self.case_selected.emit(case["id"])
