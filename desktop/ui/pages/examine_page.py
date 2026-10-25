"""Stage 4: Examination & Analysis page — deep forensic analysis of acquired data."""
import json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QGroupBox, QGridLayout, QCheckBox, QScrollArea,
                              QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
                              QProgressBar)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSignal as Signal
from PyQt6.QtGui import QFont, QColor
from ..widgets.log_console import LogConsole
from ..widgets.device_status_bar import DeviceStatusBar
from ..widgets.scroll_page import enable_page_scroll
from ...core import forensic_engine as engine


class ExaminationWorker(QThread):
    progress = Signal(str, str)
    log = Signal(str, str)
    module_done = Signal(str, dict)
    finished = Signal(dict)

    def __init__(self, serial, modules):
        super().__init__()
        self.serial = serial
        self.modules = modules
        self._running = True

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            total = len(self.modules)
            results = {}
            for i, mod_key in enumerate(self.modules):
                if not self._running:
                    break
                if mod_key not in engine.ANALYSIS_MODULES:
                    continue
                name, func = engine.ANALYSIS_MODULES[mod_key]
                self.progress.emit(f"{i + 1}", str(total))
                self._log(f"Running: {name}...", "info")
                try:
                    findings = func(self.serial)
                    total_items = sum(len(v) for v in findings.values() if isinstance(v, list))
                    result = {"name": name, "status": "completed", "findings": findings, "item_count": total_items}
                    results[mod_key] = result
                    self._log(f"✓ {name}: {total_items} items extracted", "success")
                    self.module_done.emit(mod_key, result)
                except Exception as e:
                    result = {"name": name, "status": "failed", "findings": {"error": str(e)}, "item_count": 0}
                    results[mod_key] = result
                    self._log(f"✗ {name}: {e}", "error")
            self.finished.emit(results)
        except Exception as e:
            self._log(f"Examination error: {e}", "error")
            self.finished.emit({})

    def stop(self):
        self._running = False


class ExaminePage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._serial = ""
        self._all_results = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("🔬 Stage 4 — Examination & Analysis")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #f59e0b; background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel("Deep forensic analysis of device data — call logs, SMS, browser history, GPS, apps, and more.")
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(subtitle)

        # Device status bar
        self.device_bar = DeviceStatusBar()
        self.device_bar.device_rescanned.connect(self._on_device_rescanned)
        layout.addWidget(self.device_bar)

        # Module selection
        sel_group = QGroupBox("📋 Analysis Modules")
        sel_layout = QGridLayout(sel_group)
        sel_layout.setSpacing(8)

        modules = [
            ("call_log", "📞 Call Log Analysis"),
            ("sms_mms", "💬 SMS/MMS Analysis"),
            ("contacts", "👤 Contacts"),
            ("browser", "🌐 Browser History"),
            ("gps", "📍 GPS/Location"),
            ("wifi", "📶 WiFi History"),
            ("bluetooth", "🔵 Bluetooth"),
            ("apps", "📦 App Artifacts"),
            ("calendar", "📅 Calendar"),
            ("deleted", "🗑 Deleted Data Recovery"),
            ("notes", "📝 Notes & Memos"),
        ]
        self.module_checks = []
        for key, label in modules:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setProperty("module_key", key)
            cb.setStyleSheet("QCheckBox { font-size: 12px; color: #e2e8f0; spacing: 6px; }")
            sel_layout.addWidget(cb)
            self.module_checks.append(cb)
        layout.addWidget(sel_group)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_examine = QPushButton("🔬 Run Selected Analysis")
        self.btn_examine.setObjectName("btn-accent")
        self.btn_examine.setFixedHeight(42)
        self.btn_examine.clicked.connect(self._run_examination)
        btn_row.addWidget(self.btn_examine)

        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setObjectName("btn-danger")
        self.btn_stop.setFixedHeight(42)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        btn_row.addWidget(self.btn_stop)

        btn_row.addStretch()

        btn_next = QPushButton("Next: Reporting →")
        btn_next.setObjectName("btn-success")
        btn_next.setFixedHeight(42)
        btn_next.clicked.connect(lambda: self.navigate.emit("report"))
        btn_row.addWidget(btn_next)
        layout.addLayout(btn_row)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("font-size: 12px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(self.progress_label)

        # Results tabs
        self.tabs = QTabWidget()
        self.tabs.setVisible(False)
        layout.addWidget(self.tabs)

        # Console
        self.console = LogConsole("Examination Log")
        # This page previously pinned the console to 160px, which was the
        # tightest squeeze in the app. Keep the LogConsole's own readable floor.
        layout.addWidget(self.console)

        # No scroll area previously, so a short window compressed the console.
        enable_page_scroll(self)

    def set_device(self, serial: str):
        self._serial = serial

    def _on_device_rescanned(self, devices):
        if devices:
            self._serial = devices[0].get("serial", "")

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()

    def _run_examination(self):
        serial = self._serial or self.device_bar.get_first_serial()
        if not serial:
            self.console.log("No device detected. Connect a device via USB.", "error")
            return
        self._serial = serial
        selected = [cb.property("module_key") for cb in self.module_checks if cb.isChecked()]
        if not selected:
            self.console.log("No modules selected.", "warning")
            return

        self.btn_examine.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = ExaminationWorker(self._serial, selected)
        self.worker.log.connect(self.console.log)
        self.worker.progress.connect(self._on_progress)
        self.worker.module_done.connect(self._on_module_done)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _stop(self):
        if hasattr(self, "worker"):
            self.worker.stop()

    def _on_progress(self, current, total):
        try:
            done, steps = int(current), int(total)
            pct = min(99, int(done / steps * 100)) if steps > 0 else 0
            self.progress_bar.setValue(pct)
            self.progress_label.setText(
                f"{pct}%  \u00b7  module {done} of {steps}")
        except (ValueError, ZeroDivisionError):
            pass

    def _on_module_done(self, key, result):
        self._all_results[key] = result

    def _on_finished(self, results):
        self.btn_examine.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Examination complete!")
        self._all_results = results

        # Build results tabs
        self.tabs.clear()
        for key, result in results.items():
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(16, 12, 16, 12)
            tab_layout.setSpacing(8)

            header = QLabel(f"{result['name']} — {result['item_count']} items [{result['status'].upper()}]")
            color = "#10b981" if result["status"] == "completed" else "#ef4444"
            header.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {color}; background: transparent; border: none;")
            tab_layout.addWidget(header)

            findings = result["findings"]
            for sub_key, data in findings.items():
                if isinstance(data, list) and data:
                    sub_label = QLabel(f"{sub_key.replace('_', ' ').title()} ({len(data)} items)")
                    sub_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #64748b; background: transparent; border: none; margin-top: 8px;")
                    tab_layout.addWidget(sub_label)

                    table = QTableWidget()
                    table.setAlternatingRowColors(True)
                    table.setStyleSheet("""
                        QTableWidget { background: #111827; border: 1px solid #1e293b; border-radius: 6px;
                            gridline-color: #1e293b; font-size: 11px; }
                        QTableWidget::item { padding: 6px 8px; border-bottom: 1px solid #1e293b; }
                        QTableWidget::item:selected { background: rgba(37,99,235,0.15); }
                        QHeaderView::section { background: #1a2234; color: #64748b; border: none;
                            border-bottom: 2px solid #1e293b; font-size: 10px; font-weight: 600; padding: 8px; }
                    """)
                    table.verticalHeader().setVisible(False)
                    table.setShowGrid(False)

                    if isinstance(data[0], dict):
                        headers = list(data[0].keys())
                        table.setColumnCount(len(headers))
                        table.setHorizontalHeaderLabels(headers)
                        table.setRowCount(min(len(data), 500))
                        for r, row_data in enumerate(data[:500]):
                            for c, h in enumerate(headers):
                                val = str(row_data.get(h, ""))[:200]
                                item = QTableWidgetItem(val)
                                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                                table.setItem(r, c, item)
                    else:
                        table.setColumnCount(1)
                        table.setHorizontalHeaderLabels(["Value"])
                        table.setRowCount(min(len(data), 500))
                        for r, val in enumerate(data[:500]):
                            item = QTableWidgetItem(str(val)[:200])
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                            table.setItem(r, 0, item)

                    table.horizontalHeader().setStretchLastSection(True)
                    table.resizeColumnsToContents()
                    tab_layout.addWidget(table)

                elif isinstance(data, dict) and data:
                    for dk, dv in data.items():
                        if dv:
                            lbl = QLabel(f"{dk}: {str(dv)[:200]}")
                            lbl.setStyleSheet("font-size: 12px; color: #a8b4c4; background: transparent; border: none;")
                            tab_layout.addWidget(lbl)

            tab_layout.addStretch()
            self.tabs.addTab(tab, result["name"][:20])

        self.tabs.setVisible(True)
        total_items = sum(r["item_count"] for r in results.values())
        self.console.log(f"Examination complete: {len(results)} modules, {total_items} total items", "success")
