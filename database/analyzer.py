"""High-level SQLite analysis: live schema/content plus deleted-remains data.

Combines two sources of truth:
  * stdlib ``sqlite3`` (read-only) -> live tables, columns, row counts, schema
  * the file-format parser (``sqlite_parse``) -> deleted-data assessment

The two are reported separately so the analyst can always tell what is live
content versus what *may* remain in unallocated space.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .sqlite_parse import assess_database, DatabaseAssessment

logger = logging.getLogger(__name__)


@dataclass
class TableInfo:
    name: str
    sql: Optional[str]
    row_count: int
    columns: List[Dict] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AnalyzeResult:
    path: str
    is_sqlite: bool = False
    error: Optional[str] = None
    tables: List[TableInfo] = field(default_factory=list)
    deleted: Optional[DatabaseAssessment] = None

    @property
    def has_live_tables(self) -> bool:
        return bool(self.tables)

    @property
    def has_deleted_remains(self) -> bool:
        return bool(self.deleted and self.deleted.has_deleted_remains)


def _connect_readonly(path: Path):
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list_tables(conn) -> List[Tuple[str, Optional[str]]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [(r["name"], r["sql"]) for r in rows]


def _table_columns(conn, table: str) -> List[Dict]:
    try:
        rows = conn.execute(f"PRAGMA table_info({_qs(table)})").fetchall()
        return [{"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"]),
                 "pk": r["pk"]} for r in rows]
    except sqlite3.Error:
        return []


def _qs(name: str) -> str:
    """Quote an identifier safely."""
    return '"' + name.replace('"', '""') + '"'


def analyze(file_path) -> AnalyzeResult:
    """Analyze a file as a SQLite database (live schema + deleted-remains)."""
    path = Path(file_path)
    result = AnalyzeResult(path=str(path))

    try:
        result.deleted = assess_database(path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("deleted-data assessment failed: %s", exc)
        result.deleted = None

    # Live SQLite inspection.
    try:
        conn = _connect_readonly(path)
        try:
            for name, sql in _list_tables(conn):
                info = TableInfo(name=name, sql=sql, row_count=0)
                try:
                    info.row_count = conn.execute(f"SELECT COUNT(*) FROM {_qs(name)}").fetchone()[0]
                except sqlite3.Error as exc:
                    info.error = str(exc)
                info.columns = _table_columns(conn, name)
                result.tables.append(info)
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        result.error = str(exc)
        result.is_sqlite = False
        # If the header was valid but live inspection failed (e.g. corrupt live
        # pages), record that honestly.
        if result.deleted and result.deleted.header_valid:
            result.is_sqlite = True
        return result

    result.is_sqlite = bool(result.deleted and result.deleted.header_valid) or bool(result.tables)
    return result
