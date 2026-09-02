"""Chain of Custody Logger — immutable audit trail for evidence handling."""

import sqlite3
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class CustodyLogger:
    """Logs every evidence access, transfer, and modification event."""

    def __init__(self, db_path: str = "custody.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS custody_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    details TEXT,
                    sha256_before TEXT,
                    sha256_after TEXT,
                    integrity_verified INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS custody_chain (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    current_custodian TEXT NOT NULL,
                    location TEXT,
                    last_verified TEXT
                )
            """)

    def log_access(self, case_id: str, evidence_id: str, operator: str,
                   action: str, details: str = "",
                   sha256_before: str = "", sha256_after: str = "") -> int:
        timestamp = datetime.now(timezone.utc).isoformat()
        integrity = 1 if sha256_before and sha256_before == sha256_after else 0
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                """INSERT INTO custody_log
                   (case_id, evidence_id, action, operator, timestamp,
                    details, sha256_before, sha256_after, integrity_verified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (case_id, evidence_id, action, operator, timestamp,
                 details, sha256_before, sha256_after, integrity)
            )
            return cursor.lastrowid

    def verify_integrity(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def get_log(self, case_id: str, evidence_id: str = "") -> list:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            if evidence_id:
                rows = conn.execute(
                    "SELECT * FROM custody_log WHERE case_id=? AND evidence_id=? ORDER BY timestamp",
                    (case_id, evidence_id)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM custody_log WHERE case_id=? ORDER BY timestamp",
                    (case_id,)
                ).fetchall()
        return rows

    def export_json(self, case_id: str, output_path: str):
        logs = self.get_log(case_id)
        data = []
        for row in logs:
            data.append({
                "id": row[0], "case_id": row[1], "evidence_id": row[2],
                "action": row[3], "operator": row[4], "timestamp": row[5],
                "details": row[6], "sha256_before": row[7],
                "sha256_after": row[8], "integrity_verified": bool(row[9])
            })
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
