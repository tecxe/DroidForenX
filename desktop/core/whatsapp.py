"""WhatsApp and deleted-data recovery that works without root.

The old loaders read ``/data/data/com.whatsapp`` and reported NOT_ACCESSIBLE on
an unrooted handset, which is every handset this unit actually sees. This
module takes the paths that *are* readable over adb on a stock Android:

* chat exports (``WhatsApp Chat with X.txt``) - full text, side, timestamps,
  and the app's own "this message was deleted" markers;
* a plain ``msgstore.db`` when the app left one unencrypted, parsed for both the
  old ``messages`` schema and the current ``message``/``chat``/``jid`` schema;
* backup inventory - ``msgstore-YYYY-MM-DD.1.db.crypt15`` files prove a copy of
  the database existed on that date, even when the contents stay encrypted;
* notification records (``dumpsys notification --noredact``), which carry
  message previews the app no longer shows;
* deleted-media entries (``.trashed-<epoch>-<name>``), and pre-delete text left
  in ``-wal``/``-journal`` sidecars.

Nothing here claims more than the file it read. A crypt15 backup is reported as
sealed, not as "no messages". A missing root path is reported as unreadable, not
as empty.
"""
from __future__ import annotations

import logging
import re
import shlex
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import adb_manager as adb

logger = logging.getLogger(__name__)

PACKAGE = "com.whatsapp"

#: Shared-storage roots that exist on a stock, unrooted handset.
WHATSAPP_ROOTS = (
    "/sdcard/Android/media/com.whatsapp/WhatsApp",
    "/sdcard/WhatsApp",
    "/sdcard/Android/data/com.whatsapp",
)

#: Where a user's "Export chat" lands, depending on the share target.
EXPORT_ROOTS = (
    "/sdcard/Download",
    "/sdcard/Documents",
    "/sdcard/WhatsApp/Export",
)

#: Directories worth walking for deleted entries.
DELETED_ROOTS = (
    "/sdcard/Android/media",
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/Music",
    "/sdcard/Download",
    "/sdcard/WhatsApp",
    "/sdcard/Telegram",
    "/sdcard/Android/data",
)

MEDIA_EXTENSIONS = {
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".webp": "Image",
    ".heic": "Image", ".gif": "GIF", ".mp4": "Video", ".3gp": "Video",
    ".mkv": "Video", ".opus": "Audio", ".aac": "Audio", ".mp3": "Audio",
    ".m4a": "Audio", ".ogg": "Audio", ".amr": "Audio", ".pdf": "Document",
    ".doc": "Document", ".docx": "Document", ".xls": "Document",
    ".xlsx": "Document", ".txt": "Document", ".zip": "Document",
}

#: Message text WhatsApp writes in place of a body it has discarded.
DELETED_MARKERS = (
    "this message was deleted",
    "you deleted this message",
    "message deleted",
)

#: Attachment placeholders an export writes instead of a file.
ATTACHMENT_MARKERS = {
    "<media omitted>": "Media",
    "image omitted": "Image",
    "video omitted": "Video",
    "audio omitted": "Audio",
    "document omitted": "Document",
    "gif omitted": "GIF",
    "sticker omitted": "Sticker",
    "contact card omitted": "Contact",
}

#: Export lines that are app events, not messages from a person.
SYSTEM_PREFIXES = (
    "messages and calls are end-to-end encrypted",
    "you changed the subject",
    "changed the subject",
    "changed this group's icon",
    "changed their phone number",
    "created group",
    "added you",
    "you were added",
    "joined using this group's invite link",
    "left",
    "you left",
    "removed",
    "missed voice call",
    "missed video call",
    "group call",
    "security code changed",
    "this message was deleted",
    "you deleted this message",
)

_BRACKET_LINE = re.compile(
    r"^\[(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[APap][Mm])?\]\s?(?P<rest>.*)$")
_DASH_LINE = re.compile(
    r"^(?P<date>\d{1,2}[./]\d{1,2}[./]\d{2,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)(?:\s*[APap][Mm])?\s+-\s+(?P<rest>.*)$")
_SENDER_SPLIT = re.compile(r"^(?P<sender>[^:]{1,64}?):\s(?P<body>.*)$")
_TRASH_NAME = re.compile(
    r"^\.(?P<kind>trashed|pending|deleted|trash)[.\-_]"
    r"(?:(?P<stamp>\d{9,14})[.\-_])?(?P<original>.+)$", re.I)


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

def _parse_date(date_text: str, time_text: str, day_first: bool = True) -> dict:
    """Turn an export stamp into ISO-8601 plus an ambiguity note.

    WhatsApp writes the date in the phone's locale, so ``03/04/2026`` can be
    either 3 April or 4 March. When one side is over 12 the order is proven;
    otherwise the caller is told the reading assumed day-first.
    """
    bits = re.split(r"[./]", date_text)
    if len(bits) != 3:
        return {"timestamp": "", "date_text": f"{date_text} {time_text}",
                "ambiguous": False}
    first, second, year = bits
    if len(year) == 2:
        year = "20" + year
    try:
        a, b = int(first), int(second)
        if a > 12 and b <= 12:
            day, month, ambiguous = a, b, False
        elif b > 12 and a <= 12:
            month, day, ambiguous = a, b, False
        elif day_first:
            day, month, ambiguous = a, b, True
        else:
            month, day, ambiguous = a, b, True
        clock = time_text.split(":")
        if len(clock) == 2:
            clock.append("00")
        stamp = datetime(int(year), month, day,
                         int(clock[0]), int(clock[1]), int(clock[2]))
        return {"timestamp": stamp.isoformat(), "date_text": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "ambiguous": ambiguous}
    except (ValueError, IndexError):
        return {"timestamp": "", "date_text": f"{date_text} {time_text}",
                "ambiguous": False}


def _classify_body(body: str) -> dict:
    """Decide what a message body actually is: text, media, system or deleted."""
    lowered = body.strip().lower()
    if lowered in ATTACHMENT_MARKERS:
        return {"kind": "media", "deleted": False,
                "attachment": ATTACHMENT_MARKERS[lowered]}
    if any(lowered.startswith(marker) or lowered == marker for marker in DELETED_MARKERS):
        return {"kind": "deleted", "deleted": True, "attachment": ""}
    if any(lowered.startswith(prefix) for prefix in SYSTEM_PREFIXES):
        return {"kind": "system", "deleted": False, "attachment": ""}
    return {"kind": "text", "deleted": False, "attachment": ""}


def _chat_name_from_path(path: str) -> str:
    name = Path(str(path)).stem
    for prefix in ("WhatsApp Chat with ", "WhatsApp Chat - "):
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name or "Unknown chat"


# --------------------------------------------------------------------------
# chat exports
# --------------------------------------------------------------------------

def parse_exported_chat(text: str, source: str = "", day_first: bool = True) -> dict:
    """Parse a WhatsApp "Export chat" text file into normalised messages.

    Continuation lines belong to the message above them; that is how WhatsApp
    writes a multi-line message, and dropping them loses evidence.
    """
    chat = _chat_name_from_path(source)
    messages: list[dict] = []
    current: dict | None = None
    ambiguous = 0

    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        match = _BRACKET_LINE.match(line) or _DASH_LINE.match(line)
        if match:
            if current:
                messages.append(current)
            when = _parse_date(match.group("date"), match.group("time"), day_first)
            if when["ambiguous"]:
                ambiguous += 1
            rest = match.group("rest").strip()
            sender = ""
            body = rest
            sender_match = _SENDER_SPLIT.match(rest)
            if sender_match and "\u202f" not in rest[:2]:
                sender = sender_match.group("sender").strip()
                body = sender_match.group("body").strip()
            kind = _classify_body(body)
            if not sender:
                kind = {"kind": "system", "deleted": kind["deleted"], "attachment": ""}
            current = {
                "chat": chat,
                "sender": sender,
                "from_me": None,
                "direction": "unknown",
                "text": "" if kind["kind"] in ("deleted", "media", "system") else body,
                "body": body,
                "kind": kind["kind"],
                "deleted": kind["deleted"],
                "attachment": kind["attachment"],
                "timestamp": when["timestamp"],
                "date_text": when["date_text"],
                "source": source,
            }
            continue
        if current is not None and line.strip():
            if current["kind"] == "text":
                current["text"] = f"{current['text']}\n{line.strip()}".strip()
                current["body"] = f"{current['body']}\n{line.strip()}".strip()
            else:
                current["body"] = f"{current['body']}\n{line.strip()}".strip()
    if current:
        messages.append(current)

    deleted = sum(1 for m in messages if m["deleted"])
    media = sum(1 for m in messages if m["kind"] == "media")
    return {
        "chat": chat,
        "source": source,
        "messages": messages,
        "stats": {
            "messages": len(messages),
            "deleted": deleted,
            "media": media,
            "system": sum(1 for m in messages if m["kind"] == "system"),
            "ambiguous_dates": ambiguous,
        },
    }


def _decode_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_exported_chats(paths, day_first: bool = True) -> list[dict]:
    """Parse every readable export file, skipping anything unreadable."""
    parsed = []
    for path in paths:
        candidate = Path(path)
        try:
            parsed.append(parse_exported_chat(_decode_text(candidate),
                                              source=str(candidate),
                                              day_first=day_first))
        except OSError as exc:
            logger.warning("Could not read chat export %s: %s", candidate, exc)
    return parsed


# --------------------------------------------------------------------------
# msgstore databases
# --------------------------------------------------------------------------

def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.DatabaseError:
        return []


def _tables(conn: sqlite3.Connection) -> set[str]:
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError:
        return set()


def _normalise_message(row: dict, chat: str, columns: list[str]) -> dict:
    text = row.get("text_data") or row.get("data") or row.get("text") or ""
    caption = row.get("media_caption") or ""
    body = "\n".join(part for part in (text, caption) if part).strip()
    kind = _classify_body(body)
    if row.get("media_mime_type") and kind["kind"] == "text":
        kind = {"kind": "media", "deleted": False,
                "attachment": str(row.get("media_mime_type"))}
    from_me = row.get("from_me")
    if from_me is None:
        from_me = row.get("key_from_me")
    from_me = int(from_me or 0)
    return {
        "chat": chat or row.get("jid") or row.get("key_remote_jid") or "Unknown chat",
        "sender": row.get("sender") or ("me" if from_me else chat),
        "from_me": from_me,
        "direction": "outgoing" if from_me else "incoming",
        "text": "" if kind["kind"] in ("deleted", "media", "system") else body,
        "body": body,
        "kind": kind["kind"],
        "deleted": kind["deleted"],
        "attachment": kind["attachment"],
        "timestamp": "",
        "date_text": "",
        "raw_timestamp": row.get("timestamp") or 0,
        "source": "",
        "columns_seen": len(columns),
    }


def msgstore_messages(db_path) -> dict:
    """Read a decrypted ``msgstore.db`` with whichever schema it carries."""
    path = str(db_path)
    result = {"messages": [], "contacts": [], "schema": "unknown", "readable": False}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result
    conn.row_factory = sqlite3.Row
    try:
        tables = _tables(conn)
        if not tables:
            result["error"] = "not a readable SQLite database (encrypted or empty)"
            return result
        result["readable"] = True

        if "message" in tables:                       # current schema
            columns = _table_columns(conn, "message")
            select = [c for c in ("_id", "key_id", "from_me", "timestamp",
                                  "text_data", "message_type", "media_mime_type",
                                  "media_caption", "media_name", "media_size",
                                  "status", "chat_row_id") if c in columns]
            joins = ""
            chat_expr = "''"
            if "chat" in tables:
                joins += " LEFT JOIN chat c ON c._id = m.chat_row_id"
                if "subject" in _table_columns(conn, "chat"):
                    chat_expr = "c.subject"
                if "jid" in tables and "jid_row_id" in _table_columns(conn, "chat"):
                    joins += " LEFT JOIN jid j ON j._id = c.jid_row_id"
                    jcols = _table_columns(conn, "jid")
                    if "raw_string" in jcols:
                        chat_expr = f"COALESCE(NULLIF({chat_expr}, ''), j.raw_string)"
            query = (f"SELECT {', '.join('m.' + c for c in select)}, "
                     f"{chat_expr} AS chat_name FROM message m{joins}"
                     " ORDER BY m.timestamp ASC")
            try:
                rows = conn.execute(query).fetchall()
            except sqlite3.DatabaseError:
                rows = []
            for row in (dict(r) for r in rows):
                message = _normalise_message(row, row.get("chat_name", ""), columns)
                message["raw_timestamp"] = row.get("timestamp")
                result["messages"].append(message)
            result["schema"] = "message/chat/jid"
        elif "messages" in tables:                    # legacy schema
            columns = _table_columns(conn, "messages")
            select = [c for c in ("_id", "key_remote_jid", "key_from_me", "data",
                                  "timestamp", "media_mime_type", "media_caption",
                                  "media_url", "media_size", "message_type",
                                  "status", "remote_resource") if c in columns]
            query = (f"SELECT {', '.join(select)} FROM messages"
                     " ORDER BY timestamp ASC")
            try:
                rows = conn.execute(query).fetchall()
            except sqlite3.DatabaseError:
                rows = []
            for row in (dict(r) for r in rows):
                message = _normalise_message(row, row.get("key_remote_jid", ""), columns)
                message["raw_timestamp"] = row.get("timestamp")
                result["messages"].append(message)
            result["schema"] = "messages (legacy)"

        for contact_table in ("wa_contacts", "wa_contact"):
            if contact_table in tables:
                cols = _table_columns(conn, contact_table)
                wanted = [c for c in ("jid", "display_name", "phone", "status")
                          if c in cols]
                if wanted:
                    try:
                        rows = conn.execute(
                            f"SELECT {', '.join(wanted)} FROM {contact_table}").fetchall()
                        result["contacts"] = [dict(r) for r in rows]
                    except sqlite3.DatabaseError:
                        pass
                break
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        conn.close()

    for message in result["messages"]:
        if message.get("raw_timestamp"):
            message["date_text"] = _epoch_to_text(message["raw_timestamp"])
            message["timestamp"] = message["date_text"].replace(" ", "T")
    return result


def _epoch_to_text(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number > 10_000_000_000:      # milliseconds
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


# --------------------------------------------------------------------------
# deleted entries
# --------------------------------------------------------------------------

def classify_deleted(path: str, name: str = "", size: int | None = None,
                     mtime: int | None = None) -> dict | None:
    """Describe a trashed file, or return None when it is not a deletion trace."""
    filename = name or Path(path).name
    match = _TRASH_NAME.match(filename)
    if not match:
        return None
    original = match.group("original") or filename
    stamp = match.group("stamp")
    deleted_at = _epoch_to_text(stamp) if stamp else ""
    ext = Path(original).suffix.lower()
    return {
        "path": path,
        "name": filename,
        "original_name": original,
        "extension": ext,
        "type": MEDIA_EXTENSIONS.get(ext, "File"),
        "size": size,
        "deleted_at": deleted_at,
        "deleted_at_text": deleted_at or "unknown (no timestamp in name)",
        "kind": match.group("kind").lower(),
        "status": ("Recently deleted - recoverable"
                   if match.group("kind").lower() == "trashed"
                   else "Pending deletion - recoverable"),
        "mtime": mtime,
    }


REPORT_WORTHY = re.compile(r"[ -~]{6,}\s")


def recover_text_fragments(path, limit: int = 200) -> list[str]:
    """Pull readable strings out of a journal/WAL left by the app.

    A rollback journal holds the pre-transaction page images, so a message the
    app wrote and then removed can still sit in it as plain text. This scans the
    bytes; it does not claim to reconstruct the row it came from.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    fragments: list[str] = []
    seen = set()
    for chunk in re.split(rb"[\x00-\x1f\x7f]+", data):
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(text) < 6 or not REPORT_WORTHY.search(text):
            continue
        cleaned = text.strip()
        if cleaned and cleaned not in seen and _looks_like_content(cleaned):
            seen.add(cleaned)
            fragments.append(cleaned[:400])
        if len(fragments) >= limit:
            break
    return fragments


def _looks_like_content(text: str) -> bool:
    letters = sum(1 for ch in text if ch.isalpha())
    return letters >= 3 and letters / max(len(text), 1) > 0.4


def recover_sqlite_rows(db_path, table: str, limit: int = 500) -> list[dict]:
    """Salvage intact records from the freelist of an unencrypted database.

    Deleting a row frees its page; until SQLite reuses that page the cell bytes
    are still there. Reading the freelist rather than the whole file is what
    keeps this honest: these are rows the database says are free, with their
    original column values decoded from the record header.
    """
    path = Path(db_path)
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) < 100 or data[:16] != b"SQLite format 3\x00":
        return []

    page_size = int.from_bytes(data[16:18], "big")
    if page_size == 1:
        page_size = 65536
    if page_size < 512:
        return []
    reserved = data[20]
    usable = page_size - reserved
    trunk = int.from_bytes(data[32:36], "big")
    if not trunk:
        return []

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        conn.close()
    except sqlite3.DatabaseError:
        columns = []
    if not columns:
        return []

    free_pages: list[int] = []
    seen_trunks = set()
    while trunk and trunk not in seen_trunks and len(free_pages) < 4000:
        seen_trunks.add(trunk)
        offset = (trunk - 1) * page_size
        if offset + 8 > len(data):
            break
        leaf_count = int.from_bytes(data[offset + 4:offset + 8], "big")
        next_trunk = int.from_bytes(data[offset:offset + 4], "big")
        for index in range(min(leaf_count, 2000)):
            pos = offset + 8 + index * 4
            if pos + 4 > len(data):
                break
            free_pages.append(int.from_bytes(data[pos:pos + 4], "big"))
        trunk = next_trunk

    recovered: list[dict] = []
    for page_no in free_pages:
        offset = (page_no - 1) * page_size
        page = data[offset:offset + page_size]
        if len(page) < page_size:
            continue
        header = 100 if page_no == 1 else 0
        if header >= len(page) or page[header] != 0x0D:
            continue
        cell_count = int.from_bytes(page[header + 3:header + 5], "big")
        for index in range(min(cell_count, 500)):
            pointer_at = header + 8 + index * 2
            if pointer_at + 2 > len(page):
                break
            pointer = int.from_bytes(page[pointer_at:pointer_at + 2], "big")
            row = _decode_cell(page, pointer, usable, columns)
            if row:
                row["recovered_from"] = f"{path.name} page {page_no} (freelist)"
                recovered.append(row)
            if len(recovered) >= limit:
                return recovered
    return recovered


def _varint(buffer: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        if pos + index >= len(buffer):
            raise ValueError("truncated varint")
        byte = buffer[pos + index]
        if index == 8:
            return (value << 8) | byte, pos + 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos + index + 1
    raise ValueError("varint too long")


def _decode_cell(page: bytes, pointer: int, usable: int, columns: list[str]) -> dict | None:
    try:
        payload_len, pos = _varint(page, pointer)
        _rowid, pos = _varint(page, pos)
    except ValueError:
        return None
    if payload_len <= 0 or pos + payload_len > min(len(page), usable):
        return None          # spilled to overflow pages; not reconstructed
    payload = page[pos:pos + payload_len]
    try:
        header_len, header_pos = _varint(payload, 0)
    except ValueError:
        return None
    if header_len > len(payload):
        return None
    serials = []
    cursor = header_pos
    while cursor < header_len:
        try:
            serial, cursor = _varint(payload, cursor)
        except ValueError:
            return None
        if serial > 11:
            serials.append(serial)
        else:
            serials.append(serial)
        if len(serials) > len(columns) + 8:
            return None
    values = _read_values(payload, header_len, serials)
    if len(values) != len(columns):
        return None
    row = {}
    for column, value in zip(columns, values):
        row[column] = value
    if not any(isinstance(value, str) and value.strip() for value in row.values()):
        return None
    return row


def _read_values(payload: bytes, cursor: int, serials: list[int]) -> list:
    values = []
    for serial in serials:
        if serial == 0:
            values.append(None)
        elif serial <= 6:
            size = (1, 2, 3, 4, 6, 8)[serial - 1]
            values.append(int.from_bytes(payload[cursor:cursor + size], "big", signed=True))
            cursor += size
        elif serial == 7:
            import struct
            values.append(struct.unpack(">d", payload[cursor:cursor + 8])[0])
            cursor += 8
        elif serial == 8:
            values.append(0)
        elif serial == 9:
            values.append(1)
        elif serial >= 12:
            size = (serial - 12) // 2 if serial % 2 == 0 else (serial - 13) // 2
            chunk = payload[cursor:cursor + size]
            cursor += size
            if serial % 2 == 0:
                values.append(chunk.hex())
            else:
                values.append(chunk.decode("utf-8", "replace"))
        else:
            return []
    return values


# --------------------------------------------------------------------------
# notifications
# --------------------------------------------------------------------------

_NOTIF_BLOCK = re.compile(r"NotificationRecord\{")
_ANDROID_FIELD = re.compile(r"android\.(title|text|bigText)=String \((?P<value>.*)\)$")


def parse_whatsapp_notifications(dump: str) -> list[dict]:
    """Extract WhatsApp message previews from ``dumpsys notification``.

    A notification survives in the dump after the chat itself has been cleared,
    which is exactly the kind of remnant worth recording.
    """
    records = []
    for block in _NOTIF_BLOCK.split(dump or "")[1:]:
        if f"pkg={PACKAGE}" not in block and "com.whatsapp" not in block:
            continue
        title = text = big = ""
        for line in block.splitlines():
            match = _ANDROID_FIELD.search(line.strip())
            if not match:
                continue
            value = match.group("value").strip()
            field = match.group(1)
            if field == "title":
                title = value
            elif field == "text":
                text = value
            else:
                big = value
        posted = re.search(r"postTime=(\d{13})", block)
        if title or text or big:
            records.append({
                "type": "Notification",
                "chat": title or "WhatsApp",
                "sender": title or "WhatsApp",
                "text": big or text,
                "timestamp": _epoch_to_text(posted.group(1)) if posted else "",
                "source": "dumpsys notification --noredact",
                "kind": "notification",
                "deleted": False,
            })
    return records


# --------------------------------------------------------------------------
# device collection
# --------------------------------------------------------------------------

def _find_files(serial: str, root: str, max_entries: int = 20000,
                timeout: int = 90) -> list[dict]:
    okay, output = adb.adb_shell(
        serial, f"find {shlex.quote(root)} -type f 2>/dev/null", timeout=timeout)
    if not okay or not output:
        return []
    files = []
    for line in output.splitlines():
        line = line.strip()
        if not line or "Permission denied" in line:
            continue
        files.append({"path": line, "name": Path(line).name,
                      "extension": Path(line).suffix.lower()})
        if len(files) >= max_entries:
            break
    return files


def classify_media(files: list[dict]) -> list[dict]:
    media = []
    for entry in files:
        kind = MEDIA_EXTENSIONS.get(entry.get("extension", ""))
        if kind:
            media.append({**entry, "type": kind})
    return media


def backup_inventory(serial: str, files: list[dict], limit: int = 60) -> list[dict]:
    """List local database backups with their timestamps and seal state."""
    backups = []
    for entry in files:
        name = entry["name"]
        lowered = name.lower()
        if not any(token in lowered for token in ("msgstore", "wa.db", "chatsettings")):
            continue
        sealed = any(lowered.endswith(suffix) for suffix in
                     (".crypt12", ".crypt14", ".crypt15"))
        info = adb.shell_stat(serial, entry["path"])
        backups.append({
            "name": name,
            "path": entry["path"],
            "size": info.get("size"),
            "modified": (info.get("modify_time") or "")[:19],
            "sealed": sealed,
            "format": ("Crypt15 (AES-256-GCM, key in app-private storage)"
                       if lowered.endswith(".crypt15") else
                       "Crypt14" if lowered.endswith(".crypt14") else
                       "Crypt12" if lowered.endswith(".crypt12") else "SQLite"),
            "status": ("Encrypted backup - contents readable only with the "
                       "device key (root) or the 64-digit backup key"
                       if sealed else "Plain SQLite - parseable"),
        })
        if len(backups) >= limit:
            break
    backups.sort(key=lambda b: b.get("modified") or "", reverse=True)
    return backups


def find_export_files(serial: str, max_per_root: int = 200) -> list[str]:
    """Chat export text files sitting in Download/Documents (bounded walk)."""
    found = []
    for root in EXPORT_ROOTS:
        okay, output = adb.adb_shell(
            serial, f"find {shlex.quote(root)} -maxdepth 3 -type f -iname '*.txt'"
                    f" 2>/dev/null", timeout=45)
        if not okay or not output:
            continue
        for line in output.splitlines():
            line = line.strip()
            if not line or "Permission denied" in line:
                continue
            lowered = Path(line).name.lower()
            if "chat" in lowered or lowered == "_chat.txt" or "whatsapp" in lowered:
                found.append(line)
            if len(found) >= max_per_root * 3:
                return found
    return found


def collect(serial: str, workdir, day_first: bool = True,
            pull_exports: bool = True) -> dict:
    """Gather everything the unrooted device will hand over.

    ``workdir`` is where pulled exports and plain databases are staged. The
    caller owns it so the case evidence tree stays the single place copies live.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    result = {
        "messages": [], "deleted_messages": [], "backups": [], "media": [],
        "notifications": [], "fragments": [], "notes": [], "stats": {},
    }

    files: list[dict] = []
    for root in WHATSAPP_ROOTS:
        files.extend(_find_files(serial, root))
    if not files:
        result["notes"].append(
            "No WhatsApp shared storage found. The app may never have run on "
            "this handset, or its storage was cleared.")
    result["media"] = classify_media(files)
    result["backups"] = backup_inventory(serial, files)

    if pull_exports:
        for index, remote in enumerate(find_export_files(serial)[:25]):
            local = workdir / f"export_{index}_{Path(remote).name}"
            if not adb.pull_file(serial, remote, str(local), timeout=90):
                continue
            try:
                parsed = parse_exported_chat(_decode_text(local),
                                             source=remote, day_first=day_first)
            except OSError as exc:
                logger.warning("Export read failed %s: %s", remote, exc)
                continue
            result["messages"].extend(parsed["messages"])
            result["deleted_messages"].extend(
                m for m in parsed["messages"] if m["deleted"])
            result["notes"].append(
                f"Read chat export: {Path(remote).name} "
                f"({parsed['stats']['messages']} messages)")

    plain_databases = [entry for entry in files
                       if entry["extension"] in (".db", ".sqlite")
                       and not entry["name"].lower().startswith("msgstore-")]
    for index, entry in enumerate(plain_databases[:6]):
        local = workdir / f"plain_{index}_{entry['name']}"
        if not adb.pull_file(serial, entry["path"], str(local), timeout=180):
            continue
        parsed = msgstore_messages(local)
        if parsed.get("readable"):
            result["messages"].extend(parsed["messages"])
            result["deleted_messages"].extend(
                m for m in parsed["messages"] if m["deleted"])
            result["notes"].append(
                f"Parsed {entry['name']} ({parsed['schema']}, "
                f"{len(parsed['messages'])} messages)")

    okay, dump = adb.adb_shell(serial, "dumpsys notification --noredact 2>/dev/null",
                               timeout=60)
    if okay and dump:
        result["notifications"] = parse_whatsapp_notifications(dump)
        if result["notifications"]:
            result["notes"].append(
                f"{len(result['notifications'])} WhatsApp notifications in the "
                "notification dump")

    result["stats"] = {
        "messages": len(result["messages"]),
        "deleted_messages": len(result["deleted_messages"]),
        "backups": len(result["backups"]),
        "sealed_backups": sum(1 for b in result["backups"] if b["sealed"]),
        "media": len(result["media"]),
        "notifications": len(result["notifications"]),
    }
    return result


def collect_deleted(serial: str, workdir, max_entries: int = 4000) -> dict:
    """Find deletion traces on shared storage and in database sidecars."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    journals: list[dict] = []
    seen = set()

    pattern = (r'\( -name ".trashed*" -o -name ".pending*" -o -name ".deleted*"'
               r' -o -name "*.trash" \)')
    for root in DELETED_ROOTS:
        okay, output = adb.adb_shell(
            serial, f"find {shlex.quote(root)} -type f {pattern}"
                    f" 2>/dev/null | head -{max_entries}", timeout=120)
        if not okay or not output:
            continue
        for line in output.splitlines():
            line = line.strip()
            if not line or "Permission denied" in line or line in seen:
                continue
            seen.add(line)
            entry = classify_deleted(line)
            if entry:
                entries.append(entry)

    sidecar_pattern = r'\( -name "*.db-wal" -o -name "*.db-journal" \)'
    for root in ("/sdcard/Android/media", "/sdcard/Android/data",
                 "/sdcard/Download", "/sdcard/Documents"):
        okay, output = adb.adb_shell(
            serial, f"find {shlex.quote(root)} -maxdepth 5 -type f"
                    f" {sidecar_pattern} 2>/dev/null | head -200", timeout=90)
        if not okay or not output:
            continue
        for line in output.splitlines():
            line = line.strip()
            if not line or "Permission denied" in line or line in seen:
                continue
            seen.add(line)
            name = Path(line).name
            local = workdir / f"journal_{len(journals)}_{name}"
            pulled = adb.pull_file(serial, line, str(local), timeout=90)
            fragments = recover_text_fragments(local) if pulled else []
            journals.append({
                "path": line,
                "name": name,
                "type": "DB Journal",
                "size": local.stat().st_size if pulled and local.exists() else None,
                "fragments": fragments,
                "status": ("Pre-delete page images present" if fragments
                           else "Journal present, no readable strings"),
            })
            if len(journals) >= 40:
                break

    return {
        "entries": entries,
        "journals": journals,
        "stats": {
            "trashed": len(entries),
            "journals": len(journals),
            "fragments": sum(len(j["fragments"]) for j in journals),
        },
    }
