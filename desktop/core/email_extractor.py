"""Email Extraction — Gmail content provider, Outlook database, and IMAP cache parsing."""

import subprocess
import sqlite3
import re
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

GMAIL_AUTHORITY = "content://com.google.android.gm/msgs"
GMAIL_CONVERSATIONS = "content://com.google.android.gm/conversations"
OUTLOOK_DB_PATHS = [
    "/data/data/com.microsoft.office.outlook/databases/OutlookDatabase.db",
    "/data/user/0/com.microsoft.office.outlook/databases/OutlookDatabase.db",
]


def extract_gmail_emails(serial: str, adb_path: str = "adb", max_emails: int = 5000) -> list:
    """Extract emails from Gmail via content provider dumpsys."""
    emails = []

    # Method 1: Dumpsys notification to get recent email subjects/senders
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "dumpsys", "notification"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode == 0 and result.stdout:
            current = {}
            for line in result.stdout.splitlines():
                line = line.strip()
                if "pkg=com.google.android.gm" in line:
                    if current:
                        emails.append(current)
                    current = {"source": "notification", "package": "com.google.android.gm"}
                if current:
                    if line.startswith("title="):
                        current["subject"] = line.split("=", 1)[1].strip('"')
                    elif line.startswith("text="):
                        current["preview"] = line.split("=", 1)[1].strip('"')
                    elif line.startswith("when="):
                        current["timestamp_raw"] = line.split("=", 1)[1].strip()
            if current:
                emails.append(current)
    except Exception as e:
        logger.debug(f"Gmail notification dump failed: {e}")

    # Method 2: Gmail labels and accounts via content provider
    for query in [
        "content query --uri content://com.google.android.gm/provider --projection _id:subject:from:to:read",
    ]:
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell"] + query.split(),
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0 and result.stdout and "No result" not in result.stdout:
                for line in result.stdout.splitlines():
                    if line.startswith("Row:"):
                        email = {}
                        for field in ["_id", "subject", "from", "to", "read", "snippet", "dateSentMs", "body"]:
                            m = re.search(rf"{field}=([^,)]+)", line)
                            if m:
                                email[field] = m.group(1).strip().strip('"')
                        if email:
                            emails.append(email)
        except Exception:
            pass

    # Method 3: Gmail internal storage scan
    gmail_paths = [
        "/data/data/com.google.android.gm/databases/",
        "/data/user/0/com.google.android.gm/databases/",
    ]
    for db_path in gmail_paths:
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", "ls", db_path],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0 and result.stdout:
                for fname in result.stdout.splitlines():
                    fname = fname.strip()
                    if fname.endswith(".db") and "mail" in fname.lower():
                        logger.info(f"Found Gmail DB: {fname}")
        except Exception:
            pass

    return emails[:max_emails]


def extract_outlook_emails(serial: str, adb_path: str = "adb",
                           evidence_root: Optional[Path] = None) -> list:
    """Extract Outlook emails from local database."""
    emails = []

    for remote_db in OUTLOOK_DB_PATHS:
        local_db = None
        try:
            if evidence_root:
                local_db = str(evidence_root / "emails" / "outlook.db")
                Path(local_db).parent.mkdir(parents=True, exist_ok=True)
            else:
                import tempfile
                local_db = tempfile.mktemp(suffix=".db")

            result = subprocess.run(
                [adb_path, "-s", serial, "shell", "cat", remote_db],
                capture_output=True, timeout=30,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0 and result.stdout:
                with open(local_db, "wb") as f:
                    f.write(result.stdout)

                with sqlite3.connect(local_db, timeout=10) as conn:
                    tables = [r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()]

                    email_tables = [t for t in tables if any(
                        kw in t.lower() for kw in ["message", "email", "mail", "inbox"]
                    )]

                    for table in email_tables:
                        try:
                            rows = conn.execute(f"SELECT * FROM {table} LIMIT 500").fetchall()
                            cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 1").description]
                            for row in rows:
                                email = dict(zip(cols, row))
                                emails.append(email)
                        except Exception:
                            pass
        except Exception as e:
            logger.debug(f"Outlook extraction failed for {remote_db}: {e}")
        finally:
            if local_db and Path(local_db).exists():
                Path(local_db).unlink(missing_ok=True)

    return emails


def extract_email_accounts(serial: str, adb_path: str = "adb") -> list:
    """Discover all email accounts on the device via account manager."""
    accounts = []

    # Account manager dump
    try:
        result = subprocess.run(
            [adb_path, "-s", serial, "shell", "dumpsys", "account"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode == 0 and result.stdout:
            for line in result.stdout.splitlines():
                if "Account {" in line:
                    m = re.search(r"name=([^,}]+).*type=([^,}]+)", line)
                    if m:
                        accounts.append({
                            "name": m.group(1).strip(),
                            "type": m.group(2).strip(),
                        })
                elif "@" in line and ("com.google" in line or "com.microsoft" in line
                                       or "com.imap" in line or "com.pop3" in line):
                    accounts.append({"raw": line.strip()})
    except Exception:
        pass

    # Accounts database
    db_paths = [
        "/data/system_de/0/accounts_de.db",
        "/data/system_de/0/accounts_ce.db",
        "/data/system/users/0/accounts_ce.db",
    ]
    for db_path in db_paths:
        try:
            result = subprocess.run(
                [adb_path, "-s", serial, "shell", "cat", db_path],
                capture_output=True, timeout=10,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode == 0 and result.stdout:
                import tempfile
                local = tempfile.mktemp(suffix=".db")
                with open(local, "wb") as f:
                    f.write(result.stdout)
                with sqlite3.connect(local, timeout=10) as conn:
                    rows = conn.execute(
                        "SELECT name, type FROM accounts"
                    ).fetchall()
                    for name, atype in rows:
                        accounts.append({"name": name, "type": atype, "source": db_path})
                Path(local).unlink(missing_ok=True)
        except Exception:
            pass

    return accounts
