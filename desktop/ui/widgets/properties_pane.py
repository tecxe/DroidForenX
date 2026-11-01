"""Right-hand properties pane for the artifact viewer.

Whatever is selected in the item list is described here, field by field, the way
Belkasoft and Axiom present a record: a property column and a value column,
grouped, with the raw keys the extractor wrote rather than a hand-picked few.

The pane deliberately shows nothing it was not given. A missing field is
omitted, not rendered as a dash, so an empty properties table means the record
carries no metadata - not that the pane failed to load.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QTreeWidgetItem, QTreeWidget,
                             QVBoxLayout, QWidget)

#: Keys that describe the record itself and belong above the extractor's own.
LEADING_FIELDS = (
    ("type", "Type"),
    ("category", "Category"),
    ("summary", "Summary"),
    ("source", "Source"),
    ("timestamp", "Time (UTC)"),
)

#: Extractor keys whose presentation needs a friendly name.
FIELD_LABELS = {
    "path": "Path",
    "filename": "File name",
    "extension": "Extension",
    "folder": "Folder",
    "size": "Size",
    "bytes": "Bytes",
    "modified": "Modified",
    "address": "Address",
    "number": "Number",
    "contact_name": "Contact",
    "name": "Name",
    "chat": "Chat",
    "sender": "Sender",
    "receiver": "Receiver",
    "direction": "Direction",
    "body": "Text",
    "message": "Text",
    "date": "Date",
    "duration": "Duration",
    "media_type": "Media type",
    "mime": "MIME type",
    "package": "Package",
    "app": "Application",
    "account": "Account",
    "email": "Email",
    "subject": "Subject",
    "url": "URL",
    "title": "Title",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "hash": "Hash",
    "sha256": "SHA-256",
    "deleted": "Deleted",
    "recovered": "Recovered",
}

#: Values that are noise in a properties table.
_HIDDEN_KEYS = {"_data", "_raw"}


class PropertiesPane(QWidget):
    """Two-column property/value table for the selected artifact."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("propertiesPane")
        self.setMinimumWidth(220)
        self.setStyleSheet(
            "#propertiesPane { background: #0d1117; border-left: 1px solid #1e293b; }"
            "QLabel { background: transparent; border: none; }"
            "QTreeWidget { background: transparent; border: none; color: #cbd5e1;"
            " font-size: 11px; }"
            "QTreeWidget::item { height: 22px; }"
            "QTreeWidget::item:selected { background: #1e3a8a; color: #ffffff; }"
            "QHeaderView::section { background: #0d1117; border: none;"
            " color: #475569; font-size: 10px; padding: 3px 6px; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 6)
        title = QLabel("Properties")
        title.setStyleSheet(
            "color: #94a3b8; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; background: transparent; border: none;")
        bar_layout.addWidget(title)
        bar_layout.addStretch()
        outer.addWidget(bar)

        self.table = QTreeWidget()
        self.table.setColumnCount(2)
        self.table.setHeaderLabels(["Property", "Value"])
        self.table.setRootIsDecorated(True)
        self.table.setIndentation(8)
        self.table.setWordWrap(True)
        self.table.setColumnWidth(0, 96)
        self.table.header().setStretchLastSection(True)
        outer.addWidget(self.table, 1)

        self.hint = QLabel("Select an item to read its properties.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(
            "color: #475569; font-size: 10px; padding: 6px 12px;"
            " background: transparent; border: none;")
        outer.addWidget(self.hint)

    # -- public API ---------------------------------------------------------

    def clear(self, message: str = "Select an item to read its properties.") -> None:
        self.table.clear()
        self.hint.setText(message)

    def show_summary(self, name: str, category: str, count: int,
                     media: int = 0) -> None:
        """The category itself, shown before any single item is chosen."""
        self.table.clear()
        general = QTreeWidgetItem(self.table)
        general.setText(0, "Category")
        self._style_group(general)

        rows = [("Name", name), ("Key", category), ("Items", count)]
        if media:
            rows.append(("Media items", media))
        rows.append(("Collector", "built in"))
        for label, value in rows:
            self._add_row(general, label, value)
        general.setExpanded(True)

        self.hint.setText("Select an item in the centre to read its fields.")

    def show_item(self, artifact: dict) -> None:
        """Render one artifact: its identity first, then every recorded field."""
        self.table.clear()
        if not artifact:
            self.hint.setText("Select an item to read its properties.")
            return

        data = artifact.get("_data") or {}
        general = QTreeWidgetItem(self.table)
        general.setText(0, "General")
        self._style_group(general)

        shown = 0
        for key, label in LEADING_FIELDS:
            value = artifact.get(key)
            if value in (None, ""):
                continue
            shown += self._add_row(general, label, value)

        # The extractor's own fields come next, in the order it wrote them, so
        # the pane reflects the record rather than a second opinion about it.
        for key, value in data.items():
            if key in _HIDDEN_KEYS or value in (None, ""):
                continue
            shown += self._add_row(general, FIELD_LABELS.get(key, _pretty(key)), value)

        general.setExpanded(True)

        extra = {k: v for k, v in artifact.items()
                 if k not in data and k not in _HIDDEN_KEYS
                 and k not in {field for field, _ in LEADING_FIELDS}
                 and v not in (None, "", [], {})}
        if extra:
            other = QTreeWidgetItem(self.table)
            other.setText(0, "Record")
            self._style_group(other)
            for key, value in extra.items():
                shown += self._add_row(other, FIELD_LABELS.get(key, _pretty(key)), value)
            other.setExpanded(True)

        self.hint.setText(
            f"{shown} fields" if shown else "This record carries no metadata.")
        self.table.resizeColumnToContents(0)

    # -- internals ----------------------------------------------------------

    def _add_row(self, parent, label: str, value) -> int:
        item = QTreeWidgetItem(parent)
        item.setText(0, label)
        item.setText(1, _as_text(value))
        item.setToolTip(1, _as_text(value))
        item.setForeground(0, self._brush("#64748b"))
        item.setForeground(1, self._brush("#e2e8f0"))
        return 1

    def _style_group(self, item: QTreeWidgetItem) -> None:
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, self._brush("#94a3b8"))
        item.setFirstColumnSpanned(True)

    @staticmethod
    def _brush(hex_code: str):
        from PyQt6.QtGui import QBrush, QColor
        return QBrush(QColor(hex_code))


def _pretty(key: str) -> str:
    """``contact_name`` -> ``Contact name``; keep short keys as they are."""
    return key.replace("_", " ").strip().capitalize()


def _as_text(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(part) for part in value) if value else ""
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return str(value)
