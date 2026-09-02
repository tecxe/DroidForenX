"""Shared device status bar — instant device detection, no threads needed."""
import time
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QPushButton, QFrame)
from PyQt6.QtCore import Qt, pyqtSignal as Signal
from ...core import adb_manager as adb

# Global cache — one scan shared across all status bars
_cache = {"devices": [], "ts": 0.0}
CACHE_TTL = 2.0  # seconds


def _scan_devices_cached() -> list:
    """Return cached device list, or scan if cache is stale."""
    now = time.monotonic()
    if now - _cache["ts"] < CACHE_TTL:
        return _cache["devices"]
    try:
        devices = adb.list_devices()
    except Exception:
        devices = []
    _cache["devices"] = devices
    _cache["ts"] = now
    return devices


class DeviceStatusBar(QFrame):
    """Reusable device status bar — embeds in every forensic page header."""
    device_rescanned = Signal(list)
    device_connected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices = []
        self.setStyleSheet("""
            QFrame {
                background: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 10px 16px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        self.status_icon = QLabel("●")
        self.status_icon.setStyleSheet("font-size: 14px; color: #ef4444; border: none;")
        layout.addWidget(self.status_icon)

        self.status_text = QLabel("Scanning...")
        self.status_text.setStyleSheet("font-size: 13px; color: #f59e0b; border: none;")
        layout.addWidget(self.status_text)

        self.device_label = QLabel("")
        self.device_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #e2e8f0; border: none;")
        layout.addWidget(self.device_label)

        layout.addStretch()

        btn_scan = QPushButton("Rescan")
        btn_scan.setFixedHeight(30)
        btn_scan.setFixedWidth(80)
        btn_scan.setStyleSheet("""
            QPushButton {
                background: #1e293b; color: #94a3b8; border: 1px solid #334155;
                border-radius: 6px; font-size: 12px; font-weight: 600;
            }
            QPushButton:hover { background: #334155; color: #e2e8f0; }
        """)
        btn_scan.clicked.connect(self.scan)
        layout.addWidget(btn_scan)

    def _update_ui(self, devices):
        if devices:
            d = devices[0]
            model = d.get("model", "") or d.get("device", "") or "Unknown"
            serial = d.get("serial", "")
            self.status_icon.setStyleSheet("font-size: 14px; color: #10b981; border: none;")
            self.status_text.setText("Connected:")
            self.status_text.setStyleSheet("font-size: 13px; color: #10b981; border: none;")
            self.device_label.setText(f"{model} ({serial})")
        else:
            self.status_icon.setStyleSheet("font-size: 14px; color: #ef4444; border: none;")
            self.status_text.setText("No device — connect USB & enable debugging")
            self.status_text.setStyleSheet("font-size: 13px; color: #94a3b8; border: none;")
            self.device_label.setText("")

    def scan(self):
        """Instant synchronous scan (~200ms). Called on page show and button click."""
        devices = _scan_devices_cached()
        self._devices = devices
        self._update_ui(devices)
        self.device_rescanned.emit(devices)
        if devices:
            self.device_connected.emit(devices[0])

    def auto_scan(self):
        """Alias for scan — called from showEvent."""
        self.scan()

    def get_first_serial(self) -> str:
        if self._devices:
            return self._devices[0].get("serial", "")
        return ""

    def get_devices(self) -> list:
        return list(self._devices)
