"""Professional dark theme — Cellebrite/Oxygen-inspired forensic UI."""

COLORS = {
    "bg_dark": "#0a0e17",
    "bg_card": "#111827",
    "bg_panel": "#1a2234",
    "bg_hover": "#1e293b",
    "bg_input": "#0f172a",
    "border": "#1e293b",
    "border_light": "#334155",
    "text": "#e2e8f0",
    "text_muted": "#64748b",
    "text_dim": "#475569",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_light": "#3b82f6",
    "accent": "#f59e0b",
    "accent_hover": "#d97706",
    "success": "#10b981",
    "success_bg": "rgba(16,185,129,0.12)",
    "danger": "#ef4444",
    "danger_bg": "rgba(239,68,68,0.12)",
    "warning": "#f59e0b",
    "warning_bg": "rgba(245,158,11,0.12)",
    "info": "#06b6d4",
    "info_bg": "rgba(6,182,212,0.12)",
    "stage_1": "#8b5cf6",
    "stage_2": "#3b82f6",
    "stage_3": "#10b981",
    "stage_4": "#f59e0b",
    "stage_5": "#ef4444",
}

STAGE_COLORS = [COLORS["stage_1"], COLORS["stage_2"], COLORS["stage_3"], COLORS["stage_4"], COLORS["stage_5"]]
STAGE_NAMES = ["Seizure & Isolation", "Identification", "Acquisition", "Examination", "Reporting"]

MAIN_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {COLORS['bg_dark']};
    color: {COLORS['text']};
    font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
    font-size: 13px;
}}

QMainWindow::separator {{
    background: {COLORS['border']};
    width: 1px;
}}

/* ── Sidebar ── */
#sidebar {{
    background-color: {COLORS['bg_card']};
    border-right: 1px solid {COLORS['border']};
    min-width: 220px;
    max-width: 220px;
}}
#sidebar QLabel {{
    color: {COLORS['text_muted']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    padding: 16px 16px 4px 16px;
    text-transform: uppercase;
}}
#sidebar QPushButton {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    color: {COLORS['text_muted']};
    font-size: 13px;
    font-weight: 500;
    padding: 10px 16px;
    text-align: left;
}}
#sidebar QPushButton:hover {{
    background: {COLORS['bg_hover']};
    color: {COLORS['text']};
}}
#sidebar QPushButton:checked {{
    background: rgba(245,158,11,0.08);
    color: {COLORS['accent']};
    border-left-color: {COLORS['accent']};
}}

/* ── Cards ── */
.card {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 20px;
}}
.card-header {{
    font-size: 16px;
    font-weight: 700;
    color: {COLORS['text']};
    padding-bottom: 12px;
    border-bottom: 1px solid {COLORS['border']};
    margin-bottom: 16px;
}}

/* ── Stat Cards ── */
.stat-card {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    padding: 16px;
}}
.stat-value {{
    font-size: 28px;
    font-weight: 700;
    color: {COLORS['text']};
}}
.stat-label {{
    font-size: 11px;
    font-weight: 600;
    color: {COLORS['text_muted']};
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-top: 4px;
}}

/* ── Tables ── */
QTableWidget {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    gridline-color: {COLORS['border']};
    selection-background-color: rgba(37,99,235,0.2);
    selection-color: {COLORS['text']};
    font-size: 12px;
}}
QTableWidget::item {{
    padding: 8px 12px;
    border-bottom: 1px solid {COLORS['border']};
}}
QTableWidget::item:selected {{
    background: rgba(37,99,235,0.15);
}}
QHeaderView::section {{
    background: {COLORS['bg_panel']};
    color: {COLORS['text_muted']};
    border: none;
    border-bottom: 2px solid {COLORS['border']};
    border-right: 1px solid {COLORS['border']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    padding: 10px 12px;
}}

/* ── Buttons ── */
QPushButton {{
    background: {COLORS['primary']};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {COLORS['primary_hover']};
}}
QPushButton:pressed {{
    background: #1e40af;
}}
QPushButton:disabled {{
    background: {COLORS['bg_hover']};
    color: {COLORS['text_dim']};
}}
#btn-danger {{
    background: {COLORS['danger']};
}}
#btn-danger:hover {{
    background: #dc2626;
}}
#btn-accent {{
    background: {COLORS['accent']};
    color: #000;
}}
#btn-accent:hover {{
    background: {COLORS['accent_hover']};
}}
#btn-success {{
    background: {COLORS['success']};
}}
#btn-success:hover {{
    background: #059669;
}}
#btn-outline {{
    background: transparent;
    border: 1px solid {COLORS['border_light']};
    color: {COLORS['text']};
}}
#btn-outline:hover {{
    background: {COLORS['bg_hover']};
    border-color: {COLORS['text_muted']};
}}

/* ── Inputs ── */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background: {COLORS['bg_input']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    color: {COLORS['text']};
    padding: 10px 12px;
    font-size: 13px;
    selection-background-color: rgba(37,99,235,0.3);
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {COLORS['primary']};
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {COLORS['text_muted']};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {COLORS['bg_panel']};
    border: 1px solid {COLORS['border']};
    color: {COLORS['text']};
    selection-background-color: {COLORS['primary']};
    outline: none;
}}

/* ── Group Box ── */
QGroupBox {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 20px;
    font-weight: 600;
    font-size: 13px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 12px;
    color: {COLORS['accent']};
}}

/* ── Progress Bar ── */
QProgressBar {{
    background: {COLORS['bg_input']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {COLORS['primary']}, stop:1 {COLORS['accent']});
    border-radius: 4px;
}}

/* ── ScrollBar ── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['border_light']};
    min-height: 30px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['text_muted']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {COLORS['border_light']};
    min-width: 30px;
    border-radius: 4px;
}}

/* ── Tab Widget ── */
QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    background: {COLORS['bg_card']};
}}
QTabBar::tab {{
    background: {COLORS['bg_panel']};
    color: {COLORS['text_muted']};
    border: 1px solid {COLORS['border']};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background: {COLORS['bg_card']};
    color: {COLORS['accent']};
    border-bottom: 2px solid {COLORS['accent']};
}}
QTabBar::tab:hover {{
    color: {COLORS['text']};
}}

/* ── Labels ── */
h1 {{ font-size: 24px; font-weight: 700; color: {COLORS['text']}; }}
h2 {{ font-size: 18px; font-weight: 700; color: {COLORS['text']}; }}
h3 {{ font-size: 15px; font-weight: 600; color: {COLORS['text']}; }}
.muted {{ color: {COLORS['text_muted']}; }}
.accent {{ color: {COLORS['accent']}; }}

/* ── Tool Tips ── */
QToolTip {{
    background: {COLORS['bg_panel']};
    border: 1px solid {COLORS['border']};
    color: {COLORS['text']};
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 12px;
}}
"""
