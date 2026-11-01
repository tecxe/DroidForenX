"""WhatsApp and deleted-data recovery, against fixtures and real SQLite files.

The unrooted paths are the ones this unit depends on, so the tests exercise the
same artefacts a stock handset produces: an exported chat, a msgstore with the
current schema and with the legacy one, a trashed media entry, a rollback
journal, and a database whose rows were deleted before the scan.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from desktop.core import whatsapp

EXPORT = """[12/03/2026, 21:14:02] Alice Mensah: Are you coming tomorrow?
[12/03/2026, 21:14:40] Alice Mensah: I sent the money already, check
[12/03/2026, 21:15:10] You: Got it. Sending the receipt now
[12/03/2026, 21:16:02] Alice Mensah: <Media omitted>
[12/03/2026, 21:18:44] You: This message was deleted
[12/03/2026, 21:19:00] Alice Mensah: He said
he will call at 6
[13/03/2026, 08:02:11] Alice Mensah: Missed voice call
[13/03/2026, 08:05:00] Alice Mensah: Call me when you see this
"""

NOTIFICATION_DUMP = """\
Current Notification List:
  NotificationRecord{4b1c pkg=com.whatsapp user=0 id=7 tag=null}
    flags=0x10
    channel=chat
    extras={
      android.title=String (Alice Mensah)
      android.text=String (Meet me at the usual place)
      android.bigText=String (Meet me at the usual place, bring the documents)
    }
    postTime=1773347400000
  NotificationRecord{9f2a pkg=com.android.systemui user=0 id=2 tag=null}
    extras={
      android.title=String (Charging)
      android.text=String (80%)
    }
"""


class ExportedChatTest(unittest.TestCase):
    def setUp(self):
        self.parsed = whatsapp.parse_exported_chat(EXPORT, source="WhatsApp Chat with Alice Mensah.txt")
        self.messages = self.parsed["messages"]

    def test_every_stamped_line_becomes_a_message(self):
        self.assertEqual(len(self.messages), 8)

    def test_sender_and_direction_are_kept(self):
        first = self.messages[0]
        self.assertEqual(first["sender"], "Alice Mensah")
        self.assertEqual(first["text"], "Are you coming tomorrow?")
        self.assertEqual(first["timestamp"], "2026-03-12T21:14:02")

    def test_multiline_message_is_joined_not_dropped(self):
        joined = self.messages[5]
        self.assertEqual(joined["text"], "He said\nhe will call at 6")
        self.assertEqual(joined["sender"], "Alice Mensah")

    def test_deleted_message_is_flagged_and_body_cleared(self):
        deleted = self.messages[4]
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["kind"], "deleted")
        self.assertEqual(deleted["text"], "")
        self.assertEqual(self.parsed["stats"]["deleted"], 1)

    def test_media_placeholder_is_classified(self):
        media = self.messages[3]
        self.assertEqual(media["kind"], "media")
        self.assertEqual(media["attachment"], "Media")

    def test_call_is_a_system_event_not_a_message(self):
        call = self.messages[6]
        self.assertEqual(call["kind"], "system")

    def test_stats_are_reported(self):
        stats = self.parsed["stats"]
        self.assertEqual(stats["messages"], 8)
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(stats["media"], 1)

    def test_unbracketed_dash_export_is_read(self):
        text = ("12/03/2026, 21:14 - Alice: hello\n"
                "12/03/2026, 21:15 - You: hi\n")
        parsed = whatsapp.parse_exported_chat(text, source="WhatsApp Chat with Alice.txt")
        self.assertEqual([m["text"] for m in parsed["messages"]], ["hello", "hi"])

    def test_ambiguous_date_is_declared(self):
        parsed = whatsapp.parse_exported_chat(
            "[03/04/2026, 10:00:00] Bob: x\n", source="chat.txt")
        self.assertEqual(parsed["messages"][0]["timestamp"], "2026-04-03T10:00:00")
        self.assertEqual(parsed["stats"]["ambiguous_dates"], 1)

    def test_decided_date_is_not_flagged_ambiguous(self):
        parsed = whatsapp.parse_exported_chat(
            "[25/04/2026, 10:00:00] Bob: x\n", source="chat.txt")
        self.assertEqual(parsed["messages"][0]["timestamp"], "2026-04-25T10:00:00")
        self.assertEqual(parsed["stats"]["ambiguous_dates"], 0)

    def test_empty_export_is_not_an_error(self):
        parsed = whatsapp.parse_exported_chat("", source="chat.txt")
        self.assertEqual(parsed["messages"], [])
        self.assertEqual(parsed["stats"]["messages"], 0)


class MsgstoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-wa-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _current_schema(self):
        path = self.tmp / "msgstore.db"
        conn = sqlite3.connect(str(path))
        conn.executescript("""
            CREATE TABLE jid (_id INTEGER PRIMARY KEY, user TEXT, raw_string TEXT,
                              server TEXT);
            CREATE TABLE chat (_id INTEGER PRIMARY KEY, jid_row_id INTEGER,
                               subject TEXT);
            CREATE TABLE message (_id INTEGER PRIMARY KEY, key_id TEXT,
                                  chat_row_id INTEGER, from_me INTEGER,
                                  timestamp INTEGER, text_data TEXT,
                                  message_type INTEGER, media_mime_type TEXT,
                                  media_caption TEXT, media_name TEXT,
                                  media_size INTEGER, status INTEGER);
        """)
        conn.execute("INSERT INTO jid VALUES (1, '233201234567', '233201234567@s.whatsapp.net', 's.whatsapp.net')")
        conn.execute("INSERT INTO chat VALUES (1, 1, 'Alice Mensah')")
        conn.execute("INSERT INTO message VALUES (1, 'a', 1, 0, 1773347400,"
                     " 'Are you coming?', 0, '', '', '', 0, 13)")
        conn.execute("INSERT INTO message VALUES (2, 'b', 1, 1, 1773347460,"
                     " 'On my way', 0, '', '', '', 0, 13)")
        conn.execute("INSERT INTO message VALUES (3, 'c', 1, 0, 1773347520,"
                     " '', 0, 'image/jpeg', 'the money', 'IMG-1.jpg', 4021, 13)")
        conn.execute("INSERT INTO message VALUES (4, 'd', 1, 0, 1773347580,"
                     " 'This message was deleted', 0, '', '', '', 0, 13)")
        conn.commit()
        conn.close()
        return path

    def _legacy_schema(self):
        path = self.tmp / "wa_legacy.db"
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE messages (_id INTEGER PRIMARY KEY,"
                     " key_remote_jid TEXT, key_from_me INTEGER, data TEXT,"
                     " timestamp INTEGER, media_mime_type TEXT,"
                     " media_caption TEXT, media_url TEXT, message_type INTEGER,"
                     " status INTEGER)")
        conn.execute("INSERT INTO messages VALUES (1, '233201234567@s.whatsapp.net',"
                     " 0, 'hello from the old schema', 1773347400000, '', '', '', 0, 13)")
        conn.commit()
        conn.close()
        return path

    def test_current_schema_is_read_with_chat_names(self):
        parsed = whatsapp.msgstore_messages(self._current_schema())
        self.assertTrue(parsed["readable"])
        self.assertEqual(parsed["schema"], "message/chat/jid")
        self.assertEqual(len(parsed["messages"]), 4)
        self.assertEqual(parsed["messages"][0]["chat"], "Alice Mensah")
        self.assertEqual(parsed["messages"][0]["text"], "Are you coming?")
        self.assertEqual(parsed["messages"][1]["direction"], "outgoing")

    def test_media_caption_survives(self):
        parsed = whatsapp.msgstore_messages(self._current_schema())
        media = parsed["messages"][2]
        self.assertEqual(media["kind"], "media")
        self.assertEqual(media["attachment"], "image/jpeg")

    def test_deleted_marker_in_database_is_flagged(self):
        parsed = whatsapp.msgstore_messages(self._current_schema())
        self.assertTrue(parsed["messages"][3]["deleted"])

    def test_legacy_schema_is_read(self):
        parsed = whatsapp.msgstore_messages(self._legacy_schema())
        self.assertTrue(parsed["readable"])
        self.assertIn("legacy", parsed["schema"])
        self.assertEqual(parsed["messages"][0]["text"], "hello from the old schema")
        self.assertIn("2026-03-12", parsed["messages"][0]["date_text"])

    def test_encrypted_file_is_reported_not_silently_empty(self):
        sealed = self.tmp / "msgstore.db.crypt15"
        sealed.write_bytes(b"\x00\x01" + b"not sqlite" * 32)
        parsed = whatsapp.msgstore_messages(sealed)
        self.assertFalse(parsed["readable"])
        self.assertIn("encrypted", parsed["error"])


class DeletedEntryTest(unittest.TestCase):
    def test_whatsapp_trashed_media_keeps_original_name_and_time(self):
        entry = whatsapp.classify_deleted(
            "/sdcard/Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/"
            ".trashed-1773347580-IMG-20260312-WA0007.jpg")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["original_name"], "IMG-20260312-WA0007.jpg")
        self.assertEqual(entry["type"], "Image")
        self.assertIn("2026-03-12", entry["deleted_at"])
        self.assertIn("Recently deleted", entry["status"])

    def test_pending_entry_is_labelled_as_pending_not_recovered(self):
        entry = whatsapp.classify_deleted("/sdcard/DCIM/.pending-1700000000-a.mp4")
        self.assertIn("Pending", entry["status"])
        self.assertEqual(entry["type"], "Video")

    def test_ordinary_file_is_not_a_deletion_trace(self):
        self.assertIsNone(whatsapp.classify_deleted("/sdcard/DCIM/IMG_0001.JPG"))


class JournalFragmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-wal-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_readable_text_is_recovered_from_a_journal(self):
        journal = self.tmp / "msgstore.db-wal"
        journal.write_bytes(
            b"\x37\x7f\x06\x83\x00" + b"meet me at the usual place" + b"\x00\x13\x04"
            + b"the transfer went through" + b"\x00\x00")
        fragments = whatsapp.recover_text_fragments(journal)
        self.assertIn("meet me at the usual place", fragments)
        self.assertIn("the transfer went through", fragments)

    def test_binary_noise_is_not_reported_as_text(self):
        journal = self.tmp / "noise.db-journal"
        journal.write_bytes(bytes(range(0, 32)) * 40)
        self.assertEqual(whatsapp.recover_text_fragments(journal), [])

    def test_missing_file_returns_empty(self):
        self.assertEqual(whatsapp.recover_text_fragments(self.tmp / "nope"), [])


class FreelistRecoveryTest(unittest.TestCase):
    """A deleted row is still on its freelist page until SQLite reuses it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-free-"))
        self.db = self.tmp / "msgstore.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE message (_id INTEGER PRIMARY KEY,"
                     " key_id TEXT, text_data TEXT, timestamp INTEGER)")
        for index in range(800):
            conn.execute(
                "INSERT INTO message (key_id, text_data, timestamp) VALUES (?,?,?)",
                (f"key-{index}", f"message body number {index} about the transfer " + "x" * 180,
                 1773347400 + index))
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_rows_are_not_reported_as_recovered(self):
        recovered = whatsapp.recover_sqlite_rows(self.db, "message")
        self.assertEqual(recovered, [])

    def test_deleted_rows_come_back_with_their_columns(self):
        conn = sqlite3.connect(str(self.db))
        conn.execute("DELETE FROM message WHERE _id > 40")
        conn.commit()
        conn.close()

        recovered = whatsapp.recover_sqlite_rows(self.db, "message")
        texts = [row.get("text_data") for row in recovered
                 if isinstance(row.get("text_data"), str)]
        self.assertTrue(any("message body number" in text for text in texts),
                        f"nothing recovered: {recovered[:3]}")
        sample = next(row for row in recovered if row.get("text_data"))
        self.assertIn("key_id", sample)
        self.assertIn("freelist", sample["recovered_from"])

    def test_unknown_table_yields_nothing(self):
        self.assertEqual(whatsapp.recover_sqlite_rows(self.db, "no_such_table"), [])

    def test_non_sqlite_file_is_ignored(self):
        junk = self.tmp / "junk.db"
        junk.write_bytes(b"definitely not a database" * 10)
        self.assertEqual(whatsapp.recover_sqlite_rows(junk, "message"), [])


class NotificationTest(unittest.TestCase):
    def test_whatsapp_records_are_extracted(self):
        records = whatsapp.parse_whatsapp_notifications(NOTIFICATION_DUMP)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["chat"], "Alice Mensah")
        self.assertIn("documents", records[0]["text"])
        self.assertIn("2026-03-12", records[0]["timestamp"])

    def test_other_packages_are_not_mixed_in(self):
        records = whatsapp.parse_whatsapp_notifications(NOTIFICATION_DUMP)
        self.assertFalse(any("Charging" in r["chat"] for r in records))

    def test_empty_dump_is_safe(self):
        self.assertEqual(whatsapp.parse_whatsapp_notifications(""), [])


class MediaClassificationTest(unittest.TestCase):
    def test_media_types(self):
        files = [
            {"path": "/a/x.jpg", "name": "x.jpg", "extension": ".jpg"},
            {"path": "/a/y.opus", "name": "y.opus", "extension": ".opus"},
            {"path": "/a/z.pdf", "name": "z.pdf", "extension": ".pdf"},
            {"path": "/a/skip.tmp", "name": "skip.tmp", "extension": ".tmp"},
        ]
        self.assertEqual([m["type"] for m in whatsapp.classify_media(files)],
                         ["Image", "Audio", "Document"])


if __name__ == "__main__":
    unittest.main()
