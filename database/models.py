"""Database Models — schema definitions for cases, evidence, and audit trails."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_number TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    investigator TEXT,
    agency TEXT,
    status TEXT DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    evidence_number TEXT UNIQUE NOT NULL,
    device_model TEXT,
    serial_number TEXT,
    imei TEXT,
    storage_size TEXT,
    acquisition_status TEXT DEFAULT 'pending',
    sha256_hash TEXT,
    acquisition_date TEXT,
    notes TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,
    action TEXT NOT NULL,
    operator TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    details TEXT,
    ip_address TEXT
);

CREATE TABLE IF NOT EXISTS examination_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER,
    evidence_id INTEGER,
    module_name TEXT NOT NULL,
    result_data TEXT,
    artifact_count INTEGER DEFAULT 0,
    execution_time_ms INTEGER,
    completed_at TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);
"""


class DatabaseManager:
    """SQLite database manager with connection pooling and timeout handling."""

    def __init__(self, db_path: str = "droidforenx.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.executescript(DB_SCHEMA)

    def create_case(self, case_number: str, title: str, description: str = "",
                    investigator: str = "", agency: str = "") -> int:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                """INSERT INTO cases (case_number, title, description,
                   investigator, agency, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (case_number, title, description, investigator, agency, now)
            )
            return cursor.lastrowid

    def log_audit(self, action: str, operator: str, details: str = "",
                  case_id: int = None):
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """INSERT INTO audit_log (case_id, action, operator, timestamp, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (case_id, action, operator, now, details)
            )

    def get_cases(self) -> list:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            return conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
