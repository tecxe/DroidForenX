"""Stage 2: Device Identification page — fingerprint device, enumerate storage."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QGridLayout, QGroupBox, QScrollArea, QProgressBar)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSignal as Signal
from ..widgets.log_console import LogConsole
from ..widgets.device_status_bar import DeviceStatusBar
from ..widgets.scroll_page import enable_page_scroll
from ...core import adb_manager as adb
import uuid
from datetime import datetime, timezone


class IdentificationWorker(QThread):
    finished = Signal(object, list)
    log = Signal(str, str)

    def __init__(self, serial):
        super().__init__()
        self.serial = serial

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            self._log("Starting device identification...", "info")
            info = adb.get_device_info(self.serial)
            self._log(f"Manufacturer: {info.manufacturer}", "info")
            self._log(f"Model: {info.model}", "info")
            self._log(f"Android: {info.android_version} (API {info.api_level})", "info")
            self._log(f"Security Patch: {info.security_patch}", "info")
            self._log(f"Encryption: {info.encryption_status}", "info")
            self._log(f"Bootloader: {info.bootloader_state}", "info")
            self._log(f"Kernel: {info.kernel_version}", "info")

            self._log("Enumerating installed packages...", "info")
            packages = adb.get_installed_packages(self.serial)
            self._log(f"Found {len(packages)} installed packages", "success")

            self._log("Device identification complete.", "success")
            self.finished.emit(info, packages)
        except Exception as e:
            self._log(f"Identification error: {e}", "error")


class IdentifyPage(QWidget):
    session_advanced = pyqtSignal()
    navigate = pyqtSignal(str)

    def __init__(self, db_get, parent=None):
        super().__init__(parent)
        self.db_get = db_get
        self._serial = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("Stage 2 — Device Identification")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #3b82f6; background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel("Fingerprint the device hardware, software, and storage architecture.")
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(subtitle)

        # Device status bar
        self.device_bar = DeviceStatusBar()
        self.device_bar.device_rescanned.connect(self._on_device_rescanned)
        layout.addWidget(self.device_bar)

        # Action row
        btn_row = QHBoxLayout()
        btn_identify = QPushButton("Identify Device")
        btn_identify.setObjectName("btn-accent")
        btn_identify.setFixedHeight(42)
        btn_identify.clicked.connect(self._identify_device)
        btn_row.addWidget(btn_identify)
        btn_row.addStretch()

        btn_next = QPushButton("Next: Acquisition")
        btn_next.setObjectName("btn-success")
        btn_next.setFixedHeight(42)
        btn_next.clicked.connect(lambda: self.navigate.emit("acquire"))
        btn_row.addWidget(btn_next)
        layout.addLayout(btn_row)

        # Results grid
        self.results_group = QGroupBox("Device Properties")
        self.results_group.setVisible(False)
        results_layout = QGridLayout(self.results_group)
        results_layout.setSpacing(10)
        self.specs_grid = results_layout
        layout.addWidget(self.results_group)

        # Package summary
        self.pkg_group = QGroupBox("Installed Packages")
        self.pkg_group.setVisible(False)
        pkg_layout = QVBoxLayout(self.pkg_group)
        self.pkg_label = QLabel()
        self.pkg_label.setStyleSheet("font-size: 13px; color: #e2e8f0; background: transparent; border: none;")
        self.pkg_label.setWordWrap(True)
        pkg_layout.addWidget(self.pkg_label)
        layout.addWidget(self.pkg_group)

        layout.addStretch()

        # Console
        self.console = LogConsole("Identification Log")
        self.console.setMinimumHeight(200)
        layout.addWidget(self.console)

        # The page had no scroll area, so a short window forced the layout to
        # compress the console instead of scrolling.
        enable_page_scroll(self)

    def set_device(self, serial: str):
        self._serial = serial

    def _on_device_rescanned(self, devices):
        if devices:
            self._serial = devices[0].get("serial", "")

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()

    def _identify_device(self):
        serial = self._serial or self.device_bar.get_first_serial()
        if not serial:
            self.console.log("No device detected. Connect a device via USB and enable debugging.", "error")
            return
        self._serial = serial
        self.worker = IdentificationWorker(serial)
        self.worker.log.connect(self.console.log)
        self.worker.finished.connect(self._on_identified)
        self.worker.start()

    def _on_identified(self, info, packages):
        specs = [
            ("Serial Number", info.serial),
            ("Manufacturer", info.manufacturer),
            ("Model", info.model),
            ("Device Name", info.device_name),
            ("Android Version", info.android_version),
            ("API Level", str(info.api_level)),
            ("Security Patch", info.security_patch),
            ("Build Fingerprint", info.build_fingerprint),
            ("Kernel Version", info.kernel_version),
            ("Encryption Status", info.encryption_status),
            ("Bootloader State", info.bootloader_state),
            ("SIM State", info.sim_state),
            ("Battery", f"{info.battery_level}% ({info.battery_temp}°C)"),
            ("Storage", f"{info.total_storage // (1024**3)}GB total, {info.free_storage // (1024**3)}GB free"),
        ]

        # Clear old
        while self.specs_grid.count():
            item = self.specs_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for i, (key, val) in enumerate(specs):
            k = QLabel(f"{key}:")
            k.setStyleSheet("font-size: 12px; font-weight: 600; color: #64748b; background: transparent; border: none;")
            v = QLabel(val if val else "N/A")
            v.setStyleSheet("font-size: 12px; color: #e2e8f0; background: transparent; border: none;")
            self.specs_grid.addWidget(k, i // 3, (i % 3) * 2)
            self.specs_grid.addWidget(v, i // 3, (i % 3) * 2 + 1)

        self.results_group.setVisible(True)

        social = [p for p in packages if any(k in p["package"].lower() for k in ["facebook", "twitter", "instagram", "snapchat", "whatsapp", "telegram", "signal", "tiktok"])]
        self.pkg_label.setText(
            f"Total packages: {len(packages)}  |  Social media: {len(social)}  |  "
            f"Third-party: {len([p for p in packages if not p['package'].startswith('com.android')])}"
        )
        self.pkg_group.setVisible(True)
        self.session_advanced.emit()
