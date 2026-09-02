"""Professional data table widget for forensic data display."""
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor


class DataTable(QTableWidget):
    """Styled forensic data table with alternating rows and sortable columns."""

    def __init__(self, columns: list, parent=None):
        super().__init__(parent)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setWordWrap(False)

        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultSectionSize(150)
        header.setMinimumSectionSize(80)

        self.setStyleSheet("""
            QTableWidget {
                background: #111827;
                border: 1px solid #1e293b;
                border-radius: 8px;
                gridline-color: #1e293b;
                selection-background-color: rgba(37,99,235,0.2);
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 8px 12px;
                border-bottom: 1px solid #1e293b;
            }
            QTableWidget::item:alternate {
                background: rgba(255,255,255,0.02);
            }
            QTableWidget::item:selected {
                background: rgba(37,99,235,0.15);
            }
            QHeaderView::section {
                background: #1a2234;
                color: #64748b;
                border: none;
                border-bottom: 2px solid #1e293b;
                border-right: 1px solid #1e293b;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding: 10px 12px;
            }
        """)

    def load_data(self, rows: list, monospace_cols: set = None):
        """Load rows of data into the table."""
        monospace_cols = monospace_cols or set()
        self.setRowCount(len(rows))
        for r, row_data in enumerate(rows):
            for c, val in enumerate(row_data):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if c in monospace_cols:
                    item.setFont(QFont("Cascadia Code, Consolas, monospace", 11))
                self.setItem(r, c, item)
        self.resizeColumnsToContents()
        for c in range(self.columnCount()):
            if self.columnWidth(c) > 400:
                self.setColumnWidth(c, 400)
