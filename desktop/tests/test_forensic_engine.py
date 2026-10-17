"""
Core forensic-engine tests.

These run without a device. They pin the two properties that decide whether an
acquisition can be trusted:

  1. ``_unique_name()`` never hands back a name that already exists in the
     target directory. Two different remote paths often share a basename, and
     the same remote path can be pulled more than once; either way the earlier
     artifact must not be overwritten.
  2. ``_safe_pull_db()`` records what actually happened: a successful pull lands
     in ``acquired/`` and in the manifest, while an unreadable or zero-length
     pull is recorded as NOT_ACCESSIBLE / NOT_PRESENT and leaves no artifact
     behind.

Run with:  python -m unittest desktop.tests.test_forensic_engine -v
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop.core import evidence_store, forensic_engine
from desktop.core.evidence_store import AcquisitionStatus


class UniqueNameTest(unittest.TestCase):
    """Pure name-collision behaviour; no case tree needed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-name-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_pull_keeps_original_name(self):
        self.assertEqual(
            forensic_engine._unique_name(self.tmp, "photo.jpg", "/sdcard/a/photo.jpg"),
            "photo.jpg")

    def test_same_basename_different_remote_paths(self):
        (self.tmp / "photo.jpg").write_bytes(b"first")
        second = forensic_engine._unique_name(self.tmp, "photo.jpg", "/sdcard/b/photo.jpg")
        self.assertNotEqual(second, "photo.jpg")
        self.assertTrue(second.startswith("photo_") and second.endswith(".jpg"))

    def test_repeated_pull_never_reuses_a_name(self):
        remote = "/sdcard/dcim/photo.jpg"
        seen = set()
        for _ in range(5):
            name = forensic_engine._unique_name(self.tmp, "photo.jpg", remote)
            self.assertNotIn(name, seen, "a name was handed out twice")
            seen.add(name)
            (self.tmp / name).write_bytes(b"x")
        self.assertEqual(len(seen), 5)

    def test_suffix_is_preserved(self):
        (self.tmp / "wa.db").write_bytes(b"a")
        name = forensic_engine._unique_name(self.tmp, "wa.db", "/data/data/x/wa.db")
        self.assertTrue(name.endswith(".db"), name)


class SafePullDbTest(unittest.TestCase):
    """``_safe_pull_db`` against a patched ADB, so no device is required."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-pull-"))
        self.evidence_root = self.tmp / "evidence"
        self.evidence_root.mkdir()
        self._original_root = evidence_store.EVIDENCE_ROOT
        evidence_store.EVIDENCE_ROOT = self.evidence_root
        self.case_id = "CASE-PULL-001"
        evidence_store.open_case(self.case_id, operator="test")
        evidence_store.set_active_case(self.case_id)

    def tearDown(self):
        evidence_store.EVIDENCE_ROOT = self._original_root
        evidence_store.set_active_case(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_pull(self, payload):
        """Return a ``pull_file`` stub that writes ``payload`` to the target."""
        def _pull(serial, remote_path, local_path):
            if payload is None:
                return False
            Path(local_path).write_bytes(payload)
            return True
        return _pull

    def _statuses(self):
        return evidence_store.read_status(self.case_id)

    def test_successful_pull_is_recorded(self):
        with mock.patch.object(forensic_engine.adb, "pull_file", self._fake_pull(b"SQLite\x00")):
            path = forensic_engine._safe_pull_db("SERIAL", "/data/db/wa.db", "wa_1234.db")

        self.assertIsNotNone(path)
        pulled = Path(path)
        self.assertTrue(pulled.exists())
        self.assertEqual(pulled.read_bytes(), b"SQLite\x00")

        sums = (evidence_store.manifest_dir(self.case_id) / "SHA256SUMS").read_text()
        self.assertIn(evidence_store.hash_file(pulled), sums)
        self.assertIn("wa_1234.db", sums)

    def test_unreadable_pull_is_not_an_artifact(self):
        with mock.patch.object(forensic_engine.adb, "pull_file", self._fake_pull(None)):
            path = forensic_engine._safe_pull_db("SERIAL", "/data/db/wa.db", "wa_none.db")

        self.assertIsNone(path)
        self.assertFalse((evidence_store.acquired_dir(self.case_id) / "wa_none.db").exists())
        statuses = {s["loader"]: s for s in self._statuses()}
        self.assertEqual(statuses["acquire"]["status"], AcquisitionStatus.NOT_ACCESSIBLE)

    def test_zero_length_pull_leaves_nothing_behind(self):
        with mock.patch.object(forensic_engine.adb, "pull_file", self._fake_pull(b"")):
            path = forensic_engine._safe_pull_db("SERIAL", "/data/db/empty.db", "empty.db")

        self.assertIsNone(path)
        self.assertFalse((evidence_store.acquired_dir(self.case_id) / "empty.db").exists())
        statuses = {s["loader"]: s for s in self._statuses()}
        self.assertEqual(statuses["acquire"]["status"], AcquisitionStatus.NOT_PRESENT)

    def test_colliding_basenames_both_survive(self):
        with mock.patch.object(forensic_engine.adb, "pull_file", self._fake_pull(b"first")):
            first = forensic_engine._safe_pull_db("SERIAL", "/sdcard/a/report.pdf", "report.pdf")
        with mock.patch.object(forensic_engine.adb, "pull_file", self._fake_pull(b"second")):
            second = forensic_engine._safe_pull_db("SERIAL", "/sdcard/b/report.pdf", "report.pdf")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), b"first")
        self.assertEqual(Path(second).read_bytes(), b"second",
                         "the second pull overwrote the first artifact")

        sums = (evidence_store.manifest_dir(self.case_id) / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 2, "both artifacts must be in the manifest")


if __name__ == "__main__":
    unittest.main()
