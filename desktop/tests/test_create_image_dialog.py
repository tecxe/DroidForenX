"""
Headless Qt tests for the *Create Image* dialog.

These need a Qt application but no display and no device: the platform is forced
to ``offscreen`` and the module is skipped if PyQt6 cannot start one.

They pin the parts of the dialog that are easy to get wrong and hard to notice:

  1. Source and format are gated against each other -- a device partition offers
     RAW only, a folder offers ZIP only.
  2. ``build_request()`` produces a request the writers actually accept, and an
     invalid base name is rejected before anything is written.
  3. ``resolve_source()`` refuses an unreadable partition instead of starting a
     doomed ``dd``.

No ADB call is made: partitions are handed to the dialog directly.

Run with:  python -m unittest desktop.tests.test_create_image_dialog -v
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, QRect, QSize
    from PyQt6.QtWidgets import QApplication
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False

from desktop.core.imaging import ImageRequest, create_image, sources
from desktop.ui.dialogs.create_image_dialog import (
    MIN_HEIGHT, MIN_WIDTH, CreateImageDialog, ImageWorker, _Cancelled,
    clamp_to_screen, fit_size,
)


def _make_qapp():
    if not _QT_IMPORT:
        return None
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class CreateImageDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-dialog-"))
        self.source = self.tmp / "case"
        (self.source / "nested").mkdir(parents=True)
        (self.source / "note.txt").write_bytes(b"hello")
        (self.source / "nested" / "blob.bin").write_bytes(b"\x00\x01\x02")
        self.dest = self.tmp / "out"
        self.dialog = CreateImageDialog(serial="")

    def tearDown(self):
        self.dialog.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use_device_tab(self, partitions):
        """Reach the device tab without letting the loader thread call ADB."""
        self.dialog.source_tabs.setTabEnabled(0, True)
        self.dialog.source_tabs.setCurrentIndex(0)
        self.dialog._on_partitions(partitions)

    def test_a_connected_device_offers_raw_only(self):
        self.use_device_tab([sources.Partition(name="boot_a", block="/dev/block/sde13", readable=True)])
        self.assertEqual(self.dialog.source_kind, "stream")
        self.assertEqual(self.dialog.available_formats(), ["raw"])
        self.assertEqual(self.dialog.format_combo.currentData(), "raw")

    def test_a_folder_offers_zip_only(self):
        self.assertEqual(self.dialog.source_kind, "folder")
        self.assertEqual(
            [self.dialog.format_combo.itemData(i)
             for i in range(self.dialog.format_combo.count())],
            ["zip"])

    def test_switching_source_repopulates_the_format_list(self):
        self.use_device_tab([sources.Partition(name="boot_a", block="/dev/block/sde13", readable=True)])
        self.assertEqual(self.dialog.format_combo.count(), 1)

        self.dialog.source_tabs.setCurrentIndex(1)
        self.assertEqual(
            [self.dialog.format_combo.itemData(i)
             for i in range(self.dialog.format_combo.count())],
            ["zip"])

    def test_a_cancelled_worker_stops_at_the_next_progress_event(self):
        request = ImageRequest(dest_dir=Path(self.tmp), base_name="x", image_format="raw")
        worker = ImageWorker(request, None, "stream", "")
        worker._cancelled = True
        with self.assertRaises(_Cancelled):
            worker._on_progress(1, 2, "writing")

    def test_the_logical_tab_offers_raw_and_needs_no_partition(self):
        self.dialog.source_tabs.setTabEnabled(2, True)
        self.dialog.source_tabs.setCurrentIndex(2)
        self.dialog._serial = "SER123"
        self.dialog.name_edit.setText("logical")

        self.assertEqual(self.dialog.source_kind, "stream")
        self.assertEqual(self.dialog.stream_kind, "logical")
        self.assertEqual(self.dialog.available_formats(), ["raw"])

        source, factory = self.dialog.resolve_source()
        self.assertIsNone(source)
        self.assertTrue(callable(factory))

        request = self.dialog.build_request()
        self.assertIn("/sdcard", request.source_label)
        self.assertIn("SER123", request.source_label)

    def test_the_logical_tab_is_refused_without_a_device(self):
        self.dialog.source_tabs.setTabEnabled(2, True)
        self.dialog.source_tabs.setCurrentIndex(2)
        with self.assertRaises(ValueError):
            self.dialog.resolve_source()

    def test_compression_is_disabled_for_raw_and_enabled_for_containers(self):
        self.use_device_tab([sources.Partition(
            name="boot_a", block="/dev/block/sde13", readable=True)])
        self.assertFalse(self.dialog.compression_combo.isEnabled())
        self.assertTrue(self.dialog.segment_combo.isEnabled())

        self.dialog.source_tabs.setCurrentIndex(1)
        self.assertTrue(self.dialog.compression_combo.isEnabled())
        self.assertFalse(self.dialog.segment_combo.isEnabled())

    def test_build_request_reflects_the_widgets(self):
        self.dialog.folder_edit.setText(str(self.source))
        self.dialog.dest_edit.setText(str(self.dest))
        self.dialog.name_edit.setText("evidence")
        self.dialog.examiner_edit.setText("Tester")
        self.dialog.segment_combo.setCurrentIndex(0)

        request = self.dialog.build_request()

        self.assertEqual(request.image_format, "zip")
        self.assertEqual(request.base_name, "evidence")
        self.assertEqual(request.dest_dir, self.dest)
        self.assertEqual(request.source_label, str(self.source))
        self.assertEqual(request.hash_algo, "sha256")
        self.assertEqual(request.segment_size, 0)
        self.assertTrue(request.verify)
        self.assertEqual(request.metadata, {"examiner": "Tester"})

    def test_an_invalid_base_name_is_rejected_before_writing(self):
        self.dialog.folder_edit.setText(str(self.source))
        self.dialog.name_edit.setText("../escape")
        with self.assertRaises(ValueError):
            self.dialog.build_request()
        self.assertEqual(list(self.dest.glob("*")) if self.dest.exists() else [], [])

    def test_resolve_source_refuses_an_unreadable_partition(self):
        self.use_device_tab([sources.Partition(
            name="userdata", block="/dev/block/sda21", readable=False, needs_root=True)])
        with self.assertRaises(ValueError):
            self.dialog.resolve_source()

    def test_resolve_source_needs_a_folder(self):
        self.dialog.folder_edit.setText("")
        with self.assertRaises(ValueError):
            self.dialog.resolve_source()

    def test_the_built_request_produces_a_real_image(self):
        self.dialog.folder_edit.setText(str(self.source))
        self.dialog.dest_edit.setText(str(self.dest))
        self.dialog.name_edit.setText("evidence")
        self.dialog.segment_combo.setCurrentIndex(0)

        source, factory = self.dialog.resolve_source()
        self.assertIsNone(factory)
        result = create_image(source, self.dialog.build_request())

        self.assertTrue(result.verified)
        self.assertEqual(result.primary.name, "evidence.zip")
        self.assertTrue((self.dest / "evidence.zip.sha256").exists())


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class DialogFitsOnScreenTest(unittest.TestCase):
    """The dialog must never open larger than the desktop it opens on.

    It used to build a 798 px tall form regardless of the screen: on a 720 px
    desktop the Examiner fields and both buttons sat below the taskbar, so the
    operator could fill the form and still have nowhere to press Create Image.
    """

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def test_height_is_capped_to_the_desktop(self):
        capped = fit_size(QSize(MIN_WIDTH, 798), QRect(0, 0, 1280, 720))
        self.assertLessEqual(capped.height(), 720)
        self.assertGreaterEqual(capped.height(), MIN_HEIGHT)

    def test_a_dialog_that_already_fits_is_left_alone(self):
        desired = QSize(760, 500)
        self.assertEqual(fit_size(desired, QRect(0, 0, 1280, 720)), desired)

    def test_width_never_drops_below_the_usable_minimum(self):
        shrunk = fit_size(QSize(100, 100), QRect(0, 0, 1280, 720))
        self.assertEqual(shrunk.width(), MIN_WIDTH)

    def test_the_action_buttons_are_outside_the_scrolling_area(self):
        dialog = CreateImageDialog(serial="")
        try:
            self.assertFalse(dialog.scroll.isAncestorOf(dialog.btn_start))
            self.assertFalse(dialog.scroll.isAncestorOf(dialog.btn_cancel))
            # the form itself is what scrolls
            self.assertTrue(dialog.scroll.isAncestorOf(dialog.format_combo))
        finally:
            dialog.close()

    def test_the_console_stays_hidden_until_an_image_is_started(self):
        dialog = CreateImageDialog(serial="")
        try:
            self.assertFalse(dialog.progress_group.isVisibleTo(dialog))
        finally:
            dialog.close()

    def test_an_opened_dialog_lands_inside_the_desktop(self):
        dialog = CreateImageDialog(serial="")
        try:
            dialog.show()
            self._app.processEvents()
            available = dialog.screen().availableGeometry()
            self.assertLessEqual(dialog.y() + dialog.height() - 1, available.bottom())
            self.assertLessEqual(dialog.x() + dialog.width() - 1, available.right())
            self.assertGreaterEqual(dialog.y(), available.top())
        finally:
            dialog.close()

    def test_a_dialog_placed_past_the_bottom_edge_is_pulled_back(self):
        """Qt centres a dialog on its parent, which on a short desktop can land
        the Create Image button below the screen edge."""
        dialog = CreateImageDialog(serial="")
        try:
            desktop = QRect(0, 0, 1280, 720)
            dialog.resize(800, 680)
            dialog.move(270, 105)  # where parent-centred placement lands
            dialog._keep_on_screen(desktop)
            self.assertEqual(dialog.y(), 40)
            self.assertLessEqual(dialog.y() + dialog.height() - 1, desktop.bottom())
        finally:
            dialog.close()

    def test_a_dialog_that_is_already_inside_the_desktop_is_not_moved(self):
        dialog = CreateImageDialog(serial="")
        try:
            desktop = QRect(0, 0, 1280, 720)
            dialog.resize(800, 500)
            dialog.move(100, 100)
            dialog._keep_on_screen(desktop)
            self.assertEqual((dialog.x(), dialog.y()), (100, 100))
        finally:
            dialog.close()

    def test_the_title_bar_is_part_of_what_has_to_fit(self):
        """The frame, not the client area, is what the window manager places.

        Measured from the running app on a 720 px desktop: the dialog's client
        area was 720x680 at y=70, while its frame was 710 px tall starting at
        y=40. Clamping the client rectangle computed 40 and declared success
        while the frame ran 30 px off the bottom of the screen.
        """
        available = QRect(0, 0, 1280, 720)
        frame = QRect(270, 40, 720, 710)
        self.assertEqual(clamp_to_screen(frame, available), QPoint(270, 10))

        # What the client-only clamp used to compute for the same window.
        client_only = min(max(40, available.top()), available.bottom() - 680 + 1)
        self.assertEqual(client_only, 40)

    def test_a_frame_that_already_fits_is_left_where_it_is(self):
        available = QRect(0, 0, 1280, 720)
        self.assertEqual(clamp_to_screen(QRect(100, 100, 800, 500), available),
                         QPoint(100, 100))

    def test_a_frame_taller_than_the_desktop_pins_to_the_top_edge(self):
        available = QRect(0, 0, 1280, 720)
        self.assertEqual(clamp_to_screen(QRect(0, 300, 800, 900), available).y(),
                         available.top())

    def test_revealing_the_console_keeps_the_buttons_on_screen(self):
        dialog = CreateImageDialog(serial="")
        try:
            dialog.show()
            self._app.processEvents()
            dialog.progress_group.setVisible(True)
            dialog._body.adjustSize()
            dialog._fit_to_screen()
            self._app.processEvents()

            available = dialog.screen().availableGeometry()
            self.assertLessEqual(dialog.y() + dialog.height() - 1, available.bottom())
            self.assertTrue(dialog.btn_start.isVisibleTo(dialog))
            self.assertGreater(dialog.btn_start.y(), 0)
            self.assertLessEqual(dialog.btn_start.y() + dialog.btn_start.height(),
                                 dialog.height())
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
