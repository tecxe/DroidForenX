"""Stage 3: Data Acquisition — full forensic extraction."""
import uuid
import hashlib
import re
import json
import time
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QComboBox, QGroupBox, QGridLayout, QProgressBar,
                              QCheckBox, QScrollArea, QMessageBox)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSignal as Signal
from ..widgets.log_console import LogConsole
from ..widgets.data_table import DataTable
from ..widgets.device_status_bar import DeviceStatusBar
from ..widgets.scroll_page import enable_page_scroll
from ...core import adb_manager as adb
from ...core.database import get_db, audit_log
from ...core.email_extractor import extract_gmail_emails, extract_outlook_emails, extract_email_accounts
from ...core.doc_metadata import extract_all_metadata

try:
    from ....custody.custody_logger import CustodyLogger
    _CUSTODY = CustodyLogger()
except Exception:
    _CUSTODY = None


def _sha256(filepath: str) -> str:
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _store_artifact(session_id: str, name: str, source: str, category: str,
                    file_path: str, file_size: int, sha256: str, metadata: dict = None):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO evidence_artifacts (id, session_id, artifact_id, name, source, category, "
            "mime_type, file_size, file_path, sha256_hash, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), session_id, f"art_{uuid.uuid4().hex[:12]}",
                name, source, category, "", file_size, file_path, sha256,
                json.dumps(metadata or {}),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


class AcquisitionWorker(QThread):
    progress = Signal(str, str)
    log = Signal(str, str)
    finished = Signal(dict)
    file_pulled = Signal(str, int)
    phase_changed = Signal(str)

    def __init__(self, serial, level, case_id, hash_algo="sha256"):
        super().__init__()
        self.serial = serial
        self.level = level
        self.case_id = case_id
        self.hash_algo = hash_algo
        self._running = True
        self._session_id = ""

    def _log(self, msg, level="info"):
        self.log.emit(msg, level)

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            self._log(f"CRITICAL acquisition error: {e}", "error")
            self.finished.emit({"files": 0, "bytes": 0, "errors": 1, "phases": {}})

    def _run_inner(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        evidence_root = Path.home() / ".droidforenx" / "evidence" / f"acq_{ts}_{self.serial[:8]}"
        evidence_root.mkdir(parents=True, exist_ok=True)

        self._create_session()
        audit_log("acquisition_started", "examiner", self.serial,
                  f"level={self.level}, hash={self.hash_algo}")

        results = {
            "files": 0, "bytes": 0, "errors": 0,
            "contacts": 0, "sms": 0, "calls": 0, "calendar": 0,
            "media_files": 0, "documents": 0, "databases": 0,
            "app_data": 0, "browser": 0, "device_info": 0,
            "full_storage_files": 0, "full_storage_bytes": 0,
            "app_data_deep_files": 0, "app_data_deep_bytes": 0,
            "recovered_files": 0, "recovered_bytes": 0,
            "backup_files": 0, "backup_bytes": 0,
            "partitions": 0, "signatures_found": 0,
            "emails": 0, "document_metadata": 0, "browser_bookmarks": 0,
            "phases": {},
        }

        # ── Phase 1: Device Properties & SIM ──
        self.phase_changed.emit("Device Properties")
        self._log("━━━ Phase 1: Device Properties & SIM ━━━", "info")
        self._extract_device_properties(evidence_root, results)

        # ── Phase 2: Logical — Contacts, SMS, Calls, Calendar ──
        self.phase_changed.emit("Logical Extraction")
        self._log("━━━ Phase 2: Logical Extraction (contacts, SMS, calls, calendar) ━━━", "info")
        self._extract_contacts(evidence_root, results)
        self._extract_sms(evidence_root, results)
        self._extract_call_log(evidence_root, results)
        self._extract_calendar(evidence_root, results)

        # ── Phase 3: Browser History ──
        if self.level >= 1:
            self.phase_changed.emit("Browser History")
            self._log("━━━ Phase 3: Browser History ━━━", "info")
            self._extract_browser_history(evidence_root, results)

        # ── Phase 4: App Databases ──
        if self.level >= 1:
            self.phase_changed.emit("App Databases")
            self._log("━━━ Phase 4: Application Databases ━━━", "info")
            self._extract_app_databases(evidence_root, results)

        # ── Phase 5: File System — Media, Documents, Downloads ──
        if self.level >= 2:
            self.phase_changed.emit("File System")
            self._log("━━━ Phase 5: File System Extraction ━━━", "info")
            self._extract_filesystem(evidence_root, results)

        # ── Phase 6: WhatsApp / Telegram / Social Data ──
        if self.level >= 2:
            self.phase_changed.emit("Social Apps")
            self._log("━━━ Phase 6: Social Application Data ━━━", "info")
            self._extract_social_apps(evidence_root, results)

        # ── Phase 7: Deleted / Trashed Files ──
        self.phase_changed.emit("Deleted Files")
        self._log("━━━ Phase 7: Deleted & Trashed Files ━━━", "info")
        self._extract_deleted_files(evidence_root, results)

        # ── Phase 8: Physical Image (if root) ──
        if self.level >= 3:
            self.phase_changed.emit("Physical Image")
            self._log("━━━ Phase 8: Physical Image Attempt ━━━", "info")
            self._extract_physical(evidence_root, results)

        # ── Phase 9: Full Storage Image ──
        if self.level >= 2:
            self.phase_changed.emit("Full Storage Image")
            self._log("━━━ Phase 9: Full Storage Image ━━━", "info")
            self._extract_full_storage(evidence_root, results)

        # ── Phase 10: App Data Deep Extraction ──
        if self.level >= 1:
            self.phase_changed.emit("App Data Deep Extraction")
            self._log("━━━ Phase 10: Application Data Deep Extraction ━━━", "info")
            self._extract_app_data_deep(evidence_root, results)

        # ── Phase 11: Deleted Data Recovery & File Carving ──
        if self.level >= 2:
            self.phase_changed.emit("Deleted Data Recovery")
            self._log("━━━ Phase 11: Deleted Data Recovery & File Carving ━━━", "info")
            self._extract_recovered_files(evidence_root, results)

        # ── Phase 12: Evidence Packaging & Verification ──
        self.phase_changed.emit("Evidence Packaging")
        self._log("━━━ Phase 12: Evidence Packaging & Verification ━━━", "info")
        self._package_evidence(evidence_root, results)

        # ── Phase 13: Email Extraction ──
        self.phase_changed.emit("Email Extraction")
        self._log("━━━ Phase 13: Email Extraction (Gmail, Outlook) ━━━", "info")
        self._extract_emails(evidence_root, results)

        # ── Phase 14: Document Metadata ──
        self.phase_changed.emit("Document Metadata")
        self._log("━━━ Phase 14: Document Metadata Extraction ━━━", "info")
        self._extract_document_metadata(evidence_root, results)

        # ── Phase 15: Chrome Bookmarks (non-root) ──
        self.phase_changed.emit("Chrome Bookmarks")
        self._log("━━━ Phase 15: Chrome Bookmarks (non-root) ━━━", "info")
        self._extract_chrome_bookmarks(evidence_root, results)

        # ── Chain of Custody Logging ──
        self._log("━━ Recording Chain of Custody ━━", "info")
        self._record_custody(results)

        # ── Write manifest ──
        self._write_manifest(evidence_root, results)

        self._complete_session(results)
        audit_log("acquisition_completed", "examiner", self.serial,
                  json.dumps({"files": results["files"], "bytes": results["bytes"]}))

        self._log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "info")
        self._log(f"ACQUISITION COMPLETE", "success")
        self._log(f"  Total files:      {results['files']}", "success")
        self._log(f"  Total size:       {results['bytes']:,} bytes ({results['bytes'] / (1024*1024):.1f} MB)", "success")
        self._log(f"  Contacts:         {results['contacts']}", "success")
        self._log(f"  SMS messages:     {results['sms']}", "success")
        self._log(f"  Call log entries:  {results['calls']}", "success")
        self._log(f"  Calendar events:  {results['calendar']}", "success")
        self._log(f"  Media files:      {results['media_files']}", "success")
        self._log(f"  Documents:        {results['documents']}", "success")
        self._log(f"  App databases:    {results['databases']}", "success")
        self._log(f"  Browser history:  {results['browser']}", "success")
        if results.get("full_storage_files"):
            self._log(f"  Full storage:     {results['full_storage_files']} files ({results['full_storage_bytes'] / (1024*1024):.1f} MB)", "success")
        if results.get("app_data_deep_files"):
            self._log(f"  App data deep:    {results['app_data_deep_files']} files ({results['app_data_deep_bytes'] / (1024*1024):.1f} MB)", "success")
        if results.get("backup_files"):
            self._log(f"  ADB backups:      {results['backup_files']} files ({results['backup_bytes'] / (1024*1024):.1f} MB)", "success")
        if results.get("recovered_files"):
            self._log(f"  Recovered files:  {results['recovered_files']} ({results['recovered_bytes'] / (1024*1024):.1f} MB)", "success")
        if results.get("emails"):
            self._log(f"  Emails:           {results['emails']}", "success")
        if results.get("document_metadata"):
            self._log(f"  Doc metadata:     {results['document_metadata']} documents", "success")
        if results.get("browser_bookmarks"):
            self._log(f"  Bookmarks:        {results['browser_bookmarks']}", "success")
        if results.get("signatures_found"):
            self._log(f"  Signatures found: {results['signatures_found']}", "success")
        if results.get("partitions"):
            self._log(f"  Partitions:       {results['partitions']}", "success")
        self._log(f"  Evidence root:    {evidence_root}", "success")
        self._log(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "success")

        self.finished.emit(results)

    def _create_session(self):
        try:
            self._session_id = str(uuid.uuid4())
            conn = get_db()
            conn.execute(
                "INSERT INTO forensic_sessions (id, case_id, status, current_stage, created_at, updated_at) "
                "VALUES (?, ?, 'acquiring', 3, ?, ?)",
                (self._session_id, self.case_id or "unknown",
                 datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            self._session_id = ""

    def _complete_session(self, results):
        if not self._session_id:
            return
        try:
            conn = get_db()
            conn.execute(
                "UPDATE forensic_sessions SET status='completed', current_stage=4, updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), self._session_id),
            )
            conn.execute(
                "INSERT INTO acquisition_profiles (id, session_id, primary_mode, hash_algorithm, status, "
                "total_files, total_bytes, started_at, completed_at) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
                (str(uuid.uuid4()), self._session_id, f"level_{self.level}", self.hash_algo,
                 results["files"], results["bytes"],
                 datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _hash_and_store(self, filepath: str, remote: str, category: str, evidence_root: Path):
        try:
            sz = Path(filepath).stat().st_size
        except OSError:
            sz = 0
        h = _sha256(filepath) if sz > 0 else ""
        rel = str(Path(filepath).relative_to(evidence_root))
        _store_artifact(self._session_id, Path(filepath).name, remote, category,
                        rel, sz, h)
        return sz, h

    # ── Phase 1: Device Properties ──

    def _extract_device_properties(self, evidence_root, results):
        self._log("Extracting device properties...", "info")
        info = adb.get_device_info(self.serial)
        props = {
            "serial": info.serial, "model": info.model, "manufacturer": info.manufacturer,
            "device_name": info.device_name, "android_version": info.android_version,
            "api_level": info.api_level, "security_patch": info.security_patch,
            "build_fingerprint": info.build_fingerprint, "kernel_version": info.kernel_version,
            "encryption_status": info.encryption_status, "bootloader_state": info.bootloader_state,
            "battery_level": info.battery_level, "battery_temp": info.battery_temp,
            "total_storage": info.total_storage, "free_storage": info.free_storage,
            "sim_state": info.sim_state,
        }
        props_path = evidence_root / "device" / "device_properties.json"
        props_path.parent.mkdir(parents=True, exist_ok=True)
        props_path.write_text(json.dumps(props, indent=2, default=str), encoding="utf-8")
        self._hash_and_store(str(props_path), "getprop", "device_properties", evidence_root)
        self._log(f"  Model: {info.manufacturer} {info.model} | Android {info.android_version} (API {info.api_level})", "info")
        self._log(f"  Storage: {info.total_storage / (1024*1024*1024):.1f} GB total, {info.free_storage / (1024*1024*1024):.1f} GB free", "info")
        results["device_info"] = 1
        results["files"] += 1
        results["bytes"] += props_path.stat().st_size

        # SIM info
        ok, sim_info = adb.adb_shell(self.serial, "dumpsys iphonesubinfo")
        if ok and sim_info:
            sim_path = evidence_root / "device" / "sim_info.txt"
            sim_path.write_text(sim_info, encoding="utf-8", errors="replace")
            self._hash_and_store(str(sim_path), "dumpsys iphonesubinfo", "sim_info", evidence_root)
            results["files"] += 1
            results["bytes"] += sim_path.stat().st_size

        # Full getprop dump
        ok, all_props = adb.adb_shell(self.serial, "getprop")
        if ok and all_props:
            prop_dump = evidence_root / "device" / "full_getprop.txt"
            prop_dump.write_text(all_props, encoding="utf-8", errors="replace")
            self._hash_and_store(str(prop_dump), "getprop", "device_properties", evidence_root)
            results["files"] += 1
            results["bytes"] += prop_dump.stat().st_size

        # Battery status
        ok, batt = adb.adb_shell(self.serial, "dumpsys battery")
        if ok and batt:
            batt_path = evidence_root / "device" / "battery_status.txt"
            batt_path.write_text(batt, encoding="utf-8", errors="replace")
            self._hash_and_store(str(batt_path), "dumpsys battery", "device_properties", evidence_root)
            results["files"] += 1
            results["bytes"] += batt_path.stat().st_size

    # ── Phase 2: Contacts, SMS, Calls, Calendar ──

    def _extract_contacts(self, evidence_root, results):
        self._log("Extracting contacts...", "info")
        data = adb.query_content(self.serial, "content://com.android.contacts/contacts")
        if "Row:" not in data:
            self._log("  No contacts found or access denied", "warning")
            return

        contacts = []
        for line in data.splitlines():
            if "Row:" not in line:
                continue
            c = {}
            for field in ["_id", "display_name", "starred", "times_contacted",
                          "last_time_contacted", "has_phone_number", "photo_uri",
                          "sort_key", "account_name", "account_type"]:
                m = re.search(rf"{field}=([^,)]+)", line)
                if m:
                    c[field] = m.group(1).strip()
            contacts.append(c)

        # Get phone numbers
        phone_data = adb.query_content(self.serial, "content://com.android.contacts/data/phones")
        phone_map = {}
        if "Row:" in phone_data:
            for line in phone_data.splitlines():
                if "Row:" not in line:
                    continue
                contact_id = m.group(1) if (m := re.search(r"contact_id=(\d+)", line)) else ""
                number = m.group(1) if (m := re.search(r"data1=([^,)]+)", line)) else ""
                if contact_id and number:
                    phone_map.setdefault(contact_id, []).append(number)

        # Get emails
        email_data = adb.query_content(self.serial, "content://com.android.contacts/data/emails")
        email_map = {}
        if "Row:" in email_data:
            for line in email_data.splitlines():
                if "Row:" not in line:
                    continue
                contact_id = m.group(1) if (m := re.search(r"contact_id=(\d+)", line)) else ""
                email = m.group(1) if (m := re.search(r"data1=([^,)]+)", line)) else ""
                if contact_id and email:
                    email_map.setdefault(contact_id, []).append(email)

        for c in contacts:
            cid = c.get("_id", "")
            c["phones"] = phone_map.get(cid, [])
            c["emails"] = email_map.get(cid, [])

        out_path = evidence_root / "logical" / "contacts.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(contacts, indent=2, default=str), encoding="utf-8")
        sz = self._hash_and_store(str(out_path), "content://com.android.contacts", "contacts", evidence_root)[0]
        results["contacts"] = len(contacts)
        results["files"] += 1
        results["bytes"] += sz
        self._log(f"  Extracted {len(contacts)} contacts", "success")

    def _extract_sms(self, evidence_root, results):
        self._log("Extracting SMS/MMS messages...", "info")
        data = adb.query_content(self.serial, "content://sms", sort="date DESC")
        if "Row:" not in data:
            self._log("  No SMS found or access denied", "warning")
            return

        messages = []
        for line in data.splitlines():
            if "Row:" not in line:
                continue
            msg = {}
            for field in ["_id", "address", "body", "date", "date_sent", "type", "read",
                          "seen", "service_center", "subject", "person", "person_id",
                          "error_code", "read_status", "delivery_status"]:
                m = re.search(rf"{field}=([^,)]+)", line)
                if m:
                    val = m.group(1).strip()
                    msg[field] = val
            if "date" in msg:
                try:
                    ts = int(msg["date"])
                    msg["date_readable"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pass
            messages.append(msg)

        out_path = evidence_root / "logical" / "sms_messages.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(messages, indent=2, default=str), encoding="utf-8")
        sz = self._hash_and_store(str(out_path), "content://sms", "sms_messages", evidence_root)[0]
        results["sms"] = len(messages)
        results["files"] += 1
        results["bytes"] += sz
        self._log(f"  Extracted {len(messages)} SMS messages", "success")

    def _extract_call_log(self, evidence_root, results):
        self._log("Extracting call log...", "info")
        data = adb.query_content(self.serial, "content://call_log/calls", sort="date DESC")
        if "Row:" not in data:
            self._log("  No call log found or access denied", "warning")
            return

        calls = []
        for line in data.splitlines():
            if "Row:" not in line:
                continue
            call = {}
            for field in ["_id", "number", "type", "date", "duration", "new",
                          "geocoded_location", "features", "add_for_all_users",
                          "calls_via_notification", "transcription"]:
                m = re.search(rf"{field}=([^,)]+)", line)
                if m:
                    call[field] = m.group(1).strip()
            if "date" in call:
                try:
                    ts = int(call["date"])
                    call["date_readable"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pass
            if "duration" in call:
                try:
                    secs = int(call["duration"])
                    call["duration_readable"] = f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s"
                except ValueError:
                    pass
            calls.append(call)

        out_path = evidence_root / "logical" / "call_log.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(calls, indent=2, default=str), encoding="utf-8")
        sz = self._hash_and_store(str(out_path), "content://call_log", "call_log", evidence_root)[0]
        results["calls"] = len(calls)
        results["files"] += 1
        results["bytes"] += sz
        self._log(f"  Extracted {len(calls)} call log entries", "success")

    def _extract_calendar(self, evidence_root, results):
        self._log("Extracting calendar events...", "info")
        data = adb.query_content(self.serial, "content://com.android.calendar/events", sort="dtstart DESC")
        if "Row:" not in data:
            self._log("  No calendar events found", "warning")
            return

        events = []
        for line in data.splitlines():
            if "Row:" not in line:
                continue
            ev = {}
            for field in ["_id", "title", "description", "eventLocation", "dtstart", "dtend",
                          "allDay", "rrule", "calendar_id", "organizer", "status", "eventTimezone"]:
                m = re.search(rf"{field}=([^,)]+)", line)
                if m:
                    ev[field] = m.group(1).strip()
            if "dtstart" in ev:
                try:
                    ts = int(ev["dtstart"])
                    ev["start_readable"] = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    pass
            events.append(ev)

        out_path = evidence_root / "logical" / "calendar_events.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(events, indent=2, default=str), encoding="utf-8")
        sz = self._hash_and_store(str(out_path), "content://calendar", "calendar", evidence_root)[0]
        results["calendar"] = len(events)
        results["files"] += 1
        results["bytes"] += sz
        self._log(f"  Extracted {len(events)} calendar events", "success")

    # ── Phase 3: Browser History ──

    def _extract_browser_history(self, evidence_root, results):
        self._log("Extracting browser history...", "info")
        browser_dir = evidence_root / "browser"
        browser_dir.mkdir(parents=True, exist_ok=True)
        count = 0

        # Chrome
        chrome_paths = [
            "/data/data/com.android.chrome/app_chrome/Default/History",
            "/data/user/0/com.android.chrome/app_chrome/Default/History",
        ]
        for remote_db in chrome_paths:
            local_db = str(browser_dir / "chrome_history.db")
            if adb.pull_file(self.serial, remote_db, local_db, timeout=30):
                try:
                    import sqlite3
                    conn = sqlite3.connect(local_db, timeout=10)
                    rows = conn.execute(
                        "SELECT url, title, visit_count, last_visit_time, typed_count "
                        "FROM urls ORDER BY last_visit_time DESC LIMIT 5000"
                    ).fetchall()
                    conn.close()
                    entries = [{"url": r[0], "title": r[1], "visits": r[2], "typed": r[4]} for r in rows]
                    out = browser_dir / "chrome_history.json"
                    out.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
                    self._hash_and_store(str(out), remote_db, "browser_history", evidence_root)
                    count += len(entries)
                    self._log(f"  Chrome: {len(entries)} history entries", "success")
                except Exception as e:
                    self._log(f"  Chrome DB parse error: {e}", "warning")
                finally:
                    Path(local_db).unlink(missing_ok=True)
                break

        # Firefox
        ff_remote = "/data/data/org.mozilla.firefox/files/mozilla/*.default*/places.sqlite"
        ok, ff_list = adb.adb_shell(self.serial, f"ls {ff_remote} 2>/dev/null")
        if ok and ff_list:
            for remote_ff in ff_list.splitlines():
                remote_ff = remote_ff.strip()
                if not remote_ff:
                    continue
                local_ff = str(browser_dir / "firefox_history.db")
                if adb.pull_file(self.serial, remote_ff, local_ff, timeout=30):
                    try:
                        import sqlite3
                        conn = sqlite3.connect(local_ff, timeout=10)
                        rows = conn.execute(
                            "SELECT url, title, visit_count, last_visit_date "
                            "FROM moz_places ORDER BY last_visit_date DESC LIMIT 5000"
                        ).fetchall()
                        conn.close()
                        entries = [{"url": r[0], "title": r[1], "visits": r[2]} for r in rows]
                        out = browser_dir / "firefox_history.json"
                        out.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
                        self._hash_and_store(str(out), remote_ff, "browser_history", evidence_root)
                        count += len(entries)
                        self._log(f"  Firefox: {len(entries)} history entries", "success")
                    except Exception as e:
                        self._log(f"  Firefox DB parse error: {e}", "warning")
                    finally:
                        Path(local_ff).unlink(missing_ok=True)
                    break

        results["browser"] = count
        results["files"] += count
        if count == 0:
            self._log("  No browser history accessible", "warning")

    # ── Phase 4: App Databases ──

    def _extract_app_databases(self, evidence_root, results):
        self._log("Extracting application databases...", "info")
        db_dir = evidence_root / "app_databases"
        db_dir.mkdir(parents=True, exist_ok=True)

        # Known app database paths (common apps in Ghana / TECNO devices)
        known_dbs = [
            # WhatsApp
            ("/data/data/com.whatsapp/databases/msgstore.db", "WhatsApp/msgstore.db"),
            ("/data/data/com.whatsapp/databases/wa.db", "WhatsApp/wa.db"),
            ("/data/data/com.whatsapp/databases/contacts2.db", "WhatsApp/contacts2.db"),
            ("/data/data/com.whatsapp/databases/chats.db", "WhatsApp/chats.db"),
            # Telegram
            ("/data/data/org.telegram.messenger/databases/cache4.db", "Telegram/cache4.db"),
            ("/data/data/org.telegram.messenger/databases/td.db", "Telegram/td.db"),
            # Facebook
            ("/data/data/com.facebook.katana/databases/friends.db", "Facebook/friends.db"),
            ("/data/data/com.facebook.orca/databases/threads_db2", "Messenger/threads_db2.db"),
            # Instagram
            ("/data/data/com.instagram.android/databases/direct_messages.db", "Instagram/dm.db"),
            # Twitter/X
            ("/data/data/com.twitter.android/databases/operations_db", "Twitter/operations.db"),
            # System SMS/contacts backup
            ("/data/data/com.android.providers.telephony/databases/mmssms.db", "System/mmssms.db"),
            ("/data/data/com.android.providers.contacts/databases/contacts2.db", "System/contacts2.db"),
            # Downloads / file manager
            ("/data/data/com.android.providers.downloads/databases/downloads.db", "System/downloads.db"),
        ]

        count = 0
        for remote_path, local_name in known_dbs:
            if not self._running:
                break
            local_path = str(db_dir / local_name)
            if adb.pull_file(self.serial, remote_path, local_path, timeout=30):
                sz = Path(local_path).stat().st_size if Path(local_path).exists() else 0
                if sz > 0:
                    h = self._hash_and_store(local_path, remote_path, "app_database", evidence_root)[1]
                    count += 1
                    results["databases"] += 1
                    results["files"] += 1
                    results["bytes"] += sz
                    self._log(f"  ✓ {local_name} ({sz:,} bytes)", "success")
                    self.file_pulled.emit(local_name, sz)
                else:
                    Path(local_path).unlink(missing_ok=True)
            self.progress.emit(str(count), str(len(known_dbs)))

        # Also try to find any other .db files in accessible app dirs
        self._log("  Scanning for additional app databases...", "info")
        ok, db_files = adb.adb_shell(self.serial,
            'find /data/data/ -maxdepth 4 -name "*.db" -size +0c 2>/dev/null | head -50', timeout=30)
        if ok and db_files:
            for remote_db in db_files.splitlines():
                remote_db = remote_db.strip()
                if not remote_db or "Permission denied" in remote_db:
                    continue
                if not self._running:
                    break
                safe_name = remote_db.replace("/", "_").lstrip("_")
                local_path = str(db_dir / "extra" / safe_name)
                if adb.pull_file(self.serial, remote_db, local_path, timeout=20):
                    sz = Path(local_path).stat().st_size if Path(local_path).exists() else 0
                    if sz > 0:
                        self._hash_and_store(local_path, remote_db, "app_database", evidence_root)
                        count += 1
                        results["databases"] += 1
                        results["files"] += 1
                        results["bytes"] += sz

        self._log(f"  Total databases extracted: {count}", "success" if count > 0 else "warning")

    # ── Phase 5: File System ──

    def _extract_filesystem(self, evidence_root, results):
        self._log("Extracting user file system (Android scoped storage aware)...", "info")
        fs_dir = evidence_root / "filesystem"
        fs_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Discover ALL accessible directories under /sdcard/
        self._log("  Discovering accessible directories...", "info")
        ok, listing = adb.adb_shell(self.serial,
            'find /sdcard/ -maxdepth 1 -type d 2>/dev/null', timeout=30)
        top_dirs = []
        if ok and listing:
            for line in listing.splitlines():
                d = line.strip()
                if d and d.startswith("/sdcard/") and d != "/sdcard/":
                    top_dirs.append(d)
        self._log(f"  Found {len(top_dirs)} top-level directories", "info")

        # Step 2: Pull each accessible directory individually
        total_pulled = 0
        for remote_dir in top_dirs:
            if not self._running:
                break
            dir_name = remote_dir.replace("/sdcard/", "").replace("/", "_")
            local_dir = str(fs_dir / dir_name)

            # Check if directory has content
            ok, ls_out = adb.adb_shell(self.serial, f'ls -1 "{remote_dir}" 2>/dev/null', timeout=10)
            if not ok or not ls_out or "No such file" in ls_out or "Permission denied" in ls_out:
                continue

            entries = [l.strip() for l in ls_out.splitlines() if l.strip()]
            if not entries:
                continue

            self.phase_changed.emit(f"FS: {dir_name}")
            self._log(f"  Pulling {remote_dir} ({len(entries)} items)...", "info")

            # Use longer timeout for larger directories
            timeout = 600 if len(entries) > 50 else 300
            count, bts = adb.pull_directory(self.serial, remote_dir, local_dir, timeout=timeout)
            if count > 0:
                total_pulled += count
                results["media_files"] += count
                results["files"] += count
                results["bytes"] += bts
                self._log(f"  OK {remote_dir}: {count} files ({bts / (1024*1024):.1f} MB)", "success")
                self.file_pulled.emit(dir_name, bts)
            else:
                self._log(f"  SKIP {remote_dir}: empty or access denied", "warning")

        self._log(f"  Filesystem total: {total_pulled} files", "success" if total_pulled > 0 else "warning")

    # ── Phase 6: Social Apps ──

    def _extract_social_apps(self, evidence_root, results):
        self._log("Extracting social application data (Android 11+ scoped storage)...", "info")
        social_dir = evidence_root / "social_apps"
        social_dir.mkdir(parents=True, exist_ok=True)

        # On Android 11+, WhatsApp/Telegram/etc data is in /sdcard/Android/data/{package}/
        # NOT in /sdcard/WhatsApp/ (that's blocked by scoped storage)
        social_packages = {
            "com.whatsapp": "WhatsApp",
            "org.telegram.messenger": "Telegram",
            "com.instagram.android": "Instagram",
            "com.twitter.android": "Twitter",
            "com.facebook.katana": "Facebook",
            "com.facebook.lite": "FacebookLite",
            "com.snapchat.android": "Snapchat",
            "com.mtn.momo": "MTNMoMo",
            "com.google.android.gm": "Gmail",
            "com.discord": "Discord",
            "com.spotify.music": "Spotify",
            "com.tiktok.ugc": "TikTok",
            "com.viber.voip": "Viber",
            "com.microsoft.teams": "Teams",
            "com.linkedin.android": "LinkedIn",
        }

        count = 0
        for package, name in social_packages.items():
            if not self._running:
                break

            # Try /sdcard/Android/data/{package}/
            remote_data = f"/sdcard/Android/data/{package}"
            ok, listing = adb.adb_shell(self.serial, f'ls -la "{remote_data}" 2>/dev/null', timeout=10)
            if not ok or not listing or "No such file" in listing:
                continue

            entries = [l.strip() for l in listing.splitlines() if l.strip()]
            if not entries:
                continue

            local_dir = str(social_dir / name)
            self.phase_changed.emit(f"Social: {name}")
            self._log(f"  Pulling {name} ({remote_data}) - {len(entries)} items...", "info")

            file_count, bts = adb.pull_directory(self.serial, remote_data, local_dir, timeout=600)
            if file_count > 0:
                count += file_count
                results["app_data"] += file_count
                results["files"] += file_count
                results["bytes"] += bts
                self._log(f"  OK {name}: {file_count} files ({bts / (1024*1024):.1f} MB)", "success")
                self.file_pulled.emit(name, bts)

            # Also try /sdcard/Android/media/{package}/
            remote_media = f"/sdcard/Android/media/{package}"
            ok2, listing2 = adb.adb_shell(self.serial, f'ls -la "{remote_media}" 2>/dev/null', timeout=10)
            if ok2 and listing2 and "No such file" not in listing2:
                entries2 = [l.strip() for l in listing2.splitlines() if l.strip()]
                if entries2:
                    local_media = str(social_dir / f"{name}_media")
                    self._log(f"  Pulling {name} media ({len(entries2)} items)...", "info")
                    c2, b2 = adb.pull_directory(self.serial, remote_media, local_media, timeout=600)
                    if c2 > 0:
                        count += c2
                        results["app_data"] += c2
                        results["files"] += c2
                        results["bytes"] += b2
                        self._log(f"  OK {name} media: {c2} files ({b2 / (1024*1024):.1f} MB)", "success")

        if count > 0:
            self._log(f"  Social apps total: {count} files", "success")
        else:
            self._log("  No social app data accessible via scoped storage", "warning")

    # ── Phase 7: Deleted Files ──

    def _extract_deleted_files(self, evidence_root, results):
        self._log("Scanning for deleted/trashed files...", "info")
        deleted_dir = evidence_root / "deleted_files"
        deleted_dir.mkdir(parents=True, exist_ok=True)
        count = 0

        # Trashed files
        ok, trashed = adb.adb_shell(self.serial,
            'find /sdcard/ -maxdepth 4 \\( -name ".trashed*" -o -name ".nomedia" '
            '-o -name "*.tmp" -o -name "*.bak" -o -name "*~" \\) -type f 2>/dev/null | head -200')
        if ok and trashed:
            trash_list = [l.strip() for l in trashed.splitlines() if l.strip()]
            self._log(f"  Found {len(trash_list)} trashed/temporary files", "info")
            for remote in trash_list:
                if not self._running:
                    break
                safe = remote.replace("/", "_").lstrip("_")
                local = str(deleted_dir / safe)
                if adb.pull_file(self.serial, remote, local, timeout=15):
                    sz = Path(local).stat().st_size if Path(local).exists() else 0
                    if sz > 0:
                        self._hash_and_store(local, remote, "deleted_file", evidence_root)
                        count += 1
                        results["files"] += 1
                        results["bytes"] += sz

        # SQLite WAL/SHM files (potential deleted data)
        ok, wal_files = adb.adb_shell(self.serial,
            'find /data/data/ -maxdepth 4 \\( -name "*.db-wal" -o -name "*.db-shm" \\) -size +0c 2>/dev/null | head -50')
        if ok and wal_files:
            wal_list = [l.strip() for l in wal_files.splitlines() if l.strip() and "Permission" not in l]
            if wal_list:
                self._log(f"  Found {len(wal_list)} SQLite WAL files (potential deleted data)", "info")
                for remote in wal_list:
                    if not self._running:
                        break
                    safe = remote.replace("/", "_").lstrip("_")
                    local = str(deleted_dir / safe)
                    if adb.pull_file(self.serial, remote, local, timeout=15):
                        sz = Path(local).stat().st_size if Path(local).exists() else 0
                        if sz > 0:
                            self._hash_and_store(local, remote, "sqlite_wal", evidence_root)
                            count += 1
                            results["files"] += 1
                            results["bytes"] += sz

        self._log(f"  Recovered {count} deleted/temporary files", "success" if count > 0 else "info")

    # ── Phase 8: Physical ──

    def _extract_physical(self, evidence_root, results):
        self._log("Attempting physical image (requires root)...", "warning")
        ok, test = adb.adb_shell(self.serial, "id")
        is_root = ok and "uid=0" in test

        if not is_root:
            # Try su
            ok, test = adb.adb_shell(self.serial, "su -c id")
            is_root = ok and "uid=0" in test

        if not is_root:
            self._log("  Device is not rooted — physical image skipped", "warning")
            self._log("  Logical extraction provides comprehensive data without root", "info")
            return

        phys_dir = evidence_root / "physical"
        phys_dir.mkdir(parents=True, exist_ok=True)

        # Get block device info
        ok, blocks = adb.adb_shell(self.serial, "ls -la /dev/block/by-name/ 2>/dev/null")
        if ok and blocks:
            blocks_path = phys_dir / "block_devices.txt"
            blocks_path.write_text(blocks, encoding="utf-8", errors="replace")
            self._hash_and_store(str(blocks_path), "ls /dev/block/by-name", "physical", evidence_root)
            results["files"] += 1
            results["bytes"] += blocks_path.stat().st_size

        # Attempt to image boot partition (small, fast)
        for part, name in [("/dev/block/by-name/boot", "boot.img"),
                          ("/dev/block/by-name/recovery", "recovery.img")]:
            ok, _ = adb.adb_shell(self.serial, f"dd if={part} of=/sdcard/dfx_{name} bs=4096 2>/dev/null")
            if ok:
                local = str(phys_dir / name)
                if adb.pull_file(self.serial, f"/sdcard/dfx_{name}", local, timeout=60):
                    sz = Path(local).stat().st_size if Path(local).exists() else 0
                    if sz > 0:
                        self._hash_and_store(local, part, "physical_image", evidence_root)
                        results["files"] += 1
                        results["bytes"] += sz
                        self._log(f"  ✓ {name}: {sz:,} bytes", "success")
                adb.adb_shell(self.serial, f"rm /sdcard/dfx_{name}")

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 9: FULL STORAGE IMAGE
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_full_storage(self, evidence_root, results):
        """Pull ALL accessible files from /sdcard/ using find + batch pull."""
        storage_dir = evidence_root / "full_storage"
        storage_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Discover ALL files under /sdcard/ using find
        self._log("  [9.1] Discovering ALL files under /sdcard/...", "info")
        ok, listing = adb.adb_shell(self.serial,
            'find /sdcard/ -type f 2>/dev/null', timeout=120)
        if not ok or not listing:
            self._log("  Could not enumerate files, falling back to directory pulls", "warning")
            listing = ""

        all_files = [f.strip() for f in listing.splitlines() if f.strip() and not f.strip().startswith("Permission denied")]
        self._log(f"  Discovered {len(all_files)} files on /sdcard/", "info")

        # Step 2: Organize files by parent directory for batch pulling
        if all_files:
            # Group by top-level directory
            dir_groups = {}
            for f in all_files:
                parts = f.replace("/sdcard/", "").split("/")
                top_dir = parts[0] if parts else "root"
                if top_dir not in dir_groups:
                    dir_groups[top_dir] = []
                dir_groups[top_dir].append(f)

            self._log(f"  Organized into {len(dir_groups)} directory groups", "info")

            # Pull each group
            for dir_name, files in sorted(dir_groups.items()):
                if not self._running:
                    break
                local_base = storage_dir / "sdcard" / dir_name
                local_base.mkdir(parents=True, exist_ok=True)

                self.phase_changed.emit(f"Storage: {dir_name}")
                self._log(f"  Pulling {dir_name} ({len(files)} files)...", "info")

                # Pull files in batches
                batch_size = 50
                for i in range(0, len(files), batch_size):
                    if not self._running:
                        break
                    batch = files[i:i+batch_size]
                    ok_b, total_b, errors = adb.pull_files_batch(
                        self.serial, batch, str(local_base), timeout=30)
                    if ok_b > 0:
                        results["full_storage_files"] += ok_b
                        results["full_storage_bytes"] += total_b
                        results["files"] += ok_b
                        results["bytes"] += total_b
        else:
            # Fallback: pull accessible subdirectories
            self._log("  Fallback: pulling accessible subdirectories...", "info")
            ok, dirs_out = adb.adb_shell(self.serial,
                'find /sdcard/ -maxdepth 1 -type d 2>/dev/null', timeout=30)
            if ok and dirs_out:
                for d in dirs_out.splitlines():
                    d = d.strip()
                    if not d or d == "/sdcard/" or not d.startswith("/sdcard/"):
                        continue
                    if not self._running:
                        break
                    dir_name = d.replace("/sdcard/", "").replace("/", "_")
                    local_dir = str(storage_dir / dir_name)
                    ok_ls, ls_out = adb.adb_shell(self.serial, f'ls -1 "{d}" 2>/dev/null', timeout=10)
                    if ok_ls and ls_out:
                        count, bts = adb.pull_directory(self.serial, d, local_dir, timeout=600)
                        if count > 0:
                            results["full_storage_files"] += count
                            results["full_storage_bytes"] += bts
                            results["files"] += count
                            results["bytes"] += bts
                            self._log(f"  OK {d}: {count} files", "success")

        # Step 3: Capture filesystem metadata
        self._log("  [9.2] Capturing filesystem metadata...", "info")
        try:
            entries = adb.shell_ls_recursive(self.serial, "/sdcard/", timeout=120)
            if entries:
                meta_path = storage_dir / "sdcard_metadata.json"
                meta_path.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
                self._hash_and_store(str(meta_path), "sdcard_metadata", "filesystem_metadata", evidence_root)
                results["files"] += 1
                results["bytes"] += meta_path.stat().st_size
                self._log(f"  OK Metadata: {len(entries)} entries", "success")
        except Exception as e:
            self._log(f"  Metadata capture: {e}", "warning")

        # Step 4: Partition enumeration
        self._log("  [9.3] Enumerating partitions...", "info")
        try:
            partitions = adb.get_partition_list(self.serial)
            if partitions:
                parts_path = storage_dir / "partitions.json"
                parts_path.write_text(json.dumps(partitions, indent=2, default=str), encoding="utf-8")
                self._hash_and_store(str(parts_path), "partition_table", "partition_info", evidence_root)
                results["partitions"] = len(partitions)
                results["files"] += 1
                results["bytes"] += parts_path.stat().st_size
                self._log(f"  OK {len(partitions)} partitions identified", "success")
        except Exception as e:
            self._log(f"  Partition enumeration: {e}", "warning")

        # Step 5: Filesystem info
        self._log("  [9.4] Capturing filesystem info...", "info")
        try:
            fs_info = {}
            for cmd, label in [
                ("df 2>/dev/null", "disk_usage"),
                ("mount 2>/dev/null", "mount_points"),
            ]:
                ok, out = adb.adb_shell(self.serial, cmd, timeout=10)
                if ok and out:
                    fs_info[label] = out
            if fs_info:
                fs_path = storage_dir / "filesystem_info.json"
                fs_path.write_text(json.dumps(fs_info, indent=2), encoding="utf-8")
                self._hash_and_store(str(fs_path), "filesystem_info", "filesystem_metadata", evidence_root)
                results["files"] += 1
                results["bytes"] += fs_path.stat().st_size
                self._log(f"  OK Filesystem info captured", "success")
        except Exception as e:
            self._log(f"  Filesystem info: {e}", "warning")

        self._log(f"  Full storage total: {results['full_storage_files']} files "
                  f"({results['full_storage_bytes'] / (1024*1024):.1f} MB)", "success")

        results["phases"]["full_storage"] = {
            "files": results["full_storage_files"],
            "bytes": results["full_storage_bytes"],
        }

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 10: APP DATA DEEP EXTRACTION
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_app_data_deep(self, evidence_root, results):
        """Extract ALL accessible app data from /sdcard/Android/data/ and /sdcard/Android/media/."""
        app_dir = evidence_root / "app_data"
        app_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Enumerate all packages
        self._log("  [10.1] Enumerating all installed packages...", "info")
        packages = adb.get_installed_packages(self.serial)
        self._log(f"  Found {len(packages)} packages", "info")

        # Step 2: Discover which packages have accessible data in /sdcard/Android/data/
        self._log("  [10.2] Discovering accessible package data...", "info")
        ok, data_listing = adb.adb_shell(self.serial,
            'ls /sdcard/Android/data/ 2>/dev/null', timeout=15)
        accessible_data = []
        if ok and data_listing:
            for line in data_listing.splitlines():
                pkg = line.strip()
                if pkg:
                    accessible_data.append(pkg)
        self._log(f"  {len(accessible_data)} packages have data in /sdcard/Android/data/", "info")

        # Step 3: Pull /sdcard/Android/data/{package}/ for ALL accessible packages
        self._log("  [10.3] Pulling per-package external data...", "info")
        data_base = app_dir / "external_data"
        data_base.mkdir(parents=True, exist_ok=True)

        pulled_count = 0
        for pkg in accessible_data:
            if not self._running:
                break
            remote_data = f"/sdcard/Android/data/{pkg}"
            local_pkg = data_base / pkg
            local_pkg.mkdir(parents=True, exist_ok=True)

            self.phase_changed.emit(f"App: {pkg.split('.')[-1]}")

            try:
                count, size = adb.pull_directory(self.serial, remote_data, str(local_pkg), timeout=300)
                if count > 0:
                    pulled_count += 1
                    results["app_data_deep_files"] += count
                    results["app_data_deep_bytes"] += size
                    results["files"] += count
                    results["bytes"] += size
                    self._log(f"  OK {pkg}: {count} files ({size / (1024*1024):.1f} MB)", "info")
            except Exception:
                pass

        self._log(f"  Pulled data for {pulled_count} packages", "success")

        # Step 4: Discover which packages have accessible media in /sdcard/Android/media/
        self._log("  [10.4] Discovering accessible package media...", "info")
        ok, media_listing = adb.adb_shell(self.serial,
            'ls /sdcard/Android/media/ 2>/dev/null', timeout=15)
        accessible_media = []
        if ok and media_listing:
            for line in media_listing.splitlines():
                pkg = line.strip()
                if pkg:
                    accessible_media.append(pkg)
        self._log(f"  {len(accessible_media)} packages have media in /sdcard/Android/media/", "info")

        # Step 5: Pull /sdcard/Android/media/{package}/ for ALL accessible packages
        self._log("  [10.5] Pulling per-package media data...", "info")
        media_base = app_dir / "external_media"
        media_base.mkdir(parents=True, exist_ok=True)

        media_count = 0
        for pkg in accessible_media:
            if not self._running:
                break
            remote_media = f"/sdcard/Android/media/{pkg}"
            local_pkg = media_base / pkg
            local_pkg.mkdir(parents=True, exist_ok=True)

            try:
                count, size = adb.pull_directory(self.serial, remote_media, str(local_pkg), timeout=300)
                if count > 0:
                    media_count += 1
                    results["app_data_deep_files"] += count
                    results["app_data_deep_bytes"] += size
                    results["files"] += count
                    results["bytes"] += size
            except Exception:
                pass

        self._log(f"  Pulled media for {media_count} packages", "success")

        # Step 6: ADB backup attempts for key apps
        self._log("  [10.6] Attempting ADB backup for key applications...", "info")
        backup_dir = app_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        key_apps = [
            "com.whatsapp", "org.telegram.messenger", "com.android.chrome",
            "com.instagram.android", "com.facebook.katana", "com.twitter.android",
            "com.snapchat.android", "com.mtn.momo", "com.facebook.lite",
            "com.google.android.gm", "com.spotify.music",
        ]

        backup_success = 0
        for pkg in key_apps:
            if not self._running:
                break
            local_backup = str(backup_dir / f"{pkg}.ab")
            self._log(f"    Backup attempt: {pkg}...", "info")
            ok, msg = adb.adb_backup(self.serial, pkg, local_backup, timeout=60)
            if ok:
                backup_success += 1
                sz = Path(local_backup).stat().st_size if Path(local_backup).exists() else 0
                results["backup_files"] += 1
                results["backup_bytes"] += sz
                results["files"] += 1
                results["bytes"] += sz
                self._log(f"    OK {pkg}: {sz:,} bytes", "success")
                self._hash_and_store(local_backup, pkg, "adb_backup", evidence_root)
            else:
                self._log(f"    SKIP {pkg}: {msg[:60]}", "warning")

        self._log(f"  ADB backup: {backup_success}/{len(key_apps)} successful", "success")

        results["phases"]["app_data_deep"] = {
            "packages_enumerated": len(packages),
            "data_packages_pulled": pulled_count,
            "media_packages_pulled": media_count,
            "backups_successful": backup_success,
            "files": results["app_data_deep_files"],
            "bytes": results["app_data_deep_bytes"],
        }

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 11: DELETED DATA RECOVERY & FILE CARVING
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_recovered_files(self, evidence_root, results):
        """Scan pulled data for file signatures, recover deleted/trashed files, analyze SQLite journals."""
        recovered_dir = evidence_root / "recovered"
        recovered_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Find trashed files
        self._log("  [11.1] Scanning for trashed and deleted files...", "info")
        trash_patterns = [".trashed-*", ".Trash-*", "*.tmp", "*.temp", "*.bak", "*~", "*.orig", "*.old"]
        trashed = adb.shell_find(self.serial, "/sdcard/", trash_patterns, timeout=60)
        self._log(f"  Found {len(trashed)} trashed/temporary files", "info")

        trashed_dir = recovered_dir / "trashed"
        trashed_dir.mkdir(parents=True, exist_ok=True)
        trashed_pulled = 0
        for f in trashed[:500]:  # Limit to 500 files
            if not self._running:
                break
            local = str(trashed_dir / Path(f).name)
            if adb.pull_file(self.serial, f, local, timeout=15):
                sz = Path(local).stat().st_size if Path(local).exists() else 0
                if sz > 0:
                    trashed_pulled += 1
                    results["recovered_files"] += 1
                    results["recovered_bytes"] += sz
                    results["files"] += 1
                    results["bytes"] += sz
                    self._hash_and_store(local, Path(f).name, "recovered_trash", evidence_root)

        self._log(f"  ✓ Recovered {trashed_pulled} trashed files", "success")

        # Step 2: Find SQLite WAL/SHM files (potential deleted data)
        self._log("  [11.2] Scanning for SQLite journal files (WAL/SHM)...", "info")
        wal_files = adb.shell_find(self.serial, "/sdcard/", ["*.db-wal", "*.db-shm", "*.wal", "*.shm"], timeout=60)
        self._log(f"  Found {len(wal_files)} SQLite journal files", "info")

        wal_dir = recovered_dir / "sqlite_journals"
        wal_dir.mkdir(parents=True, exist_ok=True)
        wal_pulled = 0
        for f in wal_files[:200]:
            if not self._running:
                break
            fname = Path(f).name
            local = str(wal_dir / fname)
            if adb.pull_file(self.serial, f, local, timeout=15):
                sz = Path(local).stat().st_size if Path(local).exists() else 0
                if sz > 0:
                    wal_pulled += 1
                    results["recovered_files"] += 1
                    results["recovered_bytes"] += sz
                    results["files"] += 1
                    results["bytes"] += sz
                    self._hash_and_store(local, fname, "recovered_sqlite_journal", evidence_root)

        self._log(f"  ✓ Recovered {wal_pulled} SQLite journal files", "success")

        # Step 3: File signature scanning on pulled data
        self._log("  [11.3] Scanning for file signatures (magic bytes)...", "info")
        scan_dirs = ["/sdcard/"]
        if (evidence_root / "full_storage" / "sdcard").exists():
            scan_dirs.append(str(evidence_root / "full_storage" / "sdcard"))

        signatures_found = 0
        for scan_dir in scan_dirs:
            try:
                sigs = adb.shell_find_signatures(self.serial, scan_dir, timeout=120)
                signatures_found += len(sigs)
                if sigs:
                    sig_path = recovered_dir / f"signatures_{Path(scan_dir).name}.json"
                    sig_path.write_text(json.dumps(sigs, indent=2, default=str), encoding="utf-8")
                    self._hash_and_store(str(sig_path), f"signatures_{Path(scan_dir).name}", "file_signatures", evidence_root)
                    results["files"] += 1
                    results["bytes"] += sig_path.stat().st_size
                    # Count by type
                    type_counts = {}
                    for s in sigs:
                        st = s.get("signature_type", "UNKNOWN")
                        type_counts[st] = type_counts.get(st, 0) + 1
                    for st, cnt in sorted(type_counts.items()):
                        self._log(f"    {st}: {cnt} files", "info")
            except Exception as e:
                self._log(f"  ✗ Signature scan failed for {scan_dir}: {e}", "error")

        results["signatures_found"] = signatures_found
        self._log(f"  ✓ {signatures_found} files with recognized signatures", "success")

        # Step 4: Find recovered documents (PDF, DOCX, XLSX, etc.)
        self._log("  [11.4] Scanning for recovered documents...", "info")
        doc_patterns = ["*.pdf", "*.docx", "*.xlsx", "*.pptx", "*.doc", "*.xls", "*.csv", "*.rtf", "*.odt"]
        docs = adb.shell_find(self.serial, "/sdcard/", doc_patterns, timeout=60)
        self._log(f"  Found {len(docs)} document files", "info")

        docs_dir = recovered_dir / "documents"
        docs_dir.mkdir(parents=True, exist_ok=True)
        docs_pulled = 0
        for f in docs[:200]:
            if not self._running:
                break
            local = str(docs_dir / Path(f).name)
            if adb.pull_file(self.serial, f, local, timeout=15):
                sz = Path(local).stat().st_size if Path(local).exists() else 0
                if sz > 0:
                    docs_pulled += 1
                    results["recovered_files"] += 1
                    results["recovered_bytes"] += sz
                    results["files"] += 1
                    results["bytes"] += sz
                    results["documents"] += 1
                    self._hash_and_store(local, Path(f).name, "recovered_document", evidence_root)

        self._log(f"  ✓ Recovered {docs_pulled} documents", "success")

        results["phases"]["recovered"] = {
            "trashed_files": trashed_pulled,
            "sqlite_journals": wal_pulled,
            "signatures_found": signatures_found,
            "documents": docs_pulled,
            "files": results["recovered_files"],
            "bytes": results["recovered_bytes"],
        }

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 12: EVIDENCE PACKAGING & VERIFICATION
    # ═══════════════════════════════════════════════════════════════════════

    def _package_evidence(self, evidence_root, results):
        """Create MANIFEST.json, INDEX.json, verification report, and audit trail."""
        self._log("  [12.1] Building evidence manifest...", "info")

        # Collect all artifact hashes from DB
        try:
            conn = get_db()
            artifacts = conn.execute(
                "SELECT name, source, category, file_path, file_size, sha256_hash, created_at "
                "FROM evidence_artifacts WHERE session_id=?",
                (self._session_id,),
            ).fetchall()
            conn.close()
        except Exception:
            artifacts = []

        # Build file inventory
        file_inventory = []
        for art in artifacts:
            file_inventory.append({
                "name": art[0],
                "source": art[1],
                "category": art[2],
                "path": art[3],
                "size": art[4],
                "sha256": art[5],
                "acquired_at": art[6],
            })

        # MANIFEST.json
        manifest = {
            "case_id": self.case_id,
            "device_serial": self.serial,
            "acquisition_level": self.level,
            "hash_algorithm": self.hash_algo,
            "acquisition_date": datetime.now(timezone.utc).isoformat(),
            "tool": "DroidForenX v2.0",
            "examiner_notes": "",
            "total_files": results["files"],
            "total_bytes": results["bytes"],
            "phases": {
                "phase_1_device_properties": results.get("device_info", 0),
                "phase_2_logical": {
                    "contacts": results["contacts"],
                    "sms": results["sms"],
                    "calls": results["calls"],
                    "calendar": results["calendar"],
                },
                "phase_3_browser": results["browser"],
                "phase_4_app_databases": results["databases"],
                "phase_5_filesystem": results["media_files"],
                "phase_6_social_apps": results["app_data"],
                "phase_7_deleted": results.get("recovered_files", 0),
                "phase_8_physical": results.get("partitions", 0),
                "phase_9_full_storage": {
                    "files": results["full_storage_files"],
                    "bytes": results["full_storage_bytes"],
                },
                "phase_10_app_data_deep": {
                    "files": results["app_data_deep_files"],
                    "bytes": results["app_data_deep_bytes"],
                    "backups": results["backup_files"],
                },
                "phase_11_recovered": {
                    "files": results["recovered_files"],
                    "bytes": results["recovered_bytes"],
                    "signatures": results["signatures_found"],
                },
                "phase_12_packaging": "complete",
            },
            "file_inventory": file_inventory,
        }

        manifest_path = evidence_root / "ACQUISITION_MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        self._hash_and_store(str(manifest_path), "ACQUISITION_MANIFEST", "evidence_manifest", evidence_root)
        results["files"] += 1
        results["bytes"] += manifest_path.stat().st_size
        self._log(f"  ✓ MANIFEST.json: {len(file_inventory)} artifacts cataloged", "success")

        # Step 2: Verification report
        self._log("  [12.2] Verifying evidence integrity...", "info")
        verified = 0
        failed = 0
        for item in file_inventory:
            fpath = item.get("path", "")
            expected_hash = item.get("sha256", "")
            if fpath and expected_hash and Path(fpath).exists():
                actual_hash = _sha256(fpath)
                if actual_hash == expected_hash:
                    verified += 1
                else:
                    failed += 1
                    self._log(f"  ✗ HASH MISMATCH: {fpath}", "error")

        verification = {
            "verification_date": datetime.now(timezone.utc).isoformat(),
            "total_artifacts": len(file_inventory),
            "verified_ok": verified,
            "hash_mismatches": failed,
            "all_passed": failed == 0,
        }

        ver_path = evidence_root / "VERIFICATION.json"
        ver_path.write_text(json.dumps(verification, indent=2), encoding="utf-8")
        self._hash_and_store(str(ver_path), "VERIFICATION", "evidence_verification", evidence_root)
        results["files"] += 1
        results["bytes"] += ver_path.stat().st_size

        if failed == 0:
            self._log(f"  ✓ All {verified} artifacts verified — integrity confirmed", "success")
        else:
            self._log(f"  ✗ {failed} hash mismatches detected!", "error")

        # Step 3: Audit trail
        self._log("  [12.3] Recording audit trail...", "info")
        audit_entry = {
            "action": "acquisition_completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "examiner": "system",
            "device_serial": self.serial,
            "case_id": self.case_id,
            "level": self.level,
            "results": {
                "total_files": results["files"],
                "total_bytes": results["bytes"],
                "errors": results["errors"],
            },
        }
        audit_path = evidence_root / "AUDIT_TRAIL.json"
        audit_path.write_text(json.dumps(audit_entry, indent=2), encoding="utf-8")
        self._hash_and_store(str(audit_path), "AUDIT_TRAIL", "audit_trail", evidence_root)
        results["files"] += 1
        results["bytes"] += audit_path.stat().st_size
        self._log(f"  ✓ Audit trail recorded", "success")

        results["phases"]["evidence_packaging"] = {
            "artifacts_cataloged": len(file_inventory),
            "verified": verified,
            "mismatches": failed,
            "manifest_hash": _sha256(str(manifest_path)),
        }

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 13: EMAIL EXTRACTION
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_emails(self, evidence_root, results):
        """Extract emails from Gmail, Outlook, and discover all email accounts."""
        email_dir = evidence_root / "emails"
        email_dir.mkdir(parents=True, exist_ok=True)
        email_count = 0

        # Step 1: Discover all email accounts on device
        self._log("  [13.1] Discovering email accounts...", "info")
        try:
            accounts = extract_email_accounts(self.serial)
            if accounts:
                acc_path = email_dir / "email_accounts.json"
                acc_path.write_text(json.dumps(accounts, indent=2, default=str), encoding="utf-8")
                self._hash_and_store(str(acc_path), "email_accounts", "email_metadata", evidence_root)
                results["files"] += 1
                results["bytes"] += acc_path.stat().st_size
                self._log(f"  Found {len(accounts)} email accounts on device", "success")
            else:
                self._log("  No email accounts discovered", "warning")
        except Exception as e:
            self._log(f"  Account discovery failed: {e}", "warning")

        # Step 2: Gmail extraction via content provider
        self._log("  [13.2] Extracting Gmail messages...", "info")
        try:
            gmail = extract_gmail_emails(self.serial)
            if gmail:
                gmail_path = email_dir / "gmail_messages.json"
                gmail_path.write_text(json.dumps(gmail, indent=2, default=str), encoding="utf-8")
                self._hash_and_store(str(gmail_path), "gmail", "email_messages", evidence_root)
                email_count += len(gmail)
                results["files"] += 1
                results["bytes"] += gmail_path.stat().st_size
                self._log(f"  Gmail: {len(gmail)} messages extracted", "success")
            else:
                self._log("  Gmail: no messages accessible via content provider", "warning")
        except Exception as e:
            self._log(f"  Gmail extraction failed: {e}", "warning")

        # Step 3: Outlook extraction
        self._log("  [13.3] Extracting Outlook messages...", "info")
        try:
            outlook = extract_outlook_emails(self.serial, evidence_root=evidence_root)
            if outlook:
                out_path = email_dir / "outlook_messages.json"
                out_path.write_text(json.dumps(outlook, indent=2, default=str), encoding="utf-8")
                self._hash_and_store(str(out_path), "outlook", "email_messages", evidence_root)
                email_count += len(outlook)
                results["files"] += 1
                results["bytes"] += out_path.stat().st_size
                self._log(f"  Outlook: {len(outlook)} messages extracted", "success")
            else:
                self._log("  Outlook: no messages accessible", "warning")
        except Exception as e:
            self._log(f"  Outlook extraction failed: {e}", "warning")

        # Step 4: Scan for other email app caches
        self._log("  [13.4] Scanning email app caches...", "info")
        email_packages = {
            "com.google.android.gm": "Gmail",
            "com.microsoft.office.outlook": "Outlook",
            "com.yahoo.mobile.client.android.mail": "Yahoo",
            "com.kitmail": "KitMail",
            "com.fsck.k9": "K-9 Mail",
            "org.kman.AquaMail": "AquaMail",
        }
        for pkg, name in email_packages.items():
            remote = f"/sdcard/Android/data/{pkg}"
            ok, listing = adb.adb_shell(self.serial, f'ls -la "{remote}" 2>/dev/null', timeout=10)
            if ok and listing and "No such file" not in listing:
                entries = [l.strip() for l in listing.splitlines() if l.strip()]
                if entries:
                    local_dir = str(email_dir / f"cache_{name}")
                    c, b = adb.pull_directory(self.serial, remote, local_dir, timeout=300)
                    if c > 0:
                        email_count += c
                        results["files"] += c
                        results["bytes"] += b
                        self._log(f"  {name} cache: {c} files ({b / (1024*1024):.1f} MB)", "success")

        self._log(f"  Email extraction total: {email_count} items", "success" if email_count > 0 else "info")
        results["emails"] = email_count

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 14: DOCUMENT METADATA EXTRACTION
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_document_metadata(self, evidence_root, results):
        """Extract metadata (author, dates, revision) from pulled documents."""
        self._log("  [14.1] Scanning pulled documents for metadata...", "info")

        doc_extensions = {".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls"}
        metadata_entries = []

        # Scan all evidence directories for documents
        for subdir in evidence_root.rglob("*"):
            if not subdir.is_file():
                continue
            if subdir.suffix.lower() in doc_extensions:
                try:
                    meta = extract_all_metadata(str(subdir))
                    meta["evidence_path"] = str(subdir.relative_to(evidence_root))
                    metadata_entries.append(meta)
                except Exception as e:
                    logger.debug(f"Metadata extraction failed for {subdir}: {e}")

        if metadata_entries:
            meta_path = evidence_root / "document_metadata.json"
            meta_path.write_text(json.dumps(metadata_entries, indent=2, default=str), encoding="utf-8")
            self._hash_and_store(str(meta_path), "document_metadata", "metadata", evidence_root)
            results["files"] += 1
            results["bytes"] += meta_path.stat().st_size

            # Log authors found
            authors = set()
            for m in metadata_entries:
                author = m.get("author", "") or m.get("lastModifiedBy", "")
                if author:
                    authors.add(author)
            if authors:
                self._log(f"  Document authors found: {', '.join(authors)}", "success")

            # Log PDF titles
            pdf_titles = [m.get("title") for m in metadata_entries
                          if m.get("type") == "PDF" and m.get("title")]
            if pdf_titles:
                self._log(f"  PDF titles: {', '.join(pdf_titles[:5])}", "success")

            self._log(f"  Metadata extracted from {len(metadata_entries)} documents", "success")
        else:
            self._log("  No documents found for metadata extraction", "info")

        results["document_metadata"] = len(metadata_entries)

    # ═══════════════════════════════════════════════════════════════════════
    #  PHASE 15: CHROME BOOKMARKS (NON-ROOT)
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_chrome_bookmarks(self, evidence_root, results):
        """Extract Chrome bookmarks via content provider (non-root)."""
        self._log("  [15.1] Extracting Chrome bookmarks...", "info")
        browser_dir = evidence_root / "browser"
        browser_dir.mkdir(parents=True, exist_ok=True)
        bookmark_count = 0

        # Chrome bookmarks via content provider (no root needed)
        chrome_bookmark_uris = [
            "content://com.android.chrome.browser/bookmarks",
        ]
        for uri in chrome_bookmark_uris:
            ok, data = adb.adb_shell(self.serial, f"content query --uri {uri}", timeout=15)
            if ok and data and "No result" not in data:
                entries = []
                for line in data.splitlines():
                    if line.startswith("Row:"):
                        entry = {}
                        for field in ["_id", "title", "url", "created", "date_modified", "folder"]:
                            m = re.search(rf"{field}=([^,)]+)", line)
                            if m:
                                entry[field] = m.group(1).strip().strip('"')
                        if entry:
                            entries.append(entry)
                if entries:
                    bk_path = browser_dir / "chrome_bookmarks.json"
                    bk_path.write_text(json.dumps(entries, indent=2, default=str), encoding="utf-8")
                    self._hash_and_store(str(bk_path), "chrome_bookmarks", "browser_bookmarks", evidence_root)
                    bookmark_count += len(entries)
                    results["files"] += 1
                    results["bytes"] += bk_path.stat().st_size
                    self._log(f"  Chrome bookmarks: {len(entries)} entries", "success")

        # Also try to pull Chrome's Bookmarks file directly (JSON format)
        chrome_bookmark_files = [
            "/data/data/com.android.chrome/app_chrome/Default/Bookmarks",
            "/data/user/0/com.android.chrome/app_chrome/Default/Bookmarks",
        ]
        for remote_path in chrome_bookmark_files:
            local_path = str(browser_dir / "chrome_bookmarks_file.json")
            if adb.pull_file(self.serial, remote_path, local_path, timeout=15):
                try:
                    with open(local_path, "r", encoding="utf-8", errors="replace") as f:
                        data = json.load(f)

                    def _parse_bookmark_node(node, entries=None):
                        if entries is None:
                            entries = []
                        if node.get("type") == "url":
                            entries.append({
                                "name": node.get("name", ""),
                                "url": node.get("url", ""),
                                "date_added": node.get("date_added", ""),
                            })
                        for child in node.get("children", []):
                            _parse_bookmark_node(child, entries)
                        return entries

                    bookmarks = _parse_bookmark_node(data.get("roots", {}))
                    if bookmarks:
                        bk_file_path = browser_dir / "chrome_bookmarks_parsed.json"
                        bk_file_path.write_text(json.dumps(bookmarks, indent=2, default=str), encoding="utf-8")
                        self._hash_and_store(str(bk_file_path), "chrome_bookmarks_file", "browser_bookmarks", evidence_root)
                        bookmark_count += len(bookmarks)
                        results["files"] += 1
                        results["bytes"] += bk_file_path.stat().st_size
                        self._log(f"  Chrome bookmarks (file): {len(bookmarks)} entries", "success")
                except Exception as e:
                    self._log(f"  Chrome bookmarks file parse error: {e}", "warning")
                Path(local_path).unlink(missing_ok=True)
                break

        results["browser_bookmarks"] = bookmark_count
        if bookmark_count == 0:
            self._log("  No Chrome bookmarks accessible (requires Chrome to be default browser)", "warning")

    # ═══════════════════════════════════════════════════════════════════════
    #  CHAIN OF CUSTODY LOGGING
    # ═══════════════════════════════════════════════════════════════════════

    def _record_custody(self, results):
        """Record chain of custody entry in SQLite audit log."""
        if not _CUSTODY:
            return

        try:
            _CUSTODY.log_access(
                case_id=self.case_id or "unknown",
                evidence_id=f"acq_{self.serial[:8]}",
                operator="DroidForenX Auto",
                action="acquisition_complete",
                details=json.dumps({
                    "serial": self.serial,
                    "level": self.level,
                    "total_files": results["files"],
                    "total_bytes": results["bytes"],
                    "contacts": results["contacts"],
                    "sms": results["sms"],
                    "calls": results["calls"],
                    "emails": results.get("emails", 0),
                    "document_metadata": results.get("document_metadata", 0),
                    "browser_bookmarks": results.get("browser_bookmarks", 0),
                    "full_storage": results["full_storage_files"],
                    "app_data_deep": results["app_data_deep_files"],
                    "recovered": results["recovered_files"],
                }),
            )
            self._log("  Chain of custody entry recorded", "success")
        except Exception as e:
            self._log(f"  Custody logging error: {e}", "warning")

    # ── Manifest ──

    def _write_manifest(self, evidence_root, results):
        manifest = {
            "case_id": self.case_id,
            "device_serial": self.serial,
            "acquisition_level": self.level,
            "hash_algorithm": self.hash_algo,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": "DroidForenX v1.0",
            "results_summary": {
                "total_files": results["files"],
                "total_bytes": results["bytes"],
                "contacts": results["contacts"],
                "sms_messages": results["sms"],
                "call_log_entries": results["calls"],
                "calendar_events": results["calendar"],
                "media_files": results["media_files"],
                "documents": results["documents"],
                "app_databases": results["databases"],
                "browser_history": results["browser"],
            },
        }
        manifest_path = evidence_root / "ACQUISITION_MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        self._hash_and_store(str(manifest_path), "manifest", "acquisition_manifest", evidence_root)

    def stop(self):
        self._running = False


class AcquirePage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, db_get, parent=None):
        super().__init__(parent)
        self.db_get = db_get
        self._serial = ""
        self._case_id = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("📥 Stage 3 — Data Acquisition")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #10b981; background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel("Comprehensive forensic extraction — contacts, SMS, calls, media, app data, and more.")
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(subtitle)

        self.device_bar = DeviceStatusBar()
        self.device_bar.device_rescanned.connect(self._on_device_rescanned)
        layout.addWidget(self.device_bar)

        # Config
        config_group = QGroupBox("⚙ Acquisition Configuration")
        config_layout = QGridLayout(config_group)

        config_layout.addWidget(QLabel("Extraction Level:"), 0, 0)
        self.level_combo = QComboBox()
        self.level_combo.addItems([
            "Level 1 — Logical (contacts, SMS, calls, calendar, app data, adb backup)",
            "Level 2 — Full (+sdcard media, social apps, documents, full storage, file carving)",
            "Level 3 — Physical (+disk image, requires root)",
        ])
        self.level_combo.setStyleSheet("padding: 10px;")
        config_layout.addWidget(self.level_combo, 0, 1, 1, 2)

        config_layout.addWidget(QLabel("Hash Algorithm:"), 1, 0)
        self.hash_combo = QComboBox()
        self.hash_combo.addItems(["SHA-256", "MD5", "SHA-1"])
        config_layout.addWidget(self.hash_combo, 1, 1)

        layout.addWidget(config_group)

        # Extraction checklist
        check_group = QGroupBox("📋 Extraction Checklist (auto-selected by level)")
        check_layout = QVBoxLayout(check_group)
        self.checks = {}
        check_items = [
            ("contacts", "Contacts (names, phones, emails)"),
            ("sms", "SMS/MMS Messages"),
            ("calls", "Call Log (incoming, outgoing, missed)"),
            ("calendar", "Calendar Events"),
            ("browser", "Browser History (Chrome, Firefox)"),
            ("databases", "Application Databases (WhatsApp, Telegram, etc.)"),
            ("media", "Media Files (photos, videos, audio)"),
            ("documents", "Documents & Downloads"),
            ("social", "Social App Data (WhatsApp, Telegram, Facebook media)"),
            ("deleted", "Deleted & Trashed Files"),
            ("device", "Device Properties & SIM Info"),
            ("full_storage", "Full /sdcard/ Storage Image (Level 2+)"),
            ("app_deep", "Per-App External Data & ADB Backups (Level 1+)"),
            ("recovered", "File Signature Carving & SQLite Journal Recovery (Level 2+)"),
            ("emails", "Email Extraction (Gmail, Outlook, Yahoo, IMAP)"),
            ("doc_meta", "Document Metadata (author, dates, revision, XMP)"),
            ("bookmarks", "Chrome Bookmarks (non-root content provider)"),
            ("evidence_pkg", "Evidence Packaging, Manifest & Verification"),
        ]
        for key, label in check_items:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("font-size: 13px; color: #e2e8f0; background: transparent;")
            self.checks[key] = cb
            check_layout.addWidget(cb)
        layout.addWidget(check_group)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_acquire = QPushButton("📥 Start Acquisition")
        self.btn_acquire.setObjectName("btn-success")
        self.btn_acquire.setFixedHeight(42)
        self.btn_acquire.clicked.connect(self._start_acquisition)
        btn_row.addWidget(self.btn_acquire)

        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setObjectName("btn-danger")
        self.btn_stop.setFixedHeight(42)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_acquisition)
        btn_row.addWidget(self.btn_stop)

        btn_row.addStretch()

        btn_next = QPushButton("Next: Examination →")
        btn_next.setObjectName("btn-accent")
        btn_next.setFixedHeight(42)
        btn_next.clicked.connect(lambda: self.navigate.emit("examine"))
        btn_row.addWidget(btn_next)
        layout.addLayout(btn_row)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(10)
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("font-size: 12px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(self.progress_label)

        # Results
        self.results_frame = QFrame()
        self.results_frame.setStyleSheet(
            "QFrame { background: #111827; border: 1px solid #1e293b; border-radius: 10px; padding: 16px; }")
        self.results_frame.setVisible(False)
        r_layout = QVBoxLayout(self.results_frame)
        r_layout.setSpacing(12)

        self.results_title = QLabel("📊 Acquisition Results")
        self.results_title.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: #10b981; background: transparent; border: none;")
        r_layout.addWidget(self.results_title)

        self.results_table = DataTable(["Category", "Count", "Size"])
        self.results_table.setMaximumHeight(300)
        r_layout.addWidget(self.results_table)

        layout.addWidget(self.results_frame)

        # Console
        self.console = LogConsole("Acquisition Log")
        self.console.setMinimumHeight(200)
        layout.addWidget(self.console)

        # No scroll area previously, so a short window compressed the console.
        enable_page_scroll(self)

    def set_device(self, serial: str, case_id: str = ""):
        self._serial = serial
        self._case_id = case_id

    def set_case(self, case_id: str):
        self._case_id = case_id

    def _on_device_rescanned(self, devices):
        if devices:
            self._serial = devices[0].get("serial", "")

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()

    def _start_acquisition(self):
        serial = self._serial or self.device_bar.get_first_serial()
        if not serial:
            self.console.log("No device detected. Connect a device via USB.", "error")
            return
        self._serial = serial
        level = self.level_combo.currentIndex() + 1
        hash_algo = self.hash_combo.currentText().split(" ")[0].lower()

        self.btn_acquire.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting acquisition...")
        self.results_frame.setVisible(False)
        self.console.clear()

        self.worker = AcquisitionWorker(serial, level, self._case_id, hash_algo)
        self.worker.log.connect(self.console.log)
        self.worker.progress.connect(self._on_progress)
        self.worker.file_pulled.connect(self._on_file)
        self.worker.phase_changed.connect(self._on_phase)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _stop_acquisition(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
            self.console.log("Acquisition stopped by user.", "warning")

    def _on_progress(self, current, total):
        try:
            val = int(int(current) / int(total) * 100)
            self.progress_bar.setValue(min(val, 99))
            self.progress_label.setText(f"Processing {current} of {total}...")
        except (ValueError, ZeroDivisionError):
            pass

    def _on_phase(self, phase: str):
        self.progress_label.setText(f"Phase: {phase}...")

    def _on_file(self, name, size):
        self.console.log(f"  ✓ {name} ({size:,} bytes)", "success")

    def _on_finished(self, results):
        self.btn_acquire.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_label.setText("Acquisition complete!")
        total_mb = results['bytes'] / (1024 * 1024)
        self.progress_label.setText(
            f"Complete — {results['files']:,} files, {total_mb:.1f} MB extracted")

        rows = [
            ("Contacts", str(results["contacts"]), ""),
            ("SMS Messages", str(results["sms"]), ""),
            ("Call Log Entries", str(results["calls"]), ""),
            ("Calendar Events", str(results["calendar"]), ""),
            ("Browser History", str(results["browser"]), ""),
            ("App Databases", str(results["databases"]), ""),
            ("Media Files", str(results["media_files"]), ""),
            ("Documents", str(results["documents"]), ""),
            ("App Data (Social)", str(results["app_data"]), ""),
            ("Device Info", str(results["device_info"]), ""),
        ]

        if results.get("full_storage_files"):
            rows.append(("Full Storage Image", str(results["full_storage_files"]),
                         f"{results['full_storage_bytes'] / (1024*1024):.1f} MB"))
        if results.get("app_data_deep_files"):
            rows.append(("App Data Deep", str(results["app_data_deep_files"]),
                         f"{results['app_data_deep_bytes'] / (1024*1024):.1f} MB"))
        if results.get("backup_files"):
            rows.append(("ADB Backups", str(results["backup_files"]),
                         f"{results['backup_bytes'] / (1024*1024):.1f} MB"))
        if results.get("recovered_files"):
            rows.append(("Recovered Files", str(results["recovered_files"]),
                         f"{results['recovered_bytes'] / (1024*1024):.1f} MB"))
        if results.get("signatures_found"):
            rows.append(("File Signatures", str(results["signatures_found"]), ""))
        if results.get("emails"):
            rows.append(("Email Messages", str(results["emails"]), ""))
        if results.get("document_metadata"):
            rows.append(("Document Metadata", str(results["document_metadata"]), ""))
        if results.get("browser_bookmarks"):
            rows.append(("Browser Bookmarks", str(results["browser_bookmarks"]), ""))
        if results.get("partitions"):
            rows.append(("Partitions", str(results["partitions"]), ""))

        rows.append(("━━━━━━━━━━━━━━━", "━━━━━━━━", "━━━━━━━━"))
        rows.append(("TOTAL FILES", str(results["files"]), f"{total_mb:.1f} MB"))
        rows.append(("Errors", str(results["errors"]), ""))
        self.results_table.load_data(rows)
        self.results_frame.setVisible(True)

        if results["errors"] > 0:
            self.console.log(f"⚠ {results['errors']} errors occurred during acquisition", "warning")
