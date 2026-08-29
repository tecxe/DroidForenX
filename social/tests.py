"""Tests for social-media (WhatsApp) artifact analysis."""

import sqlite3
import tempfile
from pathlib import Path

from django.test import TestCase

from cases.models import Case, Investigator
from evidence.services import register_artifact, register_evidence

from .service import NoFileError, UnsupportedAppError, analyze_social_artifact
from .whatsapp import extract


def build_whatsapp_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, key_remote_jid TEXT, "
                "timestamp INTEGER, from_me INTEGER, message_type TEXT, data TEXT, deleted INTEGER)")
    cur.execute("INSERT INTO messages VALUES (1,'jid1@s.whatsapp.net',1700000000,0,'text','hello there',NULL)")
    cur.execute("INSERT INTO messages VALUES (2,'jid1@s.whatsapp.net',1700000001,1,'text','hi back',NULL)")
    cur.execute("INSERT INTO messages VALUES (3,'jid2@s.whatsapp.net',1700000002,0,'image','',NULL)")
    cur.execute("CREATE TABLE wa_contacts (jid TEXT PRIMARY KEY, display_name TEXT, wa_name TEXT)")
    cur.execute("INSERT INTO wa_contacts VALUES ('jid1@s.whatsapp.net','Alice','Alice A.')")
    conn.commit()
    conn.close()
    return path


def make_artifact(path: Path):
    inv = Investigator.objects.create(name="Ada")
    case = Case.objects.create(case_number="CASE-SOC-1", lead_investigator=inv)
    ev = register_evidence(case, title="social")
    return register_artifact(ev, name=path.name, source="test", storage_path=str(path))


class WhatsappExtractTest(TestCase):
    def test_extracts_messages_and_contacts(self):
        path = Path(tempfile.mkdtemp()) / "msgstore.db"
        build_whatsapp_db(path)
        result = extract(path)
        self.assertTrue(result.is_whatsapp_db)
        self.assertEqual(result.message_count, 3)
        self.assertEqual(result.contacts[0]["display_name"], "Alice")
        messages = result.messages
        self.assertEqual(messages[0].body, "hello there")
        self.assertEqual(messages[0].jid, "jid1@s.whatsapp.net")

    def test_non_whatsapp_db(self):
        conn = sqlite3.connect(str(path := Path(tempfile.mkdtemp()) / "x.db"))
        conn.execute("CREATE TABLE foo (a INTEGER)")
        conn.commit()
        conn.close()
        result = extract(path)
        self.assertFalse(result.is_whatsapp_db)
        self.assertEqual(result.message_count, 0)

    def test_missing_file(self):
        result = extract(Path(tempfile.mkdtemp()) / "nope.db")
        self.assertIsNotNone(result.error)


class SocialServiceTest(TestCase):
    def test_analyze_and_record(self):
        path = Path(tempfile.mkdtemp()) / "msgstore.db"
        build_whatsapp_db(path)
        art = make_artifact(path)
        summary = analyze_social_artifact(art)
        art.refresh_from_db()
        self.assertEqual(art.artifact_type, "whatsapp_database")
        self.assertEqual(art.category, "social_media")
        self.assertIn("social", art.metadata)
        self.assertEqual(summary["message_count"], 3)

    def test_unsupported_raises(self):
        conn = sqlite3.connect(str(path := Path(tempfile.mkdtemp()) / "x.db"))
        conn.execute("CREATE TABLE foo (a INTEGER)")
        conn.commit()
        conn.close()
        art = make_artifact(path)
        with self.assertRaises(UnsupportedAppError):
            analyze_social_artifact(art)

    def test_no_file_raises(self):
        inv = Investigator.objects.create(name="Ada")
        case = Case.objects.create(case_number="CASE-SOC2", lead_investigator=inv)
        ev = register_evidence(case, title="c")
        art = register_artifact(ev, name="nf", source="s", storage_path="")
        with self.assertRaises(NoFileError):
            analyze_social_artifact(art)
