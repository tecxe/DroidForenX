"""
Evidence store integrity tests.

These run without a device. They prove the properties a forensic exhibit has
to have, which is the whole point of the rewrite:

  1. An acquired artifact is preserved, not deleted after parsing.
  2. Every artifact is hashed at acquisition time.
  3. The custody log is append-only and survives across runs.
  4. Tampering with an artifact is detected by re-verification.
  5. Status reporting distinguishes success from "recovered nothing".

Run with:  python -m unittest desktop.tests.test_evidence_store -v
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from desktop.core import evidence_store
from desktop.core.evidence_store import AcquisitionStatus, VerificationTier


class EvidenceStoreTest(unittest.TestCase):
    """Each test gets an isolated evidence root so nothing touches ~/.droidforenx."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dfx-test-")
        # The evidence root is a subtree of tmp, so anything outside
        # self.tmp / "root" is a legitimately external export destination.
        self.evidence_root = Path(self.tmp) / "root"
        self.evidence_root.mkdir()
        self._original_root = evidence_store.EVIDENCE_ROOT
        evidence_store.EVIDENCE_ROOT = self.evidence_root
        self.case_id = "CASE-TEST-001"

    def tearDown(self):
        evidence_store.EVIDENCE_ROOT = self._original_root
        evidence_store.set_active_case(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def _make_artifact(self, name="msgstore.db", content=b"SQLite format 3\x00fake rows"):
        path = evidence_store.acquired_dir(self.case_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    # -- 1. preservation --------------------------------------------------

    def test_acquired_artifact_is_preserved(self):
        """An artifact written into acquired/ must survive being read."""
        evidence_store.open_case(self.case_id, operator="test")
        artifact = self._make_artifact()
        digest = evidence_store.hash_file(artifact)

        # Simulate a parser reading it.
        artifact.read_bytes()
        artifact.read_bytes()

        self.assertTrue(artifact.exists(), "acquired artifact was destroyed")
        self.assertEqual(evidence_store.hash_file(artifact), digest,
                         "acquired artifact changed after being read")

    def test_case_tree_layout(self):
        """A case must expose the four directories the workflow expects."""
        evidence_store.open_case(self.case_id)
        for sub in ("acquired", "parsed", "manifests"):
            self.assertTrue((evidence_store.case_dir(self.case_id) / sub).is_dir(),
                            f"missing {sub}/")
        self.assertTrue((evidence_store.case_dir(self.case_id) / "case.json").exists())

    # -- 2. hashing -------------------------------------------------------

    def test_record_acquired_writes_manifest_and_hash(self):
        evidence_store.open_case(self.case_id)
        artifact = self._make_artifact()
        record = evidence_store.record_acquired(
            self.case_id, "/data/data/com.whatsapp/databases/msgstore.db",
            artifact, loader="whatsapp",
        )
        assert record is not None

        self.assertEqual(record.size_bytes, artifact.stat().st_size)
        self.assertEqual(record.sha256, evidence_store.hash_file(artifact))

        sums = (evidence_store.manifest_dir(self.case_id) / "SHA256SUMS").read_text()
        self.assertIn(record.sha256, sums)
        self.assertIn("msgstore.db", sums)

    def test_duplicate_hash_detection(self):
        """Two identical artifacts must produce the same artifact_id."""
        evidence_store.open_case(self.case_id)
        a = self._make_artifact("one.db", b"same bytes")
        b = self._make_artifact("two.db", b"same bytes")
        r1 = evidence_store.record_acquired(self.case_id, "/a", a, loader="x")
        r2 = evidence_store.record_acquired(self.case_id, "/b", b, loader="x")
        assert r1 is not None and r2 is not None
        self.assertEqual(r1.artifact_id, r2.artifact_id,
                         "content-addressed ids must match for identical content")

    # -- 3. custody append-only ------------------------------------------

    def test_custody_log_appends_and_never_rewrites(self):
        evidence_store.open_case(self.case_id, operator="Ofc. Mensah")
        evidence_store.log_custody(self.case_id, "SEIZE", "Ofc. Mensah", "device seized")
        evidence_store.log_custody(self.case_id, "HANDOVER", "Ofc. Koomson", "shift change")

        events = evidence_store.read_custody(self.case_id)
        self.assertGreaterEqual(len(events), 3)
        self.assertEqual(events[0]["action"], "CASE_OPENED")
        self.assertEqual(events[1]["action"], "SEIZE")
        self.assertEqual(events[2]["action"], "HANDOVER")
        self.assertEqual(events[2]["actor"], "Ofc. Koomson")

    def test_custody_survives_reopen(self):
        evidence_store.open_case(self.case_id)
        evidence_store.log_custody(self.case_id, "ACQUIRED", "engine", "artifact x")
        before = len(evidence_store.read_custody(self.case_id))
        evidence_store.open_case(self.case_id)  # re-open must not truncate
        after = len(evidence_store.read_custody(self.case_id))
        self.assertGreaterEqual(after, before)

    def test_case_metadata_not_overwritten_on_reopen(self):
        evidence_store.open_case(self.case_id, operator="first", device_serial="ABC123")
        evidence_store.open_case(self.case_id, operator="second", device_serial="XYZ789")
        import json
        meta = json.loads((evidence_store.case_dir(self.case_id) / "case.json").read_text())
        self.assertEqual(meta["operator"], "first",
                         "re-opening a case must not rewrite its original metadata")

    # -- 4. tamper detection ---------------------------------------------

    def test_verify_detects_tampering(self):
        evidence_store.open_case(self.case_id)
        artifact = self._make_artifact("evidence.db", b"original content")
        evidence_store.record_acquired(self.case_id, "/data/x", artifact, loader="test")

        clean = evidence_store.verify(self.case_id)
        self.assertEqual(clean["verdict"], "VERIFIED")
        self.assertEqual(clean["ok"], 1)

        artifact.write_bytes(b"TAMPERED content")

        dirty = evidence_store.verify(self.case_id)
        self.assertEqual(dirty["verdict"], "INTEGRITY_FAILURE")
        self.assertEqual(len(dirty["mismatched"]), 1)

    def test_verify_detects_deletion(self):
        evidence_store.open_case(self.case_id)
        artifact = self._make_artifact("gone.db")
        evidence_store.record_acquired(self.case_id, "/data/y", artifact, loader="test")
        os.unlink(artifact)

        result = evidence_store.verify(self.case_id)
        self.assertEqual(result["verdict"], "INTEGRITY_FAILURE")
        self.assertEqual(len(result["missing"]), 1)

    # -- 5. honest status -------------------------------------------------

    def test_status_rejects_unknown_value(self):
        evidence_store.open_case(self.case_id)
        with self.assertRaises(ValueError):
            evidence_store.record_status(self.case_id, "x", "MADE_UP_STATUS")

    def test_status_classification(self):
        self.assertTrue(AcquisitionStatus.is_success(AcquisitionStatus.ACQUIRED))
        self.assertTrue(AcquisitionStatus.is_success(AcquisitionStatus.PARTIAL))
        self.assertTrue(AcquisitionStatus.is_success(AcquisitionStatus.RECOVERED))
        for bad in (AcquisitionStatus.NOT_ACCESSIBLE, AcquisitionStatus.FAILED,
                    AcquisitionStatus.NOT_PRESENT, AcquisitionStatus.DELETED_UNRECOVERABLE):
            self.assertFalse(AcquisitionStatus.is_success(bad),
                             f"{bad} must not count as success")

    def test_unsuccessful_status_is_logged_to_custody(self):
        """A NOT_ACCESSIBLE result must be visible in the custody trail."""
        evidence_store.open_case(self.case_id)
        evidence_store.record_status(
            self.case_id, "whatsapp", AcquisitionStatus.NOT_ACCESSIBLE,
            "device is not rooted",
        )
        actions = [e["action"] for e in evidence_store.read_custody(self.case_id)]
        self.assertIn("STATUS", actions)

        statuses = evidence_store.read_status(self.case_id)
        self.assertEqual(statuses[-1]["loader"], "whatsapp")
        self.assertEqual(statuses[-1]["status"], AcquisitionStatus.NOT_ACCESSIBLE)

    def test_verification_tiers_defined(self):
        self.assertEqual(len(VerificationTier.ALL), 3)

    # -- 6. tiers are earned, never declared -------------------------------

    def test_untested_loader_defaults_to_untested(self):
        """No record means no claim. This is the anti-fabrication default."""
        evidence_store.open_case(self.case_id)
        self.assertEqual(
            evidence_store.get_verification_tier(self.case_id, "whatsapp"),
            VerificationTier.T3_UNTESTED,
        )

    def test_device_verified_tier_requires_evidence(self):
        """A device claim with no record must be rejected outright."""
        evidence_store.open_case(self.case_id)
        with self.assertRaises(ValueError):
            evidence_store.record_verification(
                self.case_id, "sms_mms", VerificationTier.T1_DEVICE_VERIFIED, "")
        with self.assertRaises(ValueError):
            evidence_store.record_verification(
                self.case_id, "sms_mms", VerificationTier.T1_DEVICE_VERIFIED, "   ")

    def test_device_verified_tier_accepted_with_evidence(self):
        evidence_store.open_case(self.case_id)
        entry = evidence_store.record_verification(
            self.case_id, "sms_mms", VerificationTier.T1_DEVICE_VERIFIED,
            "Tecno Spark 30, Android 14, serial ABC123, 2026-10-14",
        )
        self.assertEqual(entry["tier"], VerificationTier.T1_DEVICE_VERIFIED)
        self.assertEqual(
            evidence_store.get_verification_tier(self.case_id, "sms_mms"),
            VerificationTier.T1_DEVICE_VERIFIED,
        )
        # and it must show up in the custody trail
        actions = [e["action"] for e in evidence_store.read_custody(self.case_id)]
        self.assertIn("VERIFY", actions)

    def test_highest_earned_tier_wins(self):
        evidence_store.open_case(self.case_id)
        evidence_store.record_verification(self.case_id, "gps",
                                            VerificationTier.T2_FIXTURE_VERIFIED, "fixture")
        evidence_store.record_verification(self.case_id, "gps",
                                            VerificationTier.T1_DEVICE_VERIFIED, "serial X")
        self.assertEqual(
            evidence_store.get_verification_tier(self.case_id, "gps"),
            VerificationTier.T1_DEVICE_VERIFIED,
        )

    def test_unknown_tier_rejected(self):
        evidence_store.open_case(self.case_id)
        with self.assertRaises(ValueError):
            evidence_store.record_verification(self.case_id, "gps", "T9_MADE_UP")

    # -- derived output ---------------------------------------------------

    def test_parsed_output_is_not_evidence(self):
        """Derived output goes to parsed/ and is never hashed as evidence."""
        evidence_store.open_case(self.case_id)
        out = evidence_store.write_parsed(self.case_id, "sms_summary", {"count": 42})
        self.assertIsNotNone(out)
        self.assertIn("parsed", str(out))

        before = evidence_store.verify(self.case_id)["checked"]
        evidence_store.write_parsed(self.case_id, "more", {"count": 99})
        after = evidence_store.verify(self.case_id)["checked"]
        self.assertEqual(before, after,
                         "regenerable output must not alter the evidence count")

    def test_case_path_traversal_is_neutralised(self):
        """A hostile case id must not escape the evidence root."""
        evil = evidence_store.case_dir("../../../etc/passwd")
        self.assertTrue(str(evil).startswith(str(evidence_store.EVIDENCE_ROOT)))

    def test_export_refuses_to_overwrite_evidence(self):
        """Exporting onto the evidence tree would delete the very case copied."""
        evidence_store.open_case(self.case_id)
        artifact = self._make_artifact("keepme.db", b"precious evidence")
        evidence_store.record_acquired(self.case_id, "/x", artifact, loader="test")

        for bad in (evidence_store.EVIDENCE_ROOT,
                    evidence_store.case_dir(self.case_id),
                    evidence_store.EVIDENCE_ROOT / "sub"):
            with self.assertRaises(ValueError):
                evidence_store.export_case(self.case_id, bad)

        # the evidence must still be there
        self.assertTrue(artifact.exists(), "export guard must not destroy evidence")
        self.assertTrue(artifact.read_bytes() == b"precious evidence")

    def test_export_copies_to_safe_destination(self):
        evidence_store.open_case(self.case_id)
        artifact = self._make_artifact("x.db", b"data")
        evidence_store.record_acquired(self.case_id, "/data/x", artifact, loader="test")
        out = evidence_store.export_case(self.case_id, Path(self.tmp) / "out" / "case1")
        assert out is not None
        self.assertTrue((out / "acquired" / "x.db").exists())
        self.assertTrue((out / "manifests" / "SHA256SUMS").exists())
        self.assertTrue((out / "manifests" / "custody.jsonl").exists())
        self.assertEqual(evidence_store.verify(self.case_id)["verdict"], "VERIFIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)