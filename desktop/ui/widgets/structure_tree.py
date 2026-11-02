"""Left-hand structure tree for the artifact viewer.

The artifact viewer used to open on a grid of tiles, which is fine for showing
what *can* be extracted and useless for reading what *was*. An examiner works
down a structure: a case has categories, a category has items, an item has
properties. This is the first of those three panes.

The tree is built from :data:`STRUCTURE_SPEC`, which groups categories the way
an analysis is actually read rather than the way the extractor is written. A
leaf with no category is a section this build does not collect yet; it is shown
disabled and counted as zero rather than hidden, because knowing the gap is part
of reading the evidence honestly.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPushButton, QTreeWidget, QTreeWidgetItem,
                             QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from ..theme import colour, theme

#: ``(section, [(label, category) | (label, [(label, category), ...])])``.
#: ``category`` is the key used by :data:`desktop.ui.pages.artifacts_page.APP_DEFS`;
#: ``None`` marks a leaf with no collector in this build.
STRUCTURE_SPEC = [
    ("Analyze Data", [
        ("Notes", None),
        ("WhatsApp", "WhatsApp"),
        ("Device Network", "WiFi & Network"),
        ("Recent Items", None),
        ("Location Related", None),
        ("Media", [
            ("Images", "Media"),
            ("Video & Audio", "Recordings"),
            ("Memos", None),
            ("Physical Activity", None),
            ("Web Search", "Chrome"),
        ]),
        ("System Logs", "Crash Logs"),
        ("User Accounts", "Emails & Accounts"),
    ]),
    ("Data Files", [
        ("Documents", "Documents"),
        ("Configuration", "System Info"),
        ("All Files", "All Files"),
        ("Downloads", "Downloads"),
        ("Screenshots", "Screenshots"),
        ("Camera", "Camera"),
    ]),
    ("Exchange", [
        ("Text Messages", "SMS"),
        ("Call Log", "Calls"),
        ("Contacts", "Contacts"),
        ("Calendar", "Calendar"),
        ("Telegram", "Telegram"),
        ("Instagram", "Instagram"),
        ("Twitter / X", "Twitter"),
        ("Facebook", "Facebook"),
        ("Snapchat", "Snapchat"),
        ("TikTok", "TikTok"),
        ("YouTube", "YouTube"),
        ("Transactions", "Transactions"),
        ("Deleted & Recovered", "Deleted Files"),
    ]),
]

#: Column indices in the tree.
COL_NAME = 0
COL_COUNT = 1

#: Role carrying the dark-authored colour an item was painted with. Item
#: brushes are not stylesheets, so the theme manager cannot translate them
#: after the fact; keeping the source here lets the tree repaint itself when
#: the mode changes instead of staying dark on a white panel.
ROLE_COLOUR = 0x0101


class StructureTree(QWidget):
    """A category tree with counts, searchable, emitting the chosen category."""

    category_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("structureTree")
        self.setMinimumWidth(210)
        self.setStyleSheet(
            "#structureTree { background: #0d1117; border-right: 1px solid #1e293b; }"
            "QLabel { background: transparent; border: none; }"
            "QLineEdit { background: #111827; border: 1px solid #1e293b;"
            " border-radius: 7px; color: #e2e8f0; font-size: 11px;"
            " padding: 5px 9px; }"
            "QLineEdit:focus { border: 1px solid #2563eb; }"
            "QTreeWidget { background: transparent; border: none; color: #cbd5e1;"
            " font-size: 12px; outline: 0; }"
            "QTreeWidget::item { height: 26px; border-radius: 5px; }"
            "QTreeWidget::item:hover { background: #1e293b; }"
            "QTreeWidget::item:selected { background: #1e3a8a; color: #e2e8f0; }"
            "QTreeWidget::branch { background: transparent; }"
            "QHeaderView::section { background: #0d1117; border: none; color: #475569; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- header: the pane's name, and the one action that shortens a
        #    long tree. The old control was a bare en dash, which read as a
        #    broken minus sign rather than "fold this up".
        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 10, 12, 8)
        bar_layout.setSpacing(6)
        self.title = QLabel("STRUCTURE")
        self.title.setStyleSheet(
            "color: #94a3b8; font-size: 10px; font-weight: 700;"
            " letter-spacing: 1px; background: transparent; border: none;")
        bar_layout.addWidget(self.title)
        bar_layout.addStretch()
        self.collapse_btn = QPushButton("Collapse")
        self.collapse_btn.setFixedHeight(22)
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setToolTip("Collapse every section")
        self.collapse_btn.setStyleSheet(
            "QPushButton { background: #111827; color: #94a3b8;"
            " border: 1px solid #1e293b; border-radius: 11px; font-size: 10px;"
            " font-weight: 600; padding: 0 10px; }"
            "QPushButton:hover { background: #1e293b; color: #e2e8f0;"
            " border-color: #334155; }")
        self.collapse_btn.clicked.connect(self.collapse_all)
        bar_layout.addWidget(self.collapse_btn)
        outer.addWidget(bar)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter structure...")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedHeight(28)
        self.search.textChanged.connect(self._apply_filter)
        search_row = QWidget()
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(12, 0, 12, 10)
        search_layout.addWidget(self.search)
        outer.addWidget(search_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setColumnWidth(COL_COUNT, 52)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemActivated.connect(self._on_item_clicked)
        outer.addWidget(self.tree, 1)

        self.footer = QLabel("No artifact loaded")
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet(
            "color: #64748b; font-size: 10px; padding: 8px 12px;"
            " background: #0d1117; border-top: 1px solid #1e293b;")
        outer.addWidget(self.footer)

        #: category -> leaf item, so extraction counts can land on the tree.
        self._leaves: dict[str, QTreeWidgetItem] = {}
        self._build()

        # Brushes are painted by hand, so a mode switch has to be answered
        # here; the stylesheet side of the pane is handled by ThemeManager.
        theme.changed.connect(self._retint)

    # -- construction -------------------------------------------------------

    def _build(self):
        for section, children in STRUCTURE_SPEC:
            top = QTreeWidgetItem(self.tree)
            top.setText(COL_NAME, section)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = top.font(COL_NAME)
            font.setBold(True)
            top.setFont(COL_NAME, font)
            self._paint(top, COL_NAME, "#e2e8f0")
            for label, target in children:
                if isinstance(target, list):
                    group = QTreeWidgetItem(top)
                    group.setText(COL_NAME, label)
                    group.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self._paint(group, COL_NAME, "#94a3b8")
                    for sub_label, sub_category in target:
                        self._add_leaf(group, sub_label, sub_category)
                else:
                    self._add_leaf(top, label, target)
            top.setExpanded(True)

    def _add_leaf(self, parent, label: str, category):
        leaf = QTreeWidgetItem(parent)
        leaf.setText(COL_NAME, label)
        if category is None:
            # No collector in this build. Say so instead of pretending zero
            # findings means zero data on the handset.
            leaf.setFlags(Qt.ItemFlag.NoItemFlags)
            leaf.setText(COL_COUNT, "\u2013")
            self._paint(leaf, COL_NAME, "#475569")
            leaf.setToolTip(COL_NAME, "Not collected by this build")
            leaf.setData(COL_NAME, Qt.ItemDataRole.UserRole, None)
            return
        leaf.setData(COL_NAME, Qt.ItemDataRole.UserRole, category)
        leaf.setText(COL_COUNT, "0")
        self._paint(leaf, COL_COUNT, "#64748b")
        leaf.setTextAlignment(COL_COUNT, Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
        self._leaves[category] = leaf

    # -- public API ---------------------------------------------------------

    def set_counts(self, counts: dict) -> None:
        filled = 0
        for category, leaf in self._leaves.items():
            count = int(counts.get(category, 0) or 0)
            if count:
                filled += 1
            leaf.setText(COL_COUNT, f"{count:,}")
            self._paint(leaf, COL_COUNT, "#60a5fa" if count else "#475569")
        total = sum(int(v or 0) for v in (counts or {}).values())
        if total:
            self.footer.setText(
                f"{total:,} items \u00b7 {filled} of {len(self._leaves)} "
                "categories hold data")
        else:
            self.footer.setText(
                "No artifact loaded \u2014 counts fill in as extraction runs")

    def select_category(self, category: str) -> None:
        leaf = self._leaves.get(category)
        if leaf is not None:
            self.tree.setCurrentItem(leaf)

    def collapse_all(self) -> None:
        self.tree.collapseAll()

    # -- internals ----------------------------------------------------------

    def _on_item_clicked(self, item, _column=0):
        category = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if category:
            self.category_selected.emit(category)

    def _apply_filter(self, text: str) -> None:
        query = (text or "").strip().lower()
        for index in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(index)
            self._filter_node(section, query)

    def _filter_node(self, node, query: str) -> bool:
        """Show a node if it or any descendant matches; unfold the parents."""
        own = node.text(COL_NAME).lower()
        match = bool(query) and query in own
        child_match = False
        for index in range(node.childCount()):
            child_match = self._filter_node(node.child(index), query) or child_match
        visible = match or child_match or not query
        node.setHidden(not visible)
        if query and child_match:
            node.setExpanded(True)
        return visible or match

    def _paint(self, item, column: int, dark_hex: str) -> None:
        item.setData(column, ROLE_COLOUR, dark_hex)
        item.setForeground(column, QBrush(QColor(colour(dark_hex))))

    def _retint(self, _mode=None) -> None:
        """Repaint every item that carries a remembered colour."""
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value() is not None:
            item = iterator.value()
            for column in (COL_NAME, COL_COUNT):
                dark = item.data(column, ROLE_COLOUR)
                if dark:
                    item.setForeground(column, QBrush(QColor(colour(dark))))
            iterator += 1
