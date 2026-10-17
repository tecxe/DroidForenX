"""
Headless Qt tests for the report exports and the browse-page pull worker.

These need a Qt application but no display and no device: the platform is forced
to ``offscreen`` and the whole module is skipped if PyQt6 cannot start one.

They pin the report page's four formats, because three of them are easy to break
silently:
  * ``HTML`` is the default and must render the custody log
  * ``PDF`` uses FPDF's Latin-1 core font, so typographic characters must be
    folded rather than raising
  * ``Verification Workbook (XLSX)`` is derived from the evidence store, not the
    app database, and must be a readable workbook

``PullFileWorker`` is checked for its success and failure contracts.

Run with:  python -m unittest desktop.tests.test_ui_export -v
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    _QT_IMPORT = True
except Exception:  # pragma: no cover - depends on the environment
    _QT_IMPORT = False

from desktop.core import database, evidence_store
from desktop.ui.pages import report_page as report_page_module


def _make_qapp():
    if not _QT_IMPORT:
        return None
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class ReportFormatsTest(unittest.TestCase):
    """Generate each report format once and check the artifact is real."""

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-export-"))
        self._db_path = database.DB_PATH
        self._evidence_root = evidence_store.EVIDENCE_ROOT
        self._report_root = report_page_module.REPORT_ROOT

        database.DB_PATH = self.tmp / "dfx.db"
        evidence_store.EVIDENCE_ROOT = self.tmp / "evidence"
        report_page_module.REPORT_ROOT = self.tmp / "reports"
        database.init_db()

        self.case = "CASE-UI-2026-001"
        evidence_store.open_case(self.case, operator="examiner")
        artifact = evidence_store.acquired_dir(self.case) / "sms.json"
        artifact.write_bytes(b'{"sms": 2}')
        evidence_store.record_acquired(self.case, "content://sms", artifact, loader="sms")

        now = "2026-10-17T00:00:00+00:00"
        conn = database.get_db()
        conn.execute("INSERT INTO forensic_sessions (id, status, current_stage, operator, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     ("sess-1", "in_progress", 5, "examiner", now, now))
        conn.execute("INSERT INTO audit_log (timestamp, action, actor, target, details) "
                     "VALUES (?,?,?,?,?)",
                     (now, "DEVICE_SEIZED", "Ofc. Mensah", "sess-1", "device seized"))
        conn.commit()
        conn.close()

        self.page = report_page_module.ReportPage(db_get=database.get_db)
        self.page.set_session("sess-1", self.case)
        self.page.case_input.setText(self.case)
        self.page.examiner_input.setText("Jane Smith")
        self.page.badge_input.setText("D-9")
        self.page.org_input.setText("DFU")

    def tearDown(self):
        database.DB_PATH = self._db_path
        evidence_store.EVIDENCE_ROOT = self._evidence_root
        report_page_module.REPORT_ROOT = self._report_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _generate(self, fmt_index):
        self.page.format_combo.setCurrentIndex(fmt_index)
        with mock.patch.object(report_page_module.QMessageBox, "information", return_value=None):
            self.page._generate_report()
        conn = database.get_db()
        row = conn.execute("SELECT format, file_path FROM forensic_reports "
                           "WHERE case_number=? ORDER BY created_at DESC LIMIT 1",
                           (self.case,)).fetchone()
        conn.close()
        return row["format"], Path(row["file_path"])

    def test_combo_offers_four_formats(self):
        labels = [self.page.format_combo.itemText(i)
                  for i in range(self.page.format_combo.count())]
        self.assertEqual(len(labels), 4)
        self.assertTrue(any("XLSX" in label for label in labels))

    def test_html_is_the_default_and_renders_the_custody_log(self):
        self.assertEqual(self.page.format_combo.currentIndex(), 0)
        fmt, path = self._generate(0)
        self.assertEqual(fmt, "html")
        html = path.read_text(encoding="utf-8")
        self.assertIn("Chain of Custody", html)
        self.assertIn("DEVICE_SEIZED", html)

    def test_pdf_is_generated_with_safe_text(self):
        fmt, path = self._generate(1)
        self.assertEqual(fmt, "pdf")
        data = path.read_bytes()
        self.assertEqual(data[:5], b"%PDF-")
        self.assertGreater(len(data), 2000)

    def test_json_is_generated(self):
        fmt, path = self._generate(2)
        self.assertEqual(fmt, "json")
        self.assertTrue(path.exists())

    def test_workbook_is_generated_and_readable(self):
        fmt, path = self._generate(3)
        self.assertEqual(fmt, "xlsx")
        self.assertEqual(path.suffix, ".xlsx")
        self.assertTrue(path.with_suffix(".xlsx.sha256").exists())

        from openpyxl import load_workbook
        wb = load_workbook(path)
        self.assertIn("Integrity", wb.sheetnames)
        # The case has one acquired artifact; it must re-hash OK.
        row = list(wb["Integrity"].iter_rows(min_row=2, max_row=2, values_only=True))[0]
        self.assertEqual(row[3], "OK")
        self.assertEqual(row[1], row[2])


@unittest.skipUnless(_QT_IMPORT, "PyQt6 is not available")
class PullFileWorkerTest(unittest.TestCase):
    """The browse-page pull worker's success and failure contracts."""

    @classmethod
    def setUpClass(cls):
        cls._app = _make_qapp()

    def setUp(self):
        from desktop.ui.pages.browse_page import PullFileWorker
        self.PullFileWorker = PullFileWorker
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-pullw-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, serial, remote):
        worker = self.PullFileWorker(serial, remote)
        captured = {}
        worker.finished.connect(captured.update)
        worker.run()  # synchronous, same thread: no event loop needed
        return captured

    def test_no_serial_reports_no_device(self):
        result = self._run("", "/sdcard/photo.jpg")
        self.assertFalse(result["success"])
        self.assertIn("No device", result["error"])

    def test_directory_path_is_rejected(self):
        result = self._run("SERIAL", "/")
        self.assertFalse(result["success"])
        self.assertIn("Not a file", result["error"])

    def test_successful_pull_reports_local_path(self):
        def fake_adb_cmd(args, timeout=120):
            Path(args[-1]).write_bytes(b"payload")
            return True, ""

        with mock.patch("desktop.core.adb_manager.adb_cmd", fake_adb_cmd), \
             mock.patch("desktop.ui.pages.browse_page.os.path.expanduser",
                        return_value=str(self.tmp)):
            result = self._run("SERIAL", "/sdcard/dcim/photo.jpg")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["filename"], "photo.jpg")
        self.assertEqual(result["extension"], ".jpg")
        self.assertTrue(Path(result["local_path"]).exists())
        self.assertTrue(str(self.tmp) in result["local_path"],
                        "pull should land under the (patched) cache dir")

    def test_failed_pull_reports_error(self):
        with mock.patch("desktop.core.adb_manager.adb_cmd", return_value=(False, "")), \
             mock.patch("desktop.ui.pages.browse_page.os.path.expanduser",
                        return_value=str(self.tmp)):
            result = self._run("SERIAL", "/sdcard/dcim/photo.jpg")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "ADB pull failed")


if __name__ == "__main__":
    unittest.main()
