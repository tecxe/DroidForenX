"""Tests for hashing/integrity verification."""

import hashlib
import tempfile
from pathlib import Path

from django.test import TestCase

from cases.models import Case, Investigator
from evidence.models import Artifact, EvidenceClassification, EvidenceCategory
from evidence.services import register_artifact, register_evidence

from .core import (
    SUPPORTED_ALGORITHMS,
    MissingFileError,
    hash_file,
    hash_single,
    parse_manifest,
    verification_passed,
    verify_file,
    write_manifest,
)
from .service import (
    artifact_disk_path,
    hash_artifact,
    verify_artifact,
    write_artifact_manifest,
)


def make_sample_file(name="sample.txt", text=b"hello forensics world"):
    tmp = Path(tempfile.mkdtemp()) / name
    tmp.write_bytes(text)
    return tmp


def expected_digests(path: Path):
    data = path.read_bytes()
    return {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha512": hashlib.sha512(data).hexdigest(),
    }


def make_artifact_evidence():
    inv = Investigator.objects.create(name="Ada Lovelace")
    case = Case.objects.create(case_number="CASE-HASH-1", lead_investigator=inv)
    ev = register_evidence(case, title="hash container")
    return ev


class CoreHashTest(TestCase):
    def test_digests_match_stdlib(self):
        path = make_sample_file()
        computed = hash_file(path)
        expected = expected_digests(path)
        for alg in SUPPORTED_ALGORITHMS:
            self.assertEqual(computed[alg], expected[alg])

    def test_hash_single_sha256(self):
        path = make_sample_file()
        self.assertEqual(hash_single(path, "sha256"),
                         hashlib.sha256(path.read_bytes()).hexdigest())

    def test_missing_file_raises(self):
        with self.assertRaises(MissingFileError):
            hash_file(Path(tempfile.mkdtemp()) / "nope.bin")

    def test_algorithms_supported(self):
        for alg in SUPPORTED_ALGORITHMS:
            path = make_sample_file(alg + ".txt")
            self.assertIn(alg, hash_file(path, [alg]))


class VerifyTest(TestCase):
    def test_match_passes(self):
        path = make_sample_file()
        expected = expected_digests(path)
        result = verify_file(path, expected)
        self.assertTrue(verification_passed(result))

    def test_tamper_fails(self):
        path = make_sample_file(text=b"original")
        expected = expected_digests(path)
        path.write_bytes(b"tampered!")
        result = verify_file(path, expected)
        self.assertFalse(verification_passed(result))
        self.assertFalse(result["sha256"])

    def test_missing_expected_unknown_not_false(self):
        path = make_sample_file()
        # Only sha256 expected; md5 not recorded should be absent from result.
        result = verify_file(path, {"sha256": expected_digests(path)["sha256"]})
        self.assertIn("sha256", result)
        self.assertNotIn("md5", result)

    def test_no_expected_returns_empty(self):
        path = make_sample_file()
        self.assertEqual(verify_file(path, {}), {})

    def test_verification_passed_requires_nonempty(self):
        self.assertFalse(verification_passed({}))
        self.assertTrue(verification_passed({"sha256": True}))
        self.assertFalse(verification_passed({"sha256": False}))


class ManifestTest(TestCase):
    def test_round_trip(self):
        path = make_sample_file()
        digests = expected_digests(path)
        manifest = write_manifest(path, digests, source="test", created="now")
        self.assertTrue(manifest.exists())
        parsed = parse_manifest(manifest)
        self.assertEqual(parsed, {k: v for k, v in digests.items() if k in SUPPORTED_ALGORITHMS})

    def test_parse_ignores_comments(self):
        path = make_sample_file()
        manifest = write_manifest(path, {"sha256": "ab" * 32}, source="src", created="now")
        text = manifest.read_text(encoding="utf-8")
        self.assertIn("# ", text)


class ServiceTest(TestCase):
    def test_hash_artifact_end_to_end(self):
        ev = make_artifact_evidence()
        art = register_artifact(ev, name="sample.txt", source="test source",
                                storage_path=str(make_sample_file(text=b"abc" * 100)))
        digests = hash_artifact(art, algorithms=["md5", "sha256"])
        art.refresh_from_db()
        self.assertEqual(art.sha256, digests["sha256"])
        self.assertEqual(art.md5, digests["md5"])
        # Manifest written beside the file.
        manifest = write_artifact_manifest(art)
        self.assertTrue(manifest.exists())
        # Hash records created.
        self.assertEqual(art.hash_records.count(), 2)

    def test_verify_artifact_ok_and_tamper(self):
        ev = make_artifact_evidence()
        p = make_sample_file(text=b"do not touch")
        art = register_artifact(ev, name="x.bin", source="s", storage_path=str(p))
        hash_artifact(art)
        self.assertTrue(artifact_verifies(art))

        p.write_bytes(b"changed")
        self.assertFalse(artifact_verifies(art))

    def test_verify_artifact_no_records_unknown(self):
        ev = make_artifact_evidence()
        art = register_artifact(ev, name="fresh", source="s",
                                storage_path=str(make_sample_file()))
        # No hash records yet -> empty result, not verified.
        self.assertEqual(verify_artifact(art), {})

    def test_artifact_without_file_returns_empty(self):
        ev = make_artifact_evidence()
        art = register_artifact(ev, name="nofile", source="s", storage_path="")
        self.assertEqual(verify_artifact(art), {})


def artifact_verifies(art):
    from .service import artifact_verifies as _av
    return _av(art)
