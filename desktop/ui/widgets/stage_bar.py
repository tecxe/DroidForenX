"""5-stage forensic workflow progress bar widget."""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class StageBar(QWidget):
    """Professional 5-stage workflow bar like Cellebrite UFED."""
    stage_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_stage = 0
        self.completed_stages = set()
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        colors = ["#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"]
        icons = ["🔒", "🔍", "📥", "🔬", "📋"]
        names = ["Seizure &\nIsolation", "Device\nIdentification", "Data\nAcquisition", "Examination\n& Analysis", "Court-Ready\nReporting"]

        self.stage_frames = []
        self.stage_labels = []
        self.stage_icons = []

        for i in range(5):
            frame = QFrame()
            frame.setObjectName("stage_frame")
            frame.setStyleSheet(f"""
                QFrame {{
                    background: {"rgba(255,255,255,0.05)" if i != 0 else "rgba(139,92,246,0.15)"};
                    border: 1px solid {"rgba(255,255,255,0.08)"};
                    border-radius: 8px;
                    padding: 12px;
                }}
            """)

            v = QVBoxLayout(frame)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(4)

            num = QLabel(str(i + 1))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(f"""
                font-size: 20px; font-weight: 700; color: {colors[i]};
                background: rgba(255,255,255,0.05); border-radius: 14px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            """)

            icon_label = QLabel(icons[i])
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet("font-size: 22px; background: transparent; border: none;")

            name_label = QLabel(names[i])
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {colors[i]}; background: transparent; border: none;")
            name_label.setWordWrap(True)

            v.addWidget(num, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)

            layout.addWidget(frame)
            self.stage_frames.append(frame)
            self.stage_labels.append(name_label)
            self.stage_icons.append(num)

            if i < 4:
                arrow = QLabel("→")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setStyleSheet("font-size: 16px; color: #475569; font-weight: 700; padding: 0 2px; background: transparent; border: none;")
                layout.addWidget(arrow)

    def set_stage(self, stage: int, completed: set = None):
        self.current_stage = stage
        self.completed_stages = completed or set()
        colors = ["#8b5cf6", "#3b82f6", "#10b981", "#f59e0b", "#ef4444"]
        for i, frame in enumerate(self.stage_frames):
            if i in self.completed_stages:
                frame.setStyleSheet(f"""
                    QFrame {{
                        background: rgba(16,185,129,0.12);
                        border: 1px solid rgba(16,185,129,0.3);
                        border-radius: 8px; padding: 12px;
                    }}
                """)
                self.stage_icons[i].setStyleSheet(f"""
                    font-size: 20px; font-weight: 700; color: #10b981;
                    background: rgba(16,185,129,0.2); border-radius: 14px;
                    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
                """)
                self.stage_labels[i].setStyleSheet("font-size: 11px; font-weight: 600; color: #10b981; background: transparent; border: none;")
            elif i == stage:
                frame.setStyleSheet(f"""
                    QFrame {{
                        background: rgba({int(colors[i][1:3],16)},{int(colors[i][3:5],16)},{int(colors[i][5:7],16)},0.15);
                        border: 1px solid rgba({int(colors[i][1:3],16)},{int(colors[i][3:5],16)},{int(colors[i][5:7],16)},0.4);
                        border-radius: 8px; padding: 12px;
                    }}
                """)
                self.stage_icons[i].setStyleSheet(f"""
                    font-size: 20px; font-weight: 700; color: {colors[i]};
                    background: rgba({int(colors[i][1:3],16)},{int(colors[i][3:5],16)},{int(colors[i][5:7],16)},0.25);
                    border-radius: 14px; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
                """)
                self.stage_labels[i].setStyleSheet(f"font-size: 11px; font-weight: 600; color: {colors[i]}; background: transparent; border: none;")
            else:
                frame.setStyleSheet("""
                    QFrame {
                        background: rgba(255,255,255,0.03);
                        border: 1px solid rgba(255,255,255,0.06);
                        border-radius: 8px; padding: 12px;
                    }
                """)
                self.stage_icons[i].setStyleSheet("""
                    font-size: 20px; font-weight: 700; color: #475569;
                    background: rgba(255,255,255,0.05); border-radius: 14px;
                    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
                """)
                self.stage_labels[i].setStyleSheet("font-size: 11px; font-weight: 600; color: #475569; background: transparent; border: none;")
