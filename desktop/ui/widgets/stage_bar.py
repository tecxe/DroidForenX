"""4-stage forensic workflow progress bar widget."""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt, pyqtSignal


class StageBar(QWidget):
    """Professional 4-stage workflow bar like Cellebrite UFED."""
    stage_clicked = pyqtSignal(int)

    #: One list drives the frames, the icons and the colour lookups, so a stage
    #: can never be half-removed again (the old five hardcoded loops kept drawing
    #: a "Seizure & Isolation" tile after the page behind it was deleted).
    STAGES = [
        ("\U0001F50D", "Device\nIdentification", "#3b82f6"),
        ("\U0001F4E5", "Data\nAcquisition", "#10b981"),
        ("\U0001F52C", "Examination\n& Analysis", "#f59e0b"),
        ("\U0001F4CB", "Court-Ready\nReporting", "#ef4444"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_stage = 0
        self.completed_stages = set()
        self._setup_ui()

    @staticmethod
    def _rgb(hex_colour: str) -> str:
        return (f"{int(hex_colour[1:3], 16)},{int(hex_colour[3:5], 16)},"
                f"{int(hex_colour[5:7], 16)}")

    def _frame_style(self, background: str, border: str) -> str:
        # Scoped to the tile: a bare "QFrame" selector also matches the QLabel
        # children (QLabel derives from QFrame), which added 12 px of padding to
        # every caption inside the bar.
        return (f"QFrame#stage_frame {{ background: {background};"
                f" border: 1px solid {border}; border-radius: 8px; }}")

    @staticmethod
    def _number_style(colour: str, background: str) -> str:
        return (f"font-size: 20px; font-weight: 700; color: {colour};"
                f" background: {background}; border-radius: 14px; padding: 0;"
                " min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;")

    @staticmethod
    def _name_style(colour: str) -> str:
        return (f"font-size: 11px; font-weight: 600; color: {colour}; padding: 0;"
                " background: transparent; border: none;")

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stage_frames = []
        self.stage_labels = []
        self.stage_icons = []

        for i, (icon, name, colour) in enumerate(self.STAGES):
            frame = QFrame()
            frame.setObjectName("stage_frame")
            frame.setStyleSheet(self._frame_style(
                "rgba(255,255,255,0.05)" if i != 0
                else f"rgba({self._rgb(colour)},0.15)",
                "rgba(255,255,255,0.08)"))

            v = QVBoxLayout(frame)
            v.setContentsMargins(8, 8, 8, 8)
            v.setSpacing(4)

            num = QLabel(str(i + 1))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(self._number_style(colour, "rgba(255,255,255,0.05)"))

            icon_label = QLabel(icon)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet(
                "font-size: 22px; background: transparent; border: none; padding: 0;")

            name_label = QLabel(name)
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setStyleSheet(self._name_style(colour))
            name_label.setWordWrap(True)

            v.addWidget(num, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignCenter)
            v.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)

            layout.addWidget(frame)
            self.stage_frames.append(frame)
            self.stage_labels.append(name_label)
            self.stage_icons.append(num)

            if i < len(self.STAGES) - 1:
                arrow = QLabel("\u2192")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                arrow.setStyleSheet(
                    "font-size: 16px; color: #475569; font-weight: 700; padding: 0 2px;"
                    " background: transparent; border: none;")
                layout.addWidget(arrow)

    def set_stage(self, stage: int, completed: set = None):
        self.current_stage = stage
        self.completed_stages = completed or set()
        for i, frame in enumerate(self.stage_frames):
            colour = self.STAGES[i][2]
            if i in self.completed_stages:
                frame.setStyleSheet(self._frame_style(
                    "rgba(16,185,129,0.12)", "rgba(16,185,129,0.3)"))
                self.stage_icons[i].setStyleSheet(
                    self._number_style("#10b981", "rgba(16,185,129,0.2)"))
                self.stage_labels[i].setStyleSheet(self._name_style("#10b981"))
            elif i == stage:
                frame.setStyleSheet(self._frame_style(
                    f"rgba({self._rgb(colour)},0.15)",
                    f"rgba({self._rgb(colour)},0.4)"))
                self.stage_icons[i].setStyleSheet(
                    self._number_style(colour, f"rgba({self._rgb(colour)},0.25)"))
                self.stage_labels[i].setStyleSheet(self._name_style(colour))
            else:
                frame.setStyleSheet(self._frame_style(
                    "rgba(255,255,255,0.03)", "rgba(255,255,255,0.06)"))
                self.stage_icons[i].setStyleSheet(
                    self._number_style("#475569", "rgba(255,255,255,0.05)"))
                self.stage_labels[i].setStyleSheet(self._name_style("#475569"))
