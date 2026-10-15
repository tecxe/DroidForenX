import re
import json
import sqlite3
import logging
import urllib.parse
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Optional
from . import adb_manager as adb
from . import evidence_store
from .evidence_store import AcquisitionStatus

logger = logging.getLogger(__name__)

#: Kept for backward compatibility. New code should use evidence_store.case().
EVIDENCE_ROOT = evidence_store.EVIDENCE_ROOT


def _unique_name(target_dir: Path, local_name: str, remote_path: str) -> str:
    """
    Pick a filename inside ``target_dir`` that cannot clobber an existing artifact.

    Two different remote paths routinely share a basename (``.../a/photo.jpg``
    and ``.../b/photo.jpg``). Pulling both into the evidence set would
    silently overwrite the first artifact, so a collision is disambiguated
    with a short digest of the remote path. The original name is always kept
    for the first pull, so normal cases stay readable.
    """
    base = Path(local_name).name
    candidate = target_dir / base
    if not candidate.exists():
        return base
    tag = hashlib.sha1(remote_path.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{candidate.stem}_{tag}{candidate.suffix}"


def _ensure_evidence_dir():
    evidence_store.ensure_case_tree(evidence_store.get_active_case() or "unassigned")


def _ts_to_str(ts_ms):
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return ""


def _ts_to_str_s(ts_s):
    try:
        return datetime.fromtimestamp(int(ts_s), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, TypeError):
        return ""


def _safe_pull_db(serial, remote_path, local_name, evidence_root=None):
    """
    Pull a file off the device into the case's immutable ``acquired/`` tree,
    hash it, and record it in the manifest.

    The file is never deleted. Parsers read from this location and the copy
    survives for re-verification and for the defence of the acquisition.
    """
    case_id = evidence_store.resolve_case()
    target_dir = evidence_store.acquired_dir(case_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    local_path = str(target_dir / _unique_name(target_dir, local_name, remote_path))

    if not adb.pull_file(serial, remote_path, local_path):
        evidence_store.record_status(
            case_id, "acquire", AcquisitionStatus.NOT_ACCESSIBLE,
            f"{remote_path}: not readable on this device (root required, or app not installed)",
        )
        return None

    path = Path(local_path)
    if not path.exists() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        evidence_store.record_status(
            case_id, "acquire", AcquisitionStatus.NOT_PRESENT,
            f"{remote_path}: absent or zero-length on device",
        )
        return None

    evidence_store.record_acquired(case_id, remote_path, path, loader="adb-pull")
    return str(path)


def _query_db(db_path, sql, params=None):
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        if params:
            cursor = conn.execute(sql, params)
        else:
            cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"DB query error on {db_path}: {e}")
        return []


def analyze_call_log(serial):
    results = {"calls": [], "stats": {}, "frequent_contacts": []}
    data = adb.query_content(serial, "content://call_log/calls", sort="date DESC")
    if "Row:" not in data:
        return results
    calls = []
    for line in data.splitlines():
        if "Row:" not in line:
            continue
        call = {}
        for field in ["number", "type", "date", "duration", "new", "geocoded_location"]:
            match = re.search(rf"{field}=([^,]+)", line)
            if match:
                call[field] = match.group(1).strip()
        if "date" in call:
            call["date_readable"] = _ts_to_str(call["date"])
        if "duration" in call:
            try:
                secs = int(call["duration"])
                call["duration_readable"] = f"{secs // 3600}h {(secs % 3600) // 60}m {secs % 60}s"
            except ValueError:
                pass
        calls.append(call)
    results["calls"] = calls
    if calls:
        numbers = [c.get("number", "") for c in calls if c.get("number")]
        freq = Counter(numbers).most_common(20)
        results["frequent_contacts"] = [{"number": n, "count": c} for n, c in freq]
        types = Counter(c.get("type", "") for c in calls)
        results["stats"] = {
            "total_calls": len(calls),
            "incoming": types.get("1", 0),
            "outgoing": types.get("2", 0),
            "missed": types.get("3", 0),
            "rejected": types.get("5", 0),
            "unique_contacts": len(set(numbers)),
        }
    return results


def analyze_sms_mms(serial):
    results = {"messages": [], "stats": {}, "keyword_hits": []}
    data = adb.query_content(serial, "content://sms", sort="date DESC")
    if "Row:" not in data:
        return results
    messages = []
    for line in data.splitlines():
        if "Row:" not in line:
            continue
        msg = {}
        for field in ["address", "body", "date", "type", "read", "seen", "service_center"]:
            match = re.search(rf"{field}=([^,]+)", line)
            if match:
                msg[field] = match.group(1).strip()
        if "date" in msg:
            msg["date_readable"] = _ts_to_str(msg["date"])
        messages.append(msg)
    results["messages"] = messages
    if messages:
        senders = Counter(m.get("address", "") for m in messages)
        results["stats"] = {
            "total_messages": len(messages),
            "unique_senders": len(senders),
            "most_active": [{"number": n, "count": c} for n, c in senders.most_common(10)],
        }
    suspicious = ["kill", "bomb", "drugs", "weapon", "gun", "money", "cash",
                  "password", "pin", "code", "address", "meet", "secret",
                  "evidence", "destroy", "delete", "hide", "escape"]
    hits = []
    for msg in messages:
        body = msg.get("body", "").lower()
        for kw in suspicious:
            if kw in body:
                hits.append({"keyword": kw, "message": msg.get("body", "")[:120], "date": msg.get("date_readable", "")})
    results["keyword_hits"] = hits
    return results


def analyze_contacts(serial):
    results = {"contacts": [], "stats": {}}
    data = adb.query_content(serial, "content://com.android.contacts/contacts")
    if "Row:" not in data:
        return results
    contacts = []
    for line in data.splitlines():
        if "Row:" not in line:
            continue
        contact = {}
        for field in ["display_name", "starred", "times_contacted", "last_time_contacted", "has_phone_number"]:
            match = re.search(rf"{field}=([^,]+)", line)
            if match:
                contact[field] = match.group(1).strip()
        contacts.append(contact)
    results["contacts"] = contacts
    results["stats"] = {
        "total": len(contacts),
        "starred": sum(1 for c in contacts if c.get("starred") == "1"),
        "with_phone": sum(1 for c in contacts if c.get("has_phone_number") == "1"),
    }
    return results


def analyze_browser(serial):
    _ensure_evidence_dir()
    results = {"chrome": [], "frequent_domains": []}
    chrome_db = "/data/data/com.android.chrome/app_chrome/Default/History"
    local_db = str(EVIDENCE_ROOT / f"chrome_{serial[:8]}.db")
    if adb.pull_file(serial, chrome_db, local_db):
        try:
            rows = _query_db(local_db, "SELECT url, title, visit_count, last_visit_time, typed_count FROM urls ORDER BY last_visit_time DESC LIMIT 5000")
            results["chrome"] = [{"url": r.get("url", ""), "title": r.get("title", ""), "visits": r.get("visit_count", 0), "typed": r.get("typed_count", 0)} for r in rows]
        except Exception as e:
            logger.error(f"Chrome DB error: {e}")
    if results["chrome"]:
        domains = []
        for entry in results["chrome"]:
            try:
                parsed = urllib.parse.urlparse(entry["url"])
                if parsed.hostname:
                    domains.append(parsed.hostname)
            except Exception:
                pass
        results["frequent_domains"] = [{"domain": d, "count": c} for d, c in Counter(domains).most_common(20)]
    return results


def analyze_gps(serial):
    results = {"locations": [], "wifi_bssids": []}
    ok, loc = adb.adb_shell(serial, "dumpsys location")
    if ok and loc:
        for line in loc.splitlines():
            if "last known" in line.lower() or "gps" in line.lower():
                lat_m = re.search(r"lat[=:]?\s*([-+]?\d+\.?\d*)", line, re.I)
                lon_m = re.search(r"lon[=:]?\s*([-+]?\d+\.?\d*)", line, re.I)
                if lat_m and lon_m:
                    results["locations"].append({"lat": float(lat_m.group(1)), "lon": float(lon_m.group(1))})
    ok, wifi = adb.adb_shell(serial, "dumpsys wifi")
    if ok and wifi:
        for line in wifi.splitlines():
            bssid_m = re.search(r'([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})', line)
            ssid_m = re.search(r'SSID:\s*"?([^"]+)"?', line)
            if bssid_m or ssid_m:
                results["wifi_bssids"].append({
                    "bssid": bssid_m.group(1) if bssid_m else "",
                    "ssid": ssid_m.group(1) if ssid_m else "",
                })
    return results


def analyze_wifi(serial):
    results = {"saved_networks": [], "current": "", "stats": {}}
    ok, data = adb.adb_shell(serial, "dumpsys wifi")
    if ok and data:
        for line in data.splitlines():
            if "mWifiInfo" in line or "SSID:" in line:
                ssid_m = re.search(r'SSID:\s*"?([^"]+)"?', line)
                if ssid_m:
                    results["current"] = ssid_m.group(1)
            ssid_m = re.search(r'SSID:\s*"?([^"]+)"?', line)
            bssid_m = re.search(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line)
            if ssid_m and "WifiConfiguration" in line:
                results["saved_networks"].append({
                    "ssid": ssid_m.group(1),
                    "bssid": bssid_m.group(0) if bssid_m else "",
                })
    ok, config = adb.adb_shell(serial, "cat /data/misc/wifi/WifiConfigStore.xml")
    if ok and config:
        for m in re.finditer(r'name="SSID"\s+value="([^"]+)"', config):
            results["saved_networks"].append({"ssid": m.group(1), "source": "WifiConfigStore"})
    results["stats"] = {"total_saved": len(results["saved_networks"])}
    return results


def analyze_bluetooth(serial):
    results = {"paired_devices": [], "stats": {}}
    ok, data = adb.adb_shell(serial, "dumpsys bluetooth_manager")
    if ok and data:
        name = addr = None
        for line in data.splitlines():
            if "name:" in line.lower():
                m = re.search(r"name:\s*(.+)", line, re.I)
                if m:
                    name = m.group(1).strip()
            if "address:" in line.lower() or "mac:" in line.lower():
                m = re.search(r'([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', line)
                if m:
                    addr = m.group(0)
                    if name:
                        results["paired_devices"].append({"name": name, "address": addr})
                        name = addr = None
    results["stats"] = {"paired_count": len(results["paired_devices"])}
    return results


def analyze_apps(serial):
    results = {"installed": [], "social_media": [], "messaging": [], "suspicious": []}
    ok, packages = adb.pm_list(serial, "packages", "-f")
    if ok and packages:
        social_kw = ["facebook", "twitter", "instagram", "snapchat", "tiktok",
                     "telegram", "whatsapp", "signal", "linkedin", "tinder"]
        msg_kw = ["messaging", "sms", "mms", "chat", "messenger"]
        for line in packages.splitlines():
            if "package:" in line:
                pkg_line = line.replace("package:", "").strip()
                parts = pkg_line.split("=")
                if len(parts) == 2:
                    pkg = parts[0]
                    results["installed"].append({"package": pkg, "apk_path": parts[1]})
                    if any(kw in pkg.lower() for kw in social_kw):
                        results["social_media"].append({"package": pkg})
                    if any(kw in pkg.lower() for kw in msg_kw):
                        results["messaging"].append({"package": pkg})
    ok, backup = adb.adb_shell(serial, "dumpsys package backup")
    if ok and backup:
        for line in backup.splitlines():
            m = re.search(r"packageName=([^\s]+)", line)
            if m:
                results["suspicious"].append({"package": m.group(1), "reason": "backup_enabled"})
    return results


def analyze_calendar(serial):
    results = {"events": [], "stats": {}}
    data = adb.query_content(serial, "content://com.android.calendar/events", sort="dtstart DESC")
    if "Row:" not in data:
        return results
    events = []
    for line in data.splitlines():
        if "Row:" not in line:
            continue
        event = {}
        for field in ["title", "description", "eventLocation", "dtstart", "dtend", "allDay"]:
            match = re.search(rf"{field}=([^,]+)", line)
            if match:
                event[field] = match.group(1).strip()
        if "dtstart" in event:
            event["start_readable"] = _ts_to_str(event["dtstart"])
        events.append(event)
    results["events"] = events
    results["stats"] = {"total": len(events)}
    return results


def recover_deleted(serial):
    _ensure_evidence_dir()
    results = {"trash_files": [], "deleted_files": [], "sqlite_journals": [], "summary": {}}
    ok, trashed = adb.adb_shell(serial, 'find /sdcard/ -name ".trashed*" -o -name ".deleted*" -o -name "*.trash" 2>/dev/null')
    if ok and trashed:
        for line in trashed.splitlines():
            line = line.strip()
            if line and "Permission denied" not in line:
                results["trash_files"].append({"path": line})
    ok, deleted = adb.adb_shell(serial, 'find /sdcard/DCIM/ -name ".trashed*" -o -name ".pending*" 2>/dev/null')
    if ok and deleted:
        for line in deleted.splitlines():
            line = line.strip()
            if line and "Permission denied" not in line:
                results["deleted_files"].append({"path": line})
    ok, wal = adb.adb_shell(serial, 'find /data/data/ -name "*.db-wal" -o -name "*.db-shm" 2>/dev/null')
    if ok and wal:
        for line in wal.splitlines():
            line = line.strip()
            if line and "Permission denied" not in line:
                results["sqlite_journals"].append({"path": line})
    results["summary"] = {
        "trash": len(results["trash_files"]),
        "deleted": len(results["deleted_files"]),
        "sqlite_journals": len(results["sqlite_journals"]),
    }
    return results


def analyze_notes(serial):
    results = {"google_keep": [], "samsung_notes": []}
    ok, data = adb.query_content(serial, "content://com.google.android.keep/notes")
    if ok and data and "Row:" in data:
        for line in data.splitlines():
            if "Row:" in line:
                t = re.search(r"title=([^,]+)", line)
                x = re.search(r"text=([^,]+)", line)
                if t or x:
                    results["google_keep"].append({
                        "title": t.group(1).strip() if t else "",
                        "text": x.group(1).strip() if x else "",
                    })
    return results


def analyze_whatsapp(serial, evidence_root=None):
    results = {"messages": [], "contacts": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    msgstore_paths = [
        "/data/data/com.whatsapp/databases/msgstore.db",
        "/data/data/com.whatsapp/databases/msgstore.db.crypt14",
        "/data/data/com.whatsapp/databases/msgstore.db.crypt15",
    ]
    wa_db_path = "/data/data/com.whatsapp/databases/wa.db"
    local_msgstore = None
    local_wa = None
    for remote in msgstore_paths:
        local_msgstore = _safe_pull_db(serial, remote, f"whatsapp_msgstore_{serial[:8]}.db", base)
        if local_msgstore:
            break
    local_wa = _safe_pull_db(serial, wa_db_path, f"whatsapp_wa_{serial[:8]}.db", base)
    if local_msgstore:
        try:
            messages = _query_db(local_msgstore, "SELECT _id, key_remote_jid, key_from_me, data, timestamp, status, subject, remote_resource, row_type, media_url, media_mime_type, media_size FROM messages ORDER BY timestamp DESC LIMIT 10000")
            for msg in messages:
                msg["timestamp_readable"] = _ts_to_str(msg.get("timestamp", 0))
                msg["direction"] = "outgoing" if msg.get("key_from_me") == 1 else "incoming"
            results["messages"] = messages
        except Exception as e:
            logger.error(f"WhatsApp msgstore parse error: {e}")
    if local_wa:
        try:
            contacts = _query_db(local_wa, "SELECT jid, display_name, phone, status FROM wa_contacts WHERE is_whatsapp=1")
            results["contacts"] = contacts
        except Exception as e:
            logger.error(f"WhatsApp wa.db parse error: {e}")
    results["stats"] = {
        "total_messages": len(results["messages"]),
        "total_contacts": len(results["contacts"]),
        "incoming": sum(1 for m in results["messages"] if m.get("direction") == "incoming"),
        "outgoing": sum(1 for m in results["messages"] if m.get("direction") == "outgoing"),
        "with_media": sum(1 for m in results["messages"] if m.get("media_url")),
    }
    return results


def analyze_telegram(serial, evidence_root=None):
    results = {"messages": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    cache_paths = [
        "/data/data/org.telegram.messenger/files/cache4.db",
        "/data/data/org.telegram.messenger/databases/cache4.db",
    ]
    userconfig_paths = [
        "/data/data/org.telegram.messenger/files/userconfig.sql",
        "/data/data/org.telegram.messenger/shared_prefs/userconfig.xml",
    ]
    local_cache = None
    for remote in cache_paths:
        local_cache = _safe_pull_db(serial, remote, f"telegram_cache_{serial[:8]}.db", base)
        if local_cache:
            break
    if local_cache:
        try:
            tables = _query_db(local_cache, "SELECT name FROM sqlite_master WHERE type='table'")
            table_names = [t.get("name", "") for t in tables]
            messages = []
            if "messages_v2" in table_names:
                messages = _query_db(local_cache, "SELECT mid, uid, message, date, out, media, reply_to_mid, forward_mid FROM messages_v2 ORDER BY date DESC LIMIT 10000")
            elif "messages" in table_names:
                messages = _query_db(local_cache, "SELECT mid, uid, message, date, out, media FROM messages ORDER BY date DESC LIMIT 10000")
            for msg in messages:
                msg["date_readable"] = _ts_to_str(msg.get("date", 0))
                msg["direction"] = "outgoing" if msg.get("out") == 1 else "incoming"
            results["messages"] = messages
        except Exception as e:
            logger.error(f"Telegram cache4 parse error: {e}")
    for remote in userconfig_paths:
        local_conf = _safe_pull_db(serial, remote, f"telegram_userconfig_{serial[:8]}.xml", base)
        if local_conf:
            try:
                with open(local_conf, "r", errors="replace") as f:
                    content = f.read()
                    phone_m = re.search(r'phone="([^"]+)"', content)
                    name_m = re.search(r'auth_name="([^"]+)"', content)
                    results["user_config"] = {
                        "phone": phone_m.group(1) if phone_m else "",
                        "name": name_m.group(1) if name_m else "",
                    }
            except Exception:
                pass
            break
    results["stats"] = {
        "total_messages": len(results["messages"]),
        "incoming": sum(1 for m in results["messages"] if m.get("direction") == "incoming"),
        "outgoing": sum(1 for m in results["messages"] if m.get("direction") == "outgoing"),
    }
    return results


def analyze_instagram(serial, evidence_root=None):
    results = {"databases": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    db_paths = adb.shell_find(serial, "/data/data/com.instagram.android", ["*.db", "*.db-wal"], timeout=30)
    pulled = 0
    for remote_db in db_paths:
        fname = Path(remote_db).name
        local_db = _safe_pull_db(serial, remote_db, f"instagram_{fname}_{serial[:8]}", base)
        if local_db:
            try:
                tables = _query_db(local_db, "SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [t.get("name", "") for t in tables]
                db_info = {"remote_path": remote_db, "tables": table_names, "rows": {}}
                for tbl in table_names:
                    count_rows = _query_db(local_db, f"SELECT COUNT(*) as cnt FROM [{tbl}]")
                    db_info["rows"][tbl] = count_rows[0].get("cnt", 0) if count_rows else 0
                for tbl in table_names:
                    if any(kw in tbl.lower() for kw in ["message", "inbox", "thread", "dm"]):
                        sample = _query_db(local_db, f"SELECT * FROM [{tbl}] LIMIT 50")
                        db_info[f"sample_{tbl}"] = sample
                results["databases"].append(db_info)
                pulled += 1
            except Exception as e:
                logger.error(f"Instagram DB parse error: {e}")
    results["stats"] = {"databases_found": len(db_paths), "databases_pulled": pulled}
    return results


def analyze_snapchat(serial, evidence_root=None):
    results = {"databases": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    db_paths = adb.shell_find(serial, "/data/data/com.snapchat.android", ["*.db", "*.db-wal"], timeout=30)
    pulled = 0
    for remote_db in db_paths:
        fname = Path(remote_db).name
        local_db = _safe_pull_db(serial, remote_db, f"snapchat_{fname}_{serial[:8]}", base)
        if local_db:
            try:
                tables = _query_db(local_db, "SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [t.get("name", "") for t in tables]
                db_info = {"remote_path": remote_db, "tables": table_names, "rows": {}}
                for tbl in table_names:
                    count_rows = _query_db(local_db, f"SELECT COUNT(*) as cnt FROM [{tbl}]")
                    db_info["rows"][tbl] = count_rows[0].get("cnt", 0) if count_rows else 0
                for tbl in table_names:
                    if any(kw in tbl.lower() for kw in ["message", "conversation", "snap", "chat"]):
                        sample = _query_db(local_db, f"SELECT * FROM [{tbl}] LIMIT 50")
                        db_info[f"sample_{tbl}"] = sample
                results["databases"].append(db_info)
                pulled += 1
            except Exception as e:
                logger.error(f"Snapchat DB parse error: {e}")
    results["stats"] = {"databases_found": len(db_paths), "databases_pulled": pulled}
    return results


def analyze_tiktok(serial, evidence_root=None):
    results = {"databases": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    db_paths = adb.shell_find(serial, "/data/data/com.zhiliaoapp.musically", ["*.db", "*.db-wal"], timeout=30)
    if not db_paths:
        db_paths = adb.shell_find(serial, "/data/data/com.ss.android.ugc.trill", ["*.db", "*.db-wal"], timeout=30)
    pulled = 0
    for remote_db in db_paths:
        fname = Path(remote_db).name
        local_db = _safe_pull_db(serial, remote_db, f"tiktok_{fname}_{serial[:8]}", base)
        if local_db:
            try:
                tables = _query_db(local_db, "SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [t.get("name", "") for t in tables]
                db_info = {"remote_path": remote_db, "tables": table_names, "rows": {}}
                for tbl in table_names:
                    count_rows = _query_db(local_db, f"SELECT COUNT(*) as cnt FROM [{tbl}]")
                    db_info["rows"][tbl] = count_rows[0].get("cnt", 0) if count_rows else 0
                results["databases"].append(db_info)
                pulled += 1
            except Exception as e:
                logger.error(f"TikTok DB parse error: {e}")
    results["stats"] = {"databases_found": len(db_paths), "databases_pulled": pulled}
    return results


def analyze_facebook(serial, evidence_root=None):
    results = {"databases": [], "messages": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    db_paths = adb.shell_find(serial, "/data/data/com.facebook.orca", ["*.db", "*.db-wal"], timeout=30)
    pulled = 0
    for remote_db in db_paths:
        fname = Path(remote_db).name
        local_db = _safe_pull_db(serial, remote_db, f"facebook_{fname}_{serial[:8]}", base)
        if local_db:
            try:
                tables = _query_db(local_db, "SELECT name FROM sqlite_master WHERE type='table'")
                table_names = [t.get("name", "") for t in tables]
                db_info = {"remote_path": remote_db, "tables": table_names, "rows": {}}
                for tbl in table_names:
                    count_rows = _query_db(local_db, f"SELECT COUNT(*) as cnt FROM [{tbl}]")
                    db_info["rows"][tbl] = count_rows[0].get("cnt", 0) if count_rows else 0
                for tbl in table_names:
                    if any(kw in tbl.lower() for kw in ["message", "sms", "thread", "inbox"]):
                        sample = _query_db(local_db, f"SELECT * FROM [{tbl}] LIMIT 50")
                        db_info[f"sample_{tbl}"] = sample
                        results["messages"].extend(sample)
                results["databases"].append(db_info)
                pulled += 1
            except Exception as e:
                logger.error(f"Facebook Messenger DB parse error: {e}")
    results["stats"] = {"databases_found": len(db_paths), "databases_pulled": pulled, "message_samples": len(results["messages"])}
    return results


def analyze_transactions(serial, evidence_root=None):
    results = {"momo": [], "airteltigo": [], "vodafone_cash": [], "bank_apps": [], "stats": {}}
    _ensure_evidence_dir()
    base = evidence_root or EVIDENCE_ROOT
    mtn_pkg = "com.mtraffic"
    airtel_pkg = "com.airtelmoney"
    voda_pkg = "com.vodafone.cash"
    bank_keywords = ["bank", "finance", "payment", "wallet", "credit", "debit"]
    all_pkgs_ok, packages_raw = adb.pm_list(serial, "packages")
    installed = []
    if all_pkgs_ok and packages_raw:
        installed = [line.replace("package:", "").strip() for line in packages_raw.splitlines() if "package:" in line]
    app_db_map = {
        "momo": ([mtn_pkg], [
            "/data/data/com.mtraffic/databases/",
        ]),
        "airteltigo": ([airtel_pkg], [
            "/data/data/com.airtelmoney/databases/",
        ]),
        "vodafone_cash": ([voda_pkg], [
            "/data/data/com.vodafone.cash/databases/",
        ]),
    }
    for category, (pkg_list, db_dirs) in app_db_map.items():
        found_pkg = any(p in installed for p in pkg_list)
        if not found_pkg:
            continue
        for db_dir in db_dirs:
            db_files = adb.shell_ls(serial, db_dir, timeout=15)
            for fname in db_files:
                if not fname.endswith(".db"):
                    continue
                remote_db = db_dir + fname
                local_db = _safe_pull_db(serial, remote_db, f"transaction_{category}_{fname}_{serial[:8]}", base)
                if local_db:
                    try:
                        tables = _query_db(local_db, "SELECT name FROM sqlite_master WHERE type='table'")
                        table_names = [t.get("name", "") for t in tables]
                        for tbl in table_names:
                            if any(kw in tbl.lower() for kw in ["transaction", "payment", "transfer", "history", "sms", "message"]):
                                sample = _query_db(local_db, f"SELECT * FROM [{tbl}] LIMIT 200")
                                results[category].extend(sample)
                    except Exception as e:
                        logger.error(f"Transaction DB parse error ({category}): {e}")
    for pkg in installed:
        if any(kw in pkg.lower() for kw in bank_keywords):
            db_dir = f"/data/data/{pkg}/databases/"
            db_files = adb.shell_ls(serial, db_dir, timeout=15)
            for fname in db_files:
                if not fname.endswith(".db"):
                    continue
                remote_db = db_dir + fname
                local_db = _safe_pull_db(serial, remote_db, f"bank_{pkg}_{fname}_{serial[:8]}", base)
                if local_db:
                    try:
                        tables = _query_db(local_db, "SELECT name FROM sqlite_master WHERE type='table'")
                        table_names = [t.get("name", "") for t in tables]
                        db_info = {"package": pkg, "database": fname, "tables": table_names, "sample_data": {}}
                        for tbl in table_names:
                            if any(kw in tbl.lower() for kw in ["transaction", "payment", "transfer", "history", "ledger", "statement"]):
                                sample = _query_db(local_db, f"SELECT * FROM [{tbl}] LIMIT 100")
                                db_info["sample_data"][tbl] = sample
                        if db_info["sample_data"]:
                            results["bank_apps"].append(db_info)
                    except Exception as e:
                        logger.error(f"Bank app DB parse error ({pkg}): {e}")
    results["stats"] = {
        "momo_records": len(results["momo"]),
        "airteltigo_records": len(results["airteltigo"]),
        "vodafone_cash_records": len(results["vodafone_cash"]),
        "bank_apps_found": len(results["bank_apps"]),
    }
    return results


def analyze_installed_apps(serial):
    results = {"apps": [], "stats": {}}
    ok, output = adb.pm_list(serial, "packages", "-f -i")
    if not ok or not output:
        return results
    for line in output.splitlines():
        if "package:" not in line:
            continue
        pkg_line = line.replace("package:", "").strip()
        parts = pkg_line.split("=")
        if len(parts) < 2:
            continue
        pkg = parts[0]
        apk_path = parts[1] if len(parts) > 1 else ""
        version_ok, version_name = adb.adb_shell(serial, f"dumpsys package {pkg} | grep versionName", timeout=10)
        version = ""
        if version_ok and version_name:
            vm = re.search(r"versionName=(\S+)", version_name)
            if vm:
                version = vm.group(1)
        install_ok, install_line = adb.adb_shell(serial, f"dumpsys package {pkg} | grep firstInstallTime", timeout=10)
        install_time = ""
        if install_ok and install_line:
            tm = re.search(r"firstInstallTime=(.+)", install_line)
            if tm:
                install_time = tm.group(1).strip()
        results["apps"].append({"package": pkg, "apk_path": apk_path, "version": version, "first_install": install_time})
    results["stats"] = {"total": len(results["apps"])}
    return results


def analyze_app_permissions(serial):
    results = {"permissions": {}, "dangerous": [], "stats": {}}
    ok, output = adb.pm_list(serial, "packages", "-f")
    if not ok or not output:
        return results
    packages = []
    for line in output.splitlines():
        if "package:" in line:
            pkg_line = line.replace("package:", "").strip()
            parts = pkg_line.split("=")
            if len(parts) >= 1:
                packages.append(parts[0])
    dangerous_kw = ["CAMERA", "RECORD_AUDIO", "READ_SMS", "SEND_SMS", "READ_CONTACTS",
                     "READ_PHONE_STATE", "READ_CALL_LOG", "WRITE_CALL_LOG", "ACCESS_FINE_LOCATION",
                     "ACCESS_COARSE_LOCATION", "READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE",
                     "READ_CALENDAR", "WRITE_CALENDAR", "RECEIVE_SMS", "BODY_SENSORS",
                     "CALL_PHONE", "PROCESS_OUTGOING_CALLS", "READ_PHONE_NUMBERS"]
    for pkg in packages[:200]:
        perm_ok, perm_data = adb.adb_shell(serial, f"dumpsys package {pkg} | grep permission", timeout=10)
        if not perm_ok or not perm_data:
            continue
        perms = []
        for pline in perm_data.splitlines():
            pm = re.search(r"android\.permission\.([A-Z_]+)", pline)
            if pm:
                perms.append(pm.group(1))
        perms = list(set(perms))
        if perms:
            results["permissions"][pkg] = perms
            app_dangerous = [p for p in perms if p in dangerous_kw]
            if app_dangerous:
                results["dangerous"].append({"package": pkg, "dangerous_permissions": app_dangerous})
    results["stats"] = {
        "apps_scanned": len(results["permissions"]),
        "apps_with_dangerous": len(results["dangerous"]),
    }
    return results


def analyze_wifi_passwords(serial):
    results = {"passwords": [], "config_files": [], "stats": {}}
    config_paths = [
        "/data/misc/wifi/WifiConfigStore.xml",
        "/data/misc/wifi/WifiConfigStore.xml.bak",
        "/data/misc/wifi_softap/SoftApManager.xml",
    ]
    for remote_path in config_paths:
        local_name = f"wifi_config_{Path(remote_path).name}_{serial[:8]}"
        ok, content = adb.adb_shell(serial, f"cat {remote_path} 2>/dev/null", timeout=10)
        if ok and content:
            results["config_files"].append({"path": remote_path, "size": len(content)})
            ssid_matches = re.findall(r'name="SSID"\s+value="([^"]+)"', content)
            psk_matches = re.findall(r'name="PSK"\s+value="([^"]+)"', content)
            wep_matches = re.findall(r'name="WEP"\s+value="([^"]+)"', content)
            for i, ssid in enumerate(ssid_matches):
                entry = {"ssid": ssid, "source": remote_path}
                if i < len(psk_matches):
                    entry["psk"] = psk_matches[i]
                elif i < len(wep_matches):
                    entry["wep"] = wep_matches[i]
                results["passwords"].append(entry)
    ok2, wifi_data = adb.adb_shell(serial, "dumpsys wifi", timeout=15)
    if ok2 and wifi_data:
        for line in wifi_data.splitlines():
            if "SSID:" in line and "network" in line.lower():
                ssid_m = re.search(r'SSID:\s*"?([^"]+)"?', line)
                psk_m = re.search(r'preSharedKey.*"([^"]+)"', line)
                if ssid_m and psk_m:
                    results["passwords"].append({
                        "ssid": ssid_m.group(1),
                        "pre_shared_key": psk_m.group(1),
                        "source": "dumpsys_wifi",
                    })
    results["stats"] = {
        "config_files_found": len(results["config_files"]),
        "passwords_found": len(results["passwords"]),
    }
    return results


def analyze_clipboard(serial):
    results = {"clipboard": [], "stats": {}}
    ok, data = adb.adb_shell(serial, "dumpsys clipboard", timeout=10)
    if ok and data:
        current_m = re.search(r'primary clip:.*?text=(.+?)(?:\n|$)', data, re.I)
        if current_m:
            results["clipboard"].append({"content": current_m.group(1).strip(), "type": "current"})
        for line in data.splitlines():
            if "ClipData" in line or "text=" in line.lower():
                text_m = re.search(r'text=(.+?)(?:\s*,|$)', line)
                if text_m:
                    val = text_m.group(1).strip()
                    if val and val not in [c.get("content", "") for c in results["clipboard"]]:
                        results["clipboard"].append({"content": val, "type": "historical"})
    ok2, alt = adb.adb_shell(serial, "service call clipboard 1", timeout=10)
    if ok2 and alt:
        text_m = re.search(r"'(.+?)'", alt)
        if text_m:
            val = text_m.group(1)
            if val and val not in [c.get("content", "") for c in results["clipboard"]]:
                results["clipboard"].append({"content": val, "type": "service_call"})
    results["stats"] = {"entries": len(results["clipboard"])}
    return results


def analyze_notifications(serial):
    results: dict = {"notifications": [], "stats": {}}
    ok, data = adb.adb_shell(serial, "dumpsys notification --noredact", timeout=30)
    if not ok or not data:
        return results
    current_pkg = None
    current_title = None
    current_text = None
    current_time = None
    for line in data.splitlines():
        pkg_m = re.search(r'pkg=(\S+)', line)
        if pkg_m:
            if current_pkg and (current_title or current_text):
                results["notifications"].append({
                    "package": current_pkg,
                    "title": current_title or "",
                    "text": current_text or "",
                    "time": current_time or "",
                })
            current_pkg = pkg_m.group(1)
            current_title = None
            current_text = None
            current_time = None
        title_m = re.search(r'title=(.+?)(?:\s*,|\s*$)', line)
        if title_m:
            current_title = title_m.group(1).strip()
        text_m = re.search(r'text=(.+?)(?:\s*,|\s*$)', line)
        if text_m:
            current_text = text_m.group(1).strip()
        time_m = re.search(r'when=(\d+)', line)
        if time_m:
            current_time = _ts_to_str(time_m.group(1))
    if current_pkg and (current_title or current_text):
        results["notifications"].append({
            "package": current_pkg,
            "title": current_title or "",
            "text": current_text or "",
            "time": current_time or "",
        })
    pkg_counts = Counter(n.get("package", "") for n in results["notifications"])
    results["stats"] = {
        "total": len(results["notifications"]),
        "by_package": [{"package": p, "count": c} for p, c in pkg_counts.most_common(20)],
    }
    return results


def analyze_usage_stats(serial):
    results = {"usage": [], "stats": {}}
    ok, data = adb.adb_shell(serial, "dumpsys usagestats", timeout=30)
    if not ok or not data:
        return results
    for line in data.splitlines():
        pkg_m = re.search(r'package=(\S+)', line)
        time_m = re.search(r'time=(\d+)', line)
        type_m = re.search(r'type=(\d+)', line)
        if pkg_m:
            entry = {"package": pkg_m.group(1)}
            if time_m:
                entry["time_ms"] = int(time_m.group(1))
                entry["time_readable"] = _ts_to_str(time_m.group(1))
            if type_m:
                t = type_m.group(1)
                entry["event_type"] = {"0": "none", "1": "move_to_foreground", "2": "move_to_background", "3": "activity_launch"}.get(t, t)
            results["usage"].append(entry)
    results["usage"] = results["usage"][:5000]
    if results["usage"]:
        pkg_usage = Counter(u.get("package", "") for u in results["usage"])
        results["stats"] = {
            "total_events": len(results["usage"]),
            "top_apps": [{"package": p, "count": c} for p, c in pkg_usage.most_common(20)],
        }
    return results


ANALYSIS_MODULES = {
    "call_log": ("Call Log Analysis", analyze_call_log),
    "sms_mms": ("SMS/MMS Analysis", analyze_sms_mms),
    "contacts": ("Contacts Analysis", analyze_contacts),
    "browser": ("Browser History", analyze_browser),
    "gps": ("GPS/Location", analyze_gps),
    "wifi": ("WiFi History", analyze_wifi),
    "bluetooth": ("Bluetooth Pairings", analyze_bluetooth),
    "apps": ("Application Artifacts", analyze_apps),
    "calendar": ("Calendar Events", analyze_calendar),
    "deleted": ("Deleted Data Recovery", recover_deleted),
    "notes": ("Notes & Memos", analyze_notes),
    "whatsapp": ("WhatsApp Analysis", analyze_whatsapp),
    "telegram": ("Telegram Analysis", analyze_telegram),
    "instagram": ("Instagram Analysis", analyze_instagram),
    "snapchat": ("Snapchat Analysis", analyze_snapchat),
    "tiktok": ("TikTok Analysis", analyze_tiktok),
    "facebook": ("Facebook Messenger Analysis", analyze_facebook),
    "transactions": ("Financial Transactions", analyze_transactions),
    "installed_apps": ("Installed Applications", analyze_installed_apps),
    "app_permissions": ("App Permissions Audit", analyze_app_permissions),
    "wifi_passwords": ("WiFi Password Extraction", analyze_wifi_passwords),
    "clipboard": ("Clipboard Contents", analyze_clipboard),
    "notifications": ("Notification History", analyze_notifications),
    "usage_stats": ("App Usage Statistics", analyze_usage_stats),
}


#: Loaders that pull from app-private (``/data/data/<pkg>``) or keystore
#: (``/data/misc/wifi``) paths. Android exposes these to root only, so on an
#: unrooted device they cannot succeed. The tool reports NOT_ACCESSIBLE with
#: that reason rather than returning an empty result that reads as success.
ROOT_REQUIRED_MODULES = frozenset({
    "whatsapp", "telegram", "instagram", "snapchat", "tiktok", "facebook",
    "transactions", "browser", "wifi_passwords", "deleted",
})

# Verification tiers are NOT declared here. A tier is a claim that a loader
# works, so it is earned and recorded with its evidence in the case's
# verification.jsonl via evidence_store.record_verification(). A loader with no
# such record is T3_UNTESTED - which, today, is all of them. The old hardcoded
# table claimed T1_DEVICE_VERIFIED for fourteen loaders with no device ever
# connected; that was a fabricated claim and it is gone.


def run_examination(serial, modules=None):
    """
    Run the requested loaders and report what each one actually achieved.

    Every loader ends with one of the eight AcquisitionStatus values and a
    VerificationTier. A loader that recovered nothing is never presented as a
    success - the status and its reason are recorded to the case status log.
    """
    if modules is None:
        modules = list(ANALYSIS_MODULES.keys())
    results = {}

    case_id = evidence_store.resolve_case()
    try:
        device_rooted = adb.is_device_rooted(serial)
    except Exception:
        device_rooted = False

    for mod_key in modules:
        if mod_key not in ANALYSIS_MODULES:
            continue
        name, func = ANALYSIS_MODULES[mod_key]
        needs_root = mod_key in ROOT_REQUIRED_MODULES

        if needs_root and not device_rooted:
            evidence_store.record_status(
                case_id, mod_key, AcquisitionStatus.NOT_ACCESSIBLE,
                f"{name} reads app-private paths; device is not rooted.",
            )
            results[mod_key] = {
                "name": name,
                "status": AcquisitionStatus.NOT_ACCESSIBLE,
                "detail": "Device is not rooted. App-private data is unreadable "
                          "without root. See the device capability report.",
                "findings": {},
                "item_count": 0,
                "requires_root": True,
                "verification_tier": evidence_store.get_verification_tier(case_id, mod_key),
            }
            continue

        try:
            findings = func(serial)
            total_items = sum(len(v) for v in findings.values() if isinstance(v, list))

            if total_items > 0:
                status = AcquisitionStatus.ACQUIRED
                detail = f"{total_items} item(s) recovered."
            else:
                status = AcquisitionStatus.NOT_PRESENT
                detail = ("Loader ran successfully but the device held nothing "
                          "matching this artifact type.")

            evidence_store.record_status(case_id, mod_key, status, detail)
            results[mod_key] = {
                "name": name,
                "status": status,
                "detail": detail,
                "findings": findings,
                "item_count": total_items,
                "requires_root": needs_root,
                "verification_tier": evidence_store.get_verification_tier(case_id, mod_key),
            }

        except Exception as e:
            logger.error(f"Loader {mod_key} failed: {e}")
            evidence_store.record_status(case_id, mod_key, AcquisitionStatus.FAILED, str(e))
            results[mod_key] = {
                "name": name,
                "status": AcquisitionStatus.FAILED,
                "detail": str(e),
                "findings": {},
                "item_count": 0,
                "requires_root": needs_root,
                "verification_tier": evidence_store.get_verification_tier(case_id, mod_key),
            }

    # Derived so a report or the UI can show what was and was not achieved.
    succeeded = [k for k, v in results.items()
                 if v["status"] in AcquisitionStatus.SUCCESSFUL]
    not_accessible = [k for k, v in results.items()
                      if v["status"] == AcquisitionStatus.NOT_ACCESSIBLE]

    summary = {
        "device_rooted": device_rooted,
        "modules_run": len(results),
        "modules_succeeded": len(succeeded),
        "modules_not_accessible": len(not_accessible),
        "total_items": sum(v["item_count"] for v in results.values()),
        "succeeded": succeeded,
        "not_accessible": not_accessible,
    }

    evidence_store.write_parsed(case_id, "examination_summary", summary)
    results["_summary"] = summary
    return results
