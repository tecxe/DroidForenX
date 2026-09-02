"""SQLite database for standalone desktop forensic platform."""
import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

DB_PATH = Path.home() / ".droidforenx" / "droidforenx.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cases (
        id TEXT PRIMARY KEY,
        case_number TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        investigator TEXT DEFAULT '',
        agency TEXT DEFAULT '',
        description TEXT DEFAULT '',
        status TEXT DEFAULT 'open',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        serial_number TEXT NOT NULL,
        manufacturer TEXT DEFAULT '',
        model TEXT DEFAULT '',
        android_version TEXT DEFAULT '',
        api_level INTEGER DEFAULT 0,
        security_patch TEXT DEFAULT '',
        encryption_status TEXT DEFAULT '',
        bootloader_state TEXT DEFAULT '',
        sim_info TEXT DEFAULT '{}',
        raw_props TEXT DEFAULT '{}',
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS forensic_sessions (
        id TEXT PRIMARY KEY,
        case_id TEXT DEFAULT '',
        device_id TEXT REFERENCES devices(id),
        status TEXT DEFAULT 'initialized',
        current_stage INTEGER DEFAULT 0,
        operator TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS isolation_actions (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        action_type TEXT NOT NULL,
        command_executed TEXT DEFAULT '',
        output TEXT DEFAULT '',
        success INTEGER DEFAULT 0,
        performed_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS network_state (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        airplane_mode INTEGER DEFAULT 0,
        wifi_enabled INTEGER DEFAULT 1,
        wifi_ssid TEXT DEFAULT '',
        bluetooth_enabled INTEGER DEFAULT 1,
        nfc_enabled INTEGER DEFAULT 0,
        mobile_data_enabled INTEGER DEFAULT 1,
        usb_debugging INTEGER DEFAULT 0,
        captured_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS device_identifications (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        manufacturer TEXT DEFAULT '',
        model TEXT DEFAULT '',
        device_name TEXT DEFAULT '',
        android_version TEXT DEFAULT '',
        api_level INTEGER DEFAULT 0,
        security_patch TEXT DEFAULT '',
        build_fingerprint TEXT DEFAULT '',
        kernel_version TEXT DEFAULT '',
        encryption_status TEXT DEFAULT '',
        lock_type TEXT DEFAULT '',
        storage_total_bytes INTEGER DEFAULT 0,
        storage_free_bytes INTEGER DEFAULT 0,
        sim_count INTEGER DEFAULT 0,
        identified_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS acquisition_profiles (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        primary_mode TEXT DEFAULT 'logical',
        hash_algorithm TEXT DEFAULT 'sha256',
        status TEXT DEFAULT 'pending',
        total_files INTEGER DEFAULT 0,
        total_bytes INTEGER DEFAULT 0,
        image_format TEXT DEFAULT '',
        image_hash_sha256 TEXT DEFAULT '',
        started_at TEXT,
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS examination_results (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        analysis_type TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        findings TEXT DEFAULT '{}',
        summary TEXT DEFAULT '',
        item_count INTEGER DEFAULT 0,
        tools_used TEXT DEFAULT '[]',
        completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS forensic_reports (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        title TEXT DEFAULT '',
        format TEXT DEFAULT 'pdf',
        case_number TEXT DEFAULT '',
        examiner_name TEXT DEFAULT '',
        examiner_badge TEXT DEFAULT '',
        examiner_organization TEXT DEFAULT '',
        executive_summary TEXT DEFAULT '',
        methodology_description TEXT DEFAULT '',
        chain_of_custody_summary TEXT DEFAULT '',
        findings_summary TEXT DEFAULT '',
        conclusions TEXT DEFAULT '',
        recommendations TEXT DEFAULT '',
        disclaimer TEXT DEFAULT '',
        file_path TEXT DEFAULT '',
        file_size_bytes INTEGER DEFAULT 0,
        report_hash_sha256 TEXT DEFAULT '',
        status TEXT DEFAULT 'draft',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sim_cards (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        slot INTEGER DEFAULT 1,
        status TEXT DEFAULT 'unknown',
        imsi TEXT DEFAULT '',
        iccid TEXT DEFAULT '',
        msisdn TEXT DEFAULT '',
        carrier TEXT DEFAULT '',
        country TEXT DEFAULT '',
        extracted_at TEXT
    );

    CREATE TABLE IF NOT EXISTS sim_contacts (
        id TEXT PRIMARY KEY,
        sim_id TEXT NOT NULL REFERENCES sim_cards(id),
        name TEXT DEFAULT '',
        phone_number TEXT DEFAULT '',
        email TEXT DEFAULT '',
        adn_index INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS sim_messages (
        id TEXT PRIMARY KEY,
        sim_id TEXT NOT NULL REFERENCES sim_cards(id),
        sender TEXT DEFAULT '',
        body TEXT DEFAULT '',
        timestamp TEXT DEFAULT '',
        status TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS disk_images (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        image_path TEXT DEFAULT '',
        image_format TEXT DEFAULT 'dd',
        hash_algorithm TEXT DEFAULT 'sha256',
        hash_value TEXT DEFAULT '',
        size_bytes INTEGER DEFAULT 0,
        partition_name TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS evidence_artifacts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES forensic_sessions(id),
        artifact_id TEXT UNIQUE,
        name TEXT NOT NULL,
        source TEXT DEFAULT '',
        category TEXT DEFAULT '',
        mime_type TEXT DEFAULT '',
        file_size INTEGER DEFAULT 0,
        file_path TEXT DEFAULT '',
        sha256_hash TEXT DEFAULT '',
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        action TEXT NOT NULL,
        actor TEXT DEFAULT 'system',
        target TEXT DEFAULT '',
        details TEXT DEFAULT ''
    );
    """)
    conn.commit()
    conn.close()


def audit_log(action: str, actor: str = "system", target: str = "", details: str = ""):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (timestamp, action, actor, target, details) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), action, actor, target, details),
    )
    conn.commit()
    conn.close()
