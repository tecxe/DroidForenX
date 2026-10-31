"""Case Management: create, edit and delete, plus the cleanup that follows.

Case Management used to be append-only. A mistyped case number was permanent,
and a case created against the wrong handset could not be removed at all. These
tests pin the behaviour that makes correction possible without letting a delete
take unrelated evidence with it.
"""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop.core import cases, database, evidence_store

NOW = "2026-10-23T00:00:00+00:00"


class CaseStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-cases-"))
        self._restore_db = database.DB_PATH
        database.DB_PATH = self.tmp / "droidforenx.db"
        self._restore_root = evidence_store.EVIDENCE_ROOT
        evidence_store.EVIDENCE_ROOT = self.tmp / "evidence"
        evidence_store.EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self._restore_db
        evidence_store.EVIDENCE_ROOT = self._restore_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------

    def _case(self, number="CASE-2026-0001", title="Handset seizure"):
        return cases.create_case(number, title, investigator="A. Mensah",
                                 agency="Cyber Unit", description="notes")

    def _session(self, case_id, session_id):
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO forensic_sessions (id, case_id, status, current_stage,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (session_id, case_id, "in_progress", 1, NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    def _fill_session(self, session_id):
        """One row in every table that hangs off a session."""
        conn = database.get_db()
        try:
            conn.execute(
                "INSERT INTO isolation_actions (id, session_id, action_type,"
                " performed_at) VALUES (?,?,?,?)",
                (f"iso-{session_id}", session_id, "airplane", NOW))
            conn.execute(
                "INSERT INTO network_state (id, session_id, captured_at)"
                " VALUES (?,?,?)", (f"net-{session_id}", session_id, NOW))
            conn.execute(
                "INSERT INTO device_identifications (id, session_id, identified_at)"
                " VALUES (?,?,?)", (f"dev-{session_id}", session_id, NOW))
            conn.execute(
                "INSERT INTO acquisition_profiles (id, session_id)"
                " VALUES (?,?)", (f"acq-{session_id}", session_id))
            conn.execute(
                "INSERT INTO examination_results (id, session_id, analysis_type)"
                " VALUES (?,?,?)", (f"exam-{session_id}", session_id, "filesystem"))
            conn.execute(
                "INSERT INTO forensic_reports (id, session_id, created_at)"
                " VALUES (?,?,?)", (f"rep-{session_id}", session_id, NOW))
            conn.execute(
                "INSERT INTO sim_cards (id, session_id, extracted_at)"
                " VALUES (?,?,?)", (f"sim-{session_id}", session_id, NOW))
            conn.execute(
                "INSERT INTO sim_contacts (id, sim_id, name)"
                " VALUES (?,?,?)", (f"con-{session_id}", f"sim-{session_id}", "Doe"))
            conn.execute(
                "INSERT INTO sim_messages (id, sim_id, body)"
                " VALUES (?,?,?)", (f"msg-{session_id}", f"sim-{session_id}", "hi"))
            conn.execute(
                "INSERT INTO disk_images (id, session_id, created_at)"
                " VALUES (?,?,?)", (f"img-{session_id}", session_id, NOW))
            conn.execute(
                "INSERT INTO evidence_artifacts (id, session_id, artifact_id, name,"
                " created_at) VALUES (?,?,?,?,?)",
                (f"art-{session_id}", session_id, f"a-{session_id}", "photo.jpg", NOW))
            conn.commit()
        finally:
            conn.close()

    def _count(self, table):
        conn = database.get_db()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def _total_rows(self):
        tables = ("cases", "forensic_sessions", "sim_contacts", "sim_messages") \
            + cases.SESSION_TABLES
        return {table: self._count(table) for table in tables}

    # -- create -----------------------------------------------------------

    def test_create_then_read_back(self):
        case_id = self._case()
        stored = cases.get_case(case_id)
        self.assertEqual(stored["case_number"], "CASE-2026-0001")
        self.assertEqual(stored["status"], "open")
        self.assertEqual(stored["investigator"], "A. Mensah")
        self.assertEqual([c["id"] for c in cases.list_cases()], [case_id])

    def test_create_strips_whitespace(self):
        case_id = cases.create_case("  CASE-9  ", "  Title  ")
        stored = cases.get_case(case_id)
        self.assertEqual(stored["case_number"], "CASE-9")
        self.assertEqual(stored["title"], "Title")

    def test_case_number_is_unique_at_the_database_and_the_check(self):
        self._case("CASE-1")
        self.assertTrue(cases.case_number_taken("CASE-1"))
        self.assertTrue(cases.case_number_taken("  CASE-1  "))
        with self.assertRaises(sqlite3.IntegrityError):
            self._case("CASE-1")

    def test_case_number_check_ignores_the_case_being_edited(self):
        case_id = self._case("CASE-1")
        self.assertFalse(cases.case_number_taken("CASE-1", exclude_id=case_id))
        self._case("CASE-2")
        self.assertTrue(cases.case_number_taken("CASE-2", exclude_id=case_id))

    # -- edit -------------------------------------------------------------

    def test_edit_changes_fields_and_bumps_updated_at(self):
        case_id = self._case()
        before = cases.get_case(case_id)
        changed = cases.update_case(
            case_id, case_number="CASE-2026-0099", title="Corrected title",
            investigator="B. Owusu", status="closed")
        self.assertTrue(changed)
        after = cases.get_case(case_id)
        self.assertEqual(after["case_number"], "CASE-2026-0099")
        self.assertEqual(after["title"], "Corrected title")
        self.assertEqual(after["status"], "closed")
        self.assertGreaterEqual(after["updated_at"], before["updated_at"])

    def test_edit_ignores_fields_it_does_not_own(self):
        case_id = self._case()
        original = cases.get_case(case_id)
        self.assertFalse(cases.update_case(case_id, id="hijacked",
                                           created_at=NOW))
        self.assertFalse(cases.update_case(case_id, nonsense="x"))
        stored = cases.get_case(case_id)
        self.assertEqual(stored["id"], case_id)
        self.assertEqual(stored["created_at"], original["created_at"])

    def test_edit_returns_false_for_missing_case(self):
        self.assertFalse(cases.update_case("does-not-exist", title="x"))

    def test_edited_case_number_still_has_to_be_unique(self):
        first = self._case("CASE-1")
        self._case("CASE-2")
        with self.assertRaises(sqlite3.IntegrityError):
            cases.update_case(first, case_number="CASE-2")
        self.assertEqual(cases.get_case(first)["case_number"], "CASE-1")

    # -- delete -----------------------------------------------------------

    def test_delete_removes_case_sessions_and_every_dependent_row(self):
        case_id = self._case()
        self._session(case_id, "session-1")
        self._session(case_id, "session-2")
        self._fill_session("session-1")
        self._fill_session("session-2")

        summary = cases.delete_case(case_id)

        self.assertEqual(summary["sessions"], 2)
        self.assertIsNone(cases.get_case(case_id))
        for table, count in self._total_rows().items():
            self.assertEqual(count, 0, f"{table} still has rows")

    def test_delete_leaves_other_cases_untouched(self):
        keep = self._case("CASE-KEEP")
        doomed = self._case("CASE-DOOMED")
        self._session(keep, "keep-session")
        self._session(doomed, "doomed-session")
        self._fill_session("keep-session")
        self._fill_session("doomed-session")

        cases.delete_case(doomed)

        self.assertIsNotNone(cases.get_case(keep))
        counts = self._total_rows()
        self.assertEqual(counts["forensic_sessions"], 1)
        self.assertEqual(counts["sim_contacts"], 1)
        self.assertEqual(counts["evidence_artifacts"], 1)
        self.assertEqual(counts["cases"], 1)

    def test_deleted_case_number_can_be_reused(self):
        case_id = self._case("CASE-TYPO")
        cases.delete_case(case_id)
        self.assertFalse(cases.case_number_taken("CASE-TYPO"))
        self.assertEqual(len(cases.list_cases()), 0)
        cases.create_case("CASE-TYPO", "Second attempt")
        self.assertEqual(len(cases.list_cases()), 1)

    def test_delete_is_audited(self):
        case_id = self._case()
        cases.delete_case(case_id)
        conn = database.get_db()
        try:
            actions = [row[0] for row in conn.execute(
                "SELECT action FROM audit_log ORDER BY id")]
        finally:
            conn.close()
        self.assertIn("case_deleted", actions)

    # -- evidence files ---------------------------------------------------

    def test_evidence_is_kept_by_default(self):
        case_id = self._case()
        case_dir = evidence_store.case_dir(case_id)
        (case_dir / "acquired").mkdir(parents=True)
        (case_dir / "acquired" / "dump.bin").write_bytes(b"evidence")

        summary = cases.delete_case(case_id)

        self.assertFalse(summary["removed_files"])
        self.assertTrue((case_dir / "acquired" / "dump.bin").is_file())

    def test_evidence_is_removed_when_asked(self):
        case_id = self._case()
        case_dir = evidence_store.case_dir(case_id)
        (case_dir / "acquired").mkdir(parents=True)
        (case_dir / "acquired" / "dump.bin").write_bytes(b"evidence")

        summary = cases.delete_case(case_id, delete_evidence=True)

        self.assertTrue(summary["removed_files"])
        self.assertFalse(case_dir.exists())
        # The evidence root itself survives; only the case folder goes.
        self.assertTrue(evidence_store.EVIDENCE_ROOT.is_dir())

    def test_missing_evidence_folder_is_not_an_error(self):
        case_id = self._case()
        summary = cases.delete_case(case_id, delete_evidence=True)
        self.assertFalse(summary["removed_files"])
        self.assertEqual(summary["evidence_dir"],
                         str(evidence_store.case_dir(case_id)))

    def test_evidence_deletion_refuses_paths_outside_the_root(self):
        case_id = self._case()
        outside = self.tmp / "elsewhere"
        outside.mkdir(parents=True)
        (outside / "keep.txt").write_bytes(b"not forensic evidence")

        with mock.patch.object(evidence_store, "case_dir", return_value=outside):
            summary = cases.delete_case(case_id, delete_evidence=True)

        self.assertFalse(summary["removed_files"])
        self.assertTrue((outside / "keep.txt").is_file())


if __name__ == "__main__":
    unittest.main()
