"""Telegram acquisition on a stock handset.

Telegram keeps its live database (``cache4.db``) in app-private storage, so an
unrooted phone cannot hand it over. What it does hand over is a lot:

* an **export** made from Settings → Advanced → Export chat history, which is a
  folder with ``result.json`` and the media beside it. That export carries the
  chat list, group and channel names, participants, full message text, media
  references, forwards, replies and service events;
* the **media cache** under ``Android/data/org.telegram.messenger``, named by
  Telegram itself (``-5839177312163078613_1109.jpg``) and therefore usable as
  evidence of what the account received;
* **notification records**, which carry previews of chats the app has cleared;
* a **plain ``cache4.db``** if one is ever present on shared storage, parsed
  with the same ``messages_v2``/``messages`` handling the rooted loader uses.

Chat types are normalised to the four things an examiner asks about: private
chat, group, channel and saved messages.
"""
from __future__ import annotations

import json
import logging
import shlex
from datetime import datetime, timezone
from pathlib import Path

from . import adb_manager as adb

logger = logging.getLogger(__name__)

PACKAGE = "org.telegram.messenger"

#: Shared-storage roots Telegram writes to across Android versions.
ROOTS = (
    "/sdcard/Android/data/org.telegram.messenger",
    "/sdcard/Android/media/org.telegram.messenger",
    "/sdcard/Telegram",
    "/sdcard/Telegram Desktop",
    "/sdcard/DCIM/Telegram",
    "/sdcard/Pictures/Telegram",
)

MEDIA_EXTENSIONS = {
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".webp": "Image",
    ".gif": "GIF", ".mp4": "Video", ".mkv": "Video", ".webm": "Video",
    ".opus": "Audio", ".ogg": "Audio", ".mp3": "Audio", ".m4a": "Audio",
    ".flac": "Audio", ".tgs": "Sticker (Animated)", ".webp": "Image",
    ".pdf": "Document", ".doc": "Document", ".docx": "Document",
    ".xls": "Document", ".xlsx": "Document", ".zip": "Document",
    ".apk": "Document",
}

CHAT_TYPES = {
    "personal_chat": "Private chat",
    "private_group": "Group",
    "private_supergroup": "Group",
    "public_supergroup": "Group",
    "private_channel": "Channel",
    "public_channel": "Channel",
    "bot_chat": "Bot",
    "saved_messages": "Saved messages",
    "private_group_call": "Group",
}

#: Media types an export records on a message.
MEDIA_KIND = {
    "photo": "Image",
    "video_file": "Video",
    "voice_message": "Audio",
    "audio_file": "Audio",
    "video_message": "Video",
    "animation": "GIF",
    "sticker": "Sticker",
    "file": "Document",
}


def classify_chat(chat_type: str) -> str:
    return CHAT_TYPES.get(str(chat_type or "").lower(), "Other")


def _flatten_text(value) -> str:
    """Turn an export's text field into plain text.

    Telegram writes either a string or a list of strings and formatting objects;
    links, mentions and code blocks each arrive as ``{"type": ..., "text": ...}``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(value)


def _epoch_text(value) -> str:
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return ""
    if stamp > 10_000_000_000:
        stamp //= 1000
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def parse_export(data, source: str = "") -> dict:
    """Parse a Telegram ``result.json`` export into chats and messages."""
    chats = []
    if isinstance(data, dict):
        raw_chats = data.get("chats")
        if isinstance(raw_chats, dict):
            raw_chats = raw_chats.get("list", [])
    elif isinstance(data, list):
        raw_chats = data
    else:
        raw_chats = []

    for raw in raw_chats or []:
        if not isinstance(raw, dict):
            continue
        chat_name = raw.get("name") or raw.get("title") or "Unnamed chat"
        chat_type = classify_chat(raw.get("type"))
        messages = []
        for item in raw.get("messages", []) or []:
            if not isinstance(item, dict):
                continue
            media_type = item.get("media_type") or ""
            text = _flatten_text(item.get("text"))
            kind = MEDIA_KIND.get(media_type, "text" if not media_type else "Other")
            messages.append({
                "chat": chat_name,
                "chat_id": raw.get("id"),
                "chat_type": chat_type,
                "message_id": item.get("id"),
                "sender": item.get("from") or ("You" if item.get("from_id") == "user0" else ""),
                "from_id": item.get("from_id", ""),
                "text": text,
                "kind": kind,
                "media_type": media_type,
                "file": item.get("file") or item.get("photo") or "",
                "mime_type": item.get("mime_type", ""),
                "forwarded_from": item.get("forwarded_from", ""),
                "reply_to": item.get("reply_to_message_id"),
                "edited": bool(item.get("edited")),
                "action": item.get("action", ""),
                "service": item.get("type") == "service",
                "date": str(item.get("date", "")),
                "date_unixtime": _epoch_text(item.get("date_unixtime")),
                "source": source,
            })
        chats.append({
            "id": raw.get("id"),
            "name": chat_name,
            "type": chat_type,
            "raw_type": raw.get("type", ""),
            "message_count": len(messages),
            "messages": messages,
            "source": source,
        })

    all_messages = [m for chat in chats for m in chat["messages"]]
    counts = {}
    for chat in chats:
        counts[chat["type"]] = counts.get(chat["type"], 0) + 1
    return {
        "chats": chats,
        "messages": all_messages,
        "stats": {
            "chats": len(chats),
            "messages": len(all_messages),
            "groups": counts.get("Group", 0),
            "channels": counts.get("Channel", 0),
            "private_chats": counts.get("Private chat", 0),
            "media": sum(1 for m in all_messages
                         if m["kind"] not in ("text", "Other")),
            "service": sum(1 for m in all_messages if m["service"]),
        },
    }


def read_export(path) -> dict | None:
    """Load and parse an export file, or None if it is not one."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, (dict, list)):
        return None
    if isinstance(data, dict) and "chats" not in data:
        return None
    return parse_export(data, source=str(path))


# --------------------------------------------------------------------------
# cache4.db, when a copy is reachable
# --------------------------------------------------------------------------

def cache_messages(db_path) -> dict:
    """Read a plain ``cache4.db`` (rooted devices or an examiner's copy)."""
    import sqlite3

    path = str(db_path)
    result = {"messages": [], "dialogs": [], "readable": False, "schema": ""}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        result["error"] = str(exc)
        return result
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            result["error"] = "not a readable SQLite database"
            return result
        result["readable"] = True
        table = "messages_v2" if "messages_v2" in tables else (
            "messages" if "messages" in tables else "")
        if table:
            # Telegram has shipped several column sets over the years and a
            # handset may be on any of them. Read what is actually there rather
            # than assuming a shape and losing the whole table to a bad SELECT.
            columns = {row[1] for row in conn.execute(
                f"PRAGMA table_info({table})")}
            wanted = [c for c in
                      ("mid", "uid", "message", "date", "out", "media",
                       "reply_to_mid") if c in columns]
            if "date" in columns:
                sql = (f"SELECT {', '.join(wanted)} FROM {table}"
                       " ORDER BY date ASC")
            else:
                sql = f"SELECT {', '.join(wanted)} FROM {table}"
            rows = conn.execute(sql).fetchall()
            result["schema"] = table
            result["columns"] = wanted
        else:
            rows = []
        for row in rows:
            message = dict(row)
            result["messages"].append({
                "chat": str(message.get("uid", "")),
                "sender": "me" if message.get("out") else "",
                "from_id": str(message.get("uid", "")),
                "message_id": message.get("mid"),
                "text": message.get("message") or "",
                "kind": MEDIA_KIND.get(str(message.get("media") or ""), "text"),
                "date": _epoch_text(message.get("date")),
                "date_unixtime": _epoch_text(message.get("date")),
                "source": path,
                "service": False,
            })
    except sqlite3.Error as exc:
        result["error"] = str(exc)
    finally:
        conn.close()
    return result


# --------------------------------------------------------------------------
# notifications
# --------------------------------------------------------------------------

_NOTIF_BLOCK = None


def parse_notifications(dump: str) -> list[dict]:
    """Extract Telegram previews from ``dumpsys notification --noredact``."""
    global _NOTIF_BLOCK
    import re
    if _NOTIF_BLOCK is None:
        _NOTIF_BLOCK = re.compile(r"NotificationRecord\{")
    records = []
    for block in _NOTIF_BLOCK.split(dump or "")[1:]:
        if PACKAGE not in block:
            continue
        title = text = ""
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("android.title=String ("):
                title = line.split("String (", 1)[1].rsplit(")", 1)[0]
            elif line.startswith("android.text=String ("):
                text = line.split("String (", 1)[1].rsplit(")", 1)[0]
        posted = re.search(r"postTime=(\d{13})", block)
        if title or text:
            records.append({
                "chat": title or "Telegram",
                "sender": title or "Telegram",
                "text": text,
                "chat_type": "Notification",
                "date": _epoch_text(posted.group(1)) if posted else "",
                "source": "dumpsys notification --noredact",
                "kind": "notification",
                "service": False,
            })
    return records


# --------------------------------------------------------------------------
# device collection
# --------------------------------------------------------------------------

def _find(serial: str, root: str, extra: str = "", timeout: int = 90,
          limit: int = 20000) -> list[dict]:
    okay, output = adb.adb_shell(
        serial, f"find {shlex.quote(root)} -type f {extra} 2>/dev/null",
        timeout=timeout)
    if not okay or not output:
        return []
    files = []
    for line in output.splitlines():
        line = line.strip()
        if not line or "Permission denied" in line:
            continue
        files.append({"path": line, "name": Path(line).name,
                      "extension": Path(line).suffix.lower()})
        if len(files) >= limit:
            break
    return files


def classify_media(files: list[dict]) -> list[dict]:
    media = []
    for entry in files:
        kind = MEDIA_EXTENSIONS.get(entry.get("extension", ""))
        if kind:
            media.append({**entry, "type": kind})
    return media


def find_exports(serial: str, max_depth: int = 5) -> list[str]:
    """Locate ``result.json`` files from an in-app export."""
    found = []
    okay, output = adb.adb_shell(
        serial,
        f"find /sdcard -maxdepth {max_depth} -type f -name 'result.json'"
        " 2>/dev/null | head -20", timeout=120)
    if not okay or not output:
        return found
    for line in output.splitlines():
        line = line.strip()
        if line and "Permission denied" not in line:
            found.append(line)
    return found


def collect(serial: str, workdir, pull_exports: bool = True) -> dict:
    """Gather Telegram artefacts the unrooted handset exposes."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    result = {"chats": [], "messages": [], "media": [], "notifications": [],
              "notes": [], "stats": {}}

    files: list[dict] = []
    for root in ROOTS:
        files.extend(_find(serial, root))
    result["media"] = classify_media(files)

    databases = [entry for entry in files
                 if entry["extension"] in (".db", ".sqlite")]
    for index, entry in enumerate(databases[:4]):
        local = workdir / f"tg_{index}_{entry['name']}"
        if not adb.pull_file(serial, entry["path"], str(local), timeout=180):
            continue
        parsed = cache_messages(local)
        if parsed.get("readable") and parsed["messages"]:
            result["messages"].extend(parsed["messages"])
            result["notes"].append(
                f"Parsed {entry['name']} ({parsed['schema']}, "
                f"{len(parsed['messages'])} messages)")

    if pull_exports:
        for index, remote in enumerate(find_exports(serial)[:10]):
            local = workdir / f"export_{index}.json"
            if not adb.pull_file(serial, remote, str(local), timeout=240):
                continue
            parsed = read_export(local)
            if not parsed:
                result["notes"].append(
                    f"{Path(remote).name} was pulled from {Path(remote).parent} "
                    "but did not parse as a Telegram export")
                continue
            result["chats"].extend(parsed["chats"])
            result["messages"].extend(parsed["messages"])
            for chat in parsed["chats"]:
                for message in chat["messages"]:
                    message["export_dir"] = str(Path(remote).parent)
            result["notes"].append(
                f"Read export at {Path(remote).parent}: "
                f"{parsed['stats']['chats']} chats, "
                f"{parsed['stats']['messages']} messages")

    okay, dump = adb.adb_shell(serial, "dumpsys notification --noredact 2>/dev/null",
                               timeout=60)
    if okay and dump:
        result["notifications"] = parse_notifications(dump)
        if result["notifications"]:
            result["notes"].append(
                f"{len(result['notifications'])} Telegram notifications in the dump")

    if not result["chats"] and not result["messages"]:
        result["notes"].append(
            "No Telegram export was found. Settings → Advanced → Export chat "
            "history writes result.json to shared storage; the live cache4.db "
            "is app-private and needs root. Media cache is still listed.")

    groups = sum(1 for c in result["chats"] if c["type"] == "Group")
    channels = sum(1 for c in result["chats"] if c["type"] == "Channel")
    result["stats"] = {
        "chats": len(result["chats"]),
        "groups": groups,
        "channels": channels,
        "private_chats": sum(1 for c in result["chats"] if c["type"] == "Private chat"),
        "messages": len(result["messages"]),
        "media": len(result["media"]),
        "notifications": len(result["notifications"]),
    }
    return result
