"""
Image registration tests.

A written image is only half the job -- it also has to be recorded, or the case
manifest lies about what exists. These tests pin that:

  1. ``ensure_image_schema`` adds the new ``disk_images`` columns and is safe to
     run repeatedly against an existing database.
  2. A finished image gets a ``disk_images`` row with the real format, digest,
     segment list and verification outcome.
  3. Verification that failed, or was skipped, is recorded as such -- a failed
     verification must not be written down as ``complete``.
  4. Each segment is registered in the case manifest and custody log.

Everything runs against a temporary database and evidence root; no device and no
real case tree are touched.

Run with:  python -m unittest desktop.tests.test_image_registry -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from desktop.core import database, evidence_store
from desktop.core.imaging import ImageRequest, RawImageWriter, registry
from desktop.tests.test_imaging import MIB, Reader


class ImageRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-reg-"))
        self._db_path = database.DB_PATH
        self._root = evidence_store.EVIDENCE_ROOT
        database.DB_PATH = self.tmp / "droidforenx.db"
        evidence_store.EVIDENCE_ROOT = self.tmp / "evidence"
        database.init_db()
        self.dest = self.tmp / "out"

    def tearDown(self):
        database.DB_PATH = self._db_path
        evidence_store.EVIDENCE_ROOT = self._root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, **overrides) -> ImageRequest:
        options = dict(
            dest_dir=self.dest, base_name="evidence", image_format="raw",
            segment_size=MIB, source_label="/dev/block/by-name/boot_a")
        options.update(overrides)
        return ImageRequest(**options)

    def test_schema_migration_is_idempotent(self):
        conn = database.get_db()
        try:
            self.assertEqual(registry.ensure_image_schema(conn), list(registry.IMAGE_COLUMNS))
            self.assertEqual(registry.ensure_image_schema(conn), [])
        finally:
            conn.close()

    def test_records_a_verified_image(self):
        request = self.request()
        result = RawImageWriter().write(Reader(b"z" * (2 * MIB)), request)

        info = registry.record_image(result, request, "CASE-IMG-1", actor="Tester")

        self.assertEqual(info["verification_status"], "verified")
        self.assertEqual(info["status"], "complete")
        self.assertEqual(len(info["artifacts"]), 2)

        rows = registry.read_images("CASE-IMG-1")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["image_format"], "raw")
        self.assertEqual(row["hash_algorithm"], "sha256")
        self.assertEqual(row["hash_value"], result.digest)
        self.assertEqual(row["size_bytes"], 2 * MIB)
        self.assertEqual(row["segment_size"], MIB)
        self.assertEqual(row["verified"], 1)
        self.assertEqual(row["source_type"], "device")
        self.assertEqual(row["source_path"], "/dev/block/by-name/boot_a")

        segments = json.loads(row["segments"])
        self.assertEqual([s["path"] for s in segments], ["evidence.001", "evidence.002"])
        self.assertEqual(segments[0]["sha256"], result.segment_digests[0][1])

    def test_segments_land_in_the_manifest_and_custody_log(self):
        request = self.request()
        result = RawImageWriter().write(Reader(b"z" * (2 * MIB)), request)
        registry.record_image(result, request, "CASE-IMG-1", actor="Tester")

        sums = (evidence_store.manifest_dir("CASE-IMG-1") / "SHA256SUMS").read_text(encoding="utf-8")
        self.assertIn("evidence.001", sums)
        self.assertIn("evidence.002", sums)

        actions = [event["action"] for event in evidence_store.read_custody("CASE-IMG-1")]
        self.assertIn("ACQUIRED", actions)
        self.assertIn("IMAGE_CREATED", actions)

    def test_a_session_is_created_when_stage_one_was_skipped(self):
        request = self.request()
        result = RawImageWriter().write(Reader(b"z" * MIB), request)
        info = registry.record_image(result, request, "CASE-IMG-1")

        conn = database.get_db()
        try:
            session = conn.execute(
                "SELECT * FROM forensic_sessions WHERE id = ?", (info["session_id"],)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "imaging")

    def test_an_existing_session_is_reused(self):
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO forensic_sessions (id, case_id, status, current_stage, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                ("SES-1", "CASE-IMG-3", "seizure_complete", 1, "now", "now"))
            conn.commit()
        finally:
            conn.close()

        request = self.request(image_format="raw")
        result = RawImageWriter().write(Reader(b"z" * MIB), request)
        info = registry.record_image(result, request, "CASE-IMG-3", session_id="SES-1")

        self.assertEqual(info["session_id"], "SES-1")
        conn = database.get_db()
        try:
            count = conn.execute("SELECT COUNT(*) AS n FROM forensic_sessions").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(count, 1)

    def test_failed_verification_is_not_recorded_as_complete(self):
        request = self.request(verify=False)
        result = RawImageWriter().write(Reader(b"z" * MIB), request)

        request.verify = True
        result.verified = False
        info = registry.record_image(result, request, "CASE-IMG-4")

        self.assertEqual(info["verification_status"], "failed")
        self.assertEqual(info["status"], "failed")
        self.assertEqual(registry.read_images("CASE-IMG-4")[0]["verified"], 0)

    def test_skipped_verification_is_recorded_as_skipped(self):
        request = self.request(verify=False)
        result = RawImageWriter().write(Reader(b"z" * MIB), request)
        info = registry.record_image(result, request, "CASE-IMG-5")

        self.assertEqual(info["verification_status"], "skipped")
        self.assertEqual(info["status"], "complete")

    def test_folder_sources_are_labelled_as_such(self):
        request = self.request(source_label=str(self.tmp / "case"))
        result = RawImageWriter().write(Reader(b"z" * MIB), request)
        info = registry.record_image(result, request, "CASE-IMG-6")
        self.assertEqual(registry.read_images("CASE-IMG-6")[0]["source_type"], "folder")
        self.assertEqual(info["status"], "complete")


if __name__ == "__main__":
    unittest.main()
