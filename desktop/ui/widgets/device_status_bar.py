"""Shared device status bar — device detection kept off the UI thread."""
import time
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QPushButton, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal as Signal
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


class _DeviceScanWorker(QThread):
    """Run the adb probe off the GUI thread.

    ``adb devices`` is a subprocess. During an acquisition the adb server is
    already busy, so a synchronous call on the GUI thread can stall for
    hundreds of milliseconds every time a page is shown. Doing it here keeps
    navigation responsive no matter what the device is doing.
    """

    done = Signal(list)

    def run(self):
        try:
            devices = _scan_devices_cached()
        except Exception:
            devices = []
        self.done.emit(devices)


#: Workers free-standing scans keep alive until they finish. Qt aborts the
#: process if the last reference to a running QThread is dropped.
_live_scans = set()


def scan_async(callback) -> "_DeviceScanWorker":
    """Probe adb on a worker thread and hand the list to ``callback``.

    Same contract as the old synchronous scan, without the GUI-thread stall.
    The callback runs on the GUI thread.
    """
    worker = _DeviceScanWorker()
    _live_scans.add(worker)

    def _cleanup():
        _live_scans.discard(worker)
        worker.deleteLater()

    worker.done.connect(callback)
    worker.finished.connect(_cleanup)
    worker.start()
    return worker


class DeviceStatusBar(QFrame):
    """Reusable device status bar — embeds in every forensic page header."""
    device_rescanned = Signal(list)
    device_connected = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._devices = []
        self._scan_worker = None
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
            state = str(d.get("state", "") or "device")
            ready = bool(d.get("ready", state == "device"))
            if ready:
                self.status_icon.setStyleSheet("font-size: 14px; color: #10b981; border: none;")
                self.status_text.setText("Connected:")
                self.status_text.setStyleSheet("font-size: 13px; color: #10b981; border: none;")
                self.device_label.setText(f"{model} ({serial})")
            else:
                # The handset is on the cable but not usable yet. Saying
                # "Connected" here made a pending USB-debugging prompt look
                # like a device that had simply stopped answering.
                if state == "unauthorized":
                    hint = "Unauthorized — tap Allow USB debugging on the phone"
                elif state == "offline":
                    hint = "Offline — replug the cable or restart adb"
                else:
                    hint = f"Waiting for the phone ({state})"
                self.status_icon.setStyleSheet("font-size: 14px; color: #f59e0b; border: none;")
                self.status_text.setText(hint)
                self.status_text.setStyleSheet("font-size: 13px; color: #f59e0b; border: none;")
                self.device_label.setText(f"{model} ({serial})" if model != "Unknown" else serial)
        else:
            self.status_icon.setStyleSheet("font-size: 14px; color: #ef4444; border: none;")
            self.status_text.setText("No device — connect USB & enable debugging")
            self.status_text.setStyleSheet("font-size: 13px; color: #94a3b8; border: none;")
            self.device_label.setText("")

    def scan(self):
        """Scan for devices without blocking the GUI thread.

        A fresh cache is applied immediately (no adb call, no worker). A stale
        cache is refreshed on a worker thread; the result is delivered through
        the same signals as before, so listeners behave exactly as they did
        with the old synchronous version.
        """
        if time.monotonic() - _cache["ts"] < CACHE_TTL:
            self._on_scan_done(list(_cache["devices"]))
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        self._scan_worker = _DeviceScanWorker()
        self._scan_worker.done.connect(self._on_scan_done)
        self._scan_worker.finished.connect(self._release_scan_worker)
        self._scan_worker.start()

    def _release_scan_worker(self):
        # The thread has finished, so the object can be dropped without
        # aborting the process. Without this, every poll on a visible page
        # left a dead QThread behind.
        worker = self._scan_worker
        self._scan_worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_scan_done(self, devices):
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
