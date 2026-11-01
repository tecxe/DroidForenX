"""Telegram export/cache parsing and the queue of chat kinds it must preserve."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from desktop.core import telegram

EXPORT = {
    "about": "Telegram export",
    "chats": {
        "list": [
            {
                "id": 111,
                "name": "Alice Mensah",
                "type": "personal_chat",
                "messages": [
                    {"id": 1, "type": "message",
                     "date": "2026-03-12T21:14:02", "date_unixtime": "1773347642",
                     "from": "Alice Mensah", "from_id": "user111",
                     "text": "Are you coming tomorrow?"},
                    {"id": 2, "type": "message",
                     "date": "2026-03-12T21:15:10", "date_unixtime": "1773347710",
                     "from": "You", "from_id": "user0",
                     "text": [{"type": "bold", "text": "Bring "}, "the documents"]},
                    {"id": 3, "type": "message",
                     "date": "2026-03-12T21:16:00", "date_unixtime": "1773347760",
                     "from": "Alice Mensah", "from_id": "user111",
                     "text": "", "media_type": "photo", "photo": "photos/photo_1.jpg",
                     "mime_type": "image/jpeg", "file": "photos/photo_1.jpg"},
                ],
            },
            {
                "id": 222,
                "name": "EVERYTHING T.A",
                "type": "private_supergroup",
                "messages": [
                    {"id": 10, "type": "message", "date": "2026-03-10T09:00:00",
                     "date_unixtime": "1773133200", "from": "Nico",
                     "text": "That is all for this TA class.",
                     "reply_to_message_id": 9,
                     "forwarded_from": "Exam Unit"},
                    {"id": 11, "type": "service", "date": "2026-03-10T09:01:00",
                     "date_unixtime": "1773133260", "action": "create_group",
                     "text": ""},
                ],
            },
            {
                "id": 333,
                "name": "EmperorBTC Channel",
                "type": "public_channel",
                "messages": [
                    {"id": 20, "type": "message", "date": "2026-03-09T18:00:00",
                     "date_unixtime": "1773079200",
                     "text": "I know all of you lack attention."},
                ],
            },
        ]
    },
}


class ExportParseTest(unittest.TestCase):
    def setUp(self):
        self.parsed = telegram.parse_export(EXPORT, source="result.json")

    def test_all_chats_are_kept(self):
        self.assertEqual([c["name"] for c in self.parsed["chats"]],
                         ["Alice Mensah", "EVERYTHING T.A", "EmperorBTC Channel"])

    def test_chat_kinds_are_normalised(self):
        kinds = [c["type"] for c in self.parsed["chats"]]
        self.assertEqual(kinds, ["Private chat", "Group", "Channel"])

    def test_messages_land_in_their_chat(self):
        self.assertEqual(self.parsed["chats"][0]["message_count"], 3)
        self.assertEqual(self.parsed["stats"]["messages"], 6)

    def test_formatted_text_is_flattened(self):
        second = self.parsed["chats"][0]["messages"][1]
        self.assertEqual(second["text"], "Bring the documents")

    def test_photo_message_is_classified_and_keeps_its_file(self):
        photo = self.parsed["chats"][0]["messages"][2]
        self.assertEqual(photo["kind"], "Image")
        self.assertEqual(photo["file"], "photos/photo_1.jpg")

    def test_reply_and_forward_are_preserved(self):
        message = self.parsed["chats"][1]["messages"][0]
        self.assertEqual(message["reply_to"], 9)
        self.assertEqual(message["forwarded_from"], "Exam Unit")

    def test_service_event_is_marked_service(self):
        service = self.parsed["chats"][1]["messages"][1]
        self.assertTrue(service["service"])
        self.assertEqual(service["action"], "create_group")

    def test_stats_count_groups_and_channels(self):
        stats = self.parsed["stats"]
        self.assertEqual(stats["groups"], 1)
        self.assertEqual(stats["channels"], 1)
        self.assertEqual(stats["private_chats"], 1)

    def test_empty_export_is_safe(self):
        parsed = telegram.parse_export({})
        self.assertEqual(parsed["chats"], [])
        self.assertEqual(parsed["stats"]["messages"], 0)


class ExportReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-tg-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_result_json_is_parsed_from_disk(self):
        path = self.tmp / "result.json"
        path.write_text(json.dumps(EXPORT), encoding="utf-8")
        parsed = telegram.read_export(path)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["stats"]["chats"], 3)
        self.assertEqual(parsed["chats"][0]["source"], str(path))

    def test_utf8_bom_is_tolerated(self):
        path = self.tmp / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(EXPORT).encode("utf-8"))
        parsed = telegram.read_export(path)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["stats"]["chats"], 3)

    def test_unrelated_json_is_rejected(self):
        path = self.tmp / "other.json"
        path.write_text('{"hello": "world"}', encoding="utf-8")
        self.assertIsNone(telegram.read_export(path))

    def test_missing_file_returns_none(self):
        self.assertIsNone(telegram.read_export(self.tmp / "nope.json"))


class CacheDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfx-tgc-"))
        self.db = self.tmp / "cache4.db"
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE messages_v2 (mid INTEGER PRIMARY KEY,"
                     " uid INTEGER, message TEXT, date INTEGER, out INTEGER,"
                     " media INTEGER)")
        conn.execute("INSERT INTO messages_v2 VALUES (1, 111, 'hello', 1773347400, 0, 0)")
        conn.execute("INSERT INTO messages_v2 VALUES (2, 111, 'photo', 1773347460, 1, 1)")
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_messages_v2_is_read(self):
        parsed = telegram.cache_messages(self.db)
        self.assertTrue(parsed["readable"])
        self.assertEqual(parsed["schema"], "messages_v2")
        self.assertEqual(len(parsed["messages"]), 2)
        self.assertEqual(parsed["messages"][0]["text"], "hello")
        self.assertEqual(parsed["messages"][1]["sender"], "me")

    def test_encrypted_or_binary_file_is_reported(self):
        junk = self.tmp / "cache.bin"
        junk.write_bytes(b"not sqlite" * 40)
        parsed = telegram.cache_messages(junk)
        self.assertFalse(parsed["readable"])


class NotificationTest(unittest.TestCase):
    DUMP = """\
NotificationRecord{abc pkg=org.telegram.messenger user=0 id=1 tag=null}
  extras={
    android.title=String (EVERYTHING T.A)
    android.text=String (Nico: Bring the assignment)
  }
  postTime=1773133200000
NotificationRecord{def pkg=com.whatsapp user=0 id=2 tag=null}
  extras={
    android.title=String (Alice)
    android.text=String (hello)
  }
"""

    def test_telegram_preview_is_extracted(self):
        records = telegram.parse_notifications(self.DUMP)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["chat"], "EVERYTHING T.A")
        self.assertIn("assignment", records[0]["text"])
        self.assertIn("2026-03-10", records[0]["date"])

    def test_whatsapp_records_are_not_included(self):
        records = telegram.parse_notifications(self.DUMP)
        self.assertFalse(any("Alice" in r["chat"] for r in records))


class MediaTest(unittest.TestCase):
    def test_telegram_cache_names_are_classified(self):
        files = [
            {"path": "/a/-5839177312163078613_1109.jpg", "name": "x.jpg", "extension": ".jpg"},
            {"path": "/a/-584840316437884073_97.jpg", "name": "y.jpg", "extension": ".jpg"},
            {"path": "/a/voice.opus", "name": "voice.opus", "extension": ".opus"},
            {"path": "/a/sticker.tgs", "name": "sticker.tgs", "extension": ".tgs"},
            {"path": "/a/data.db", "name": "data.db", "extension": ".db"},
        ]
        kinds = [m["type"] for m in telegram.classify_media(files)]
        self.assertEqual(kinds, ["Image", "Image", "Audio", "Sticker (Animated)"])


if __name__ == "__main__":
    unittest.main()
