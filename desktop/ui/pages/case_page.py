"""Case management page - create, edit, delete and list forensic cases."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QLineEdit, QTextEdit, QTableWidgetItem, QDialog,
                             QFormLayout, QComboBox, QMessageBox, QCheckBox)
from PyQt6.QtCore import Qt, pyqtSignal
from ..widgets.data_table import DataTable
from ..widgets.scroll_page import enable_page_scroll
from ...core import cases

_CASE_STATUSES = (("open", "Open"), ("closed", "Closed"), ("archived", "Archived"))


class CaseDialog(QDialog):
    """Create a case, or edit an existing one.

    The same dialog serves both paths so a case cannot be created with one set
    of rules and edited under another.
    """

    def __init__(self, parent=None, case: dict | None = None):
        super().__init__(parent)
        self.case = case or {}
        editing = bool(case)
        self.setWindowTitle("Edit Case" if editing else "Create New Case")
        self.setMinimumWidth(500)
        self.setMinimumHeight(430)
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

        title = QLabel("Edit Forensic Case" if editing else "Create New Forensic Case")
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

        self.status = QComboBox()
        for value, label in _CASE_STATUSES:
            self.status.addItem(label, value)
        form.addRow("Status", self.status)

        self.description = QTextEdit()
        self.description.setPlaceholderText("Case details, objectives, and notes...")
        self.description.setMaximumHeight(120)
        form.addRow("Description", self.description)

        layout.addLayout(form)

        if editing:
            self.case_number.setText(str(self.case.get("case_number", "")))
            self.case_title.setText(str(self.case.get("title", "")))
            self.investigator.setText(str(self.case.get("investigator", "")))
            self.agency.setText(str(self.case.get("agency", "")))
            self.description.setPlainText(str(self.case.get("description", "")))
            index = self.status.findData(self.case.get("status", "open"))
            self.status.setCurrentIndex(index if index >= 0 else 0)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("btn-cancel")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        btn_save = QPushButton("Save Changes" if editing else "🔒 Create Case")
        btn_save.clicked.connect(self.accept)
        btns.addWidget(btn_save)
        layout.addLayout(btns)

    def get_data(self) -> dict:
        return {
            "case_number": self.case_number.text().strip(),
            "title": self.case_title.text().strip(),
            "investigator": self.investigator.text().strip(),
            "agency": self.agency.text().strip(),
            "description": self.description.toPlainText().strip(),
            "status": self.status.currentData(),
        }


class DeleteCaseDialog(QDialog):
    """Confirmation for the one action that cannot be undone from the UI."""

    def __init__(self, case: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Delete Case")
        self.setMinimumWidth(520)
        self.setStyleSheet("""
            QDialog { background: #0a0e17; color: #e2e8f0; }
            QLabel { font-size: 13px; }
            QCheckBox { font-size: 12px; color: #cbd5e1; }
            QPushButton {
                background: #1e293b; color: #e2e8f0; border: none;
                border-radius: 6px; padding: 10px 20px; font-weight: 600;
            }
            QPushButton:hover { background: #334155; }
            #btn-delete { background: #dc2626; color: white; }
            #btn-delete:hover { background: #b91c1c; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        heading = QLabel(f"Delete case {case.get('case_number', '')}?")
        heading.setStyleSheet("font-size: 17px; font-weight: 700; color: #f87171; border: none;")
        layout.addWidget(heading)

        detail = QLabel(
            "This removes the case, its sessions, artifacts, examination results,"
            " reports, disk images and SIM records from the database."
            " The case number can then be reused if it was a mistake.")
        detail.setWordWrap(True)
        detail.setStyleSheet("color: #cbd5e1; background: transparent; border: none;")
        layout.addWidget(detail)

        warning = QLabel(
            f"Title: {case.get('title', '')}\nInvestigator: {case.get('investigator', '') or '—'}")
        warning.setStyleSheet("color: #94a3b8; background: transparent; border: none;")
        layout.addWidget(warning)

        self.delete_files = QCheckBox(
            "Also delete the case evidence folder on disk (acquired files, manifests)")
        layout.addWidget(self.delete_files)

        keep_note = QLabel(
            "Leave this unchecked to keep acquired evidence for the record.")
        keep_note.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
        layout.addWidget(keep_note)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        btn_delete = QPushButton("Delete Case")
        btn_delete.setObjectName("btn-delete")
        btn_delete.clicked.connect(self.accept)
        btns.addWidget(btn_delete)
        layout.addLayout(btns)

    def should_delete_files(self) -> bool:
        return self.delete_files.isChecked()


class CasePage(QWidget):
    case_selected = pyqtSignal(str)  # case_id
    case_deleted = pyqtSignal(str)   # case_id
    navigate = pyqtSignal(str)

    def __init__(self, db_get, parent=None):
        super().__init__(parent)
        self.db_get = db_get
        self._all_cases: list[dict] = []
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

        self.btn_edit = QPushButton("✎ Edit")
        self.btn_edit.setObjectName("btn-outline")
        self.btn_edit.setFixedHeight(40)
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self._edit_case)
        header.addWidget(self.btn_edit)

        self.btn_delete = QPushButton("🗑 Delete")
        self.btn_delete.setObjectName("btn-danger")
        self.btn_delete.setFixedHeight(40)
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete_case)
        header.addWidget(self.btn_delete)

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
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        self.hint = QLabel("Select a case to edit or delete it. Double-click to open it.")
        self.hint.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
        layout.addWidget(self.hint)

        layout.addStretch()

        # No scroll area previously, so a short window compressed the table.
        enable_page_scroll(self)

    # -- data -------------------------------------------------------------

    def refresh(self):
        self._all_cases = cases.list_cases()
        self._populate_table(self._all_cases)
        self._on_selection_changed()

    def _populate_table(self, rows):
        self.table.setRowCount(len(rows))
        for r, case in enumerate(rows):
            for col, key in enumerate(["case_number", "title", "investigator",
                                       "agency", "status", "created_at"]):
                val = case.get(key, "")
                if key == "created_at" and val:
                    val = val[:10]
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 0:
                    # Keep the id on the row: search filtering changes row
                    # indexes, so position alone must not identify a case.
                    item.setData(Qt.ItemDataRole.UserRole, case["id"])
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()

    def _filter_cases(self, text):
        text = text.lower()
        filtered = [
            c for c in self._all_cases
            if text in str(c.get("case_number", "")).lower()
            or text in str(c.get("title", "")).lower()
            or text in str(c.get("investigator", "")).lower()
        ]
        self._populate_table(filtered)
        self._on_selection_changed()

    def _selected_case(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        case_id = item.data(Qt.ItemDataRole.UserRole)
        return next((c for c in self._all_cases if c.get("id") == case_id), None)

    def _on_selection_changed(self):
        selected = self._selected_case() is not None
        self.btn_edit.setEnabled(selected)
        self.btn_delete.setEnabled(selected)

    # -- actions ----------------------------------------------------------

    def _new_case(self):
        dlg = CaseDialog(self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        if not data["case_number"] or not data["title"]:
            QMessageBox.warning(self, "Validation", "Case number and title are required.")
            return
        if cases.case_number_taken(data["case_number"]):
            QMessageBox.warning(self, "Validation",
                                f"Case number {data['case_number']} already exists.")
            return
        cases.create_case(**data)
        self.refresh()

    def _edit_case(self):
        case = self._selected_case()
        if not case:
            return
        dlg = CaseDialog(self, case=case)
        if not dlg.exec():
            return
        data = dlg.get_data()
        if not data["case_number"] or not data["title"]:
            QMessageBox.warning(self, "Validation", "Case number and title are required.")
            return
        if cases.case_number_taken(data["case_number"], exclude_id=case["id"]):
            QMessageBox.warning(self, "Validation",
                                f"Case number {data['case_number']} is used by another case.")
            return
        cases.update_case(case["id"], **data)
        self.refresh()

    def _delete_case(self):
        case = self._selected_case()
        if not case:
            return
        dlg = DeleteCaseDialog(case, self)
        if not dlg.exec():
            return

        summary = cases.delete_case(case["id"], delete_evidence=dlg.should_delete_files())
        self.refresh()
        self.case_deleted.emit(case["id"])

        message = (f"Case {case['case_number']} deleted.\n"
                   f"Sessions removed: {summary['sessions']}\n"
                   f"Related rows removed: {summary['rows']}")
        if summary["removed_files"]:
            message += f"\nEvidence folder deleted: {summary['evidence_dir']}"
        elif summary["evidence_dir"]:
            message += f"\nEvidence kept at: {summary['evidence_dir']}"
        QMessageBox.information(self, "Case Deleted", message)

    def _open_case(self, row, col):
        case = self._selected_case()
        if not case:
            return
        self.case_selected.emit(case["id"])
