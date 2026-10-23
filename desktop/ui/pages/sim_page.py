"""SIM Forensics — read the UICC the handset will expose, over ADB.

The page is deliberately honest about what an unrooted phone allows: it shows
which sources answered, which were refused, and marks identifiers Android has
redacted rather than presenting a partial ICCID as a complete one.
"""
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QFrame, QGridLayout, QGroupBox, QTableWidget,
                             QTableWidgetItem, QHeaderView)

from ...core import sim_forensics
from ..widgets.device_status_bar import DeviceStatusBar
from ..widgets.log_console import LogConsole
from ..widgets.scroll_page import enable_page_scroll

_VALUE_STYLE = "font-size: 12px; color: #e2e8f0; background: transparent; border: none;"
_KEY_STYLE = "font-size: 11px; font-weight: 600; color: #64748b; background: transparent; border: none;"


class SimReadWorker(QThread):
    finished = pyqtSignal(object, object)
    log = pyqtSignal(str, str)

    def __init__(self, serial: str, session_id: str):
        super().__init__()
        self.serial = serial
        self.session_id = session_id

    def run(self):
        try:
            self.log.emit(f"Reading SIM state for {self.serial}...", "info")
            result = sim_forensics.read_sim_cards(self.serial)
            for source, status in result["sources"].items():
                level = "success" if status == "ok" else "warn"
                self.log.emit(f"{source}: {status}", level)

            present = [sim for sim in result["sims"] if sim["status"] == "present"]
            self.log.emit(f"{len(present)} SIM card(s) present.", "success")
            for sim in present:
                redacted = ", ".join(sim["redacted"]) or "none"
                self.log.emit(
                    f"slot {sim['slot']}: {sim['carrier'] or 'unknown carrier'}, "
                    f"ICCID {sim['iccid'] or 'unreadable'}, redacted: {redacted}", "info")
                self.log.emit(f"  {len(sim['contacts'])} ADN contact(s), "
                              f"{len(sim['messages'])} SIM message(s)", "info")

            counts = sim_forensics.store_sim_cards(self.session_id, result)
            if counts.get("persisted"):
                self.log.emit(
                    f"Filed {counts['sims']} card(s), {counts['contacts']} contact(s), "
                    f"{counts['messages']} message(s) under this session.", "success")
            else:
                self.log.emit(
                    f"Not filed to the case: {counts.get('reason', 'unknown')}. "
                    "Select or start a case session first.", "warn")
            self.finished.emit(result, counts)
        except Exception as exc:  # pragma: no cover - surfaced in the UI
            self.log.emit(f"SIM read failed: {exc}", "error")


class SimForensicsPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial = ""
        self._session_id = ""
        self._result = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("SIM Forensics")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #10b981;"
                            " background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Read the UICC over ADB: dual-SIM slots, subscription identities, "
            "ADN contacts and SIM-stored messages, with the provenance of each value.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent;"
                               " border: none;")
        layout.addWidget(subtitle)

        self.device_bar = DeviceStatusBar()
        self.device_bar.device_rescanned.connect(self._on_device_rescanned)
        layout.addWidget(self.device_bar)

        btn_row = QHBoxLayout()
        self.btn_read = QPushButton("Read SIM Cards")
        self.btn_read.setObjectName("btn-accent")
        self.btn_read.setFixedHeight(42)
        self.btn_read.clicked.connect(self._read_sim)
        btn_row.addWidget(self.btn_read)

        self.summary = QLabel("No cards read yet.")
        self.summary.setStyleSheet("font-size: 12px; color: #64748b; background: transparent;"
                                   " border: none;")
        btn_row.addWidget(self.summary)
        btn_row.addStretch()

        btn_next = QPushButton("Next: Acquisition")
        btn_next.setObjectName("btn-success")
        btn_next.setFixedHeight(42)
        btn_next.clicked.connect(lambda: self.navigate.emit("acquire"))
        btn_row.addWidget(btn_next)
        layout.addLayout(btn_row)

        self.cards_group = QGroupBox("SIM Cards")
        self.cards_group.setVisible(False)
        self.cards_layout = QVBoxLayout(self.cards_group)
        layout.addWidget(self.cards_group)

        self.remembered_group = QGroupBox("Remembered Subscriptions (no card inserted)")
        self.remembered_group.setVisible(False)
        self.remembered_layout = QVBoxLayout(self.remembered_group)
        layout.addWidget(self.remembered_group)

        self.contacts_group = QGroupBox("SIM Contacts (ADN)")
        self.contacts_group.setVisible(False)
        contacts_layout = QVBoxLayout(self.contacts_group)
        self.contacts_table = QTableWidget(0, 4)
        self.contacts_table.setHorizontalHeaderLabels(["SIM Slot", "Name", "Number", "Email"])
        self.contacts_table.verticalHeader().setVisible(False)
        self.contacts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.contacts_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.contacts_table.setMinimumHeight(180)
        contacts_layout.addWidget(self.contacts_table)
        layout.addWidget(self.contacts_group)

        layout.addStretch()

        self.console = LogConsole("SIM Read Log")
        self.console.setMinimumHeight(180)
        layout.addWidget(self.console)

        enable_page_scroll(self)

    # -- device / session plumbing -----------------------------------------

    def set_device(self, serial: str):
        self._serial = serial

    def set_session(self, session_id: str):
        self._session_id = session_id or ""

    def _on_device_rescanned(self, devices):
        if devices:
            self._serial = devices[0].get("serial", "")

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()

    # -- actions ------------------------------------------------------------

    def _read_sim(self):
        serial = self._serial or self.device_bar.get_first_serial()
        if not serial:
            self.console.log("No device detected. Connect a device and enable USB debugging.",
                             "error")
            return
        self._serial = serial
        self.btn_read.setEnabled(False)
        self.summary.setText("Reading...")
        self.worker = SimReadWorker(serial, self._session_id)
        self.worker.log.connect(self.console.log)
        self.worker.finished.connect(self._on_read)
        self.worker.start()

    def _on_read(self, result, counts):  # noqa: ARG002 - counts are logged by the worker
        self._result = result
        self.btn_read.setEnabled(True)

        sims = result.get("sims", [])
        present = [sim for sim in sims if sim["status"] == "present"]
        self.summary.setText(
            f"{len(present)} present, {len(sims) - len(present)} absent slot(s); "
            f"{sum(len(sim.get('contacts', [])) for sim in sims)} ADN contact(s).")
        self._render_cards(present, [sim for sim in sims if sim["status"] != "present"])
        self._render_remembered(result.get("remembered_subscriptions", []))
        self._render_contacts(sims)

    def _render_cards(self, present, absent):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for sim in present + absent:
            frame = QFrame()
            frame.setStyleSheet("QFrame { background: #111827; border: 1px solid #1e293b;"
                                " border-radius: 8px; }")
            grid = QGridLayout(frame)
            grid.setContentsMargins(16, 12, 16, 12)
            grid.setSpacing(6)

            heading = QLabel(f"Slot {sim['slot']} — {sim['status'].replace('_', ' ').title()}")
            heading.setStyleSheet("font-size: 13px; font-weight: 700; color: #f59e0b;"
                                  " background: transparent; border: none;")
            grid.addWidget(heading, 0, 0, 1, 4)

            def masked(key, value, fallback="unreadable"):
                if not value:
                    return fallback
                if key in sim.get("redacted", []):
                    return f"{value}… (redacted tail)"
                return value

            fields = [
                ("Carrier", sim.get("carrier") or "—"),
                ("Display name", sim.get("display_name") or "—"),
                ("Country", sim.get("country") or "—"),
                ("Network", f"MCC {sim.get('mcc') or '—'} / MNC {sim.get('mnc') or '—'}"),
                ("ICCID", masked("iccid", sim.get("iccid"))),
                ("IMSI", masked("imsi", sim.get("imsi"))),
                ("MSISDN", masked("msisdn", sim.get("msisdn"), "not published")),
                ("ADN contacts", str(len(sim.get("contacts", [])))),
            ]
            if sim.get("redacted"):
                fields.append(("Redacted by Android", ", ".join(sim["redacted"])))

            for index, (key, value) in enumerate(fields):
                column = (index % 2) * 2
                row = 1 + index // 2
                k = QLabel(key)
                k.setStyleSheet(_KEY_STYLE)
                v = QLabel(str(value))
                v.setStyleSheet(_VALUE_STYLE)
                v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(k, row, column)
                grid.addWidget(v, row, column + 1)

            self.cards_layout.addWidget(frame)

        self.cards_group.setVisible(bool(present or absent))

    def _render_remembered(self, remembered):
        while self.remembered_layout.count():
            item = self.remembered_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not remembered:
            self.remembered_group.setVisible(False)
            return

        note = QLabel("The device still stores subscriptions for cards that are not "
                      "inserted. Identifiers are masked by Android for the shell user.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px; color: #64748b; background: transparent;"
                           " border: none;")
        self.remembered_layout.addWidget(note)

        for sub in remembered:
            label = QLabel(
                f"{sub.get('display_name') or 'unnamed'} · {sub.get('carrier') or 'no service'}"
                f" · ICCID {sub.get('iccid') or '—'} · IMSI {sub.get('imsi') or '—'}"
                f" · MCC {sub.get('mcc') or '—'}/MNC {sub.get('mnc') or '—'}")
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 11px; color: #94a3b8; background: transparent;"
                                " border: none;")
            self.remembered_layout.addWidget(label)

        self.remembered_group.setVisible(True)

    def _render_contacts(self, sims):
        rows = [(sim["slot"], contact) for sim in sims for contact in sim.get("contacts", [])]
        self.contacts_table.setRowCount(len(rows))
        for row, (slot, contact) in enumerate(rows):
            self.contacts_table.setItem(row, 0, QTableWidgetItem(str(slot)))
            self.contacts_table.setItem(row, 1, QTableWidgetItem(contact.get("name", "")))
            self.contacts_table.setItem(row, 2, QTableWidgetItem(contact.get("number", "")))
            self.contacts_table.setItem(row, 3, QTableWidgetItem(contact.get("email", "")))
        self.contacts_group.setVisible(bool(rows))


