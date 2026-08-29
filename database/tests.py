"""Tests for SQLite analysis (live schema + deleted-data assessment)."""

import sqlite3
import tempfile
from pathlib import Path

from django.test import TestCase

from cases.models import Case, Investigator
from evidence.services import register_artifact, register_evidence

from .analyzer import analyze
from .service import NoFileError, NotSqliteError, analyze_artifact_database
from .sqlite_parse import assess_database, DatabaseAssessment


def build_sample_db(path: Path, with_deleted: bool = True) -> Path:
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT, phone TEXT)")
    cur.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    cur.execute("INSERT INTO contacts (name, phone) VALUES ('Alice','111'),('Bob','222'),('Carol','333')")
    cur.execute("INSERT INTO messages (body) VALUES ('hello'),('world')")
    conn.commit()
    if with_deleted:
        # Deleting many rows leaves freeblocks / freelist remnants.
        cur.execute("DELETE FROM contacts WHERE name='Bob'")
        cur.execute("DELETE FROM messages WHERE body='world'")
        conn.commit()
        conn.execute("PRAGMA incremental_vacuum")  # may move freed pages to freelist
        conn.commit()
    conn.close()
    return path


def make_artifact(path: Path):
    inv = Investigator.objects.create(name="Ada")
    case = Case.objects.create(case_number="CASE-DB-1", lead_investigator=inv)
    ev = register_evidence(case, title="db")
    return register_artifact(ev, name=path.name, source="test", storage_path=str(path))


class AnalyzeDbTest(TestCase):
    def test_live_tables_and_counts(self):
        path = Path(tempfile.mkdtemp()) / "app.db"
        build_sample_db(path, with_deleted=False)
        result = analyze(path)
        self.assertTrue(result.is_sqlite)
        names = {t.name for t in result.tables}
        self.assertIn("contacts", names)
        self.assertIn("messages", names)
        by_name = {t.name: t for t in result.tables}
        self.assertEqual(by_name["contacts"].row_count, 3)
        self.assertEqual(by_name["messages"].row_count, 2)
        cols = [c["name"] for c in by_name["contacts"].columns]
        self.assertIn("name", cols)

    def test_non_sqlite_not_detected(self):
        p = Path(tempfile.mkdtemp()) / "x.bin"
        p.write_bytes(b"this is not a database at all")
        result = analyze(p)
        self.assertFalse(result.is_sqlite)

    def test_missing_file_returns_error(self):
        result = analyze(Path(tempfile.mkdtemp()) / "missing.db")
        self.assertFalse(result.is_sqlite)


class SqliteParseTest(TestCase):
    def test_header_detected(self):
        path = Path(tempfile.mkdtemp()) / "app.db"
        build_sample_db(path, with_deleted=False)
        a = assess_database(path)
        self.assertIsInstance(a, DatabaseAssessment)
        self.assertTrue(a.header_valid)
        self.assertGreater(a.page_size, 0)

    def test_invalid_header(self):
        p = Path(tempfile.mkdtemp()) / "junk.db"
        p.write_bytes(b"\x00" * 100)
        a = assess_database(p)
        self.assertFalse(a.header_valid)

    def test_missing_file(self):
        a = assess_database(Path(tempfile.mkdtemp()) / "nope.db")
        self.assertEqual(a.note, "file not found")


class ServiceTest(TestCase):
    def test_analyze_and_record(self):
        path = Path(tempfile.mkdtemp()) / "app.db"
        build_sample_db(path)
        art = make_artifact(path)
        summary = analyze_artifact_database(art)
        art.refresh_from_db()
        self.assertEqual(art.artifact_type, "sqlite_database")
        self.assertIn("sqlite", art.metadata)
        self.assertTrue(summary["tables"])

    def test_non_sqlite_artifact_raises(self):
        p = Path(tempfile.mkdtemp()) / "f.txt"
        p.write_bytes(b"plain text")
        art = make_artifact(p)
        with self.assertRaises(NotSqliteError):
            analyze_artifact_database(art)

    def test_no_file_raises(self):
        inv = Investigator.objects.create(name="Ada")
        case = Case.objects.create(case_number="CASE-DB2", lead_investigator=inv)
        ev = register_evidence(case, title="c")
        art = register_artifact(ev, name="nf", source="s", storage_path="")
        with self.assertRaises(NoFileError):
            analyze_artifact_database(art)
