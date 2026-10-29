"""Device connection page — detect, connect, fingerprint Android devices."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QGridLayout, QScrollArea, QProgressBar, QGroupBox)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSignal as Signal
from PyQt6.QtGui import QFont
from ..widgets.log_console import LogConsole
from ...core import adb_manager as adb


class DeviceScanWorker(QThread):
    finished = Signal(list)
    log = Signal(str, str)

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            self._log("Scanning for connected devices...", "info")
            devices = adb.list_devices()
            self._log(f"Found {len(devices)} device(s)", "success" if devices else "warning")
            self.finished.emit(devices)
        except Exception as e:
            self._log(f"Scan error: {e}", "error")
            self.finished.emit([])


class DeviceInfoWorker(QThread):
    finished = Signal(object)
    log = Signal(str, str)

    def __init__(self, serial):
        super().__init__()
        self.serial = serial

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            self._log(f"Querying device properties: {self.serial}", "info")
            info = adb.get_device_info(self.serial)
            self._log(f"Device identified: {info.manufacturer} {info.model} (Android {info.android_version})", "success")
            self.finished.emit(info)
        except Exception as e:
            self._log(f"Identification error: {e}", "error")


class DevicePage(QWidget):
    device_connected = pyqtSignal(dict)
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_device = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        # Header
        header = QHBoxLayout()
        title = QLabel("📱 Device Connection")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #e2e8f0; background: transparent; border: none;")
        header.addWidget(title)
        header.addStretch()

        btn_scan = QPushButton("🔍 Scan for Devices")
        btn_scan.setObjectName("btn-accent")
        btn_scan.setFixedHeight(40)
        btn_scan.clicked.connect(self._scan_devices)
        header.addWidget(btn_scan)
        layout.addLayout(header)

        # Device cards area
        self.device_area = QScrollArea()
        self.device_area.setWidgetResizable(True)
        self.device_area.setFrameShape(QFrame.Shape.NoFrame)
        self.device_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.device_container = QWidget()
        self.device_grid = QVBoxLayout(self.device_container)
        self.device_grid.setSpacing(12)
        self.device_grid.setAlignment(Qt.AlignmentFlag.AlignTop)

        placeholder = QLabel("No devices detected. Click 'Scan for Devices' to begin.")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #64748b; font-size: 14px; padding: 60px; background: transparent; border: none;")
        self.device_grid.addWidget(placeholder)
        self.device_area.setWidget(self.device_container)
        layout.addWidget(self.device_area)

        # Device info panel (hidden initially)
        self.info_panel = QFrame()
        self.info_panel.setObjectName("infoPanel")
        # Scoped to the panel itself. A bare "QFrame { padding: 20px; }" also
        # matches every QLabel inside it -- QLabel derives from QFrame -- so each
        # specification line was inflated to a 44 px box and the grid was pushed
        # out of the panel. The header showed; the specifications did not. This is
        # the same trap that collapsed the acquisition results card and the
        # sidebar footer, and the padding belongs on the layout, not the selector.
        self.info_panel.setStyleSheet(
            "QFrame#infoPanel { background: #111827; border: 1px solid #1e293b;"
            " border-radius: 10px; }")
        self.info_panel.setVisible(False)
        info_layout = QVBoxLayout(self.info_panel)
        info_layout.setContentsMargins(20, 20, 20, 20)
        info_layout.setSpacing(12)

        self.device_header = QLabel()
        self.device_header.setStyleSheet("font-size: 18px; font-weight: 700; color: #10b981; background: transparent; border: none;")
        info_layout.addWidget(self.device_header)

        self.device_specs = QGridLayout()
        self.device_specs.setSpacing(8)
        info_layout.addLayout(self.device_specs)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_acquire = QPushButton("📥 Begin Acquisition")
        btn_acquire.setObjectName("btn-success")
        btn_acquire.setFixedHeight(38)
        btn_acquire.clicked.connect(lambda: self.navigate.emit("acquire"))
        btn_row.addWidget(btn_acquire)

        btn_sim = QPushButton("💳 SIM Info")
        btn_sim.setObjectName("btn-outline")
        btn_sim.setFixedHeight(38)
        # This button shipped with no handler at all: it was drawn, looked
        # pressable, and did nothing. It now opens the SIM page.
        btn_sim.clicked.connect(lambda: self.navigate.emit("sim"))
        btn_row.addWidget(btn_sim)
        self.btn_sim = btn_sim

        btn_row.addStretch()
        info_layout.addLayout(btn_row)

        layout.addWidget(self.info_panel)

        # Console
        self.console = LogConsole("Device Connection Log")
        self.console.setMinimumHeight(180)
        layout.addWidget(self.console)

    def _scan_devices(self):
        self.console.log_cmd("adb devices -l")
        self.worker = DeviceScanWorker()
        self.worker.log.connect(self.console.log)
        self.worker.finished.connect(self._on_devices_found)
        self.worker.start()

    def _on_devices_found(self, devices):
        # Clear existing cards
        while self.device_grid.count():
            item = self.device_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not devices:
            lbl = QLabel("No devices detected. Ensure USB debugging is enabled and device is connected.")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #f59e0b; font-size: 14px; padding: 40px; background: transparent; border: none;")
            self.device_grid.addWidget(lbl)
            return

        for dev in devices:
            serial = dev.get("serial", "")
            if not serial:
                continue
            card = QFrame()
            card.setObjectName("deviceCard")
            # Scoped, and the padding moved onto the layout. A bare "QFrame"
            # selector also matches the labels inside the card -- QLabel is a
            # QFrame -- so the 16 px padding landed on the model name and the
            # serial, giving a one-line card a 60 px interior.
            card.setStyleSheet("""
                QFrame#deviceCard {
                    background: #111827; border: 1px solid #1e293b;
                    border-left: 3px solid #10b981; border-radius: 8px;
                }
                QFrame#deviceCard:hover { border-color: #3b82f6; }
            """)
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            card_layout.setSpacing(16)

            icon = QLabel("📱")
            icon.setStyleSheet("font-size: 28px; background: transparent; border: none;")
            card_layout.addWidget(icon)

            info = QVBoxLayout()
            info.setSpacing(4)
            model_name = dev.get("model", "") or dev.get("device", "") or "Unknown Device"
            name = QLabel(model_name)
            name.setStyleSheet("font-size: 15px; font-weight: 700; color: #e2e8f0; background: transparent; border: none;")
            info.addWidget(name)

            serial_lbl = QLabel(f"Serial: {serial}")
            serial_lbl.setStyleSheet("font-size: 12px; color: #64748b; font-family: 'Cascadia Code', monospace; background: transparent; border: none;")
            info.addWidget(serial_lbl)
            card_layout.addLayout(info)
            card_layout.addStretch()

            btn_connect = QPushButton("Connect & Identify")
            btn_connect.setObjectName("btn-success")
            btn_connect.setFixedHeight(36)
            btn_connect.clicked.connect(lambda checked, s=serial: self._connect_device(s))
            card_layout.addWidget(btn_connect)

            self.device_grid.addWidget(card)

    def _connect_device(self, serial):
        self.console.log_cmd(f"Connecting to {serial}...")
        self.info_worker = DeviceInfoWorker(serial)
        self.info_worker.log.connect(self.console.log)
        self.info_worker.finished.connect(self._on_device_info)
        self.info_worker.start()

    def _on_device_info(self, info):
        self._current_device = info
        self.device_header.setText(f"{info.manufacturer} {info.model} ({info.device_name})")

        specs = [
            ("Serial", info.serial),
            ("Android", f"{info.android_version} (API {info.api_level})"),
            ("Security Patch", info.security_patch),
            ("Encryption", info.encryption_status),
            ("Bootloader", info.bootloader_state),
            ("Kernel", info.kernel_version),
            ("Battery", f"{info.battery_level}% ({info.battery_temp}°C)"),
            ("Storage", f"{info.free_storage // (1024**3)}GB free / {info.total_storage // (1024**3)}GB total"),
            ("SIM State", info.sim_state),
        ]

        # Clear old specs
        while self.device_specs.count():
            item = self.device_specs.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for i, (key, val) in enumerate(specs):
            k = QLabel(f"{key}:")
            k.setStyleSheet("font-size: 12px; font-weight: 600; color: #64748b; background: transparent; border: none;")
            v = QLabel(str(val) if val else "N/A")
            v.setStyleSheet("font-size: 12px; color: #e2e8f0; font-family: 'Cascadia Code', monospace; background: transparent; border: none;")
            self.device_specs.addWidget(k, i // 3, (i % 3) * 2)
            self.device_specs.addWidget(v, i // 3, (i % 3) * 2 + 1)

        self.info_panel.setVisible(True)
        self.device_connected.emit({
            "serial": info.serial,
            "model": info.model,
            "manufacturer": info.manufacturer,
            "device_name": info.device_name,
            "android_version": info.android_version,
            "api_level": info.api_level,
            "security_patch": info.security_patch,
            "encryption_status": info.encryption_status,
            "bootloader_state": info.bootloader_state,
            "kernel_version": info.kernel_version,
            "battery_level": info.battery_level,
            "total_storage": info.total_storage,
            "free_storage": info.free_storage,
        })
