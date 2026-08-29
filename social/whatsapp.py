"""WhatsApp database artifact extraction.

Operates on an acquired WhatsApp SQLite database (for example ``msgstore.db``
or ``wa.db``) that the analyst has pointed the tool at. The analyzer inspects
the *actual* schema present and extracts only the fields that exist, so tables
that differ across WhatsApp versions yield a graceful, honest result rather
than fabricated columns.

The extraction is a straight read of committed rows. Deleted-recovery is the
separate concern of the SQLite/deleted layers; here we report live message
content and, when a ``deleted`` flag column exists, flag it as such.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import sqlite3

logger = logging.getLogger(__name__)


@dataclass
class MessageRecord:
    jid: str
    timestamp: Optional[int]
    from_me: Optional[int]
    message_type: Optional[str]
    body: Optional[str]
    deleted: Optional[bool]
    extra: Dict = field(default_factory=dict)


@dataclass
class WhatsappExtraction:
    file_path: str
    is_whatsapp_db: bool = False
    messages: List[MessageRecord] = field(default_factory=list)
    contacts: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    tables_found: List[str] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def has_data(self) -> bool:
        return bool(self.messages or self.contacts)


def _connect(path: Path):
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r["name"] for r in rows]


def _cols(conn, table: str) -> List[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34)+chr(34))}")').fetchall()
        return [r["name"] for r in rows]
    except sqlite3.Error:
        return []


def _is_whatsapp(conn, tables) -> bool:
    return "messages" in tables or "wa_contacts" in tables or "chat_view" in tables


def _extract_messages(conn, tables) -> List[MessageRecord]:
    if "messages" not in tables:
        return []
    cols = set(_cols(conn, "messages"))
    required = {"id", "key_remote_jid", "timestamp"}
    if not required.issubset(cols):
        # Some versions drop these; only proceed if we have the essential keys.
        if not ({"key_remote_jid", "timestamp"} & cols):
            return []

    select_fields = []
    if "key_remote_jid" in cols:
        select_fields.append("key_remote_jid")
    if "timestamp" in cols:
        select_fields.append("timestamp")
    if "from_me" in cols:
        select_fields.append("from_me")
    if "message_type" in cols:
        select_fields.append("message_type")
    if "data" in cols:
        select_fields.append("data")
    if "deleted" in cols:
        select_fields.append("deleted")

    sql = "SELECT " + ", ".join(select_fields) + " FROM messages ORDER BY timestamp ASC LIMIT 20000"
    records = []
    try:
        for row in conn.execute(sql):
            rec = MessageRecord(
                jid=row["key_remote_jid"] if "key_remote_jid" in cols else "",
                timestamp=row["timestamp"] if "timestamp" in cols else None,
                from_me=row["from_me"] if "from_me" in cols else None,
                message_type=str(row["message_type"]) if "message_type" in cols else None,
                body=row["data"] if "data" in cols else None,
                deleted=bool(row["deleted"]) if "deleted" in cols else None,
            )
            records.append(rec)
    except sqlite3.Error as exc:
        logger.debug("message extraction failed: %s", exc)
    return records


def _extract_contacts(conn, tables) -> List[Dict]:
    if "wa_contacts" not in tables:
        return []
    cols = set(_cols(conn, "wa_contacts"))
    if "jid" not in cols:
        return []
    fields = []
    for f in ("jid", "display_name", "wa_name", "phone"):
        if f in cols:
            fields.append(f)
    sql = "SELECT " + ", ".join(fields) + " FROM wa_contacts ORDER BY jid LIMIT 20000"
    out = []
    try:
        for row in conn.execute(sql):
            out.append({f: row[f] for f in fields})
    except sqlite3.Error as exc:
        logger.debug("contact extraction failed: %s", exc)
    return out


def extract(file_path) -> WhatsappExtraction:
    path = Path(file_path)
    result = WhatsappExtraction(file_path=str(path))
    if not path.exists():
        result.error = "file not found"
        return result
    try:
        conn = _connect(path)
        try:
            tables = _table_names(conn)
            result.tables_found = tables
            if not _is_whatsapp(conn, tables):
                return result
            result.is_whatsapp_db = True
            result.messages = _extract_messages(conn, tables)
            result.contacts = _extract_contacts(conn, tables)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        result.error = str(exc)
    return result
