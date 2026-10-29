"""Home dashboard page — case overview, device status, quick actions."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QGridLayout, QScrollArea)
from PyQt6.QtCore import Qt, pyqtSignal
from ... import __version__
from ..widgets.stage_bar import StageBar
from ..widgets.card import Card


class HomePage(QWidget):
    new_case_requested = pyqtSignal()
    open_case_requested = pyqtSignal(str)
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The page is sized to its content and grows downward; a horizontal
        # bar would only appear when a card's own width is impossible.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        # ── Header ──
        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title = QLabel("Digital Forensics")
        title.setStyleSheet("font-size: 26px; font-weight: 700; color: #e2e8f0; background: transparent; border: none;")
        subtitle = QLabel(f"DroidForenX v{__version__} - Professional Mobile Device Forensics")
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent; border: none;")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block)
        header.addStretch()

        btn_new = QPushButton("＋ New Case")
        btn_new.setObjectName("btn-accent")
        btn_new.setFixedHeight(42)
        btn_new.clicked.connect(self.new_case_requested.emit)
        header.addWidget(btn_new)

        btn_device = QPushButton("📱 Connect Device")
        btn_device.setObjectName("btn-success")
        btn_device.setFixedHeight(42)
        btn_device.clicked.connect(lambda: self.navigate.emit("device"))
        header.addWidget(btn_device)
        layout.addLayout(header)

        # ── Stats Grid ──
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)
        stats = [
            ("0", "Active Cases", "#3b82f6"),
            ("0", "Devices", "#10b981"),
            ("0", "Artifacts", "#f59e0b"),
            ("0", "Reports", "#8b5cf6"),
        ]
        self.stat_values = []
        for val, label, color in stats:
            card = Card(accent=color, radius=8)
            v = QVBoxLayout(card)
            v.setSpacing(6)
            lbl_val = QLabel(val)
            lbl_val.setStyleSheet(f"font-size: 32px; font-weight: 700; color: {color}; background: transparent; border: none;")
            lbl_name = QLabel(label)
            lbl_name.setStyleSheet("font-size: 11px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 1px; background: transparent; border: none;")
            v.addWidget(lbl_val)
            v.addWidget(lbl_name)
            stats_layout.addWidget(card)
            self.stat_values.append(lbl_val)
        layout.addLayout(stats_layout)

        # ── Workflow Progress ──
        workflow_card = Card(radius=10)
        wf_layout = QVBoxLayout(workflow_card)
        wf_layout.setSpacing(12)
        wf_title = QLabel("📋 Forensic Workflow Progress")
        wf_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #e2e8f0; background: transparent; border: none;")
        wf_layout.addWidget(wf_title)

        self.stage_bar = StageBar()
        wf_layout.addWidget(self.stage_bar)
        layout.addWidget(workflow_card)

        # ── Quick Actions ──
        actions_card = Card(radius=10)
        act_layout = QVBoxLayout(actions_card)
        act_layout.setSpacing(10)
        act_title = QLabel("⚡ Quick Actions")
        act_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #e2e8f0; background: transparent; border: none;")
        act_layout.addWidget(act_title)

        actions_grid = QGridLayout()
        actions_grid.setSpacing(10)
        actions = [
            ("📱", "Connect Device", "device"),
            ("🏷️", "Identify Device", "identify"),
            ("📥", "Acquire Data", "acquire"),
            ("🔬", "Examine Evidence", "examine"),
            ("💳", "SIM Info", "sim"),
            ("📋", "Generate Report", "report"),
        ]
        for i, (icon, text, page) in enumerate(actions):
            btn = QPushButton(f"{icon}  {text}")
            btn.setObjectName("btn-outline")
            btn.setFixedHeight(44)
            btn.clicked.connect(lambda checked, p=page: self.navigate.emit(p))
            actions_grid.addWidget(btn, i // 3, i % 3)
        act_layout.addLayout(actions_grid)
        layout.addWidget(actions_card)

        layout.addStretch()
        scroll.setWidget(container)

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.addWidget(scroll)

    def refresh(self, case_count=0, device_count=0, artifact_count=0, report_count=0):
        vals = [str(case_count), str(device_count), str(artifact_count), str(report_count)]
        for lbl, val in zip(self.stat_values, vals):
            lbl.setText(val)
