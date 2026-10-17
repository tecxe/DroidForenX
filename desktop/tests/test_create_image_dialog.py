"""
Headless Qt tests for the *Create Image* dialog.

These need a Qt application but no display and no device: the platform is forced
to ``offscreen`` and the module is skipped if PyQt6 cannot start one.

They pin the parts of the dialog that are easy to get wrong and hard to notice:

  1. Source and format are gated against each other -- a device partition offers
     RAW only, a folder offers ZIP and AFF4 only.
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
    from PyQt6.QtWidgets import QApplication
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False

from desktop.core.imaging import create_image, sources
from desktop.ui.dialogs.create_image_dialog import CreateImageDialog


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

    def test_a_folder_offers_zip_and_aff4(self):
        self.assertEqual(self.dialog.source_kind, "folder")
        self.assertEqual(
            [self.dialog.format_combo.itemData(i)
             for i in range(self.dialog.format_combo.count())],
            ["zip", "aff4"])

    def test_switching_source_repopulates_the_format_list(self):
        self.use_device_tab([sources.Partition(name="boot_a", block="/dev/block/sde13", readable=True)])
        self.assertEqual(self.dialog.format_combo.count(), 1)

        self.dialog.source_tabs.setCurrentIndex(1)
        self.assertEqual(
            [self.dialog.format_combo.itemData(i)
             for i in range(self.dialog.format_combo.count())],
            ["zip", "aff4"])

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


if __name__ == "__main__":
    unittest.main()
