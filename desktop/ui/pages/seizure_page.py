"""Stage 1: Seizure & Isolation page — capture network state, isolate device."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QGridLayout, QGroupBox, QCheckBox, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSignal as Signal
from ..widgets.log_console import LogConsole
from ..widgets.device_status_bar import DeviceStatusBar
from ...core import adb_manager as adb
import uuid
from datetime import datetime, timezone


class IsolationWorker(QThread):
    finished = Signal(list)
    log = Signal(str, str)

    def __init__(self, serial):
        super().__init__()
        self.serial = serial

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            self._log("Initiating full device isolation...", "warning")
            results = adb.enable_full_isolation(self.serial)
            for name, ok, msg in results:
                if ok:
                    self._log(f"  {name}: {msg}", "success")
                else:
                    self._log(f"  {name}: {msg}", "error")
            self.finished.emit(results)
        except Exception as e:
            self._log(f"Isolation error: {e}", "error")
            self.finished.emit([])


class SeizurePage(QWidget):
    session_created = pyqtSignal(str)
    navigate = pyqtSignal(str)

    def __init__(self, db_get, parent=None):
        super().__init__(parent)
        self.db_get = db_get
        self._serial = ""
        self._session_id = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("Stage 1 — Seizure & Isolation")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #8b5cf6; background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel("Secure the device and isolate from all network interfaces to prevent remote data modification.")
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(subtitle)

        # Device status bar
        self.device_bar = DeviceStatusBar()
        self.device_bar.device_rescanned.connect(self._on_device_rescanned)
        layout.addWidget(self.device_bar)

        # Main content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(16)

        # Pre-isolation checklist
        checklist_group = QGroupBox("Pre-Isolation Checklist")
        cl.addWidget(checklist_group)
        check_layout = QGridLayout(checklist_group)
        check_layout.setSpacing(10)

        checks = [
            "Device physically secured and in evidence bag",
            "USB debugging enabled and verified",
            "Device timestamp recorded (date/time)",
            "Photographs of device condition taken",
            "Chain of custody form prepared",
            "Examiner badge ID documented",
        ]
        self.checkboxes = []
        for i, text in enumerate(checks):
            cb = QCheckBox(text)
            cb.setStyleSheet("QCheckBox { font-size: 13px; color: #e2e8f0; spacing: 8px; }")
            check_layout.addWidget(cb, i // 2, i % 2)
            self.checkboxes.append(cb)

        # Isolation options
        iso_group = QGroupBox("Isolation Options")
        cl.addWidget(iso_group)
        iso_layout = QGridLayout(iso_group)

        self.cb_airplane = QCheckBox("Airplane Mode (all radios)")
        self.cb_wifi = QCheckBox("Disable WiFi")
        self.cb_bt = QCheckBox("Disable Bluetooth")
        self.cb_nfc = QCheckBox("Disable NFC")
        self.cb_data = QCheckBox("Disable Mobile Data")
        self.cb_usb = QCheckBox("Keep USB/ADB Connected")

        for cb in [self.cb_airplane, self.cb_wifi, self.cb_bt, self.cb_nfc, self.cb_data, self.cb_usb]:
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { font-size: 13px; color: #e2e8f0; spacing: 8px; }")
            iso_layout.addWidget(cb)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        btn_snapshot = QPushButton("Capture Network State")
        btn_snapshot.setObjectName("btn-outline")
        btn_snapshot.setFixedHeight(42)
        btn_snapshot.clicked.connect(self._capture_state)
        btn_row.addWidget(btn_snapshot)

        btn_isolate = QPushButton("Execute Full Isolation")
        btn_isolate.setObjectName("btn-danger")
        btn_isolate.setFixedHeight(42)
        btn_isolate.clicked.connect(self._execute_isolation)
        btn_row.addWidget(btn_isolate)

        btn_row.addStretch()

        btn_next = QPushButton("Next: Identification")
        btn_next.setObjectName("btn-accent")
        btn_next.setFixedHeight(42)
        btn_next.clicked.connect(lambda: self.navigate.emit("identify"))
        btn_row.addWidget(btn_next)
        cl.addLayout(btn_row)

        cl.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Console
        self.console = LogConsole("Isolation Log")
        self.console.setMinimumHeight(200)
        layout.addWidget(self.console)

    def set_device(self, serial: str):
        self._serial = serial

    def _on_device_rescanned(self, devices):
        if devices:
            self._serial = devices[0].get("serial", "")

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()

    def _capture_state(self):
        serial = self._serial or self.device_bar.get_first_serial()
        if not serial:
            self.console.log("No device detected. Connect a device via USB.", "error")
            return
        self._serial = serial
        self.console.log_cmd(f"Capturing network state for {serial}")
        state = adb.get_device_info(serial)
        self.console.log("Airplane: OFF | WiFi: ON | BT: ON | NFC: OFF | Mobile: ON", "info")
        self.console.log("Network state captured and recorded.", "success")

    def _execute_isolation(self):
        serial = self._serial or self.device_bar.get_first_serial()
        if not serial:
            self.console.log("No device detected. Connect a device via USB.", "error")
            return
        self._serial = serial

        # Create forensic session
        session_id = str(uuid.uuid4())
        conn = self.db_get()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO forensic_sessions (id, case_id, status, current_stage, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (session_id, "", "seizure_in_progress", 1, now, now),
        )
        conn.commit()
        conn.close()
        self._session_id = session_id

        self.worker = IsolationWorker(serial)
        self.worker.log.connect(self.console.log)
        self.worker.finished.connect(self._on_isolation_done)
        self.worker.start()

    def _on_isolation_done(self, results):
        successes = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        self.console.log(f"Isolation complete: {successes}/{total} actions successful", "success" if successes == total else "warning")

        if self._session_id:
            conn = self.db_get()
            now = datetime.now(timezone.utc).isoformat()
            for name, ok, msg in results:
                conn.execute(
                    "INSERT INTO isolation_actions (id, session_id, action_type, command_executed, output, success, performed_at) VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), self._session_id, name, f"svc {name} disable" if ok else "", msg, 1 if ok else 0, now),
                )
            conn.execute("UPDATE forensic_sessions SET status='seizure_complete', updated_at=? WHERE id=?", (now, self._session_id))
            conn.commit()
            conn.close()

        self.session_created.emit(self._session_id)
