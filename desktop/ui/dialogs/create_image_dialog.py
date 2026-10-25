"""FTK-Imager-style *Create Image* dialog.

The operator picks a source (a connected device's partition, or an acquired case
folder), an output format, and the parameters for it; a worker thread writes the
image and records it. Everything the writers need comes from
:mod:`desktop.core.imaging`; this module is only the front end.

Sources and formats are gated against each other on purpose: a raw block device
can only be written as RAW, the logical /sdcard tar can be written as RAW or
repacked as ZIP, and a folder can only be written as ZIP. Offering a combination
that the writer would reject is worse than not offering it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QTabWidget, QTextEdit, QVBoxLayout,
    QWidget,
)

from ...core import evidence_store
from ...core.imaging import (
    FORMAT_LABELS, FORMAT_SOURCE_KIND, FORMAT_STREAM_KIND, create_image, registry,
    sources,
)
from ...core.imaging.base import ImageRequest
from ...ui.widgets.progress_panel import ProgressPanel

#: Segment sizes offered in the dialog, ``(label, bytes)``.
SEGMENT_CHOICES = (
    ("Single file (no splitting)", 0),
    ("512 MiB", 512 * 1024 ** 2),
    ("1 GiB", 1024 ** 3),
    ("2 GiB", 2 * 1024 ** 3),
    ("4 GiB", 4 * 1024 ** 3),
)

HASH_CHOICES = (
    ("SHA-256", "sha256"),
    ("SHA-1", "sha1"),
    ("MD5", "md5"),
)

COMPRESSION_CHOICES = (
    ("Fast (default)", "fast"),
    ("Best (slower)", "best"),
    ("None (store)", "none"),
)

#: Narrowest the form is allowed to get, and the floor its height may shrink to
#: when the desktop is shorter than the content.
MIN_WIDTH = 720
MIN_HEIGHT = 420

#: Gap kept between the window and the edge of the available desktop.
SCREEN_MARGIN = 40


def fit_size(desired: QSize, available) -> QSize:
    """Clamp a window size so the whole window fits inside ``available``.

    A QDialog takes its size from the layout's ``sizeHint`` and QWidget does not
    shrink itself to fit the desktop. This form is taller than a 720 px screen,
    so it opened with its bottom edge - the Examiner fields and both buttons -
    below the taskbar. The height is capped rather than trusted.
    """
    max_w = max(MIN_WIDTH, available.width() - SCREEN_MARGIN)
    max_h = max(MIN_HEIGHT, available.height() - SCREEN_MARGIN)
    return QSize(
        min(max(desired.width(), MIN_WIDTH), max_w),
        min(max(desired.height(), MIN_HEIGHT), max_h),
    )


def clamp_to_screen(frame: QRect, available: QRect) -> QPoint:
    """Position that keeps ``frame`` inside ``available``.

    ``frame`` must be a *frame* rectangle - what ``frameGeometry()`` returns,
    including the title bar - because the frame is the rectangle the window
    manager positions. Clamping the client rectangle instead measures the top of
    the title bar against the height of the client area, so a window that
    computes as fitting still hangs below the bottom edge by exactly the height
    of its title bar. On a 720 px desktop that is how the Create Image button
    came to sit 28 px under the screen while the geometry looked fine.

    A frame larger than the desktop cannot be placed wholly inside, and the top
    is then pinned rather than the bottom: a reachable title bar beats a
    reachable status line.
    """
    max_x = max(available.left(), available.right() - frame.width() + 1)
    max_y = max(available.top(), available.bottom() - frame.height() + 1)
    return QPoint(
        min(max(frame.x(), available.left()), max_x),
        min(max(frame.y(), available.top()), max_y),
    )


class PartitionLoader(QThread):
    """Read the device's partition table off the GUI thread."""

    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, serial: str, parent=None):
        super().__init__(parent)
        self._serial = serial

    def run(self):
        try:
            self.loaded.emit(sources.list_partitions(self._serial, probe=True))
        except Exception as exc:  # pragma: no cover - depends on the device
            self.failed.emit(str(exc))


class _Cancelled(Exception):
    """Raised from a writer's progress callback to abort a run mid-way."""


class ImageWorker(QThread):
    """Write one image, then record it. Never touches widgets from ``run``."""

    log = pyqtSignal(str, str)
    progress = pyqtSignal(int, int, str)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, request: ImageRequest, source, source_kind: str, case_id: str,
                 session_id: str = "", source_factory=None, source_type: str = "",
                 serial: str = "", parent=None):
        super().__init__(parent)
        self._request = request
        self._source = source
        self._source_kind = source_kind
        self._case_id = case_id
        self._session_id = session_id
        self._source_factory = source_factory
        self._source_type = source_type
        self._serial = serial
        self._cancelled = False
        self._reader = None
        self._stage = ""
        self._expected_total = 0

    def cancel(self):
        self._cancelled = True
        if self._reader is not None:
            self._reader.close()

    def _on_progress(self, done: int, total: int, stage: str):
        # The writers call this synchronously, so raising here actually stops a
        # container write -- without it, Cancel did nothing until the whole ZIP
        # was finished and the operator was told "no image was written"
        # while one sat on disk.
        if self._cancelled:
            raise _Cancelled()
        self.progress.emit(done, total, stage)
        if stage != self._stage:
            self._stage = stage
            self.log.emit(f"  {stage}", "info")

    def run(self):
        try:
            if self._source_kind == "stream":
                if self._source_type == "logical":
                    # A tar stream cannot report its own length, so it is
                    # measured before the first byte moves. This is what turns
                    # the forty-minute logical image from a spinning bar into a
                    # percentage. When the probe fails the count stays 0 and the
                    # bar honestly stays indeterminate.
                    self.log.emit("Sizing source (du /sdcard/)...", "info")
                    self.progress.emit(0, 0, "Sizing source...")
                    est = 0
                    try:
                        if self._serial:
                            est = sources.logical_size_estimate(self._serial)
                    except Exception:
                        est = 0
                    self._expected_total = est
                    if est > 0:
                        self._request.expected_bytes = est
                        self.log.emit(
                            f"Source size estimate: {est:,} bytes "
                            f"({est / 1024 ** 3:.2f} GiB)", "info")
                        self.progress.emit(0, est, "Streaming /sdcard")
                    else:
                        self.log.emit(
                            "Could not size the source; progress will show bytes "
                            "written without a percentage.", "warning")
                self._reader = self._source_factory()
                self.log.emit(f"Streaming {self._request.source_label}...", "info")
                try:
                    result = create_image(self._reader, self._request, progress=self._on_progress)
                except ValueError as exc:
                    # adb's stderr used to be discarded, so a stream that died
                    # instantly reported nothing but "no data was read". Whatever
                    # adb said about the failure is the only real evidence.
                    detail = getattr(self._reader, "error_output", lambda: "")()
                    if detail:
                        raise ValueError(f"{exc} (adb: {detail})") from None
                    raise
                finally:
                    self._reader.close()
            else:
                result = create_image(self._source, self._request, progress=self._on_progress)

            if self._cancelled:
                self.cancelled.emit()
                return

            self.log.emit(
                f"Written {result.primary.name} ({result.total_bytes:,} bytes, "
                f"{len(result.segments)} segment(s))", "success")
            self.log.emit(f"{result.hash_algo}: {result.digest}", "info")
            if self._request.verify:
                self.log.emit("Verification: PASSED" if result.verified else "Verification: FAILED",
                              "success" if result.verified else "error")

            info = registry.record_image(
                result, self._request, self._case_id,
                session_id=self._session_id or None, actor="operator",
                source_type=self._source_type)
            self.log.emit(
                f"Recorded in case manifest ({len(info['artifacts'])} artifact(s), "
                f"status={info['status']})", "info")
            self.finished_ok.emit({"result": result, "info": info})
        except _Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            if self._cancelled:
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))


class CreateImageDialog(QDialog):
    """Pick a source and a format, then write the image."""

    def __init__(self, parent=None, serial: str = "", case_id: str = "", session_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Create Image")
        self.setMinimumWidth(MIN_WIDTH)
        self.setSizeGripEnabled(True)
        self._serial = serial or ""
        self._case_id = case_id or ""
        self._session_id = session_id or ""
        self._partitions = []
        self._loader = None
        self._worker = None
        self._fitted = False

        # Everything except the action row scrolls. The buttons are the one part
        # that must not be reachable only by scrolling: on a short display they
        # were the part that fell off the bottom of the screen.
        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        body_layout.addWidget(self._build_source_group())
        body_layout.addWidget(self._build_output_group())
        body_layout.addWidget(self._build_metadata_group())
        self.progress_group = self._build_progress_group()
        body_layout.addWidget(self.progress_group)
        body_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self._body)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(self._build_buttons())

        # Nothing has been written yet, so an empty console is only height.
        self.progress_group.setVisible(False)

        # Connected only once every widget exists: adding the first tab emits
        # currentChanged, and the handler needs the format combo to be there.
        self.source_tabs.currentChanged.connect(self._on_source_changed)
        self.folder_edit.textChanged.connect(self._on_source_changed)
        self.format_combo.currentIndexChanged.connect(self._sync_segment_toggle)

        self._on_source_changed()
        if self._serial:
            self._load_partitions()
        else:
            self.source_tabs.setTabEnabled(0, False)
            self.source_tabs.setTabEnabled(2, False)
            self.source_tabs.setCurrentIndex(1)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            self._fit_to_screen()
            # Qt centres a dialog on its parent as it is shown, and that
            # placement lands after this event - so the position clamped below
            # is not the position the window keeps. On a 720 px desktop the
            # parent-centred spot put the Create Image button 28 px below the
            # bottom edge of the screen. Clamp again once the event loop is
            # running and the window manager has finished placing the dialog.
            QTimer.singleShot(0, self._settle_on_screen)

    def _settle_on_screen(self):
        """Clamp the position again, after the window manager has placed us."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            self._keep_on_screen(screen.availableGeometry())

    def _fit_to_screen(self):
        """Size to the content, capped to the desktop that is actually present."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:  # pragma: no cover - no display at all
            return
        content = self._body.sizeHint()
        footer = self.btn_start.sizeHint().height() + 3 * 12
        desired = QSize(
            max(content.width() + 2 * 16, MIN_WIDTH),
            content.height() + footer + 2 * 16,
        )
        available = screen.availableGeometry()
        self.resize(fit_size(desired, available))
        self._keep_on_screen(available)

    def _keep_on_screen(self, available):
        """Nudge the window back inside the desktop after it settles.

        Qt reports a top-level window's position through ``pos()``/``y()`` as the
        top of its *frame*, while ``height()`` is the client height. Comparing
        those two directly leaves the title bar unaccounted for, so the frame is
        what gets clamped here, and the move is applied as a delta.
        """
        frame = self.frameGeometry()
        target = clamp_to_screen(frame, available)
        dx = target.x() - frame.x()
        dy = target.y() - frame.y()
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)

    # ------------------------------------------------------------------ build

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("Source")
        outer = QVBoxLayout(group)

        self.source_tabs = QTabWidget()

        device = QWidget()
        device_layout = QVBoxLayout(device)
        row = QHBoxLayout()
        self.partition_combo = QComboBox()
        self.partition_combo.setMinimumWidth(420)
        row.addWidget(self.partition_combo, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self._load_partitions)
        row.addWidget(self.btn_refresh)
        device_layout.addLayout(row)
        self.partition_hint = QLabel(
            "Reading the partition table..." if self._serial
            else "No device connected. Attach a device and refresh.")
        self.partition_hint.setWordWrap(True)
        self.partition_hint.setStyleSheet("color: #64748b;")
        device_layout.addWidget(self.partition_hint)
        self.source_tabs.addTab(device, "Connected device")

        folder = QWidget()
        folder_layout = QVBoxLayout(folder)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("Folder to image (e.g. an acquired case directory)")
        folder_row.addWidget(self.folder_edit, 1)
        self.btn_browse_source = QPushButton("Browse...")
        self.btn_browse_source.clicked.connect(self._pick_source_folder)
        folder_row.addWidget(self.btn_browse_source)
        folder_layout.addLayout(folder_row)
        folder_hint = QLabel(
            "Image an acquired case directory as a ZIP container. "
            "This is the usual path for unrooted devices.")
        folder_hint.setWordWrap(True)
        folder_hint.setStyleSheet("color: #64748b;")
        folder_layout.addWidget(folder_hint)
        self.source_tabs.addTab(folder, "Case folder")

        logical = QWidget()
        logical_layout = QVBoxLayout(logical)
        logical_hint = QLabel(
            "Streams the device's user storage (/sdcard) as a POSIX tar archive. "
            "Write it as RAW (dd) to preserve the stream byte for byte, or as ZIP "
            "to repack the files into one compressed, browsable archive -- both "
            "with hash sidecars. Works without root; this is the imaging path for "
            "unrooted handsets. Expect the image to be about as large as the "
            "storage in use (ZIP is smaller for documents, barely smaller for "
            "photos and video), and the verify step at the end to take a while.")
        logical_hint.setWordWrap(True)
        logical_hint.setStyleSheet("color: #64748b;")
        logical_layout.addWidget(logical_hint)
        logical_layout.addStretch(1)
        self.source_tabs.addTab(logical, "Logical (/sdcard)")

        outer.addWidget(self.source_tabs)
        return group

    def _build_output_group(self) -> QGroupBox:
        group = QGroupBox("Image output")
        form = QFormLayout(group)

        dest_row = QHBoxLayout()
        self.dest_edit = QLineEdit(self._default_destination())
        dest_row.addWidget(self.dest_edit, 1)
        btn_dest = QPushButton("Browse...")
        btn_dest.clicked.connect(self._pick_destination)
        dest_row.addWidget(btn_dest)
        form.addRow("Destination folder", dest_row)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.name_edit = QLineEdit(f"image_{stamp}")
        form.addRow("Image base name", self.name_edit)

        self.format_combo = QComboBox()
        form.addRow("Image format", self.format_combo)

        self.compression_combo = QComboBox()
        for label, value in COMPRESSION_CHOICES:
            self.compression_combo.addItem(label, value)
        form.addRow("Compression", self.compression_combo)

        self.segment_combo = QComboBox()
        for label, value in SEGMENT_CHOICES:
            self.segment_combo.addItem(label, value)
        self.segment_combo.setCurrentIndex(3)  # 2 GiB
        form.addRow("Segment size", self.segment_combo)

        self.hash_combo = QComboBox()
        for label, value in HASH_CHOICES:
            self.hash_combo.addItem(label, value)
        form.addRow("Hash algorithm", self.hash_combo)

        self.verify_check = QCheckBox("Re-read the image and verify the hash after writing")
        self.verify_check.setChecked(True)
        form.addRow("", self.verify_check)

        return group

    def _build_metadata_group(self) -> QGroupBox:
        group = QGroupBox("Examiner notes (recorded with the image)")
        grid = QGridLayout(group)
        self.case_number_edit = QLineEdit(self._case_id)
        self.evidence_number_edit = QLineEdit()
        self.examiner_edit = QLineEdit()
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("What was imaged and why")

        grid.addWidget(QLabel("Case number"), 0, 0)
        grid.addWidget(self.case_number_edit, 0, 1)
        grid.addWidget(QLabel("Evidence number"), 0, 2)
        grid.addWidget(self.evidence_number_edit, 0, 3)
        grid.addWidget(QLabel("Examiner"), 1, 0)
        grid.addWidget(self.examiner_edit, 1, 1)
        grid.addWidget(QLabel("Description"), 1, 2)
        grid.addWidget(self.description_edit, 1, 3)
        return group

    def _build_progress_group(self) -> QGroupBox:
        group = QGroupBox("Progress")
        layout = QVBoxLayout(group)
        self.progress_panel = ProgressPanel()
        layout.addWidget(self.progress_panel)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(150)
        layout.addWidget(self.console)
        return group

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()
        self.btn_start = QPushButton("Create Image")
        self.btn_start.setObjectName("btn-success")
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel_or_close)
        row.addWidget(self.btn_cancel)
        return row

    # ------------------------------------------------------------------ state

    def _default_destination(self) -> str:
        if self._case_id:
            try:
                return str(evidence_store.acquired_dir(self._case_id))
            except Exception:  # pragma: no cover - defensive
                pass
        return str(Path.home() / ".droidforenx" / "images")

    @property
    def source_kind(self) -> str:
        # Case folder (index 1) is the only folder source; the device picker and
        # the logical /sdcard stream both feed the streaming RAW writer.
        return "folder" if self.source_tabs.currentIndex() == 1 else "stream"

    @property
    def stream_kind(self) -> str:
        """``"logical"`` for the /sdcard tar stream, else ``"partition"``."""
        return "logical" if self.source_tabs.currentIndex() == 2 else "partition"

    def available_formats(self) -> list:
        """Formats that the current source can actually be written as."""
        kind = self.source_kind
        stream = self.stream_kind if kind == "stream" else ""
        formats = []
        for fmt, needs in FORMAT_SOURCE_KIND.items():
            if needs != kind:
                continue
            # A tar repack only makes sense for the logical stream; a raw
            # partition is not a tar and must not offer it.
            wants = FORMAT_STREAM_KIND.get(fmt, "")
            if wants and wants != stream:
                continue
            formats.append(fmt)
        return formats

    def _on_source_changed(self):
        """Repopulate the format list so only valid pairings are offered."""
        wanted = self.available_formats()
        previous = self.format_combo.currentData()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for fmt in wanted:
            self.format_combo.addItem(FORMAT_LABELS.get(fmt, fmt), fmt)
        if previous in wanted:
            self.format_combo.setCurrentIndex(wanted.index(previous))
        self.format_combo.blockSignals(False)
        self._sync_segment_toggle()

    def _sync_segment_toggle(self):
        is_raw = self.format_combo.currentData() == "raw"
        self.segment_combo.setEnabled(is_raw)
        # RAW stores bytes as they arrive; only the container writers compress.
        self.compression_combo.setEnabled(not is_raw)

    def _log(self, message: str, level: str = "info"):
        self.console.append(message)

    # ------------------------------------------------------------------ sources

    def _load_partitions(self):
        if not self._serial:
            self.partition_hint.setText("No device connected. Attach a device and refresh.")
            return
        if self._loader is not None and self._loader.isRunning():
            # Replacing the reference would let the running QThread be garbage
            # collected mid-run, which aborts the process.
            return
        self.btn_refresh.setEnabled(False)
        self.partition_hint.setText("Reading the partition table...")
        self._loader = PartitionLoader(self._serial, self)
        self._loader.loaded.connect(self._on_partitions)
        self._loader.failed.connect(self._on_partitions_failed)
        self._loader.start()

    def _on_partitions(self, partitions):
        self.btn_refresh.setEnabled(True)
        self._partitions = partitions
        previous = self.partition_combo.currentData()
        self.partition_combo.clear()
        for partition in partitions:
            self.partition_combo.addItem(partition.label(), partition)
        if not partitions:
            self.partition_hint.setText(
                "No partitions returned. The device may need unlocking, or its "
                "/dev/block/by-name may not be readable.")
            return
        usable = [p for p in partitions if p.readable]
        if not usable:
            # Typical for an unrooted modern device: the shell user cannot open
            # a single block device, boot included. Physical imaging is not
            # possible here, and the operator should be told that once, plainly,
            # instead of watching streams fail one partition at a time.
            self.partition_hint.setText(
                f"0 of {len(partitions)} partitions can be read on this device: "
                "it is not rooted, so the shell user is denied every block "
                "device (boot included). Physical imaging requires a rooted "
                "device; on an unrooted handset take a logical image on the "
                "'Logical (/sdcard)' tab -- as RAW, or as a compressed ZIP.")
        else:
            self.partition_hint.setText(
                f"{len(usable)} of {len(partitions)} partitions are readable without root. "
                "Partitions marked 'needs root' cannot be captured on this device.")
        if previous in partitions:
            self.partition_combo.setCurrentIndex(partitions.index(previous))
        elif usable:
            self.partition_combo.setCurrentIndex(partitions.index(usable[0]))

    def _on_partitions_failed(self, message):
        self.btn_refresh.setEnabled(True)
        self.partition_hint.setText(f"Could not read the partition table: {message}")

    def _pick_source_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select a folder to image",
                                                  self.folder_edit.text() or str(Path.home()))
        if chosen:
            self.folder_edit.setText(chosen)

    def _pick_destination(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select a destination folder",
                                                  self.dest_edit.text() or str(Path.home()))
        if chosen:
            self.dest_edit.setText(chosen)

    # ------------------------------------------------------------------ run

    def build_request(self) -> ImageRequest:
        """Assemble the request from the current widget state."""
        metadata = {
            "case_number": self.case_number_edit.text().strip(),
            "evidence_number": self.evidence_number_edit.text().strip(),
            "examiner": self.examiner_edit.text().strip(),
            "description": self.description_edit.text().strip(),
        }
        metadata = {key: value for key, value in metadata.items() if value}

        if self.source_kind == "stream":
            if self.stream_kind == "logical":
                source_label = f"{sources.LOGICAL_ROOT} (tar over ADB from {self._serial or 'device'})"
            else:
                partition = self.partition_combo.currentData()
                source_label = partition.block if partition else ""
        else:
            source_label = self.folder_edit.text().strip()

        return ImageRequest(
            dest_dir=Path(self.dest_edit.text().strip() or "."),
            base_name=self.name_edit.text().strip(),
            image_format=self.format_combo.currentData(),
            source_label=source_label,
            hash_algo=self.hash_combo.currentData(),
            compression=self.compression_combo.currentData(),
            segment_size=self.segment_combo.currentData() or 0,
            verify=self.verify_check.isChecked(),
            metadata=metadata,
        )

    def resolve_source(self):
        """Return ``(source, reader_factory)``; a reader factory only for streams."""
        if self.source_kind == "stream":
            if self.stream_kind == "logical":
                if not self._serial:
                    raise ValueError(
                        "No device is connected. Attach the device to take a "
                        "logical image of its storage.")
                serial = self._serial

                def factory():
                    return sources.open_logical_reader(serial)

                return None, factory

            partition = self.partition_combo.currentData()
            if partition is None:
                raise ValueError("Select a partition to image.")
            if not partition.readable:
                raise ValueError(
                    f"'{partition.name}' is not readable without root on this device. "
                    "Physical imaging needs a rooted device; to capture an "
                    "unrooted handset, use a logical acquisition and image the "
                    "case folder instead.")

            def factory():
                # Re-check readability at stream time: the partition list was
                # probed when the dialog opened and the device may have been
                # unplugged, re-authorized or asleep since then. A stale
                # "readable" used to open a stream that read zero bytes and
                # died with nothing but a generic message.
                if not sources.probe_readable(self._serial, partition.block):
                    raise ValueError(
                        f"'{partition.name}' cannot be read now: the device may "
                        "have disconnected, or this partition needs root.")
                return sources.open_partition_reader(
                    self._serial, partition.block, rooted=False, size=partition.size_bytes)

            return None, factory
        folder = self.folder_edit.text().strip()
        if not folder:
            raise ValueError("Choose a folder to image.")
        return Path(folder), None

    def _start(self):
        try:
            request = self.build_request()
            source, factory = self.resolve_source()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot create image", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "Cannot create image", str(exc))
            return

        if self.source_kind == "folder" and not Path(request.source_label).is_dir():
            QMessageBox.warning(self, "Cannot create image", "Choose a folder to image first.")
            return

        # The console only becomes useful once bytes are moving, and revealing it
        # here keeps the form itself short enough to fit a small desktop.
        self.progress_group.setVisible(True)
        self._body.adjustSize()
        self.scroll.ensureWidgetVisible(self.progress_group)

        self.console.clear()
        self.btn_start.setEnabled(False)
        self.progress_panel.reset()
        self.progress_panel.set_indeterminate("Preparing")
        self._log(f"Creating {request.image_format} image '{request.base_name}' "
                  f"in {request.dest_dir}", "info")

        self._worker = ImageWorker(request, source, self.source_kind, self._case_id,
                                   self._session_id, source_factory=factory,
                                   source_type="logical" if self.stream_kind == "logical" else "",
                                   serial=self._serial, parent=self)
        self._worker.log.connect(self._log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.start()

    def _on_progress(self, done: int, total: int, stage: str = ""):
        self.progress_panel.set_progress(done, total, stage)

    def _on_finished(self, payload):
        self.progress_panel.finish()
        self.btn_start.setEnabled(True)
        info = payload["info"]
        if info["verification_status"] == "failed":
            self._log("Verification FAILED -- do not rely on this image.", "error")
            QMessageBox.warning(
                self, "Verification failed",
                f"{payload['result'].primary}\n\n"
                "The re-read hash does not match the hash computed while "
                "writing. The image is recorded as failed; treat it as suspect.")
            return
        self._log("Image created and recorded.", "success")
        QMessageBox.information(
            self, "Image created",
            f"{payload['result'].primary}\n\n"
            f"Verification: {info['verification_status']}\n"
            f"Recorded status: {info['status']}")

    def _on_failed(self, message):
        self.progress_panel.stop_with("Failed")
        self.btn_start.setEnabled(True)
        self._log(f"Failed: {message}", "error")
        QMessageBox.critical(self, "Image failed", message)

    def _on_cancelled(self):
        self.progress_panel.stop_with("Cancelled")
        self.btn_start.setEnabled(True)
        self._log("Cancelled. No image was written.", "warning")

    def _cancel_or_close(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._log("Cancelling...", "warning")
            return
        self.reject()

    def closeEvent(self, event):
        if self._loader is not None and self._loader.isRunning():
            if not self._loader.wait(10000):
                QMessageBox.warning(
                    self, "Still reading",
                    "The partition table is still being read. Try closing again in a moment.")
                event.ignore()
                return
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            if not self._worker.wait(10000):
                # Destroying a running QThread aborts the process; refuse the
                # close and let the stream finish tearing down instead.
                QMessageBox.warning(
                    self, "Still stopping",
                    "The stream is still shutting down. Try closing again in a moment.")
                event.ignore()
                return
        super().closeEvent(event)
