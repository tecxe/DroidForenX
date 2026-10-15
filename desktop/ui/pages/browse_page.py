from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QLineEdit,
    QHeaderView, QProgressBar, QFileDialog, QGroupBox, QComboBox, QDialog)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QPixmap, QFont
from pathlib import Path
import os, json, hashlib, sqlite3, time, subprocess, tempfile

from ..widgets.scroll_page import enable_page_scroll


DARK_STYLE = """
QWidget { background: #0f172a; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QFrame { background: #111827; border: 1px solid #1e293b; border-radius: 6px; }
QLabel { color: #e2e8f0; }
QPushButton { background: #3b82f6; color: #fff; border: none; border-radius: 4px;
    padding: 8px 16px; font-weight: 600; font-size: 12px; }
QPushButton:hover { background: #2563eb; }
QPushButton:pressed { background: #1d4ed8; }
QPushButton:disabled { background: #1e293b; color: #64748b; }
QLineEdit { background: #1e293b; border: 1px solid #334155; border-radius: 4px;
    padding: 8px 12px; color: #e2e8f0; font-size: 13px; }
QLineEdit:focus { border: 1px solid #3b82f6; }
QTreeWidget { background: #111827; border: 1px solid #1e293b; border-radius: 6px;
    color: #e2e8f0; outline: none; font-size: 13px; }
QTreeWidget::item { padding: 5px 8px; border-bottom: 1px solid #1e293b; }
QTreeWidget::item:selected { background: #3b82f6; color: #fff; }
QTreeWidget::item:hover { background: #1e293b; }
QTreeWidget::branch { background: #111827; }
QHeaderView::section { background: #1e293b; color: #e2e8f0; border: none;
    border-right: 1px solid #0f172a; padding: 6px 10px; font-weight: 600; font-size: 12px; }
QTextEdit { background: #1e293b; border: 1px solid #334155; border-radius: 4px;
    color: #e2e8f0; font-family: 'Consolas', monospace; font-size: 12px; padding: 8px; }
QProgressBar { background: #1e293b; border: none; border-radius: 4px; height: 4px;
    text-align: center; color: transparent; }
QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
QGroupBox { border: 1px solid #1e293b; border-radius: 6px; margin-top: 12px;
    padding-top: 16px; font-weight: bold; color: #94a3b8; font-size: 12px;
    text-transform: uppercase; letter-spacing: 1px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; padding: 0 6px; color: #94a3b8; }
QComboBox { background: #1e293b; border: 1px solid #334155; border-radius: 6px;
    padding: 6px 12px; color: #e2e8f0; min-width: 140px; }
QComboBox:hover { border-color: #3b82f6; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView { background: #1e293b; border: 1px solid #334155;
    color: #e2e8f0; selection-background-color: #3b82f6; }
"""


class AdbListWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, serial, remote_path):
        super().__init__()
        self.serial = serial
        self.remote_path = remote_path

    def run(self):
        try:
            from ...core import adb_manager as adb
            ok, output = adb.adb_shell(self.serial, f'ls -la "{self.remote_path}" 2>/dev/null', timeout=20)
            if not ok or not output:
                self.finished.emit([])
                return
            entries = []
            for line in output.splitlines():
                line = line.strip()
                if not line or line.startswith("total"):
                    continue
                parts = line.split(None, 7)
                if len(parts) < 8:
                    continue
                perms = parts[0]
                size_str = parts[4]
                fname = parts[-1]
                is_dir = perms.startswith("d")
                try:
                    size = int(size_str)
                except ValueError:
                    size = 0
                date_parts = parts[5:8]
                mod_date = " ".join(date_parts) if date_parts else ""
                entries.append({
                    "name": fname,
                    "is_dir": is_dir,
                    "size": size,
                    "modified": mod_date,
                    "perms": perms,
                    "path": f"{self.remote_path.rstrip('/')}/{fname}",
                })
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
            self.finished.emit(entries)
        except Exception as e:
            self.error.emit(str(e))


class AdbStorageWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, serial):
        super().__init__()
        self.serial = serial

    def run(self):
        try:
            from ...core import adb_manager as adb
            storage = {}
            ok, output = adb.adb_shell(self.serial, "df -h 2>/dev/null", timeout=10)
            if ok and output:
                for line in output.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 6:
                        mount = parts[5]
                        if mount in ("/data", "/sdcard", "/system", "/vendor", "/cache", "/mnt/expand", "/metadata"):
                            storage[mount] = {
                                "total": parts[1],
                                "used": parts[2],
                                "avail": parts[3],
                                "use_pct": parts[4],
                                "device": parts[0],
                            }
            ok2, output2 = adb.adb_shell(self.serial, "getprop ro.product.storage.size; getprop ro.build.display.id; cat /sys/class/block/mmcblk0/device/type 2>/dev/null; cat /proc/partitions 2>/dev/null", timeout=10)
            if ok2 and output2:
                for line in output2.splitlines():
                    line = line.strip()
                    if line.isdigit() and int(line) > 1000:
                        storage["_emmc_total"] = line
            ok3, output3 = adb.adb_shell(self.serial, "ls /storage/ 2>/dev/null; ls /mnt/media_rw/ 2>/dev/null", timeout=10)
            if ok3 and output3:
                for line in output3.splitlines():
                    mount_name = line.strip()
                    if mount_name and mount_name not in (".", "..", "self", "emulated", "0"):
                        ok4, sd_info = adb.adb_shell(self.serial, f"df /storage/{mount_name} 2>/dev/null | tail -1", timeout=10)
                        if ok4 and sd_info:
                            sd_parts = sd_info.split()
                            if len(sd_parts) >= 4:
                                storage[f"/storage/{mount_name}"] = {
                                    "total": sd_parts[1],
                                    "used": sd_parts[2],
                                    "avail": sd_parts[3],
                                    "use_pct": sd_parts[4] if len(sd_parts) > 4 else "?",
                                    "device": sd_parts[0],
                                    "label": "SD Card",
                                }
            self.finished.emit(storage)
        except Exception as e:
            self.error.emit(str(e))


class BrowsePage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.evidence_path = Path.home() / ".droidforenx" / "evidence"
        self.current_file = None
        self.current_serial = None
        self.current_remote_path = "/sdcard"
        self.mode = "device"
        self.setStyleSheet(DARK_STYLE)
        self._setup_ui()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        header_row = QHBoxLayout()
        header = QLabel("File System Browser")
        header.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        header.setStyleSheet("color: #e2e8f0; background: transparent; border: none;")
        header_row.addWidget(header)
        header_row.addStretch()

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Live Device", "Evidence Files"])
        self.mode_combo.setFixedHeight(32)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        header_row.addWidget(self.mode_combo)
        root_layout.addLayout(header_row)

        from ..widgets.device_status_bar import DeviceStatusBar
        self.device_bar = DeviceStatusBar()
        self.device_bar.device_connected.connect(self._on_device_connected)
        root_layout.addWidget(self.device_bar)

        storage_frame = QFrame()
        storage_frame.setStyleSheet("QFrame { background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; }")
        storage_layout = QHBoxLayout(storage_frame)
        storage_layout.setSpacing(24)
        storage_layout.setContentsMargins(16, 10, 16, 10)

        self.storage_labels = {}
        for key, label_text, color in [
            ("total", "Total Storage", "#3b82f6"),
            ("used", "Used", "#f59e0b"),
            ("avail", "Available", "#10b981"),
            ("data_info", "Data Partition", "#8b5cf6"),
        ]:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(label_text.upper())
            lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 700; letter-spacing: 1px; background: transparent; border: none;")
            col.addWidget(lbl)
            val = QLabel("--")
            val.setStyleSheet("color: #e2e8f0; font-size: 16px; font-weight: 700; background: transparent; border: none;")
            col.addWidget(val)
            self.storage_labels[key] = val
            storage_layout.addLayout(col)

        self.refresh_storage_btn = QPushButton("\u21bb Refresh")
        self.refresh_storage_btn.setFixedHeight(32)
        self.refresh_storage_btn.setFixedWidth(90)
        self.refresh_storage_btn.clicked.connect(self._load_storage)
        storage_layout.addWidget(self.refresh_storage_btn)
        root_layout.addWidget(storage_frame)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.path_bar = QLineEdit()
        self.path_bar.setPlaceholderText("Path (type and press Enter to navigate)...")
        self.path_bar.setFixedHeight(36)
        self.path_bar.returnPressed.connect(self._on_path_submit)
        search_row.addWidget(self.path_bar)

        self.up_btn = QPushButton("\u2191 Up")
        self.up_btn.setFixedHeight(36)
        self.up_btn.setFixedWidth(60)
        self.up_btn.clicked.connect(self._go_up)
        search_row.addWidget(self.up_btn)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search files by name...")
        self.search_bar.setFixedHeight(36)
        self.search_bar.textChanged.connect(self._search_files)
        search_row.addWidget(self.search_bar)

        self.export_btn = QPushButton("Export Selected")
        self.export_btn.setFixedHeight(36)
        self.export_btn.setFixedWidth(130)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_file)
        search_row.addWidget(self.export_btn)

        root_layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background: #1e293b; }")

        left_frame = QFrame()
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        left_label = QLabel("  Directory Tree")
        left_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        left_label.setStyleSheet("color: #94a3b8; background: #111827; padding: 8px; border: 1px solid #1e293b; border-radius: 6px 6px 0 0; border-bottom: none;")
        left_layout.addWidget(left_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "Perms"])
        self.tree.setColumnCount(4)
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setAnimated(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSortingEnabled(True)
        self.tree.itemClicked.connect(self._on_file_selected)
        left_layout.addWidget(self.tree)

        splitter.addWidget(left_frame)

        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(10)

        details_label = QLabel("File Details")
        details_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        details_label.setStyleSheet("color: #94a3b8; background: transparent; border: none; margin-bottom: 4px;")
        right_layout.addWidget(details_label)

        self.file_name_label = QLabel("No file selected")
        self.file_name_label.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.file_name_label.setStyleSheet("color: #3b82f6; background: transparent; border: none;")
        self.file_name_label.setWordWrap(True)
        right_layout.addWidget(self.file_name_label)

        meta_group = QGroupBox("Metadata")
        meta_layout = QVBoxLayout(meta_group)
        meta_layout.setSpacing(6)

        self.size_label = QLabel("Size: --")
        self.size_label.setStyleSheet("color: #e2e8f0; background: transparent; border: none; font-size: 12px;")
        meta_layout.addWidget(self.size_label)

        self.modified_label = QLabel("Modified: --")
        self.modified_label.setStyleSheet("color: #e2e8f0; background: transparent; border: none; font-size: 12px;")
        meta_layout.addWidget(self.modified_label)

        self.hash_label = QLabel("SHA-256: --")
        self.hash_label.setStyleSheet("color: #e2e8f0; background: transparent; border: none; font-size: 12px;")
        self.hash_label.setWordWrap(True)
        meta_layout.addWidget(self.hash_label)

        self.device_path_label = QLabel("Device Path: --")
        self.device_path_label.setStyleSheet("color: #94a3b8; background: transparent; border: none; font-size: 11px;")
        self.device_path_label.setWordWrap(True)
        meta_layout.addWidget(self.device_path_label)

        right_layout.addWidget(meta_group)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self.pull_btn = QPushButton("Pull from Device")
        self.pull_btn.setFixedHeight(36)
        self.pull_btn.setStyleSheet("QPushButton { background: #22c55e; color: white; border-radius: 6px; font-weight: 600; } QPushButton:hover { background: #16a34a; }")
        self.pull_btn.setVisible(False)
        self.pull_btn.clicked.connect(self._pull_and_preview)
        action_row.addWidget(self.pull_btn)

        self.open_btn = QPushButton("Open in System")
        self.open_btn.setFixedHeight(36)
        self.open_btn.setStyleSheet("QPushButton { background: #3b82f6; color: white; border-radius: 6px; font-weight: 600; } QPushButton:hover { background: #2563eb; }")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self._open_in_system)
        action_row.addWidget(self.open_btn)

        action_row.addStretch()
        right_layout.addLayout(action_row)

        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setVisible(False)
        preview_layout.addWidget(self.preview_text)

        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setStyleSheet("background: #0f172a; border: 1px solid #1e293b; border-radius: 4px;")
        self.preview_image.setMinimumHeight(200)
        self.preview_image.setVisible(False)
        preview_layout.addWidget(self.preview_image)

        self.no_preview = QLabel("Select a file to preview")
        self.no_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_preview.setStyleSheet("color: #64748b; background: transparent; border: none; font-size: 13px;")
        preview_layout.addWidget(self.no_preview)

        right_layout.addWidget(preview_group, 1)

        splitter.addWidget(right_frame)
        splitter.setSizes([350, 500])

        root_layout.addWidget(splitter, 1)

        status_row = QHBoxLayout()
        status_row.setSpacing(16)

        self.file_count_label = QLabel("Files: 0")
        self.file_count_label.setStyleSheet("color: #64748b; background: transparent; border: none; font-size: 12px;")
        status_row.addWidget(self.file_count_label)

        self.total_size_label = QLabel("Total: 0 B")
        self.total_size_label.setStyleSheet("color: #64748b; background: transparent; border: none; font-size: 12px;")
        status_row.addWidget(self.total_size_label)

        status_row.addStretch()

        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.setVisible(False)
        status_row.addWidget(self.progress)

        root_layout.addLayout(status_row)

        # No scroll area previously, so a short window compressed the tree and
        # preview panes instead of scrolling.
        enable_page_scroll(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()
        if self.mode == "device":
            self._load_device_tree()
        else:
            self._load_evidence_tree()

    def _on_device_connected(self, info):
        serial = info.get("serial", "")
        if serial:
            self.current_serial = serial
            self._load_storage()
            self._load_device_tree()

    def _on_mode_changed(self, text):
        self.mode = "device" if text == "Live Device" else "evidence"
        if self.mode == "device" and self.current_serial:
            self._load_device_tree()
        elif self.mode == "evidence":
            self._load_evidence_tree()

    def _load_storage(self):
        if not self.current_serial:
            return
        self.worker = AdbStorageWorker(self.current_serial)
        self.worker.finished.connect(self._on_storage_loaded)
        self.worker.start()

    def _on_storage_loaded(self, storage):
        data = storage.get("/data", {})
        self.storage_labels["total"].setText(data.get("total", "--"))
        self.storage_labels["used"].setText(data.get("used", "--"))
        self.storage_labels["avail"].setText(data.get("avail", "--"))
        data_info = f"{data.get('device', '?')} | {data.get('use_pct', '?')} used"
        self.storage_labels["data_info"].setText(data_info)
        extra = []
        for mount, info in storage.items():
            if mount.startswith("/storage/"):
                extra.append(f"{mount}: {info.get('total', '?')} ({info.get('label', '')})")
        if extra:
            self.storage_labels["data_info"].setText(f"{data_info} | SD: {', '.join(extra)}")

    def _load_device_tree(self):
        if not self.current_serial:
            return
        self.tree.clear()
        self.current_remote_path = "/sdcard"
        self.path_bar.setText("/sdcard")
        self.file_name_label.setText("No file selected")
        self.size_label.setText("Size: --")
        self.modified_label.setText("Modified: --")
        self.hash_label.setText("SHA-256: --")
        self.preview_text.setVisible(False)
        self.preview_image.setVisible(False)
        self.no_preview.setVisible(True)
        self.no_preview.setText("Loading device filesystem...")
        self.export_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, "/")
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        root_item.setExpanded(True)
        root_item.setData(0, Qt.ItemDataRole.UserRole, "/")

        self._load_remote_dir(root_item, "/")

        self.progress.setVisible(False)
        self.file_count_label.setText("Device: Live")
        self.total_size_label.setText(self.current_serial)

    def _load_remote_dir(self, parent_item, remote_path):
        self.adb_worker = AdbListWorker(self.current_serial, remote_path)
        self.adb_worker.finished.connect(lambda entries: self._on_dir_loaded(parent_item, entries))
        self.adb_worker.error.connect(lambda err: self._on_dir_error(err))
        self.adb_worker.start()
        self.adb_worker.wait(15000)

    def _on_dir_loaded(self, parent_item, entries):
        for entry in entries:
            item = QTreeWidgetItem(parent_item)
            item.setText(0, entry["name"])
            item.setText(1, self._format_size(entry["size"]))
            item.setText(2, entry.get("modified", ""))
            item.setText(3, entry.get("perms", ""))
            item.setData(0, Qt.ItemDataRole.UserRole, entry["path"])
            if entry["is_dir"]:
                item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "dir")
            else:
                item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_FileIcon))
                item.setData(0, Qt.ItemDataRole.UserRole + 1, "file")
        parent_item.setExpanded(True)

    def _on_dir_error(self, err):
        self.no_preview.setText(f"Error: {err}")
        self.no_preview.setVisible(True)
        self.progress.setVisible(False)

    def _on_path_submit(self):
        path = self.path_bar.text().strip()
        if not path:
            return
        if self.mode == "device":
            self.tree.clear()
            root_item = QTreeWidgetItem(self.tree)
            root_item.setText(0, path)
            root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
            root_item.setExpanded(True)
            root_item.setData(0, Qt.ItemDataRole.UserRole, path)
            self.current_remote_path = path
            self._load_remote_dir(root_item, path)
            self.progress.setVisible(False)
        else:
            if os.path.isdir(path):
                self._load_evidence_tree()

    def _go_up(self):
        if self.mode != "device" or not self.current_serial:
            return
        parts = self.current_remote_path.rstrip("/").split("/")
        if len(parts) <= 1:
            return
        parent = "/".join(parts[:-1]) or "/"
        self.current_remote_path = parent
        self.path_bar.setText(parent)
        self.tree.clear()
        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, parent)
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        root_item.setExpanded(True)
        root_item.setData(0, Qt.ItemDataRole.UserRole, parent)
        self._load_remote_dir(root_item, parent)

    def _load_evidence_tree(self):
        self.tree.clear()
        self.file_name_label.setText("No file selected")
        self.size_label.setText("Size: --")
        self.modified_label.setText("Modified: --")
        self.hash_label.setText("SHA-256: --")
        self.preview_text.setVisible(False)
        self.preview_image.setVisible(False)
        self.no_preview.setVisible(True)
        self.no_preview.setText("Select a file to preview")
        self.export_btn.setEnabled(False)
        self.current_file = None

        if not self.evidence_path.exists():
            self.file_count_label.setText("Files: 0")
            self.total_size_label.setText("Total: 0 B")
            self.no_preview.setText("No evidence files found")
            return

        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        all_files = []
        for root, dirs, files in os.walk(self.evidence_path):
            for f in files:
                all_files.append(os.path.join(root, f))

        total_count = len(all_files)
        total_size = 0

        dir_items = {}
        root_item = QTreeWidgetItem(self.tree)
        root_item.setText(0, self.evidence_path.name)
        root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
        root_item.setExpanded(True)
        dir_items[str(self.evidence_path)] = root_item

        for filepath in all_files:
            try:
                stat = os.stat(filepath)
                fsize = stat.st_size
                total_size += fsize
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                fname = os.path.basename(filepath)
                parent_dir = os.path.dirname(filepath)

                parent_item = dir_items.get(parent_dir)
                if parent_item is None:
                    parts = Path(parent_dir).relative_to(self.evidence_path).parts
                    current = str(self.evidence_path)
                    parent_item = root_item
                    for part in parts:
                        current = os.path.join(current, part)
                        if current not in dir_items:
                            new_item = QTreeWidgetItem(parent_item)
                            new_item.setText(0, part)
                            new_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
                            dir_items[current] = new_item
                        parent_item = dir_items[current]

                item = QTreeWidgetItem(parent_item)
                item.setText(0, fname)
                item.setText(1, self._format_size(fsize))
                item.setText(2, mtime)
                item.setData(0, Qt.ItemDataRole.UserRole, filepath)
                item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_FileIcon))

            except (OSError, PermissionError):
                continue

        self.progress.setVisible(False)
        self.file_count_label.setText(f"Files: {total_count}")
        self.total_size_label.setText(f"Total: {self._format_size(total_size)}")
        self.tree.expandToDepth(0)

    def _on_file_selected(self, item, column):
        filepath = item.data(0, Qt.ItemDataRole.UserRole)
        if filepath is None:
            return

        if self.mode == "device":
            is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1) == "dir"
            if is_dir:
                self.current_remote_path = str(filepath)
                self.path_bar.setText(str(filepath))
                self.tree.clear()
                root_item = QTreeWidgetItem(self.tree)
                root_item.setText(0, str(filepath))
                root_item.setIcon(0, self.style().standardIcon(self.style().StandardPixmap.SP_DirIcon))
                root_item.setExpanded(True)
                root_item.setData(0, Qt.ItemDataRole.UserRole, str(filepath))
                self._load_remote_dir(root_item, str(filepath))
                self.pull_btn.setVisible(False)
                self.open_btn.setVisible(False)
                return

            fname = os.path.basename(str(filepath))
            ext = Path(fname).suffix.lower()
            self.file_name_label.setText(fname)
            self.size_label.setText(f"Path: {filepath}")
            self.modified_label.setText("Click 'Pull from Device' to preview")
            self.hash_label.setText("SHA-256: --")
            self.device_path_label.setText(f"Device: {filepath}")
            self.preview_text.setVisible(False)
            self.preview_image.setVisible(False)
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"File: {fname}\n\nClick 'Pull from Device' to download and preview this file.")
            self.pull_btn.setVisible(True)
            self.pull_btn.setProperty("remote_path", str(filepath))
            self.open_btn.setVisible(False)
            self.current_file = None
            return

        if not os.path.isfile(filepath):
            return

        self.current_file = filepath
        self.export_btn.setEnabled(True)
        self.pull_btn.setVisible(False)
        self.open_btn.setVisible(False)
        stat = os.stat(filepath)
        fname = os.path.basename(filepath)

        self.file_name_label.setText(fname)
        self.size_label.setText(f"Size: {self._format_size(stat.st_size)}")
        self.modified_label.setText(f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))}")
        self.device_path_label.setText(f"Local: {filepath}")

        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        self.hash_label.setText(f"SHA-256: {sha256.hexdigest()}")

        ext = os.path.splitext(filepath)[1].lower()

        self.preview_text.setVisible(False)
        self.preview_image.setVisible(False)
        self.no_preview.setVisible(False)

        is_image = ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.heif')
        is_video = ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov', '.webm')
        is_audio = ext in ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.amr', '.flac', '.wma')
        is_text = ext in ('.txt', '.log', '.xml', '.csv', '.html', '.htm', '.md', '.cfg', '.conf', '.ini', '.yaml', '.yml', '.json', '.py', '.js', '.java', '.c', '.cpp', '.h')
        is_db = ext in ('.db', '.sqlite', '.sqlite3')

        if is_image:
            self._preview_image(filepath)
        elif is_text or ext == '.json':
            if ext == '.json':
                self._preview_json(filepath)
            else:
                self._preview_text(filepath)
        elif is_db:
            self._preview_db(filepath)
        elif is_video:
            self.open_btn.setVisible(True)
            self.open_btn.setText("Play Video")
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"Video: {fname}\n\nSize: {self._format_size(stat.st_size)}\n\nClick 'Open in System' to play.")
        elif is_audio:
            self.open_btn.setVisible(True)
            self.open_btn.setText("Play Audio")
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"Audio: {fname}\n\nSize: {self._format_size(stat.st_size)}\n\nClick 'Open in System' to play.")
        else:
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"File: {fname}\n\nSize: {self._format_size(stat.st_size)}\n\nPreview not available for {ext or 'this'} file type.")

    def _preview_json(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
            self.preview_text.setPlainText(formatted[:500000])
            self.preview_text.setVisible(True)
        except Exception as e:
            self.preview_text.setPlainText(f"Error parsing JSON:\n{str(e)}")
            self.preview_text.setVisible(True)

    def _preview_image(self, path):
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.preview_image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_image.setPixmap(scaled)
            self.preview_image.setVisible(True)
        else:
            self.no_preview.setVisible(True)
            self.no_preview.setText("Could not load image")

    def _preview_text(self, path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(500000)
            self.preview_text.setPlainText(content)
            self.preview_text.setVisible(True)
        except Exception as e:
            self.preview_text.setPlainText(f"Error reading file:\n{str(e)}")
            self.preview_text.setVisible(True)

    def _preview_db(self, path):
        try:
            conn = sqlite3.connect(path, timeout=5)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
            tables = cursor.fetchall()
            output_lines = [f"Database: {os.path.basename(path)}", f"Tables found: {len(tables)}", ""]
            for (table_name,) in tables:
                cursor.execute(f'PRAGMA table_info("{table_name}");')
                columns = cursor.fetchall()
                col_names = [col[1] for col in columns]
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}";')
                row_count = cursor.fetchone()[0]
                output_lines.append(f"Table: {table_name} ({row_count} rows)")
                output_lines.append(f"  Columns: {', '.join(col_names)}")
                cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 5;')
                sample = cursor.fetchall()
                if sample:
                    output_lines.append("  Sample rows:")
                    for row in sample:
                        output_lines.append(f"    {row}")
                output_lines.append("")
            conn.close()
            self.preview_text.setPlainText("\n".join(output_lines)[:500000])
            self.preview_text.setVisible(True)
        except Exception as e:
            self.preview_text.setPlainText(f"Error reading database:\n{str(e)}")
            self.preview_text.setVisible(True)

    def _pull_and_preview(self):
        remote = self.pull_btn.property("remote_path")
        if not remote:
            return
        self.no_preview.setVisible(True)
        self.no_preview.setText(f"Pulling {remote} from device...")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.pull_btn.setEnabled(False)
        self._pull_worker = PullFileWorker(self.current_serial, remote)
        self._pull_worker.finished.connect(self._on_pull_finished)
        self._pull_worker.start()

    def _on_pull_finished(self, result):
        self.progress.setVisible(False)
        self.pull_btn.setEnabled(True)
        if not result.get("success"):
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"Failed to pull file:\n{result.get('error', 'Unknown error')}")
            self._pull_worker = None
            return

        local_path = result.get("local_path", "")
        fname = result.get("filename", "")
        ext = result.get("extension", "")
        self.no_preview.setVisible(False)
        self.preview_text.setVisible(False)
        self.preview_image.setVisible(False)

        is_image = ext in ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.heif')
        is_video = ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov', '.webm')
        is_audio = ext in ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.amr', '.flac', '.wma')
        is_text = ext in ('.txt', '.log', '.xml', '.csv', '.html', '.htm', '.md', '.cfg', '.conf', '.ini', '.yaml', '.yml', '.json', '.py', '.js', '.java', '.c', '.cpp', '.h')
        is_db = ext in ('.db', '.sqlite', '.sqlite3')

        if is_image:
            self._preview_image(local_path)
        elif ext == '.json':
            self._preview_json(local_path)
        elif is_text:
            self._preview_text(local_path)
        elif is_db:
            self._preview_db(local_path)
        elif is_video:
            self.open_btn.setVisible(True)
            self.open_btn.setText("Play Video")
            self.open_btn.setProperty("local_path", local_path)
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"Video pulled: {fname}\n\nClick 'Play Video' to open.")
        elif is_audio:
            self.open_btn.setVisible(True)
            self.open_btn.setText("Play Audio")
            self.open_btn.setProperty("local_path", local_path)
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"Audio pulled: {fname}\n\nClick 'Play Audio' to open.")
        else:
            self.open_btn.setVisible(True)
            self.open_btn.setText("Open in System")
            self.open_btn.setProperty("local_path", local_path)
            self.no_preview.setVisible(True)
            self.no_preview.setText(f"File pulled: {fname}\n\nClick 'Open in System' to open.")

        self.current_file = local_path
        self.export_btn.setEnabled(True)
        self._pull_worker = None

    def _open_in_system(self):
        local = self.open_btn.property("local_path")
        if not local or not os.path.isfile(local):
            return
        try:
            os.startfile(local)
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", local],
                    creationflags=0x08000000)
            except Exception:
                pass

    def _search_files(self, query):
        if not query:
            for i in range(self.tree.topLevelItemCount()):
                self._set_item_visible(self.tree.topLevelItem(i), True)
            return
        query_lower = query.lower()
        for i in range(self.tree.topLevelItemCount()):
            self._filter_item(self.tree.topLevelItem(i), query_lower)

    def _filter_item(self, item, query):
        name_match = query in item.text(0).lower()
        child_visible = False
        for i in range(item.childCount()):
            if self._filter_item(item.child(i), query):
                child_visible = True
        visible = name_match or child_visible
        self._set_item_visible(item, visible)
        if visible and child_visible:
            item.setExpanded(True)
        return visible

    def _set_item_visible(self, item, visible):
        item.setHidden(not visible)

    def _export_file(self):
        if not self.current_file or not os.path.isfile(self.current_file):
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export File", os.path.basename(self.current_file), "All Files (*)")
        if dest:
            try:
                with open(self.current_file, "rb") as src:
                    with open(dest, "wb") as dst:
                        dst.write(src.read())
            except Exception:
                pass

    def _format_size(self, size):
        if isinstance(size, str):
            try:
                size = int(size)
            except ValueError:
                return size
        if not isinstance(size, (int, float)):
            return str(size)
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"


class PullFileWorker(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, serial, remote_path):
        super().__init__()
        self.serial = serial
        self.remote_path = remote_path

    def run(self):
        try:
            if not self.serial:
                self.finished.emit({"success": False, "error": "No device connected"})
                return

            from ...core import adb_manager as adb

            fname = os.path.basename(self.remote_path.rstrip("/"))
            ext = os.path.splitext(fname)[1].lower()
            if not fname:
                self.finished.emit({"success": False, "error": "Not a file path"})
                return

            cache_dir = os.path.join(os.path.expanduser("~"), ".droidforenx", "pull_cache")
            os.makedirs(cache_dir, exist_ok=True)

            # Different paths routinely share a basename; disambiguate so a preview
            # can never silently render the wrong file.
            tag = hashlib.sha1(self.remote_path.encode("utf-8", "replace")).hexdigest()[:8]
            local_path = os.path.join(cache_dir, f"{tag}_{fname}")

            ok, msg = adb.adb_cmd(
                ["-s", self.serial, "pull", self.remote_path, local_path], timeout=120
            )

            if ok and os.path.isfile(local_path):
                self.finished.emit({
                    "success": True,
                    "local_path": local_path,
                    "filename": fname,
                    "extension": ext,
                })
            else:
                if not ok and not msg:
                    msg = "ADB pull failed"
                self.finished.emit({"success": False, "error": msg})
        except Exception as e:
            self.finished.emit({"success": False, "error": str(e)})
