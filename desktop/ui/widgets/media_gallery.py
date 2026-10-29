"""Thumbnail gallery for media and social artefacts.

The artifact viewer used to present media as a list of filenames with a "View"
button. That is fine for a dozen screenshots and useless for the two thousand
cached images a WhatsApp or Instagram account leaves behind: the examiner cannot
see what they are looking at without opening every single one.

This widget is the Cellebrite-style answer -- a grid of thumbnails, pulled from
the device in the background and cached on disk, with a size control. Nothing
here touches the evidence: thumbnails are pulled into ``~/.droidforenx/thumbs``
for display only, and the original remote path is what the viewer opens.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Iterable, List, Optional

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                             QWidget)

from ...core import adb_manager as adb

#: Extensions worth pulling a thumbnail for. Anything else gets a type badge.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_SUFFIXES = {".mp4", ".3gp", ".mkv", ".avi", ".mov", ".webm", ".m4v"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".amr", ".flac"}

TILE_SIZES = (110, 150, 200)


def media_kind(path: str) -> str:
    """Broad kind for a remote path, from its extension."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "Image"
    if suffix in VIDEO_SUFFIXES:
        return "Video"
    if suffix in AUDIO_SUFFIXES:
        return "Audio"
    return "File"


def is_thumbnailable(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def cache_root() -> Path:
    folder = Path.home() / ".droidforenx" / "thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cache_name(remote: str) -> str:
    digest = hashlib.sha1(remote.encode("utf-8", "replace")).hexdigest()
    return digest + Path(remote).suffix.lower()


class ThumbnailPuller(QThread):
    """Sequential ``adb pull`` for thumbnails.

    One worker, not a pool: ADB serialises commands per device anyway, and a
    burst of parallel pulls is what makes an examiner's whole session feel like
    the tool has frozen. A miss is reported, never raised -- a single unreadable
    file must not stop the grid from filling in.
    """

    ready = pyqtSignal(str, str)   # remote path, local path
    failed = pyqtSignal(str, str)  # remote path, reason

    def __init__(self, serial: str, parent=None):
        super().__init__(parent)
        self.serial = serial or ""
        self._queue: List[str] = []
        self._lock = threading.Lock()
        self._stop = False

    def enqueue(self, remote: str) -> None:
        if not remote:
            return
        with self._lock:
            if remote not in self._queue:
                self._queue.append(remote)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def stop(self) -> None:
        self._stop = True

    def _next(self) -> Optional[str]:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.pop(0)

    def run(self):
        cache = cache_root()
        while not self._stop:
            remote = self._next()
            if remote is None:
                self.msleep(80)
                continue
            # A finding we captured ourselves lives on this machine; there is
            # nothing to pull and no device needed to display it.
            direct = Path(remote)
            try:
                if direct.is_file() and direct.stat().st_size > 0:
                    self.ready.emit(remote, str(direct))
                    continue
            except OSError:
                pass
            local = cache / _cache_name(remote)
            if local.exists() and local.stat().st_size > 0:
                self.ready.emit(remote, str(local))
                continue
            if not self.serial:
                self.failed.emit(remote, "no device")
                continue
            try:
                ok = adb.pull_file(self.serial, remote, str(local), timeout=45)
            except Exception as exc:  # pragma: no cover - defensive
                self.failed.emit(remote, str(exc))
                continue
            if ok and local.exists() and local.stat().st_size > 0:
                self.ready.emit(remote, str(local))
            else:
                try:
                    local.unlink(missing_ok=True)
                except OSError:
                    pass
                self.failed.emit(remote, "pull failed")


class MediaTile(QFrame):
    """One square in the grid."""

    clicked = pyqtSignal(str)
    activated = pyqtSignal(str)

    def __init__(self, item: dict, size: int = TILE_SIZES[0], parent=None):
        super().__init__(parent)
        self.item = item
        self.remote = item.get("path", "")
        self.kind = item.get("kind") or media_kind(self.remote)
        self._size = size
        self._selected = False
        self._pixmap: Optional[QPixmap] = None

        self.setObjectName("mediaTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"{item.get('name', '')}\n{self.remote}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.thumb = QLabel()
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setStyleSheet(
            "background: #0f172a; border: 1px solid #1e293b; border-radius: 6px;"
            " color: #475569; font-size: 11px;")
        layout.addWidget(self.thumb)

        self.caption = QLabel(item.get("name", "") or Path(self.remote).name)
        self.caption.setStyleSheet(
            "color: #94a3b8; font-size: 10px; background: transparent; border: none;")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.caption)

        self.apply_size(size)
        self._paint_placeholder()

    # -- appearance ------------------------------------------------------
    def apply_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size + 26)
        self.thumb.setFixedSize(size - 12, size - 12)
        self._redraw()

    def _style(self) -> str:
        border = "#3b82f6" if self._selected else "#1e293b"
        background = "#1a2234" if self._selected else "#111827"
        return (f"QFrame#mediaTile {{ background: {background}; border: 1px solid {border};"
                " border-radius: 8px; }")

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setStyleSheet(self._style())

    def _paint_placeholder(self) -> None:
        side = max(self._size - 12, 40)
        pix = QPixmap(side, side)
        pix.fill(QColor("#0f172a"))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#334155"), 2))
        painter.drawRoundedRect(4, 4, side - 8, side - 8, 6, 6)
        badge = {"Video": "VIDEO", "Audio": "AUDIO"}.get(self.kind, "")
        if badge:
            painter.setPen(QPen(QColor("#64748b")))
            font = painter.font()
            font.setPointSize(max(7, side // 12))
            painter.setFont(font)
            painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, badge)
        else:
            # A simple picture glyph: a frame with a horizon and a sun.
            painter.setPen(QPen(QColor("#475569"), 2))
            painter.drawLine(side // 4, side - side // 3, side // 2, side // 2)
            painter.drawLine(side // 2, side // 2, side - side // 4, side - side // 3)
            painter.setBrush(QColor("#475569"))
            painter.drawEllipse(side // 3, side // 3, max(3, side // 12), max(3, side // 12))
        painter.end()
        self.thumb.setPixmap(pix)
        self.setStyleSheet(self._style())

    def _redraw(self) -> None:
        if self._pixmap is None:
            self._paint_placeholder()
            return
        side = self.thumb.width()
        self.thumb.setPixmap(self._pixmap.scaled(
            QSize(side, side),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation))

    def set_local_image(self, local_path: str) -> None:
        pix = QPixmap(local_path)
        if pix.isNull():
            self.mark_unavailable()
            return
        self._pixmap = pix
        self._redraw()

    def mark_unavailable(self) -> None:
        self._pixmap = None
        self.thumb.setText("unreadable")
        self.thumb.setStyleSheet(
            "background: #111827; border: 1px solid #334155; border-radius: 6px;"
            " color: #64748b; font-size: 10px;")

    # -- interaction -----------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.remote)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.remote)
        super().mouseDoubleClickEvent(event)


class MediaGallery(QWidget):
    """Scrollable thumbnail grid with a size control and its own puller."""

    back_requested = pyqtSignal()
    item_activated = pyqtSignal(str, str)  # remote path, kind

    #: Tiles appear this many at a time, so a 4,000-file category does not stall
    #: the window while Qt lays out widgets.
    CHUNK = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self.serial = ""
        self._items: List[dict] = []
        self._tiles: List[MediaTile] = []
        self._by_remote = {}
        self._pending = 0
        self._cursor = 0
        self._tile_size = TILE_SIZES[0]
        self._selected: Optional[str] = None
        self._puller: Optional[ThumbnailPuller] = None
        self._title = ""
        self._setup_ui()

    # -- construction ----------------------------------------------------
    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("galleryHeader")
        header.setStyleSheet(
            "QFrame#galleryHeader { background: #111827; border-bottom: 1px solid #1e293b; }")
        header.setFixedHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(12)

        self.back_btn = QPushButton("<")
        self.back_btn.setObjectName("galleryBack")
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.setStyleSheet(
            "QPushButton { background: #1e293b; border-radius: 18px; font-size: 16px;"
            " padding: 0; color: #e2e8f0; } QPushButton:hover { background: #334155; }")
        self.back_btn.clicked.connect(self.back_requested)
        header_layout.addWidget(self.back_btn)

        self.title_label = QLabel("Media")
        self.title_label.setStyleSheet(
            "color: #e2e8f0; font-size: 16px; font-weight: 700;"
            " background: transparent; border: none;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            "color: #64748b; font-size: 12px; background: transparent; border: none;")
        header_layout.addWidget(self.count_label)
        header_layout.addSpacing(12)

        for label, size in zip(("S", "M", "L"), TILE_SIZES):
            btn = QPushButton(label)
            btn.setFixedSize(28, 28)
            btn.setToolTip(f"{size} px thumbnails")
            btn.setStyleSheet(
                "QPushButton { background: #1e293b; color: #a8b4c4; border-radius: 6px;"
                " font-size: 11px; font-weight: 700; padding: 0; }"
                "QPushButton:hover { background: #334155; }")
            btn.clicked.connect(lambda _checked=False, s=size: self.set_tile_size(s))
            header_layout.addWidget(btn)

        outer.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_content = QWidget()
        self.grid = QGridLayout(self.scroll_content)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(16, 16, 16, 16)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.scroll_content)
        outer.addWidget(self.scroll, 1)

        footer = QFrame()
        footer.setObjectName("galleryFooter")
        footer.setStyleSheet(
            "QFrame#galleryFooter { background: #0d1117; border-top: 1px solid #1e293b; }")
        footer.setFixedHeight(40)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 0, 16, 0)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            "color: #64748b; font-size: 11px; background: transparent; border: none;")
        footer_layout.addWidget(self.status_label, 1)
        self.open_btn = QPushButton("Open")
        self.open_btn.setFixedHeight(28)
        self.open_btn.setMinimumWidth(84)
        self.open_btn.setEnabled(False)
        self.open_btn.setStyleSheet(
            "QPushButton { background: #3b82f6; color: white; border-radius: 6px;"
            " font-size: 12px; font-weight: 600; padding: 0 14px; }"
            "QPushButton:disabled { background: #1e293b; color: #475569; }")
        self.open_btn.clicked.connect(self._open_selected)
        footer_layout.addWidget(self.open_btn)
        outer.addWidget(footer)

        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_load_more)

    # -- data ------------------------------------------------------------
    def set_serial(self, serial: str) -> None:
        self.serial = serial or ""

    def _ensure_puller(self) -> ThumbnailPuller:
        if self._puller is None or not self._puller.isRunning():
            self._puller = ThumbnailPuller(self.serial)
            self._puller.ready.connect(self._on_thumb_ready)
            self._puller.failed.connect(self._on_thumb_failed)
            self._puller.start()
        return self._puller

    def load(self, title: str, items: Iterable[dict]) -> None:
        """Replace the grid contents.

        ``items`` are artifact dicts; only ``path``/``name``/``kind`` are read.
        """
        self.clear()
        self._title = title
        self.title_label.setText(title)
        self._items = [self._normalise(item) for item in items]
        self._items = [item for item in self._items if item["path"]]
        self.count_label.setText(f"{len(self._items)} files")
        if not self._items:
            self.status_label.setText("Nothing to show in this category.")
            return
        self._ensure_puller()
        self._load_chunk()

    @staticmethod
    def _normalise(item: dict) -> dict:
        data = item.get("_data") or {}
        remote = data.get("path") or item.get("source") or ""
        return {
            "path": remote,
            "name": data.get("filename") or Path(remote).name,
            "date": item.get("date", ""),
            "kind": item.get("type") or media_kind(remote),
        }

    def clear(self) -> None:
        for tile in self._tiles:
            tile.setParent(None)
            tile.deleteLater()
        self._tiles = []
        self._by_remote = {}
        self._items = []
        self._cursor = 0
        self._pending = 0
        self._selected = None
        self.open_btn.setEnabled(False)
        self.status_label.setText("")
        while self.grid.count():
            entry = self.grid.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.setParent(None)

    def _load_chunk(self) -> None:
        if self._cursor >= len(self._items):
            self._update_status()
            return
        chunk = self._items[self._cursor:self._cursor + self.CHUNK]
        self._cursor += len(chunk)
        puller = self._ensure_puller()
        for item in chunk:
            tile = MediaTile(item, self._tile_size)
            tile.clicked.connect(self._on_tile_clicked)
            tile.activated.connect(self._on_tile_activated)
            self._tiles.append(tile)
            self._by_remote[item["path"]] = tile
            if is_thumbnailable(item["path"]):
                self._pending += 1
                puller.enqueue(item["path"])
        self._reflow()
        self._update_status()

    def _maybe_load_more(self, value: int = 0) -> None:
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() > 0 and value < bar.maximum() - 400:
            return
        if self._cursor < len(self._items):
            QTimer.singleShot(0, self._load_chunk)

    # -- layout ----------------------------------------------------------
    def set_tile_size(self, size: int) -> None:
        self._tile_size = size
        for tile in self._tiles:
            tile.apply_size(size)
        self._reflow()

    def _columns(self) -> int:
        usable = max(self.scroll.viewport().width() - 32, self._tile_size)
        return max(1, usable // (self._tile_size + 10))

    def _reflow(self) -> None:
        columns = self._columns()
        for index, tile in enumerate(self._tiles):
            self.grid.addWidget(tile, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._tiles:
            self._reflow()

    # -- thumbnails ------------------------------------------------------
    def _on_thumb_ready(self, remote: str, local: str) -> None:
        tile = self._by_remote.get(remote)
        if tile is not None:
            tile.set_local_image(local)
        self._pending = max(0, self._pending - 1)
        self._update_status()

    def _on_thumb_failed(self, remote: str, reason: str) -> None:
        tile = self._by_remote.get(remote)
        if tile is not None:
            tile.mark_unavailable()
        self._pending = max(0, self._pending - 1)
        self._update_status()

    def _update_status(self) -> None:
        if not self._items:
            return
        shown = len(self._tiles)
        text = f"{shown} of {len(self._items)} shown"
        if self._pending:
            text += f" · loading {self._pending} thumbnails"
        if self._cursor < len(self._items):
            text += " · scroll for more"
        self.status_label.setText(text)

    # -- interaction -----------------------------------------------------
    def _on_tile_clicked(self, remote: str) -> None:
        for path, tile in self._by_remote.items():
            tile.set_selected(path == remote)
        self._selected = remote
        self.open_btn.setEnabled(bool(remote))

    def _on_tile_activated(self, remote: str) -> None:
        self._on_tile_clicked(remote)
        self.item_activated.emit(remote, media_kind(remote))

    def _open_selected(self) -> None:
        if self._selected:
            self.item_activated.emit(self._selected, media_kind(self._selected))

    def stop(self) -> None:
        if self._puller is not None:
            self._puller.stop()
            self._puller.wait(1500)
            self._puller = None

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
