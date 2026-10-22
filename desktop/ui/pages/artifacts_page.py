from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QGridLayout, QLineEdit)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QFont
from pathlib import Path
import re, logging, json
from datetime import datetime, timezone
from ...core import adb_manager as adb

logger = logging.getLogger(__name__)

DARK_STYLE = """
QWidget { background: #0a0e17; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QFrame { background: transparent; }
QPushButton { background: #2563eb; color: white; border: none; border-radius: 8px; padding: 8px 20px;
    font-weight: 600; font-size: 13px; }
QPushButton:hover { background: #1d4ed8; }
QPushButton:disabled { background: #334155; color: #64748b; }
QLineEdit { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 10px 14px;
    color: #e2e8f0; font-size: 13px; }
QLineEdit:focus { border: 1px solid #2563eb; }
QTextEdit { background: #0f172a; border: 1px solid #1e293b; border-radius: 6px; padding: 12px; color: #e2e8f0; }
QScrollBar:vertical { background: transparent; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #334155; border-radius: 4px; min-height: 30px; }
QScrollBar:add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
"""


def _ts(val, divide=True):
    if not val:
        return ""
    val = str(val).strip()
    if val.isdigit():
        try:
            ms = int(val)
            if divide and ms > 1000000000000:
                ms = ms // 1000
            return datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return val
    return val


def _ts_relative(val, divide=True):
    if not val:
        return ""
    val = str(val).strip()
    if val.isdigit():
        try:
            ms = int(val)
            if divide and ms > 1000000000000:
                ms = ms // 1000
            dt = datetime.fromtimestamp(ms, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            diff = now - dt
            if diff.days == 0:
                return dt.strftime("%H:%M")
            elif diff.days == 1:
                return "Yesterday"
            elif diff.days < 7:
                return dt.strftime("%A")
            elif diff.days < 365:
                return dt.strftime("%b %d")
            else:
                return dt.strftime("%b %d, %Y")
        except Exception:
            return val
    return val


def _parse_rows(data):
    results = []
    if "Row:" not in str(data):
        return results
    for line in str(data).splitlines():
        if "Row:" not in line:
            continue
        row_str = line.split("Row:", 1)[1].strip()
        entry = {}
        for pair in re.split(r',\s*(?=\w+=)', row_str):
            if "=" in pair:
                k, v = pair.split("=", 1)
                entry[k.strip()] = v.strip().strip('"')
        if entry:
            results.append(entry)
    return results


def _resolve_contact_name(contacts, phone_number):
    if not phone_number:
        return ""
    clean = re.sub(r'[\s\-\(\)]', '', phone_number)
    for c in contacts:
        cphones = c.get("_data", {}).get("phone", "")
        for p in cphones.split(","):
            p_clean = re.sub(r'[\s\-\(\)]', '', p.strip())
            if p_clean and (clean.endswith(p_clean[-7:]) or p_clean.endswith(clean[-7:])) and len(clean) >= 5:
                return c.get("_data", {}).get("name", "")
    return ""


def _conversation_sort_key(conv):
    msgs = conv.get("messages", [])
    if not msgs:
        return ""
    last = msgs[0]
    return last.get("date", "") or last.get("_data", {}).get("date", "")


APP_DEFS = [
    {"name": "SMS", "color": "#10b981", "bg": "#0d3328", "category": "SMS",
     "desc": "Text Messages", "type": "chat"},
    {"name": "Calls", "color": "#3b82f6", "bg": "#0f1d33", "category": "Calls",
     "desc": "Call Log", "type": "calls"},
    {"name": "WhatsApp", "color": "#25d366", "bg": "#0d2e1c", "category": "WhatsApp",
     "desc": "Chats & Media", "type": "chat"},
    {"name": "Telegram", "color": "#0088cc", "bg": "#0a1f2e", "category": "Telegram",
     "desc": "Chats & Cache", "type": "chat"},
    {"name": "Instagram", "color": "#e4405f", "bg": "#2e0d1a", "category": "Instagram",
     "desc": "Photos & Stories", "type": "files"},
    {"name": "Twitter / X", "color": "#1da1f2", "bg": "#0d1f2e", "category": "Twitter",
     "desc": "Tweets & Media", "type": "files"},
    {"name": "Contacts", "color": "#f59e0b", "bg": "#2e220d", "category": "Contacts",
     "desc": "Contact List", "type": "list"},
    {"name": "Calendar", "color": "#06b6d4", "bg": "#0d262e", "category": "Calendar",
     "desc": "Events & Tasks", "type": "list"},
    {"name": "Media", "color": "#8b5cf6", "bg": "#1a0d2e", "category": "Media",
     "desc": "Photos & Videos", "type": "media"},
    {"name": "Documents", "color": "#64748b", "bg": "#1a1f2e", "category": "Documents",
     "desc": "PDF, DOCX, XLSX", "type": "list"},
    {"name": "Transactions", "color": "#10b981", "bg": "#0d2e1c", "category": "Transactions",
     "desc": "MoMo / Payments", "type": "list"},
    {"name": "Emails", "color": "#ef4444", "bg": "#2e0d0d", "category": "Emails & Accounts",
     "desc": "Email Accounts", "type": "list"},
    {"name": "WiFi", "color": "#06b6d4", "bg": "#0d262e", "category": "WiFi & Network",
     "desc": "Networks & BT", "type": "list"},
    {"name": "System", "color": "#64748b", "bg": "#1a1f2e", "category": "System Info",
     "desc": "Device Info", "type": "list"},
    {"name": "Deleted", "color": "#ef4444", "bg": "#2e0d0d", "category": "Deleted Files",
     "desc": "Recovered Files", "type": "list"},
    {"name": "Crashes", "color": "#f59e0b", "bg": "#2e220d", "category": "Crash Logs",
     "desc": "Tombstones", "type": "list"},
    {"name": "Facebook", "color": "#1877f2", "bg": "#0d1f33", "category": "Facebook",
     "desc": "Messenger & Feed", "type": "files"},
    {"name": "Snapchat", "color": "#fffc00", "bg": "#2e2e0d", "category": "Snapchat",
     "desc": "Snaps & Cache", "type": "files"},
    {"name": "TikTok", "color": "#ff0050", "bg": "#2e0d1a", "category": "TikTok",
     "desc": "Videos & Cache", "type": "files"},
    {"name": "YouTube", "color": "#ff0000", "bg": "#2e0d0d", "category": "YouTube",
     "desc": "Videos & Cache", "type": "files"},
    {"name": "Downloads", "color": "#22c55e", "bg": "#0d2e15", "category": "Downloads",
     "desc": "Downloaded Files", "type": "files"},
    {"name": "Screenshots", "color": "#a855f7", "bg": "#1a0d2e", "category": "Screenshots",
     "desc": "Screenshots & Recordings", "type": "files"},
    {"name": "Camera", "color": "#f97316", "bg": "#2e1a0d", "category": "Camera",
     "desc": "Photos & Videos", "type": "files"},
    {"name": "Recordings", "color": "#ec4899", "bg": "#2e0d1a", "category": "Recordings",
     "desc": "Voice Memos", "type": "files"},
    {"name": "Chrome", "color": "#4285f4", "bg": "#0d1a2e", "category": "Chrome",
     "desc": "Bookmarks & History", "type": "list"},
    {"name": "All Files", "color": "#94a3b8", "bg": "#1a1f2e", "category": "All Files",
     "desc": "Full Storage Scan", "type": "files"},
]


class LoadWorker(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(str)
    counts_updated = pyqtSignal(dict)

    def __init__(self, serial, category="All"):
        super().__init__()
        self.serial = serial
        self.category = category
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        r = []
        try:
            loaders = self._get_loaders()
            for fn in loaders:
                if self._cancel:
                    break
                try:
                    fn(r)
                except Exception as e:
                    logger.warning(f"Loader error: {e}")
                counts = {}
                for a in r:
                    cat = a.get("category", "Other")
                    counts[cat] = counts.get(cat, 0) + 1
                self.counts_updated.emit(counts)
        except Exception as e:
            logger.warning(f"Worker error: {e}")
        self.finished.emit(r)

    def _get_loaders(self):
        m = {
            "All": [self._emails_accounts, self._messages_chat, self._sms, self._calls,
                    self._contacts, self._calendar, self._media, self._transactions,
                    self._documents, self._timeline_intent,
                    self._whatsapp, self._telegram, self._instagram, self._twitter,
                    self._facebook, self._snapchat, self._tiktok, self._youtube,
                    self._downloads, self._screenshots, self._camera, self._voice_recordings,
                    self._chrome_history,
                    self._apps, self._wifi,
                    self._notifications, self._app_usage, self._system, self._deleted, self._crashes,
                    self._all_sdcard_files],
            "Emails & Accounts": [self._emails_accounts],
            "Messages & Chat": [self._messages_chat],
            "SMS": [self._sms], "Calls": [self._calls], "Contacts": [self._contacts],
            "Calendar": [self._calendar], "Media": [self._media],
            "Transactions": [self._transactions],
            "Documents": [self._documents],
            "Timeline & Intent": [self._timeline_intent],
            "WhatsApp": [self._whatsapp], "Telegram": [self._telegram],
            "Instagram": [self._instagram], "Twitter": [self._twitter],
            "Facebook": [self._facebook], "Snapchat": [self._snapchat],
            "TikTok": [self._tiktok], "YouTube": [self._youtube],
            "Downloads": [self._downloads], "Screenshots": [self._screenshots],
            "Camera": [self._camera], "Recordings": [self._voice_recordings],
            "Chrome": [self._chrome_history],
            "Installed Apps": [self._apps], "WiFi & Network": [self._wifi],
            "Notifications": [self._notifications], "App Usage": [self._app_usage],
            "System Info": [self._system], "Deleted Files": [self._deleted],
            "Crash Logs": [self._crashes],
            "All Files": [self._all_sdcard_files],
        }
        return m.get(self.category, [])

    def _sms(self, r):
        self.progress.emit("Loading SMS messages...")
        data = adb.query_content(self.serial, "content://sms", sort="date DESC")
        c = 0
        for row in _parse_rows(data):
            ts_val = _ts(row.get("date", ""))
            body = row.get("body", "")
            read_st = "Read" if row.get("read", "1") == "1" else "Unread"
            msg_type = {"1": "Incoming", "2": "Outgoing", "3": "Draft", "4": "Queued"}.get(row.get("type", ""), row.get("type", ""))
            r.append({
                "type": "SMS", "category": "SMS", "date": ts_val,
                "summary": f"{row.get('address', '?')}: {body[:80]}",
                "source": "content://sms",
                "_data": {
                    "address": row.get("address", ""), "body": body, "date": ts_val,
                    "type": msg_type, "read_status": read_st,
                    "person": row.get("person", ""), "subject": row.get("subject", ""),
                    "seen": row.get("seen", ""), "service_center": row.get("service_center", ""),
                    "raw_date": row.get("date", ""),
                }
            })
            c += 1
        self.progress.emit(f"Loaded {c} SMS messages")

    def _calls(self, r):
        self.progress.emit("Loading call log...")
        data = adb.query_content(self.serial, "content://call_log/calls", sort="date DESC")
        c = 0
        for row in _parse_rows(data):
            ts_val = _ts(row.get("date", ""))
            call_type = {"1": "Incoming", "2": "Outgoing", "3": "Missed", "4": "Voicemail",
                         "5": "Rejected", "6": "Blocked"}.get(row.get("type", ""), row.get("type", ""))
            dur = row.get("duration", "0")
            try:
                dur_s = int(dur)
                dur_str = f"{dur_s // 60}m {dur_s % 60}s"
            except ValueError:
                dur_str = dur
            r.append({
                "type": "Call", "category": "Calls", "date": ts_val,
                "summary": f"{row.get('number', '?')} ({call_type}) {dur_str}",
                "source": "content://call_log/calls",
                "_data": {
                    "number": row.get("number", ""), "type": call_type,
                    "date": ts_val, "duration": dur_str, "duration_raw": dur,
                    "name": row.get("name", ""), "cached_name": row.get("cached_name", ""),
                    "country": row.get("countryiso", ""), "geocoded": row.get("geocoded_location", ""),
                    "voicemail": row.get("voicemail_uri", ""), "new": row.get("new", ""),
                    "raw_date": row.get("date", ""),
                }
            })
            c += 1
        self.progress.emit(f"Loaded {c} calls")

    def _contacts(self, r):
        self.progress.emit("Loading contacts...")
        phones = _parse_rows(adb.query_content(self.serial,
            "content://com.android.contacts/data",
            sort="display_name ASC"))
        c = 0
        seen = {}
        for row in phones:
            mime = row.get("mimetype", "")
            name = row.get("display_name", "")
            cid = row.get("contact_id", "")
            if cid not in seen:
                seen[cid] = {"name": name, "phones": [], "emails": [], "orgs": [], "notes": ""}
            if "phone" in mime:
                seen[cid]["phones"].append(row.get("data1", ""))
            elif "email" in mime:
                seen[cid]["emails"].append(row.get("data1", ""))
            elif "organization" in mime:
                seen[cid]["orgs"].append(row.get("data1", ""))
            elif "note" in mime:
                seen[cid]["notes"] = row.get("data1", "")
        for cid, info in seen.items():
            phones_str = ", ".join(p for p in info["phones"] if p)
            emails_str = ", ".join(e for e in info["emails"] if e)
            org_str = ", ".join(o for o in info["orgs"] if o)
            r.append({
                "type": "Contact", "category": "Contacts", "date": "",
                "summary": f"{info['name']} | {phones_str}",
                "source": "content://com.android.contacts",
                "_data": {
                    "name": info["name"], "phone": phones_str, "email": emails_str,
                    "organization": org_str, "notes": info["notes"], "contact_id": cid,
                }
            })
            c += 1
        self.progress.emit(f"Loaded {c} contacts")

    def _calendar(self, r):
        self.progress.emit("Loading calendar events...")
        data = adb.query_content(self.serial, "content://com.android.calendar/events", sort="dtstart DESC")
        c = 0
        for row in _parse_rows(data):
            start = _ts(row.get("dtstart", ""))
            end = _ts(row.get("dtend", ""))
            r.append({
                "type": "Event", "category": "Calendar", "date": start,
                "summary": row.get("title", "No title"),
                "source": "content://com.android.calendar",
                "_data": {
                    "title": row.get("title", ""), "start": start, "end": end,
                    "location": row.get("eventLocation", ""),
                    "description": row.get("description", ""),
                    "all_day": row.get("allDay", ""), "rrule": row.get("rrule", ""),
                }
            })
            c += 1
        self.progress.emit(f"Loaded {c} calendar events")

    def _transactions(self, r):
        self.progress.emit("Extracting MoMo transactions from SMS...")
        data = adb.query_content(self.serial, "content://sms", sort="date DESC")
        c = 0
        keywords = ['momo', '170', 'payment', 'MTN', 'mobile money', 'GHS', 'balance',
                     'cash in', 'cash out', 'received', 'sent to', 'transfer']
        for row in _parse_rows(data):
            body = row.get("body", "")
            address = row.get("address", "")
            body_lower = body.lower()
            addr_lower = address.lower()
            is_momo = any(kw.lower() in body_lower or kw.lower() in addr_lower for kw in keywords)
            if not is_momo:
                continue
            ts_val = _ts(row.get("date", ""))
            amount = ""
            amt_match = re.search(r'GHS\s*([\d,.]+)', body)
            if amt_match:
                amount = f"GHS {amt_match.group(1)}"
            recipient = ""
            for pat in [r'to\s+([A-Z][A-Z\s]+?)(?:\s+Current|\s+\.|\s+Transaction)',
                        r'from\s+([A-Z][A-Z\s]+?)(?:\s+Current|\s+\.|\s+Transaction)',
                        r'Payment for GHS[\d.]+\s+to\s+(.+?)\s+\.?Current',
                        r'Payment made for GHS[\d.]+\s+to\s+(.+?)\s+Current',
                        r'Payment received for GHS[\d.]+\s+from\s+(.+?)\s+Current']:
                rm = re.search(pat, body)
                if rm:
                    recipient = rm.group(1).strip()
                    break
            balance = ""
            bal_match = re.search(r'(?:Current Balance|Available Balance):\s*GHS\s*([\d,.]+)', body)
            if bal_match:
                balance = f"GHS {bal_match.group(1)}"
            txn_id = ""
            tid_match = re.search(r'(?:Transaction Id|Transaction ID|Financial Transaction Id):\s*(\d+)', body)
            if tid_match:
                txn_id = tid_match.group(1)
            fee = ""
            fee_match = re.search(r'Fee(?:\s+was|\s+charged)?:?\s*GHS\s*([\d,.]+)', body)
            if fee_match:
                fee = f"GHS {fee_match.group(1)}"
            txn_type = "Unknown"
            if "cash in" in body_lower or "cash in" in address.lower():
                txn_type = "Cash In (Deposit)"
            elif "cash out" in body_lower or "cash out" in address.lower():
                txn_type = "Cash Out (Withdrawal)"
            elif "payment received" in body_lower or "received for" in body_lower:
                txn_type = "Payment Received"
            elif "payment made" in body_lower or "payment for" in body_lower:
                txn_type = "Payment Sent"
            elif "purchase" in body_lower or "bought" in body_lower:
                txn_type = "Purchase"
            elif "transfer" in body_lower:
                txn_type = "Transfer"
            elif "airtime" in body_lower or "bundle" in body_lower or "data" in body_lower:
                txn_type = "Airtime/Data Purchase"
            r.append({
                "type": txn_type, "category": "Transactions", "date": ts_val,
                "summary": f"{address}: {amount} {'to' if 'to' in body_lower else 'from'} {recipient or '?'} [{txn_type}]",
                "source": "content://sms",
                "_data": {
                    "address": address, "amount": amount, "recipient_sender": recipient,
                    "balance": balance, "transaction_id": txn_id, "fee": fee,
                    "type": txn_type, "body": body, "date": ts_val,
                }
            })
            c += 1
        self.progress.emit(f"Loaded {c} MoMo transactions")

    def _whatsapp(self, r):
        self.progress.emit("Extracting WhatsApp files (data + media)...")
        c = 0

        wa_search_paths = [
            '/sdcard/Android/data/com.whatsapp/',
            '/sdcard/Android/media/com.whatsapp/',
        ]

        for search_path in wa_search_paths:
            ok, output = adb.adb_shell(self.serial,
                f'find {search_path} -type f 2>/dev/null', timeout=15)
            if not ok or not output:
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line or line.endswith(".nomedia"):
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "GIF" if ext == ".gif" else ("Image" if ext in ('.jpg', '.jpeg', '.png', '.webp') else
                    ("Video" if ext in ('.mp4', '.3gp', '.mkv') else
                    ("Audio" if ext in ('.opus', '.aac', '.mp3', '.m4a') else
                    ("Document" if ext in ('.pdf', '.doc', '.docx', '.txt') else "Cache File"))))
                r.append({
                    "type": ftype, "category": "WhatsApp", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 10000:
                    break

        if c == 0:
            ok2, ls_data = adb.adb_shell(self.serial, 'ls /sdcard/WhatsApp/ 2>/dev/null', timeout=10)
            if ok2 and ls_data and "No such file" not in ls_data:
                for sub in ["Images", "Video", "Audio", "Documents", "Voice Notes", "GIFs", "Stickers"]:
                    ok3, files = adb.adb_shell(self.serial, f'ls /sdcard/WhatsApp/{sub}/ 2>/dev/null', timeout=10)
                    if ok3 and files:
                        for fname in files.splitlines():
                            fname = fname.strip()
                            if not fname:
                                continue
                            ext = Path(fname).suffix.lower()
                            ftype = "GIF" if ext == ".gif" else ("Image" if ext in ('.jpg', '.jpeg', '.png', '.webp') else
                                ("Video" if ext in ('.mp4', '.3gp', '.mkv') else
                                ("Audio" if ext in ('.opus', '.aac', '.mp3', '.m4a') else
                                ("Document" if ext in ('.pdf', '.doc', '.docx', '.txt') else "Cache File"))))
                            remote = f"/sdcard/WhatsApp/{sub}/{fname}"
                            r.append({
                                "type": ftype, "category": "WhatsApp", "date": "",
                                "summary": fname, "source": remote,
                                "_data": {"filename": fname, "path": remote, "type": ftype, "size": ""}
                            })
                            c += 1

        self.progress.emit(f"Loaded {c} WhatsApp files")

    def _telegram(self, r):
        self.progress.emit("Extracting Telegram cache files...")
        ok, output = adb.adb_shell(self.serial,
            'find /sdcard/Android/data/org.telegram.messenger/ -type f 2>/dev/null', timeout=15)
        c = 0
        if ok and output:
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line or line.endswith(".nomedia"):
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "Image" if ext in ('.jpg', '.jpeg', '.png', '.webp') else (
                    "Sticker (Animated)" if ext == '.tgs' else
                    ("Video Thumbnail" if ext == '.tgv' else
                    ("Profile Photo" if ext == '.pt' else
                    ("Video" if ext in ('.mp4', '.3gp') else
                    ("Preload Cache" if ext == '.preload' else
                    ("Temp File" if ext == '.temp' else
                    ("Animation Cache" if ext == '.pcache2' else
                    "Cache File")))))))
                r.append({
                    "type": ftype, "category": "Telegram", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 5000:
                    break
        self.progress.emit(f"Loaded {c} Telegram files")

    def _instagram(self, r):
        self.progress.emit("Extracting Instagram cache files...")
        ok, output = adb.adb_shell(self.serial,
            'find /sdcard/Android/data/com.instagram.android/ -type f 2>/dev/null', timeout=15)
        c = 0
        if ok and output:
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                    ftype = "Image"
                elif ext in ('.mp4', '.3gp', '.mkv'):
                    ftype = "Video"
                elif ext in ('.opus', '.aac', '.mp3'):
                    ftype = "Audio"
                else:
                    ftype = "Cache File"
                r.append({
                    "type": ftype, "category": "Instagram", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
        self.progress.emit(f"Loaded {c} Instagram files")

    def _twitter(self, r):
        self.progress.emit("Extracting Twitter/X cache files...")
        ok, output = adb.adb_shell(self.serial,
            'find /sdcard/Android/data/com.twitter.android/ -type f 2>/dev/null', timeout=15)
        c = 0
        if ok and output:
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                    ftype = "Image"
                elif ext in ('.mp4', '.3gp', '.mkv'):
                    ftype = "Video"
                elif ext in ('.opus', '.aac', '.mp3'):
                    ftype = "Audio"
                else:
                    ftype = "Cache File"
                r.append({
                    "type": ftype, "category": "Twitter", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
        self.progress.emit(f"Loaded {c} Twitter/X files")

    def _emails_accounts(self, r):
        self.progress.emit("Extracting email accounts and messages...")
        c = 0

        email_packages = {
            "com.google.android.gm": "Gmail",
            "com.microsoft.office.outlook": "Outlook",
            "com.yahoo.mobile.client.android.mail": "Yahoo Mail",
            "com.fsck.k9": "K-9 Mail",
            "org.kman.AquaMail": "AquaMail",
            "com.maildroid": "MailDroid",
            "com.email.email": "Email (Generic)",
            "com.google.android.apps.messaging": "Google Messages",
        }

        ok, output = adb.adb_shell(self.serial, "dumpsys account", timeout=15)
        if ok and output:
            for line in output.splitlines():
                line = line.strip()
                if "Account {" in line:
                    m = re.search(r'Account \{name=(.+?), type=(.+?)\}', line)
                    if m:
                        name = m.group(1)
                        acct_type = m.group(2)
                        is_email = "@" in name
                        provider = "Google" if "google" in acct_type else \
                            "Outlook" if "outlook" in acct_type or "microsoft" in acct_type else \
                            "Yahoo" if "yahoo" in acct_type else \
                            "K-9" if "k9" in acct_type or "k9mail" in acct_type else \
                            "AquaMail" if "aqua" in acct_type else acct_type
                        r.append({
                            "type": "Email Account" if is_email else "App Account",
                            "category": "Emails & Accounts", "date": "",
                            "summary": f"{name} ({provider})",
                            "source": "dumpsys account",
                            "_data": {
                                "account_name": name, "account_type": acct_type,
                                "provider": provider, "is_email": str(is_email),
                                "status": "Active"
                            }
                        })
                        c += 1

        ok2, output2 = adb.adb_shell(self.serial, "dumpsys notification --noredact", timeout=20)
        if ok2 and output2:
            current_pkg = None
            current_notif = {}
            for line in output2.splitlines():
                line = line.strip()
                if line.startswith("NotificationRecord("):
                    if current_pkg and current_notif:
                        provider_name = email_packages.get(current_pkg, "")
                        if provider_name and current_notif.get("subject"):
                            r.append({
                                "type": f"Email ({provider_name})", "category": "Emails & Accounts",
                                "date": current_notif.get("date", ""),
                                "summary": f"{current_notif.get('sender', 'Unknown')}: {current_notif.get('subject', '')[:80]}",
                                "source": f"notification:{current_pkg}",
                                "_data": {
                                    "sender": current_notif.get("sender", ""),
                                    "subject": current_notif.get("subject", ""),
                                    "preview": current_notif.get("text", ""),
                                    "date": current_notif.get("date", ""),
                                    "provider": provider_name,
                                    "package": current_pkg,
                                }
                            })
                            c += 1
                    current_pkg = None
                    current_notif = {}
                if "pkg=" in line:
                    m = re.search(r'pkg=(\S+)', line)
                    if m:
                        current_pkg = m.group(1).rstrip("}")
                if line.startswith("title="):
                    current_notif["subject"] = line.split("=", 1)[1].strip('"')
                elif line.startswith("text="):
                    current_notif["text"] = line.split("=", 1)[1].strip('"')
                elif line.startswith("tickerText="):
                    ticker = line.split("=", 1)[1].strip('"')
                    if ticker and ":" in ticker:
                        parts = ticker.split(":", 1)
                        current_notif["sender"] = parts[0].strip()
                        if not current_notif.get("subject"):
                            current_notif["subject"] = parts[1].strip()
                elif line.startswith("when="):
                    ts = line.split("=", 1)[1].strip()
                    if ts.isdigit():
                        from datetime import datetime
                        try:
                            current_notif["date"] = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            pass

            if current_pkg and current_notif:
                provider_name = email_packages.get(current_pkg, "")
                if provider_name and current_notif.get("subject"):
                    r.append({
                        "type": f"Email ({provider_name})", "category": "Emails & Accounts",
                        "date": current_notif.get("date", ""),
                        "summary": f"{current_notif.get('sender', 'Unknown')}: {current_notif.get('subject', '')[:80]}",
                        "source": f"notification:{current_pkg}",
                        "_data": {
                            "sender": current_notif.get("sender", ""),
                            "subject": current_notif.get("subject", ""),
                            "preview": current_notif.get("text", ""),
                            "date": current_notif.get("date", ""),
                            "provider": provider_name,
                            "package": current_pkg,
                        }
                    })
                    c += 1

        gmail_uris = [
            ("content://com.google.android.gm/msgs", "_id:subject:from:to:dateSentMs:snippet:body"),
            ("content://com.google.android.gm/conversations", "_id:subject:snippet:date"),
        ]
        for uri, projection in gmail_uris:
            try:
                import subprocess
                result = subprocess.run(
                    ["adb", "-s", self.serial, "shell", "content", "query", "--uri", uri,
                     "--projection", projection],
                    capture_output=True, text=True, timeout=15,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                if result.returncode == 0 and result.stdout and "No result" not in result.stdout:
                    for line in result.stdout.splitlines():
                        if line.startswith("Row:"):
                            email = {}
                            for field in ["subject", "from", "to", "snippet", "body", "dateSentMs"]:
                                m = re.search(rf"{field}=([^,)]+)", line)
                                if m:
                                    email[field] = m.group(1).strip().strip('"')
                            if email.get("subject") or email.get("snippet"):
                                ts = ""
                                if email.get("dateSentMs") and email["dateSentMs"].isdigit():
                                    from datetime import datetime
                                    try:
                                        ts = datetime.fromtimestamp(int(email["dateSentMs"]) / 1000).strftime("%Y-%m-%d %H:%M")
                                    except Exception:
                                        pass
                                r.append({
                                    "type": "Email (Gmail)", "category": "Emails & Accounts",
                                    "date": ts,
                                    "summary": f"{email.get('from', 'Unknown')}: {email.get('subject', email.get('snippet', ''))[:80]}",
                                    "source": uri,
                                    "_data": {
                                        "sender": email.get("from", ""),
                                        "recipient": email.get("to", ""),
                                        "subject": email.get("subject", ""),
                                        "preview": email.get("snippet", email.get("body", ""))[:200],
                                        "date": ts,
                                        "provider": "Gmail",
                                        "package": "com.google.android.gm",
                                    }
                                })
                                c += 1
            except Exception:
                pass

        email_db_searches = [
            'find /sdcard/Android/data/com.google.android.gm/ -name "*.db" -type f 2>/dev/null',
            'find /sdcard/Android/data/com.microsoft.office.outlook/ -name "*.db" -type f 2>/dev/null',
            'find /sdcard/Android/data/com.yahoo.mobile.client.android.mail/ -name "*.db" -type f 2>/dev/null',
            'find /sdcard/Android/data/com.fsck.k9/ -name "*.db" -type f 2>/dev/null',
            'find /sdcard/Android/data/org.kman.AquaMail/ -name "*.db" -type f 2>/dev/null',
        ]
        for cmd in email_db_searches:
            ok3, output3 = adb.adb_shell(self.serial, cmd, timeout=10)
            if ok3 and output3:
                for line in output3.splitlines():
                    line = line.strip()
                    if not line or "Permission denied" in line:
                        continue
                    fname = Path(line).name
                    pkg = line.split("/")[4] if len(line.split("/")) > 4 else "unknown"
                    provider = email_packages.get(pkg, pkg)
                    r.append({
                        "type": f"Email DB ({provider})", "category": "Emails & Accounts",
                        "date": "",
                        "summary": f"{fname} ({provider})",
                        "source": line,
                        "_data": {
                            "filename": fname, "path": line,
                            "provider": provider, "package": pkg,
                            "status": "Database found"
                        }
                    })
                    c += 1

        self.progress.emit(f"Loaded {c} email/account entries")

    def _messages_chat(self, r):
        self.progress.emit("Building unified messages & chat timeline...")
        data = adb.query_content(self.serial, "content://sms", sort="date DESC")
        c = 0
        for row in _parse_rows(data):
            ts_val = _ts(row.get("date", ""))
            body = row.get("body", "")
            msg_type = {"1": "Incoming", "2": "Outgoing", "3": "Draft", "4": "Queued"}.get(row.get("type", ""), row.get("type", ""))
            r.append({
                "type": "SMS", "category": "Messages & Chat", "date": ts_val,
                "summary": f"SMS from {row.get('address', '?')}: {body[:80]}",
                "source": "content://sms",
                "_data": {
                    "platform": "SMS", "address": row.get("address", ""),
                    "body": body, "type": msg_type, "date": ts_val,
                    "read_status": "Read" if row.get("read", "1") == "1" else "Unread",
                }
            })
            c += 1
        self.progress.emit(f"Loaded {c} messages & chat entries")

    def _documents(self, r):
        self.progress.emit("Scanning documents across device...")
        c = 0

        doc_exts = ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "csv", "rtf", "epub", "json", "xml", "md", "html", "apk", "vcf", "zip", "rar"]
        search_dirs = [
            "/sdcard/Download/",
            "/sdcard/Documents/",
            "/sdcard/DCIM/",
            "/sdcard/Pictures/",
            "/sdcard/Telegram/",
            "/sdcard/WhatsApp/",
            "/sdcard/Android/data/",
            "/sdcard/Android/media/",
        ]

        seen = set()
        for d in search_dirs:
            for ext in doc_exts:
                ok, output = adb.adb_shell(self.serial, f'find "{d}" -maxdepth 4 -type f -name "*.{ext}" 2>/dev/null | head -500', timeout=10)
                if not ok or not output:
                    continue
                for line in output.splitlines():
                    line = line.strip()
                    if not line or "Permission denied" in line or line in seen:
                        continue
                    seen.add(line)
                    fname = Path(line).name
                    file_ext = Path(line).suffix.lower()

                    doc_type_map = {
                        ".pdf": "PDF Document", ".docx": "Word Document", ".doc": "Word (Legacy)",
                        ".xlsx": "Excel Spreadsheet", ".xls": "Excel (Legacy)",
                        ".pptx": "PowerPoint", ".ppt": "PowerPoint (Legacy)",
                        ".txt": "Text File", ".csv": "CSV Spreadsheet",
                        ".rtf": "Rich Text", ".epub": "E-Book",
                        ".json": "JSON Data", ".xml": "XML Data",
                        ".md": "Markdown", ".html": "Web Page",
                        ".apk": "Android Package", ".vcf": "Contact Card",
                        ".zip": "ZIP Archive", ".rar": "RAR Archive",
                    }
                    doc_type = doc_type_map.get(file_ext, "Document")

                    folder = ""
                    parts = line.split("/")
                    if len(parts) > 3:
                        folder = parts[2] if parts[2] != "Android" else (parts[3] if len(parts) > 3 else "")

                    r.append({
                        "type": doc_type, "category": "Documents", "date": "",
                        "summary": f"{fname}" + (f"  [{folder}]" if folder else ""),
                        "source": line,
                        "_data": {"filename": fname, "type": doc_type, "size": "", "modified": "", "path": line, "extension": file_ext, "folder": folder}
                    })
                    c += 1
                    if c >= 10000:
                        break
                if c >= 10000:
                    break
            if c >= 10000:
                break

        self.progress.emit(f"Found {c} documents across device")

    def _timeline_intent(self, r):
        self.progress.emit("Building timeline & intent analysis...")
        c = 0
        data = adb.query_content(self.serial, "content://sms", sort="date DESC")
        for row in _parse_rows(data):
            body = row.get("body", "")
            ts_val = _ts(row.get("date", ""))
            if any(kw in body.lower() for kw in ['momo', 'payment', 'transfer', 'ghs', 'sent', 'received']):
                r.append({
                    "type": "Financial Activity", "category": "Timeline & Intent",
                    "date": ts_val,
                    "summary": f"MoMo: {body[:100]}",
                    "source": "content://sms",
                    "_data": {"timestamp": ts_val, "event": "MoMo Transaction",
                              "platform": "SMS/MobileMoney", "details": body[:200],
                              "significance": "Financial transaction"}
                })
                c += 1
        self.progress.emit(f"Loaded {c} timeline & intent entries")

    def _media(self, r):
        self.progress.emit("Scanning ALL media files across device...")
        c = 0

        data = adb.query_content(self.serial, "content://media/external/file",
            sort="date_added DESC")
        for row in _parse_rows(data):
            name = row.get("_display_name", "")
            ts_val = _ts(row.get("date_added", ""), divide=False)
            mime = row.get("mime_type", "")
            fpath = row.get("_data", "")
            size = row.get("_size", "0")
            ftype = "Photo" if "image" in mime else ("Video" if "video" in mime else ("Audio" if "audio" in mime else "File"))
            try:
                sz = int(size)
                sz_str = f"{sz / (1024*1024):.1f} MB" if sz > 1024*1024 else f"{sz / 1024:.0f} KB"
            except ValueError:
                sz_str = size
            folder = ""
            if fpath:
                parts = fpath.split("/")
                if len(parts) > 3:
                    folder = "/".join(parts[1:4]) if len(parts) > 4 else "/".join(parts[1:3])
            r.append({
                "type": ftype, "category": "Media", "date": ts_val,
                "summary": f"{name}",
                "source": fpath,
                "_data": {"filename": name, "path": fpath, "size": sz_str, "media_type": ftype, "folder": folder}
            })
            c += 1
            if c >= 15000:
                break

        if c < 5000:
            media_dirs = [
                '/sdcard/DCIM/Camera/',
                '/sdcard/DCIM/',
                '/sdcard/Pictures/',
                '/sdcard/Pictures/Screenshots/',
                '/sdcard/Download/',
                '/sdcard/Movies/',
                '/sdcard/Music/',
                '/sdcard/Recordings/',
                '/sdcard/WhatsApp/Media/',
                '/sdcard/Telegram/Telegram Images/',
                '/sdcard/Telegram/Telegram Video/',
                '/sdcard/Telegram/Telegram Audio/',
                '/sdcard/Telegram/Telegram Documents/',
            ]
            seen = {a.get("source") for a in r}
            for d in media_dirs:
                ok, output = adb.adb_shell(self.serial, f'ls -la "{d}" 2>/dev/null', timeout=10)
                if not ok or not output:
                    continue
                for line in output.splitlines():
                    line = line.strip()
                    if not line or line.startswith("total") or line.startswith("d"):
                        continue
                    parts = line.split()
                    if len(parts) < 8:
                        continue
                    fname = " ".join(parts[7:])
                    if not fname or fname.startswith("."):
                        continue
                    remote = f"{d}{fname}"
                    if remote in seen:
                        continue
                    seen.add(remote)
                    ext = Path(fname).suffix.lower()
                    if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.heif', '.dng', '.arw', '.cr2', '.nef'):
                        ftype = "Photo"
                    elif ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov', '.webm', '.flv'):
                        ftype = "Video"
                    elif ext in ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.amr', '.flac', '.wma'):
                        ftype = "Audio"
                    else:
                        continue
                    r.append({
                        "type": ftype, "category": "Media", "date": "",
                        "summary": fname, "source": remote,
                        "_data": {"filename": fname, "path": remote, "size": "", "media_type": ftype, "folder": d.rstrip("/")}
                    })
                    c += 1
                    if c >= 15000:
                        break
                if c >= 15000:
                    break

        self.progress.emit(f"Loaded {c} media files")

    def _apps(self, r):
        self.progress.emit("Listing installed apps...")
        ok, output = adb.adb_shell(self.serial, "pm list packages -f", timeout=60)
        c = 0
        if ok and output:
            for line in output.splitlines():
                if "package:" not in line:
                    continue
                pkg_line = line.replace("package:", "").strip()
                parts = pkg_line.split("=")
                if len(parts) == 2:
                    pkg_name = parts[0]
                    apk_path = parts[1]
                    is_sys = apk_path.startswith("/system/") or apk_path.startswith("/product/")
                    r.append({
                        "type": "System App" if is_sys else "User App", "category": "Installed Apps", "date": "",
                        "summary": f"{pkg_name}",
                        "source": "pm list packages",
                        "_data": {"package": pkg_name, "apk_path": apk_path,
                                  "type": "System" if is_sys else "User-installed"}
                    })
                    c += 1
        self.progress.emit(f"Loaded {c} installed apps")

    def _wifi(self, r):
        self.progress.emit("Extracting WiFi, Bluetooth & network info...")
        c = 0

        ok, output = adb.adb_shell(self.serial, "dumpsys wifi", timeout=15)
        if ok and output:
            ssids = set()
            for line in output.splitlines():
                m = re.search(r'targetConfigKey="([^"]+)"', line)
                if m:
                    ssids.add(m.group(1))
            for ssid in sorted(ssids):
                r.append({
                    "type": "WiFi Network", "category": "WiFi & Network", "date": "",
                    "summary": f"Saved: {ssid}",
                    "source": "dumpsys wifi",
                    "_data": {"ssid": ssid, "type": "Saved Network", "label": "WiFi"}
                })
                c += 1

            bssid_match = re.findall(r'BSSID:\s*([0-9a-f:]{17})', output)
            for bssid in set(bssid_match):
                r.append({
                    "type": "WiFi BSSID", "category": "WiFi & Network", "date": "",
                    "summary": f"BSSID: {bssid}",
                    "source": "dumpsys wifi",
                    "_data": {"bssid": bssid, "type": "Access Point MAC", "label": "WiFi BSSID"}
                })
                c += 1

        ok2, bt = adb.adb_shell(self.serial, "dumpsys bluetooth_manager", timeout=15)
        if ok2 and bt:
            bt_names = re.findall(r'name=(.+?)[,\n]', bt)
            bt_addrs = re.findall(r'address=(.+?)[,\n]', bt)
            for i, name in enumerate(set(bt_names)):
                addr = bt_addrs[i] if i < len(bt_addrs) else ""
                r.append({
                    "type": "Bluetooth Device", "category": "WiFi & Network", "date": "",
                    "summary": f"BT: {name.strip()} ({addr.strip()})",
                    "source": "dumpsys bluetooth_manager",
                    "_data": {"name": name.strip(), "address": addr.strip(), "label": "Bluetooth"}
                })
                c += 1

        ok3, net = adb.adb_shell(self.serial, "dumpsys connectivity", timeout=15)
        if ok3 and net:
            for line in net.splitlines():
                if "NetworkAgentInfo" in line or "WIFI" in line or "MOBILE" in line:
                    r.append({
                        "type": "Network", "category": "WiFi & Network", "date": "",
                        "summary": line.strip()[:100],
                        "source": "dumpsys connectivity",
                        "_data": {"raw": line.strip(), "label": "Active Network"}
                    })
                    c += 1
                    if c > 100:
                        break

        self.progress.emit(f"Loaded {c} network entries")

    def _notifications(self, r):
        self.progress.emit("Extracting notifications...")
        ok, output = adb.adb_shell(self.serial, "dumpsys notification --noredact", timeout=20)
        c = 0
        if ok and output:
            current_pkg = ""
            current_title = ""
            current_text = ""
            current_time = ""
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("NotificationRecord("):
                    if current_pkg and current_title:
                        r.append({
                            "type": "Notification", "category": "Notifications",
                            "date": current_time,
                            "summary": f"[{current_pkg}] {current_title}: {current_text[:80]}",
                            "source": "dumpsys notification",
                            "_data": {
                                "package": current_pkg, "title": current_title,
                                "text": current_text, "timestamp": current_time
                            }
                        })
                        c += 1
                    m = re.search(r'pkg=(\S+)', line)
                    current_pkg = m.group(1).rstrip("}") if m else ""
                    current_title = current_text = current_time = ""
                if "android.title=" in line:
                    m = re.search(r'android\.title=String\s*\((.+)\)', line)
                    if m: current_title = m.group(1)
                if "android.text=" in line:
                    m = re.search(r'android\.text=String\s*\((.+)\)', line)
                    if m: current_text = m.group(1)
                if "when=" in line:
                    ts = line.split("=", 1)[1].strip()
                    if ts.isdigit():
                        from datetime import datetime
                        try:
                            current_time = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            pass
            if current_pkg and current_title:
                r.append({
                    "type": "Notification", "category": "Notifications",
                    "date": current_time,
                    "summary": f"[{current_pkg}] {current_title}: {current_text[:80]}",
                    "source": "dumpsys notification",
                    "_data": {
                        "package": current_pkg, "title": current_title,
                        "text": current_text, "timestamp": current_time
                    }
                })
                c += 1
        self.progress.emit(f"Loaded {c} notifications")

    def _app_usage(self, r):
        self.progress.emit("Extracting app usage history...")
        ok, output = adb.adb_shell(self.serial, "dumpsys usagestats", timeout=15)
        c = 0
        if ok and output:
            for line in output.splitlines():
                if "package=" in line and "time=" in line:
                    m_time = re.search(r'time="([^"]+)"', line)
                    m_pkg = re.search(r'package=(\S+)', line)
                    if m_time and m_pkg:
                        r.append({
                            "type": "Usage", "category": "App Usage", "date": m_time.group(1),
                            "summary": f"{m_pkg.group(1)}",
                            "source": "dumpsys usagestats",
                            "_data": {"time": m_time.group(1), "package": m_pkg.group(1)}
                        })
                        c += 1
                        if c >= 5000: break
        self.progress.emit(f"Loaded {c} usage events")

    def _system(self, r):
        self.progress.emit("Collecting comprehensive system information...")
        c = 0

        props = {
            "Device Identity": [
                ("ro.product.model", "Model"),
                ("ro.product.brand", "Brand"),
                ("ro.product.manufacturer", "Manufacturer"),
                ("ro.product.name", "Product Name"),
                ("ro.product.device", "Device Codename"),
                ("ro.product.board", "Board"),
                ("ro.serialno", "Serial Number"),
                ("ro.boot.serialno", "Boot Serial"),
                ("ro.hardware", "Hardware"),
                ("ro.hardware.chipname", "Chipset"),
            ],
            "Build Information": [
                ("ro.build.display.id", "Build ID"),
                ("ro.build.version.release", "Android Version"),
                ("ro.build.version.sdk", "API Level"),
                ("ro.build.version.security_patch", "Security Patch"),
                ("ro.build.version.incremental", "Incremental Build"),
                ("ro.build.type", "Build Type"),
                ("ro.build.tags", "Build Tags"),
                ("ro.build.date", "Build Date"),
                ("ro.build.date.utc", "Build Date (UTC)"),
                ("ro.build.fingerprint", "Fingerprint"),
                ("ro.build.description", "Build Description"),
                ("ro.product.first_api_level", "First API Level"),
            ],
            "System Properties": [
                ("ro.kernel.version", "Kernel Version"),
                ("ro.board.platform", "Platform"),
                ("ro.crypto.state", "Encryption State"),
                ("ro.crypto.type", "Encryption Type"),
                ("ro.boot.verifiedbootstate", "Verified Boot State"),
                ("ro.boot.flash.locked", "Bootloader Locked"),
                ("ro.adb.secure", "ADB Secure"),
                ("ro.debuggable", "Debuggable"),
                ("ro.secure", "Secure Boot"),
                ("ro.build.selinux", "SELinux"),
                ("persist.sys.timezone", "Timezone"),
                ("persist.sys.language", "Language"),
                ("persist.sys.locale", "Locale"),
                ("gsm.version.baseband", "Baseband Version"),
                ("gsm.sim.operator.alpha", "SIM Operator"),
                ("gsm.sim.operator.numeric", "SIM MCC/MNC"),
                ("gsm.sim.state", "SIM State"),
                ("gsm.network.type", "Network Type"),
                ("gsm.operator.alpha", "Network Operator"),
                ("gsm.operator.numeric", "Network MCC/MNC"),
            ],
            "Hardware Info": [
                ("ro.soc.model", "SoC Model"),
                ("ro.soc.manufacturer", "SoC Manufacturer"),
                ("ro.config.ringtone", "Default Ringtone"),
                ("ro.config.notification_sound", "Default Notification"),
                ("ro.config.alarm_alert", "Default Alarm"),
                ("dalvik.vm.heapsize", "Heap Size"),
                ("dalvik.vm.heapgrowthlimit", "Heap Growth Limit"),
                ("ro.sf.lcd_density", "Screen Density"),
                ("ro.build.characteristics", "Device Characteristics"),
            ],
            "Security & Lock": [
                ("ro.lockscreen.disable", "Lockscreen Disabled"),
                ("persist.sys.lockscreen", "Lockscreen Enabled"),
                ("ro.build.version.preview_sdk", "Preview SDK"),
                ("ro.emulator是否存在", "Emulator Check"),
            ],
        }

        for section, prop_list in props.items():
            r.append({
                "type": "Section Header", "category": "System Info", "date": "",
                "summary": f"━━━ {section} ━━━",
                "source": "getprop",
                "_data": {"section": section, "is_header": "true"}
            })
            c += 1

            for key, label in prop_list:
                ok, val = adb.adb_shell(self.serial, f"getprop {key}", timeout=10)
                if ok and val.strip():
                    r.append({
                        "type": "Property", "category": "System Info", "date": "",
                        "summary": f"{label}: {val.strip()}",
                        "source": "getprop",
                        "_data": {"key": key, "label": label, "value": val.strip(), "section": section}
                    })
                    c += 1

        self.progress.emit("Collecting battery & storage info...")

        ok, battery = adb.adb_shell(self.serial, "dumpsys battery", timeout=10)
        if ok and battery:
            r.append({
                "type": "Section Header", "category": "System Info", "date": "",
                "summary": "━━━ Battery Status ━━━",
                "source": "dumpsys battery",
                "_data": {"section": "Battery Status", "is_header": "true"}
            })
            c += 1
            for line in battery.splitlines():
                line = line.strip()
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip()
                    if key and val and key != "level":
                        r.append({
                            "type": "Battery", "category": "System Info", "date": "",
                            "summary": f"{key}: {val}",
                            "source": "dumpsys battery",
                            "_data": {"label": key, "value": val, "section": "Battery Status"}
                        })
                        c += 1

        ok2, disk = adb.adb_shell(self.serial, "df /sdcard/", timeout=10)
        if ok2 and disk:
            r.append({
                "type": "Section Header", "category": "System Info", "date": "",
                "summary": "━━━ Storage Info ━━━",
                "source": "df",
                "_data": {"section": "Storage Info", "is_header": "true"}
            })
            c += 1
            for line in disk.splitlines():
                if "/sdcard" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        r.append({
                            "type": "Storage", "category": "System Info", "date": "",
                            "summary": f"Filesystem: {parts[0]}  Size: {parts[1]}  Used: {parts[2]}  Avail: {parts[3]}",
                            "source": "df",
                            "_data": {"filesystem": parts[0], "size": parts[1], "used": parts[2], "available": parts[3], "section": "Storage Info"}
                        })
                        c += 1

        ok3, meminfo = adb.adb_shell(self.serial, "cat /proc/meminfo | head -5", timeout=10)
        if ok3 and meminfo:
            r.append({
                "type": "Section Header", "category": "System Info", "date": "",
                "summary": "━━━ Memory Info ━━━",
                "source": "/proc/meminfo",
                "_data": {"section": "Memory Info", "is_header": "true"}
            })
            c += 1
            for line in meminfo.splitlines():
                line = line.strip()
                if ":" in line:
                    key, val = line.split(":", 1)
                    r.append({
                        "type": "Memory", "category": "System Info", "date": "",
                        "summary": f"{key.strip()}: {val.strip()}",
                        "source": "/proc/meminfo",
                        "_data": {"label": key.strip(), "value": val.strip(), "section": "Memory Info"}
                    })
                    c += 1

        ok4, cpu = adb.adb_shell(self.serial, "cat /proc/cpuinfo | head -10", timeout=10)
        if ok4 and cpu:
            r.append({
                "type": "Section Header", "category": "System Info", "date": "",
                "summary": "━━━ CPU Info ━━━",
                "source": "/proc/cpuinfo",
                "_data": {"section": "CPU Info", "is_header": "true"}
            })
            c += 1
            for line in cpu.splitlines():
                line = line.strip()
                if ":" in line:
                    key, val = line.split(":", 1)
                    if key.strip() in ("Processor", "model name", "Hardware", "Serial"):
                        r.append({
                            "type": "CPU", "category": "System Info", "date": "",
                            "summary": f"{key.strip()}: {val.strip()}",
                            "source": "/proc/cpuinfo",
                            "_data": {"label": key.strip(), "value": val.strip(), "section": "CPU Info"}
                        })
                        c += 1

        ok5, uptime = adb.adb_shell(self.serial, "cat /proc/uptime", timeout=10)
        if ok5 and uptime:
            parts = uptime.strip().split()
            if parts:
                try:
                    secs = float(parts[0])
                    days = int(secs // 86400)
                    hours = int((secs % 86400) // 3600)
                    mins = int((secs % 3600) // 60)
                    r.append({
                        "type": "Uptime", "category": "System Info", "date": "",
                        "summary": f"Uptime: {days}d {hours}h {mins}m",
                        "source": "/proc/uptime",
                        "_data": {"label": "Uptime", "value": f"{days}d {hours}h {mins}m", "section": "System Info"}
                    })
                    c += 1
                except Exception:
                    pass

        self.progress.emit(f"Loaded {c} system properties")

    def _deleted(self, r):
        self.progress.emit("Scanning for deleted/recoverable files...")
        c = 0
        seen_paths = set()

        trash_dirs = [
            "/sdcard/.trashed-*/",
            "/sdcard/.Trash/",
            "/sdcard/.trash/",
            "/sdcard/Lost.dir/",
            "/sdcard/Android/data/*/cache/.trash/",
        ]
        for d in trash_dirs:
            ok, output = adb.adb_shell(self.serial, f'ls -la {d} 2>/dev/null', timeout=10)
            if not ok or not output:
                continue
            for line in output.splitlines():
                if "Permission denied" in line or "No such file" in line:
                    continue
                parts = line.split()
                if len(parts) < 8:
                    continue
                fname = parts[-1]
                if fname in (".", ".."):
                    continue
                full_path = f"{d.rstrip('/')}/{fname}"
                if full_path in seen_paths:
                    continue
                seen_paths.add(full_path)
                ext = Path(fname).suffix.lower()
                ftype, status = self._classify_deleted(fname, ext, full_path)
                r.append({
                    "type": ftype, "category": "Deleted Files", "date": "",
                    "summary": fname, "source": full_path,
                    "_data": {"filename": fname, "path": full_path, "type": ftype, "size": "", "status": status, "extension": ext}
                })
                c += 1
                if c >= 5000:
                    break
            if c >= 5000:
                break

        ok, output = adb.adb_shell(self.serial,
            'find /sdcard/Android/data/ -maxdepth 3 \\( -name "*.db-wal" -o -name "*.db-shm" -o -name "*.wal" -o -name "*.shm" \\) 2>/dev/null | head -2000',
            timeout=15)
        if ok and output:
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line or line in seen_paths:
                    continue
                seen_paths.add(line)
                fname = Path(line).name
                r.append({
                    "type": "DB Journal", "category": "Deleted Files", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": "DB Journal", "size": "", "status": "Uncommitted DB changes", "extension": ".db-wal"}
                })
                c += 1

        self.progress.emit(f"Found {c} deleted/recoverable files")

    def _classify_deleted(self, fname, ext, path):
        if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
            ftype, status = "Deleted Image", "Recoverable (image)"
        elif ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov'):
            ftype, status = "Deleted Video", "Recoverable (video)"
        elif ext in ('.opus', '.aac', '.mp3', '.m4a', '.amr'):
            ftype, status = "Deleted Audio", "Recoverable (audio)"
        elif ext in ('.db', '.db-wal', '.db-shm', '.wal', '.shm'):
            ftype, status = "DB Journal", "Contains recent data"
        elif ext in ('.pdf', '.doc', '.docx', '.txt', '.csv', '.xlsx'):
            ftype, status = "Deleted Document", "Recoverable (document)"
        elif ext in ('.tmp', '.bak', '.old'):
            ftype, status = "Temp/Backup", "Possible data"
        else:
            ftype, status = "Deleted File", "Deleted"
        if "trashed" in path.lower() or ".trash" in path.lower():
            status = "In trash — recoverable"
        elif "lost.dir" in path.lower():
            status = "Lost+found — recoverable"
        return ftype, status

    def _crashes(self, r):
        self.progress.emit("Loading crash logs...")
        ok, output = adb.adb_shell(self.serial, "dumpsys dropbox --print 2>/dev/null | head -200", timeout=15)
        c = 0
        if ok and output:
            crash_count = output.lower().count("app_crash")
            if crash_count:
                r.append({
                    "type": "Crash Stats", "category": "Crash Logs", "date": "",
                    "summary": f"App Crashes: {crash_count} recorded",
                    "source": "dumpsys dropbox",
                    "_data": {"crash_count": str(crash_count)}
                })
                c += 1
        self.progress.emit(f"Loaded {c} crash entries")

    def _facebook(self, r):
        self.progress.emit("Extracting Facebook/Messenger files...")
        paths = [
            '/sdcard/Android/data/com.facebook.katana/',
            '/sdcard/Android/data/com.facebook.orca/',
            '/sdcard/Android/media/com.facebook.katana/',
            '/sdcard/Android/media/com.facebook.orca/',
        ]
        c = 0
        for search_path in paths:
            ok, output = adb.adb_shell(self.serial,
                f'find {search_path} -type f 2>/dev/null', timeout=15)
            if not ok or not output:
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "Image" if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif') else (
                    "Video" if ext in ('.mp4', '.3gp') else "Cache File")
                r.append({
                    "type": ftype, "category": "Facebook", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 5000:
                    break
            if c >= 5000:
                break
        self.progress.emit(f"Loaded {c} Facebook files")

    def _snapchat(self, r):
        self.progress.emit("Extracting Snapchat cache files...")
        paths = [
            '/sdcard/Android/data/com.snapchat.android/',
            '/sdcard/Android/media/com.snapchat.android/',
        ]
        c = 0
        for search_path in paths:
            ok, output = adb.adb_shell(self.serial,
                f'find {search_path} -type f 2>/dev/null', timeout=15)
            if not ok or not output:
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "Image" if ext in ('.jpg', '.jpeg', '.png', '.webp') else (
                    "Video" if ext in ('.mp4', '.3gp') else (
                    "Audio" if ext in ('.opus', '.aac', '.mp3') else "Cache File"))
                r.append({
                    "type": ftype, "category": "Snapchat", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 5000:
                    break
            if c >= 5000:
                break
        self.progress.emit(f"Loaded {c} Snapchat files")

    def _tiktok(self, r):
        self.progress.emit("Extracting TikTok cache files...")
        paths = [
            '/sdcard/Android/data/com.zhiliaoapp.musically/',
            '/sdcard/Android/media/com.zhiliaoapp.musically/',
        ]
        c = 0
        for search_path in paths:
            ok, output = adb.adb_shell(self.serial,
                f'find {search_path} -type f 2>/dev/null', timeout=15)
            if not ok or not output:
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "Image" if ext in ('.jpg', '.jpeg', '.png', '.webp') else (
                    "Video" if ext in ('.mp4', '.3gp') else "Cache File")
                r.append({
                    "type": ftype, "category": "TikTok", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 5000:
                    break
            if c >= 5000:
                break
        self.progress.emit(f"Loaded {c} TikTok files")

    def _youtube(self, r):
        self.progress.emit("Extracting YouTube cache files...")
        paths = [
            '/sdcard/Android/data/com.google.android.youtube/',
            '/sdcard/Android/media/com.google.android.youtube/',
        ]
        c = 0
        for search_path in paths:
            ok, output = adb.adb_shell(self.serial,
                f'find {search_path} -type f 2>/dev/null', timeout=15)
            if not ok or not output:
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "Image" if ext in ('.jpg', '.jpeg', '.png', '.webp') else (
                    "Video" if ext in ('.mp4', '.3gp') else "Cache File")
                r.append({
                    "type": ftype, "category": "YouTube", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 3000:
                    break
            if c >= 3000:
                break
        self.progress.emit(f"Loaded {c} YouTube files")

    def _downloads(self, r):
        self.progress.emit("Scanning Downloads folder...")
        ok, output = adb.adb_shell(self.serial,
            'find /sdcard/Download/ -type f 2>/dev/null', timeout=15)
        c = 0
        if ok and output:
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
                    ftype = "Image"
                elif ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov'):
                    ftype = "Video"
                elif ext in ('.opus', '.aac', '.mp3', '.m4a', '.amr'):
                    ftype = "Audio"
                elif ext in ('.pdf', '.doc', '.docx', '.txt', '.csv', '.xlsx', '.xls', '.pptx'):
                    ftype = "Document"
                elif ext in ('.apk', '.xapk'):
                    ftype = "Android Package"
                elif ext in ('.zip', '.rar', '.7z', '.tar', '.gz'):
                    ftype = "Archive"
                else:
                    ftype = "File"
                r.append({
                    "type": ftype, "category": "Downloads", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 5000:
                    break
        self.progress.emit(f"Loaded {c} downloads")

    def _screenshots(self, r):
        self.progress.emit("Scanning screenshots...")
        screenshot_dirs = [
            '/sdcard/Pictures/Screenshots/',
            '/sdcard/DCIM/Screenshots/',
            '/sdcard/Screenshots/',
        ]
        c = 0
        for d in screenshot_dirs:
            ok, output = adb.adb_shell(self.serial, f'ls "{d}" 2>/dev/null', timeout=10)
            if not ok or not output:
                continue
            for fname in output.splitlines():
                fname = fname.strip()
                if not fname:
                    continue
                remote = f"{d}{fname}"
                ext = Path(fname).suffix.lower()
                if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                    ftype = "Screenshot"
                elif ext in ('.mp4', '.3gp'):
                    ftype = "Screen Recording"
                else:
                    ftype = "Screenshot"
                r.append({
                    "type": ftype, "category": "Screenshots", "date": "",
                    "summary": fname, "source": remote,
                    "_data": {"filename": fname, "path": remote, "type": ftype, "size": ""}
                })
                c += 1
        self.progress.emit(f"Loaded {c} screenshots")

    def _camera(self, r):
        self.progress.emit("Scanning camera photos/videos...")
        camera_dirs = [
            '/sdcard/DCIM/Camera/',
            '/sdcard/DCIM/',
        ]
        c = 0
        for d in camera_dirs:
            ok, output = adb.adb_shell(self.serial, f'ls "{d}" 2>/dev/null', timeout=10)
            if not ok or not output:
                continue
            for fname in output.splitlines():
                fname = fname.strip()
                if not fname or fname.endswith("/"):
                    continue
                remote = f"{d}{fname}"
                ext = Path(fname).suffix.lower()
                if ext in ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.dng', '.arw'):
                    ftype = "Photo"
                elif ext in ('.mp4', '.3gp', '.mkv', '.mov'):
                    ftype = "Video"
                else:
                    continue
                r.append({
                    "type": ftype, "category": "Camera", "date": "",
                    "summary": fname, "source": remote,
                    "_data": {"filename": fname, "path": remote, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 3000:
                    break
            if c >= 3000:
                break
        self.progress.emit(f"Loaded {c} camera files")

    def _voice_recordings(self, r):
        self.progress.emit("Scanning voice recordings...")
        rec_dirs = [
            '/sdcard/Recordings/',
            '/sdcard/Sound Recorder/',
            '/sdcard/VoiceRecorder/',
            '/sdcard/DCIM/Voice/',
        ]
        c = 0
        for d in rec_dirs:
            ok, output = adb.adb_shell(self.serial, f'ls "{d}" 2>/dev/null', timeout=10)
            if not ok or not output:
                continue
            for fname in output.splitlines():
                fname = fname.strip()
                if not fname:
                    continue
                remote = f"{d}{fname}"
                ext = Path(fname).suffix.lower()
                if ext in ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.amr', '.3gp'):
                    r.append({
                        "type": "Voice Recording", "category": "Recordings", "date": "",
                        "summary": fname, "source": remote,
                        "_data": {"filename": fname, "path": remote, "type": "Voice Recording", "size": ""}
                    })
                    c += 1
        self.progress.emit(f"Loaded {c} voice recordings")

    def _chrome_history(self, r):
        self.progress.emit("Extracting Chrome bookmarks & history...")
        c = 0
        chrome_paths = [
            "/sdcard/Android/data/com.android.chrome/",
            "/sdcard/Android/media/com.android.chrome/",
        ]
        for base in chrome_paths:
            for fname in ["Bookmarks", "History", "Login Data", "Web Data", "Cookies"]:
                ok, output = adb.adb_shell(self.serial, f'find "{base}" -maxdepth 4 -type f -name "{fname}" 2>/dev/null', timeout=10)
                if not ok or not output:
                    continue
                for line in output.splitlines():
                    line = line.strip()
                    if not line or "Permission denied" in line:
                        continue
                    actual_name = Path(line).name
                    r.append({
                        "type": "Browser Data", "category": "Chrome", "date": "",
                        "summary": f"Chrome {actual_name}", "source": line,
                        "_data": {"filename": actual_name, "path": line, "type": "Browser Data", "size": ""}
                    })
                    c += 1
        self.progress.emit(f"Loaded {c} Chrome entries")

    def _all_sdcard_files(self, r):
        self.progress.emit("Scanning files across key directories...")
        c = 0
        existing_sources = {a.get("source") for a in r}

        dirs_to_scan = [
            "/sdcard/Download/",
            "/sdcard/DCIM/",
            "/sdcard/Pictures/",
            "/sdcard/Documents/",
            "/sdcard/Recordings/",
            "/sdcard/Telegram/",
            "/sdcard/WhatsApp/",
            "/sdcard/Android/data/",
            "/sdcard/Android/media/",
        ]

        for d in dirs_to_scan:
            ok, output = adb.adb_shell(self.serial, f'find "{d}" -maxdepth 5 -type f 2>/dev/null | head -3000', timeout=15)
            if not ok or not output:
                continue
            for line in output.splitlines():
                line = line.strip()
                if not line or "Permission denied" in line or line in existing_sources:
                    continue
                fname = Path(line).name
                ext = Path(line).suffix.lower()
                ftype = "Other"
                if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.heif'):
                    ftype = "Image"
                elif ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov'):
                    ftype = "Video"
                elif ext in ('.opus', '.aac', '.mp3', '.m4a', '.amr', '.wav', '.ogg'):
                    ftype = "Audio"
                elif ext in ('.pdf', '.doc', '.docx', '.txt', '.csv', '.xlsx', '.xls', '.pptx'):
                    ftype = "Document"
                elif ext in ('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm'):
                    ftype = "Database"
                elif ext in ('.apk', '.xapk', '.obb'):
                    ftype = "App Package"
                elif ext in ('.zip', '.rar', '.7z', '.tar', '.gz'):
                    ftype = "Archive"
                elif ext in ('.json', '.xml', '.yaml', '.yml', '.log', '.conf', '.ini'):
                    ftype = "Config/Data"
                elif ext in ('.vcf', '.vcard'):
                    ftype = "Contact"
                elif ext in ('.ics', '.ical'):
                    ftype = "Calendar"
                elif ext in ('.tmp', '.bak', '.old', '.temp'):
                    ftype = "Temp/Backup"

                if ftype == "Other":
                    continue

                existing_sources.add(line)
                r.append({
                    "type": ftype, "category": "All Files", "date": "",
                    "summary": fname, "source": line,
                    "_data": {"filename": fname, "path": line, "type": ftype, "size": ""}
                })
                c += 1
                if c >= 5000:
                    break
            if c >= 5000:
                break
        self.progress.emit(f"Found {c} additional files")


class IconPainter(QWidget):
    """Custom widget that paints social media brand icons using QPainter."""

    def __init__(self, category, color, parent=None):
        super().__init__(parent)
        self.category = category
        self.color = color
        self.setFixedSize(48, 48)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QFont
        from PyQt6.QtCore import Qt, QPointF, QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        w, h = 48, 48
        cx, cy = w / 2, h / 2

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(c))
        p.drawRoundedRect(0, 0, w, h, 24, 24)

        p.setPen(QPen(QColor("white"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)

        cat = self.category

        if cat == "SMS":
            path = QPainterPath()
            path.addRoundedRect(QRectF(8, 12, 32, 22), 4, 4)
            path.moveTo(14, 34)
            path.lineTo(10, 40)
            path.lineTo(20, 34)
            p.drawPath(path)
            p.drawLine(QPointF(16, 20), QPointF(32, 20))
            p.drawLine(QPointF(16, 26), QPointF(28, 26))

        elif cat == "Calls":
            path = QPainterPath()
            path.moveTo(14, 12)
            path.cubicTo(14, 12, 12, 18, 16, 22)
            path.lineTo(18, 20)
            path.cubicTo(18, 20, 20, 24, 24, 28)
            path.lineTo(22, 30)
            path.cubicTo(22, 30, 28, 36, 34, 34)
            path.cubicTo(36, 32, 36, 30, 36, 30)
            path.lineTo(34, 26)
            path.cubicTo(34, 26, 30, 22, 26, 18)
            path.lineTo(28, 16)
            path.cubicTo(28, 16, 22, 10, 14, 12)
            p.drawPath(path)

        elif cat == "WhatsApp":
            p.setPen(QPen(QColor("white"), 2.4))
            p.drawEllipse(QPointF(24, 22), 14, 14)
            path = QPainterPath()
            path.moveTo(30, 32)
            path.lineTo(34, 40)
            path.lineTo(38, 34)
            p.drawPath(path)
            p.setPen(QPen(QColor("white"), 1.8))
            p.drawEllipse(QPointF(19, 22), 2.5, 2.5)
            p.drawEllipse(QPointF(29, 22), 2.5, 2.5)
            arc_path = QPainterPath()
            arc_path.moveTo(16, 28)
            arc_path.arcTo(QRectF(16, 26, 20, 10), 200, 140)
            p.drawPath(arc_path)

        elif cat == "Telegram":
            path = QPainterPath()
            path.moveTo(8, 24)
            path.lineTo(38, 14)
            path.lineTo(30, 38)
            path.lineTo(22, 28)
            path.lineTo(30, 18)
            path.lineTo(18, 26)
            path.lineTo(24, 30)
            path.closeSubpath()
            p.drawPath(path)
            p.setPen(QPen(QColor("white"), 1.5))
            p.drawLine(QPointF(22, 28), QPointF(30, 38))

        elif cat == "Instagram":
            p.drawRoundedRect(QRectF(8, 8, 32, 32), 8, 8)
            p.setPen(QPen(QColor("white"), 2.2))
            p.drawRoundedRect(QRectF(12, 12, 24, 24), 6, 6)
            p.setBrush(QBrush(QColor(self.color)))
            p.drawEllipse(QPointF(24, 24), 6, 6)
            p.setPen(QPen(QColor("white"), 2.2))
            p.drawEllipse(QPointF(24, 24), 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(34, 14), 2.2, 2.2)

        elif cat == "Twitter":
            path = QPainterPath()
            path.moveTo(10, 34)
            path.cubicTo(10, 34, 14, 36, 20, 32)
            path.cubicTo(14, 32, 12, 26, 12, 26)
            path.cubicTo(14, 20, 18, 18, 18, 18)
            path.cubicTo(16, 14, 18, 10, 22, 10)
            path.cubicTo(26, 10, 28, 14, 28, 14)
            path.cubicTo(32, 14, 36, 12, 38, 10)
            path.cubicTo(38, 16, 36, 20, 34, 22)
            path.cubicTo(34, 28, 30, 34, 24, 36)
            p.drawPath(path)

        elif cat == "Contacts":
            p.drawEllipse(QPointF(24, 16), 8, 8)
            path = QPainterPath()
            path.arcTo(QRectF(12, 26, 24, 18), 180, 180)
            p.drawPath(path)

        elif cat == "Calendar":
            p.drawRoundedRect(QRectF(8, 12, 32, 28), 3, 3)
            p.setPen(QPen(QColor("white"), 3))
            p.drawLine(QPointF(8, 22), QPointF(40, 22))
            p.setPen(QPen(QColor("white"), 2))
            p.drawLine(QPointF(16, 8), QPointF(16, 16))
            p.drawLine(QPointF(32, 8), QPointF(32, 16))
            p.setPen(QPen(QColor("white"), 1.5))
            for row in range(3):
                for col in range(4):
                    x = 13 + col * 7
                    y = 26 + row * 7
                    p.drawEllipse(QPointF(x, y), 1.5, 1.5)

        elif cat == "Media":
            path = QPainterPath()
            path.moveTo(18, 12)
            path.lineTo(36, 24)
            path.lineTo(18, 36)
            path.closeSubpath()
            p.drawPath(path)

        elif cat == "Documents":
            path = QPainterPath()
            path.moveTo(14, 8)
            path.lineTo(30, 8)
            path.lineTo(36, 14)
            path.lineTo(36, 40)
            path.lineTo(14, 40)
            path.closeSubpath()
            p.drawPath(path)
            p.setPen(QPen(QColor(self.color), 1.8))
            p.drawLine(QPointF(20, 20), QPointF(32, 20))
            p.drawLine(QPointF(20, 26), QPointF(32, 26))
            p.drawLine(QPointF(20, 32), QPointF(28, 32))
            path2 = QPainterPath()
            path2.moveTo(30, 8)
            path2.lineTo(30, 14)
            path2.lineTo(36, 14)
            p.setPen(QPen(QColor("white"), 1.8))
            p.drawPath(path2)

        elif cat == "Transactions":
            p.setPen(QPen(QColor("white"), 2))
            p.drawEllipse(QPointF(24, 24), 14, 14)
            p.setPen(QPen(QColor("white"), 2.5))
            path = QPainterPath()
            path.moveTo(24, 12)
            path.lineTo(24, 36)
            p.drawPath(path)
            p.setPen(QPen(QColor("white"), 1.8))
            p.drawEllipse(QPointF(24, 18), 5, 4)
            p.drawEllipse(QPointF(24, 30), 5, 4)

        elif cat == "Emails":
            p.drawRoundedRect(QRectF(8, 12, 32, 24), 3, 3)
            path = QPainterPath()
            path.moveTo(8, 12)
            path.lineTo(24, 26)
            path.lineTo(40, 12)
            p.drawPath(path)

        elif cat == "WiFi":
            for i, r in enumerate([6, 11, 16]):
                arc = QPainterPath()
                arc.arcTo(QRectF(cx - r, cy - r + 4, r * 2, r * 2), 220, 100)
                p.setPen(QPen(QColor("white"), 2.0 - i * 0.3))
                p.drawPath(arc)
            p.setBrush(QBrush(QColor("white")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy + 4), 2.5, 2.5)

        elif cat == "System":
            p.setPen(QPen(QColor("white"), 2))
            p.drawEllipse(QPointF(cx, cy), 8, 8)
            p.drawEllipse(QPointF(cx, cy), 4, 4)
            for angle in range(0, 360, 45):
                import math
                rad = math.radians(angle)
                x1 = cx + 10 * math.cos(rad)
                y1 = cy + 10 * math.sin(rad)
                x2 = cx + 14 * math.cos(rad)
                y2 = cy + 14 * math.sin(rad)
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        elif cat == "Deleted":
            path = QPainterPath()
            path.moveTo(14, 14)
            path.lineTo(34, 14)
            path.lineTo(32, 38)
            path.lineTo(16, 38)
            path.closeSubpath()
            p.drawPath(path)
            p.setPen(QPen(QColor(self.color), 2))
            p.drawLine(QPointF(20, 10), QPointF(28, 10))
            p.drawLine(QPointF(20, 10), QPointF(20, 14))
            p.drawLine(QPointF(28, 10), QPointF(28, 14))
            p.setPen(QPen(QColor("white"), 1.5))
            p.drawLine(QPointF(21, 20), QPointF(21, 32))
            p.drawLine(QPointF(27, 20), QPointF(27, 32))

        elif cat == "Crashes":
            path = QPainterPath()
            path.moveTo(24, 8)
            path.lineTo(40, 40)
            path.lineTo(8, 40)
            path.closeSubpath()
            p.drawPath(path)
            p.setPen(QPen(QColor("white"), 2.5))
            p.drawLine(QPointF(24, 18), QPointF(24, 28))
            p.setBrush(QBrush(QColor("white")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(24, 34), 2, 2)

        elif cat == "Facebook":
            p.setFont(QFont("Arial", 20, QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "f")

        elif cat == "Snapchat":
            p.setPen(QPen(QColor("white"), 2.2))
            p.drawEllipse(QPointF(24, 22), 12, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(20, 20), 2, 2)
            p.drawEllipse(QPointF(28, 20), 2, 2)
            path = QPainterPath()
            path.moveTo(20, 30)
            path.cubicTo(20, 30, 24, 36, 28, 30)
            p.drawPath(path)

        elif cat == "TikTok":
            p.setFont(QFont("Arial", 18, QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "d")

        elif cat == "YouTube":
            path = QPainterPath()
            path.addRoundedRect(QRectF(8, 14, 32, 20), 6, 6)
            p.drawPath(path)
            path2 = QPainterPath()
            path2.moveTo(20, 18)
            path2.lineTo(32, 24)
            path2.lineTo(20, 30)
            path2.closeSubpath()
            p.setBrush(QBrush(QColor("white")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path2)

        elif cat == "Downloads":
            p.drawLine(QPointF(24, 10), QPointF(24, 32))
            path = QPainterPath()
            path.moveTo(16, 26)
            path.lineTo(24, 34)
            path.lineTo(32, 26)
            p.drawPath(path)
            p.drawLine(QPointF(14, 36), QPointF(34, 36))

        elif cat == "Screenshots":
            path = QPainterPath()
            path.addRoundedRect(QRectF(8, 10, 32, 28), 4, 4)
            p.drawPath(path)
            p.drawEllipse(QPointF(24, 24), 8, 8)
            p.setBrush(QBrush(QColor("white")))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(24, 24), 4, 4)

        elif cat == "Camera":
            path = QPainterPath()
            path.addRoundedRect(QRectF(6, 16, 36, 24), 4, 4)
            p.drawPath(path)
            path.moveTo(16, 16)
            path.lineTo(20, 10)
            path.lineTo(28, 10)
            path.lineTo(32, 16)
            p.drawPath(path)
            p.drawEllipse(QPointF(24, 28), 6, 6)

        elif cat == "Recordings":
            p.drawLine(QPointF(24, 12), QPointF(24, 28))
            path = QPainterPath()
            path.addEllipse(QPointF(24, 32), 6, 4)
            p.drawPath(path)
            p.drawLine(QPointF(18, 12), QPointF(18, 20))
            p.drawLine(QPointF(30, 12), QPointF(30, 20))

        elif cat == "Chrome":
            p.drawEllipse(QPointF(24, 24), 16, 16)
            p.drawEllipse(QPointF(24, 24), 6, 6)
            p.drawLine(QPointF(24, 8), QPointF(24, 18))
            p.drawLine(QPointF(24, 30), QPointF(24, 40))
            p.drawLine(QPointF(8, 24), QPointF(18, 24))
            p.drawLine(QPointF(30, 24), QPointF(40, 24))

        elif cat == "All Files":
            p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "...")

        elif cat == "Emails & Accounts":
            path = QPainterPath()
            path.moveTo(8, 14)
            path.lineTo(24, 26)
            path.lineTo(40, 14)
            path.lineTo(40, 36)
            path.lineTo(8, 36)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(QPointF(8, 14), QPointF(24, 26))
            p.drawLine(QPointF(40, 14), QPointF(24, 26))

        p.end()


class AppIconWidget(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, app_def, count=0, parent=None):
        super().__init__(parent)
        self.app_def = app_def
        self.count = count
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(110, 120)
        self.setStyleSheet(f"""
            AppIconWidget {{
                background: {app_def['bg']};
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.05);
            }}
            AppIconWidget:hover {{
                border: 1px solid {app_def['color']}40;
                background: {app_def['bg']};
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 10)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = IconPainter(app_def["category"], app_def["color"])
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel(app_def["name"])
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("color: #e2e8f0; font-size: 11px; font-weight: 600; background: transparent; border: none;")
        layout.addWidget(name_label)

        self.count_label = QLabel("0")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setStyleSheet(f"color: {app_def['color']}; font-size: 10px; font-weight: 500; background: transparent; border: none;")
        layout.addWidget(self.count_label)

    def update_count(self, count):
        self.count = count
        self.count_label.setText(str(count) if count > 0 else "")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.app_def)


class ConversationItemWidget(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, conversation, parent=None):
        super().__init__(parent)
        self.conversation = conversation
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(72)
        self.setStyleSheet("""
            ConversationItemWidget {
                background: #111827;
                border-radius: 12px;
                border: 1px solid #1e293b;
            }
            ConversationItemWidget:hover {
                background: #1a2234;
                border: 1px solid #334155;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        contact = conversation.get("contact_name", "")
        phone = conversation.get("phone", "")
        messages = conversation.get("messages", [])
        display_name = contact if contact else phone if phone else "Unknown"

        initial = display_name[0].upper() if display_name else "?"
        colors = ["#2563eb", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#e4405f", "#f97316"]
        color_idx = sum(ord(c) for c in display_name) % len(colors)
        avatar_color = colors[color_idx]

        avatar = QLabel(initial)
        avatar.setFixedSize(44, 44)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"""
            background: {avatar_color};
            border-radius: 22px;
            color: white;
            font-size: 17px;
            font-weight: 700;
            font-family: 'Segoe UI', sans-serif;
        """)
        layout.addWidget(avatar)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        text_layout.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        name_lbl = QLabel(display_name)
        name_lbl.setStyleSheet("color: #e2e8f0; font-size: 14px; font-weight: 600; background: transparent; border: none;")
        top_row.addWidget(name_lbl)
        top_row.addStretch()

        last_date = ""
        if messages:
            last_date = messages[0].get("date", "") or messages[0].get("_data", {}).get("date", "")
            if last_date:
                rel = _ts_relative(last_date)
                date_lbl = QLabel(rel)
                date_lbl.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
                top_row.addWidget(date_lbl)

        text_layout.addLayout(top_row)

        preview = ""
        if messages:
            last_msg = messages[0]
            msg_type = last_msg.get("type", "")
            body = last_msg.get("_data", {}).get("body", "")
            direction = last_msg.get("_data", {}).get("type", "")
            if direction == "Outgoing":
                preview = f"You: {body[:60]}"
            else:
                preview = body[:70]
            if len(body) > 60:
                preview += "..."

        preview_lbl = QLabel(preview)
        preview_lbl.setStyleSheet("color: #64748b; font-size: 12px; background: transparent; border: none;")
        preview_lbl.setMaximumWidth(400)
        text_layout.addWidget(preview_lbl)

        layout.addLayout(text_layout, 1)

        count = len(messages)
        if count > 1:
            badge = QLabel(str(count))
            badge.setFixedSize(24, 24)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet("""
                background: #2563eb;
                border-radius: 12px;
                color: white;
                font-size: 10px;
                font-weight: 700;
            """)
            layout.addWidget(badge)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.conversation)


class MessageBubbleWidget(QFrame):
    def __init__(self, message, is_outgoing=False, show_date=False, parent=None):
        super().__init__(parent)
        self.message = message
        self._is_outgoing = is_outgoing

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 2, 16, 2)
        layout.setSpacing(2)

        if show_date:
            date_str = message.get("date", "") or message.get("_data", {}).get("date", "")
            if date_str:
                date_lbl = QLabel(date_str)
                date_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                date_lbl.setStyleSheet("color: #475569; font-size: 10px; padding: 6px 0; background: transparent; border: none;")
                layout.addWidget(date_lbl)

        bubble = QFrame()
        body = message.get("_data", {}).get("body", "") or message.get("summary", "")
        time_str = ""
        raw_date = message.get("_data", {}).get("date", "")
        if raw_date and ":" in raw_date:
            time_str = raw_date.split(" ")[-1] if " " in raw_date else raw_date

        if is_outgoing:
            bubble.setStyleSheet("""
                QFrame {
                    background: #1e40af;
                    border-radius: 16px;
                    border-bottom-right-radius: 4px;
                }
            """)
            text_color = "#e2e8f0"
        else:
            bubble.setStyleSheet("""
                QFrame {
                    background: #1e293b;
                    border-radius: 16px;
                    border-bottom-left-radius: 4px;
                }
            """)
            text_color = "#e2e8f0"

        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(4)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(f"color: {text_color}; font-size: 13px; background: transparent; border: none; line-height: 1.4;")
        bubble_layout.addWidget(body_lbl)

        if time_str:
            time_lbl = QLabel(time_str)
            time_lbl.setStyleSheet("color: #64748b; font-size: 10px; background: transparent; border: none;")
            bubble_layout.addWidget(time_lbl, 0, Qt.AlignmentFlag.AlignRight if is_outgoing else Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight if is_outgoing else Qt.AlignmentFlag.AlignLeft)

        self.setMaximumWidth(480)


class ArtifactsPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.evidence_root = None
        self.all_artifacts = []
        self._worker = None
        self._data_loaded = False
        self._contacts = []
        self._conversations = {}
        self._current_app = None
        #: Active case session; findings are only written to the case when the
        #: window has one (the artifact rows require a session and a timestamp).
        self._session_id = ""
        self.setStyleSheet(DARK_STYLE)
        self._setup_ui()

    def set_session(self, session_id: str):
        self._session_id = session_id or ""

    def set_evidence_root(self, path):
        self.evidence_root = Path(path) if path else None

    def _setup_ui(self):
        from PyQt6.QtWidgets import QStackedWidget
        ml = QVBoxLayout(self)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        self.stacked = QStackedWidget()
        ml.addWidget(self.stacked)

        self.app_grid_view = self._build_app_grid()
        self.conversation_view = self._build_conversation_view()
        self.chat_view = self._build_chat_view()

        self.stacked.addWidget(self.app_grid_view)
        self.stacked.addWidget(self.conversation_view)
        self.stacked.addWidget(self.chat_view)

    def _build_app_grid(self):
        page = QWidget()
        page.setStyleSheet("background: #0a0e17;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)

        from ..widgets.device_status_bar import DeviceStatusBar
        self.device_bar = DeviceStatusBar()
        self.device_bar.device_connected.connect(self._on_device_connected)
        outer.addWidget(self.device_bar)

        header = QHBoxLayout()
        header.setSpacing(12)
        title = QLabel("DroidForenX")
        title.setStyleSheet("color: #2563eb; font-size: 18px; font-weight: 700; background: transparent; border: none;")
        header.addWidget(title)
        subtitle = QLabel("Artifact Viewer")
        subtitle.setStyleSheet("color: #64748b; font-size: 13px; background: transparent; border: none;")
        header.addWidget(subtitle)
        header.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search artifacts...")
        self.search_input.setFixedWidth(260)
        self.search_input.textChanged.connect(self._filter_apps)
        header.addWidget(self.search_input)

        self.refresh_btn = QPushButton("Extract All")
        self.refresh_btn.setFixedWidth(110)
        # Flat by design, but its text has to be themed: it sits on the page
        # background with no fill, and the inherited white was invisible once
        # that background stopped being near-black.
        self.refresh_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #e2e8f0;"
            " font-weight: 600; font-size: 13px; padding: 6px 10px; }"
            "QPushButton:hover { color: #3b82f6; }"
        )
        self.refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(self.refresh_btn)
        outer.addLayout(header)

        self.progress_frame = QFrame()
        self.progress_frame.setStyleSheet("background: #111827; border-radius: 8px;")
        pf_layout = QHBoxLayout(self.progress_frame)
        pf_layout.setContentsMargins(16, 10, 16, 10)
        self.progress_label = QLabel("Connecting to device...")
        self.progress_label.setStyleSheet("color: #94a3b8; font-size: 12px; background: transparent; border: none;")
        pf_layout.addWidget(self.progress_label)
        pf_layout.addStretch()
        self.progress_frame.setVisible(False)
        outer.addWidget(self.progress_frame)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        self.app_grid_layout = QGridLayout(scroll_content)
        self.app_grid_layout.setSpacing(12)
        self.app_grid_layout.setContentsMargins(0, 0, 0, 0)

        self.app_icon_widgets = {}
        for i, app_def in enumerate(APP_DEFS):
            icon = AppIconWidget(app_def, 0)
            icon.clicked.connect(self._on_app_clicked)
            row = i // 5
            col = i % 5
            self.app_grid_layout.addWidget(icon, row, col)
            self.app_icon_widgets[app_def["category"]] = icon

        scroll.setWidget(scroll_content)
        outer.addWidget(scroll, 1)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #475569; font-size: 11px; padding: 8px 0; background: transparent; border: none;")
        outer.addWidget(self.stats_label)

        return page

    def _build_conversation_view(self):
        page = QWidget()
        page.setStyleSheet("background: #0a0e17;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setStyleSheet("background: #111827; border-bottom: 1px solid #1e293b;")
        header.setFixedHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(12)

        self.back_btn = QPushButton("<")
        self.back_btn.setFixedSize(36, 36)
        self.back_btn.setStyleSheet("""
            QPushButton { background: #1e293b; border-radius: 18px; font-size: 16px; padding: 0; color: #e2e8f0; }
            QPushButton:hover { background: #334155; }
        """)
        self.back_btn.clicked.connect(self._go_back_to_grid)
        header_layout.addWidget(self.back_btn)

        self.conv_title = QLabel("")
        self.conv_title.setStyleSheet("color: #e2e8f0; font-size: 16px; font-weight: 700; background: transparent; border: none;")
        header_layout.addWidget(self.conv_title)
        header_layout.addStretch()

        self.conv_count = QLabel("")
        self.conv_count.setStyleSheet("color: #64748b; font-size: 12px; background: transparent; border: none;")
        header_layout.addWidget(self.conv_count)
        outer.addWidget(header)

        self.conv_scroll = QScrollArea()
        self.conv_scroll.setWidgetResizable(True)
        self.conv_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conv_list_widget = QWidget()
        self.conv_list_layout = QVBoxLayout(self.conv_list_widget)
        self.conv_list_layout.setContentsMargins(12, 12, 12, 12)
        self.conv_list_layout.setSpacing(6)
        self.conv_list_layout.addStretch()
        self.conv_scroll.setWidget(self.conv_list_widget)
        outer.addWidget(self.conv_scroll, 1)

        return page

    def _build_chat_view(self):
        page = QWidget()
        page.setStyleSheet("background: #0a0e17;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setStyleSheet("background: #111827; border-bottom: 1px solid #1e293b;")
        header.setFixedHeight(56)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(12)

        self.chat_back_btn = QPushButton("<")
        self.chat_back_btn.setFixedSize(36, 36)
        self.chat_back_btn.setStyleSheet("""
            QPushButton { background: #1e293b; border-radius: 18px; font-size: 16px; padding: 0; color: #e2e8f0; }
            QPushButton:hover { background: #334155; }
        """)
        self.chat_back_btn.clicked.connect(self._go_back_to_conversations)
        header_layout.addWidget(self.chat_back_btn)

        self.chat_contact_name = QLabel("")
        self.chat_contact_name.setStyleSheet("color: #e2e8f0; font-size: 16px; font-weight: 700; background: transparent; border: none;")
        header_layout.addWidget(self.chat_contact_name)
        header_layout.addStretch()

        self.chat_msg_count = QLabel("")
        self.chat_msg_count.setStyleSheet("color: #64748b; font-size: 12px; background: transparent; border: none;")
        header_layout.addWidget(self.chat_msg_count)
        outer.addWidget(header)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list_widget = QWidget()
        self.chat_list_layout = QVBoxLayout(self.chat_list_widget)
        self.chat_list_layout.setContentsMargins(80, 16, 80, 16)
        self.chat_list_layout.setSpacing(4)
        self.chat_list_layout.addStretch()
        self.chat_scroll.setWidget(self.chat_list_widget)
        outer.addWidget(self.chat_scroll, 1)

        return page

    def _filter_apps(self, text):
        query = text.strip().lower()
        for cat, widget in self.app_icon_widgets.items():
            if not query:
                widget.setVisible(True)
            else:
                app_def = widget.app_def
                match = query in app_def["name"].lower() or query in app_def["desc"].lower()
                widget.setVisible(match)

    def _on_app_clicked(self, app_def):
        self._current_app = app_def
        category = app_def["category"]
        app_type = app_def.get("type", "list")

        if category == "SMS":
            self._show_sms_conversations()
        elif category == "Calls":
            self._show_call_conversations()
        elif category == "WhatsApp":
            self._show_whatsapp_conversations()
        elif category == "Telegram":
            self._show_telegram_conversations()
        elif category == "Deleted Files":
            self._show_deleted_files()
        elif category == "Documents":
            self._show_documents()
        elif category == "Emails & Accounts":
            self._show_emails()
        elif category == "System Info":
            self._show_system()
        elif app_type == "media":
            self._show_media_gallery(category, app_def)
        else:
            self._show_generic_list(category, app_def)

    def _show_sms_conversations(self):
        self.conv_title.setText("Messages")
        self.conv_count.setText(f"{len(self._conversations)} conversations")

        self._clear_layout(self.conv_list_layout)

        sorted_convs = sorted(self._conversations.values(), key=_conversation_sort_key, reverse=True)
        for conv in sorted_convs:
            item = ConversationItemWidget(conv)
            item.clicked.connect(self._on_conversation_clicked)
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item)

        self.stacked.setCurrentIndex(1)

    def _show_call_conversations(self):
        self.conv_title.setText("Call Log")
        calls = [a for a in self.all_artifacts if a.get("category") == "Calls"]
        self.conv_count.setText(f"{len(calls)} calls")

        self._clear_layout(self.conv_list_layout)

        call_convs = {}
        for call in calls:
            number = call.get("_data", {}).get("number", "Unknown")
            if number not in call_convs:
                contact_name = _resolve_contact_name(self._contacts, number)
                call_convs[number] = {
                    "phone": number,
                    "contact_name": contact_name,
                    "messages": [],
                }
            call_convs[number]["messages"].append(call)

        sorted_convs = sorted(call_convs.values(), key=_conversation_sort_key, reverse=True)
        for conv in sorted_convs:
            item = ConversationItemWidget(conv)
            item.clicked.connect(self._on_conversation_clicked)
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item)

        self.stacked.setCurrentIndex(1)

    def _show_whatsapp_conversations(self):
        self.conv_title.setText("WhatsApp")
        wa_items = [a for a in self.all_artifacts if a.get("category") == "WhatsApp"]
        self.conv_count.setText(f"{len(wa_items)} files")

        self._clear_layout(self.conv_list_layout)

        if not wa_items:
            empty = QLabel("No WhatsApp data accessible.\n\nWhatsApp databases require root access.\nCached media from /sdcard/Android/data/com.whatsapp/ is shown.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        type_groups = {}
        for item in wa_items:
            ftype = item.get("type", "Cache File")
            if ftype not in type_groups:
                type_groups[ftype] = []
            type_groups[ftype].append(item)

        for ftype in ["Image", "Video", "Audio", "GIF", "Document", "Cache File"]:
            items = type_groups.get(ftype, [])
            if not items:
                continue
            header = QLabel(f"  {ftype} ({len(items)})")
            header.setStyleSheet(f"color: #25d366; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)

            for item in items[:200]:
                item_frame = QFrame()
                item_frame.setStyleSheet("""
                    QFrame { background: #111827; border-radius: 8px; border: 1px solid #1e293b; }
                    QFrame:hover { background: #1a2234; border: 1px solid #334155; }
                """)
                item_frame.setFixedHeight(44)
                item_layout = QHBoxLayout(item_frame)
                item_layout.setContentsMargins(12, 6, 12, 6)

                fname = item.get("_data", {}).get("filename", "")
                sz = item.get("_data", {}).get("size", "")
                name_lbl = QLabel(fname[:50])
                name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                item_layout.addWidget(name_lbl, 1)

                if sz:
                    sz_lbl = QLabel(sz)
                    sz_lbl.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
                    item_layout.addWidget(sz_lbl)

                if ftype in ("Image", "Video", "GIF"):
                    view_btn = QPushButton("View")
                    view_btn.setFixedSize(50, 26)
                    view_btn.setStyleSheet("QPushButton { background: #25d366; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #1da851; }")
                    remote_path = item.get("_data", {}).get("path", "")
                    view_btn.clicked.connect(lambda checked, p=remote_path, t=ftype: self._open_media_viewer(p, t))
                    item_layout.addWidget(view_btn)

                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _show_telegram_conversations(self):
        self.conv_title.setText("Telegram")
        tg_items = [a for a in self.all_artifacts if a.get("category") == "Telegram"]
        self.conv_count.setText(f"{len(tg_items)} files")

        self._clear_layout(self.conv_list_layout)

        if not tg_items:
            empty = QLabel("No Telegram data accessible.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        type_groups = {}
        for item in tg_items:
            ftype = item.get("type", "Cache File")
            if ftype not in type_groups:
                type_groups[ftype] = []
            type_groups[ftype].append(item)

        for ftype in ["Image", "Video", "Sticker (Animated)", "Profile Photo", "Audio", "Cache File"]:
            items = type_groups.get(ftype, [])
            if not items:
                continue
            header = QLabel(f"  {ftype} ({len(items)})")
            header.setStyleSheet(f"color: #0088cc; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)

            for item in items[:200]:
                item_frame = QFrame()
                item_frame.setStyleSheet("""
                    QFrame { background: #111827; border-radius: 8px; border: 1px solid #1e293b; }
                    QFrame:hover { background: #1a2234; border: 1px solid #334155; }
                """)
                item_frame.setFixedHeight(44)
                item_layout = QHBoxLayout(item_frame)
                item_layout.setContentsMargins(12, 6, 12, 6)

                fname = item.get("_data", {}).get("filename", "")
                sz = item.get("_data", {}).get("size", "")
                name_lbl = QLabel(fname[:50])
                name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                item_layout.addWidget(name_lbl, 1)

                if sz:
                    sz_lbl = QLabel(sz)
                    sz_lbl.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
                    item_layout.addWidget(sz_lbl)

                if ftype in ("Image", "Video", "Profile Photo"):
                    view_btn = QPushButton("View")
                    view_btn.setFixedSize(50, 26)
                    view_btn.setStyleSheet("QPushButton { background: #0088cc; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #006ba3; }")
                    remote_path = item.get("_data", {}).get("path", "")
                    view_btn.clicked.connect(lambda checked, p=remote_path, t=ftype: self._open_media_viewer(p, t))
                    item_layout.addWidget(view_btn)

                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _show_media_gallery(self, category, app_def):
        self.conv_title.setText(app_def["name"] + " — Media Gallery")
        media_items = [a for a in self.all_artifacts if a.get("category") == category
                       and a.get("type") in ("Image", "Video", "GIF", "Audio", "Photo", "Voice Recording")]
        self.conv_count.setText(f"{len(media_items)} media files")

        self._clear_layout(self.conv_list_layout)

        if not media_items:
            empty = QLabel("No media files found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        type_groups = {}
        for item in media_items:
            ftype = item.get("type", "")
            if ftype not in type_groups:
                type_groups[ftype] = []
            type_groups[ftype].append(item)

        total_label = QLabel(f"  Total: {len(media_items)} files")
        total_label.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 4px 0 8px 0; background: transparent; border: none;")
        self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, total_label)

        for ftype in ["Photo", "Image", "Video", "GIF", "Audio", "Voice Recording"]:
            items = type_groups.get(ftype, [])
            if not items:
                continue

            color = "#8b5cf6"
            if ftype in ("Photo", "Image"):
                color = "#22c55e"
            elif ftype == "Video":
                color = "#3b82f6"
            elif ftype == "GIF":
                color = "#f59e0b"
            elif ftype in ("Audio", "Voice Recording"):
                color = "#ec4899"

            header = QLabel(f"  {ftype} ({len(items)})")
            header.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)

            folder_groups = {}
            for item in items:
                folder = item.get("_data", {}).get("folder", "Unknown")
                if folder not in folder_groups:
                    folder_groups[folder] = []
                folder_groups[folder].append(item)

            for folder, folder_items in folder_groups.items():
                if len(folder_groups) > 1:
                    folder_short = folder.split("/")[-1] if "/" in folder else folder
                    folder_lbl = QLabel(f"    {folder_short} ({len(folder_items)})")
                    folder_lbl.setStyleSheet("color: #64748b; font-size: 10px; padding: 2px 0 2px 12px; background: transparent; border: none;")
                    self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, folder_lbl)

                for item in folder_items[:1000]:
                    item_frame = QFrame()
                    item_frame.setStyleSheet(f"""
                        QFrame {{ background: #111827; border-radius: 8px; border: 1px solid #1e293b; }}
                        QFrame:hover {{ background: #1a2234; border: 1px solid {color}; }}
                    """)
                    item_frame.setFixedHeight(44)
                    item_layout = QHBoxLayout(item_frame)
                    item_layout.setContentsMargins(12, 6, 12, 6)

                    fname = item.get("_data", {}).get("filename", "")
                    sz = item.get("_data", {}).get("size", "")
                    date = item.get("date", "")

                    name_lbl = QLabel(fname[:50])
                    name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                    name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    item_layout.addWidget(name_lbl, 1)

                    if sz:
                        sz_lbl = QLabel(sz)
                        sz_lbl.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
                        item_layout.addWidget(sz_lbl)

                    if date:
                        date_lbl = QLabel(date[:10])
                        date_lbl.setStyleSheet("color: #475569; font-size: 10px; background: transparent; border: none;")
                        item_layout.addWidget(date_lbl)

                    remote_path = item.get("_data", {}).get("path", "")
                    is_media = ftype in ("Image", "Photo", "Video", "GIF")
                    is_audio = ftype in ("Audio", "Voice Recording")

                    if is_media:
                        view_btn = QPushButton("View")
                        view_btn.setFixedSize(50, 26)
                        view_btn.setStyleSheet(f"QPushButton {{ background: {color}; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; }} QPushButton:hover {{ opacity: 0.8; }}")
                        media_t = "Image" if ftype in ("Photo", "Image") else ("Video" if ftype == "Video" else "GIF")
                        view_btn.clicked.connect(lambda checked, p=remote_path, t=media_t: self._open_media_viewer(p, t))
                        item_layout.addWidget(view_btn)
                    elif is_audio:
                        play_btn = QPushButton("Play")
                        play_btn.setFixedSize(50, 26)
                        play_btn.setStyleSheet(f"QPushButton {{ background: {color}; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; }} QPushButton:hover {{ opacity: 0.8; }}")
                        play_btn.clicked.connect(lambda checked, p=remote_path: self._open_media_viewer(p, "Audio"))
                        item_layout.addWidget(play_btn)

                    self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _open_media_viewer(self, remote_path, media_type):
        """Open universal file viewer — shows actual content for images, video, audio, text, PDF, DB, etc."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                                     QTextEdit, QFrame, QScrollArea, QWidget, QSizePolicy)
        from PyQt6.QtCore import Qt, QThread, pyqtSignal
        from PyQt6.QtGui import QPixmap, QImage, QFont
        import tempfile, os

        ext = Path(remote_path).suffix.lower()
        fname = Path(remote_path).name
        is_image = ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.heif')
        is_video = ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov', '.webm')
        is_audio = ext in ('.mp3', '.m4a', '.aac', '.wav', '.ogg', '.opus', '.amr', '.flac', '.wma')
        is_text = ext in ('.txt', '.log', '.csv', '.json', '.xml', '.yaml', '.yml', '.ini', '.conf', '.cfg', '.md', '.html', '.htm', '.js', '.py', '.java', '.c', '.cpp', '.h')
        is_db = ext in ('.db', '.sqlite', '.sqlite3', '.db-wal', '.db-shm', '.db-journal')
        is_pdf = ext == '.pdf'

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Forensic File Viewer — {fname}")
        dialog.setMinimumSize(820, 640)
        dialog.resize(900, 680)
        dialog.setStyleSheet("QDialog { background: #080c14; }")

        main_layout = QVBoxLayout(dialog)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(52)
        header.setStyleSheet("QFrame { background: #0d1117; border-bottom: 1px solid #1e293b; }")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)

        icon_text = "IMG" if is_image else "VID" if is_video else "AUD" if is_audio else "TXT" if is_text else "DB" if is_db else "PDF" if is_pdf else "FILE"
        icon_color = "#3b82f6" if is_image else "#ef4444" if is_video else "#a855f7" if is_audio else "#22c55e" if is_text else "#f59e0b" if is_db else "#ec4899" if is_pdf else "#64748b"
        icon_lbl = QLabel(f" {icon_text} ")
        icon_lbl.setStyleSheet(f"color: white; font-size: 11px; font-weight: 800; background: {icon_color}; border-radius: 4px; padding: 3px 6px;")
        header_layout.addWidget(icon_lbl)

        name_lbl = QLabel(fname)
        name_lbl.setStyleSheet("color: #e2e8f0; font-size: 14px; font-weight: 600; background: transparent; border: none;")
        header_layout.addWidget(name_lbl)

        header_layout.addStretch()

        size_lbl = QLabel("")
        size_lbl.setStyleSheet("color: #64748b; font-size: 11px; background: transparent; border: none;")
        header_layout.addWidget(size_lbl)

        header_layout.addSpacing(12)

        pull_btn = QPushButton("Open in System")
        pull_btn.setFixedHeight(30)
        pull_btn.setStyleSheet("QPushButton { background: #3b82f6; color: white; border-radius: 6px; font-weight: 600; padding: 0 14px; } QPushButton:hover { background: #2563eb; }")
        header_layout.addWidget(pull_btn)

        close_header_btn = QPushButton("X")
        close_header_btn.setFixedSize(30, 30)
        close_header_btn.setStyleSheet("QPushButton { background: transparent; color: #64748b; border-radius: 15px; font-size: 14px; font-weight: 700; } QPushButton:hover { background: #1e293b; color: #e2e8f0; }")
        close_header_btn.clicked.connect(dialog.close)
        header_layout.addWidget(close_header_btn)

        main_layout.addWidget(header)

        content_area = QFrame()
        content_area.setStyleSheet("QFrame { background: #080c14; }")
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(0)
        main_layout.addWidget(content_area, 1)

        image_label = QLabel("Pulling file from device...")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setMinimumHeight(400)
        image_label.setStyleSheet("color: #64748b; font-size: 13px; background: #0d1117; border-radius: 12px; border: 1px solid #1e293b;")
        content_layout.addWidget(image_label, 1)

        status_bar = QFrame()
        status_bar.setFixedHeight(28)
        status_bar.setStyleSheet("QFrame { background: #0d1117; border-top: 1px solid #1e293b; }")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(16, 0, 16, 0)
        status_text = QLabel(f"Path: {remote_path}")
        status_text.setStyleSheet("color: #475569; font-size: 10px; background: transparent; border: none;")
        status_layout.addWidget(status_text)
        status_layout.addStretch()
        main_layout.addWidget(status_bar)

        dialog.show()

        serial = self.device_bar.get_first_serial()
        if not serial:
            image_label.setText("No device connected")
            return

        import subprocess

        class PullWorker(QThread):
            done = pyqtSignal(str, str, int)
            def __init__(self, serial, remote, local):
                super().__init__()
                self.serial = serial
                self.remote = remote
                self.local = local
            def run(self):
                try:
                    adb_path = adb.ADB_PATH
                    result = subprocess.run(
                        [adb_path, "-s", self.serial, "pull", self.remote, self.local],
                        capture_output=True, text=True, timeout=60,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                    size = 0
                    if result.returncode == 0 and os.path.exists(self.local):
                        size = os.path.getsize(self.local)
                    self.done.emit(self.local, result.stdout if result.returncode == 0 else "", size)
                except Exception as e:
                    self.done.emit("", str(e), 0)

        local_file = tempfile.mktemp(suffix=ext or ".tmp")
        puller = PullWorker(serial, remote_path, local_file)
        self._active_puller = puller

        def on_pulled(path, msg, file_size):
            try:
                if not path or not os.path.exists(path):
                    image_label.setText(f"Failed to pull file\n\n{msg[:300]}")
                    return

                size_str = f"{file_size / (1024*1024):.1f} MB" if file_size > 1024*1024 else f"{file_size / 1024:.0f} KB" if file_size > 1024 else f"{file_size} B"
                size_lbl.setText(size_str)

                def open_system():
                    try:
                        os.startfile(path)
                    except Exception:
                        subprocess.Popen(["cmd", "/c", "start", "", path],
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                pull_btn.clicked.connect(open_system)

                if is_image:
                    pixmap = QPixmap(path)
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(780, 520, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
                        image_label.setPixmap(scaled)
                        image_label.setText("")
                        status_text.setText(f"{remote_path}  |  {pixmap.width()}x{pixmap.height()}  |  {size_str}")
                    else:
                        image_label.setText(f"Cannot decode image: {fname}\nFormat may be unsupported (HEIC)")

                elif is_video:
                    try:
                        from PIL import Image as PILImage
                        pil_img = PILImage.open(path) if ext in ('.jpg', '.jpeg', '.png') else None
                    except Exception:
                        pil_img = None

                    info_lines = [
                        f"VIDEO FILE",
                        f"",
                        f"Name:    {fname}",
                        f"Size:    {size_str}",
                        f"Path:    {remote_path}",
                        f"Local:   {path}",
                        f"",
                        f"Click 'Open in System' to play with default media player",
                    ]
                    image_label.setText("\n".join(info_lines))
                    image_label.setStyleSheet(
                        "color: #e2e8f0; font-size: 13px; background: #0d1117; border-radius: 12px; "
                        "border: 1px solid #1e293b; padding: 30px; font-family: 'Consolas', monospace;"
                    )
                    status_text.setText(f"{remote_path}  |  Video  |  {size_str}")

                elif is_audio:
                    info_lines = [
                        f"AUDIO FILE",
                        f"",
                        f"Name:    {fname}",
                        f"Size:    {size_str}",
                        f"Path:    {remote_path}",
                        f"Local:   {path}",
                        f"",
                        f"Click 'Open in System' to play with default audio player",
                    ]
                    image_label.setText("\n".join(info_lines))
                    image_label.setStyleSheet(
                        "color: #e2e8f0; font-size: 13px; background: #0d1117; border-radius: 12px; "
                        "border: 1px solid #1e293b; padding: 30px; font-family: 'Consolas', monospace;"
                    )
                    status_text.setText(f"{remote_path}  |  Audio  |  {size_str}")

                elif is_text:
                    try:
                        with open(path, 'r', encoding='utf-8', errors='replace') as f:
                            content = f.read(80000)
                        lines = content.split('\n')
                        total_lines = len(lines)
                        content_layout.removeWidget(image_label)
                        image_label.deleteLater()

                        text_edit = QTextEdit()
                        text_edit.setReadOnly(True)
                        text_edit.setStyleSheet(
                            "QTextEdit { background: #0d1117; color: #e2e8f0; border: 1px solid #1e293b; "
                            "border-radius: 10px; padding: 16px; font-family: 'Consolas', monospace; font-size: 12px; }"
                        )
                        text_edit.setPlainText(content)
                        content_layout.addWidget(text_edit, 1)
                        status_text.setText(f"{remote_path}  |  {total_lines} lines  |  {size_str}")
                    except Exception as e:
                        image_label.setText(f"Cannot read file: {e}")

                elif is_db:
                    try:
                        import sqlite3
                        conn = sqlite3.connect(path)
                        cursor = conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                        tables = [row[0] for row in cursor.fetchall()]
                        content_layout.removeWidget(image_label)
                        image_label.deleteLater()

                        db_text = QTextEdit()
                        db_text.setReadOnly(True)
                        db_text.setStyleSheet(
                            "QTextEdit { background: #0d1117; color: #e2e8f0; border: 1px solid #1e293b; "
                            "border-radius: 10px; padding: 16px; font-family: 'Consolas', monospace; font-size: 12px; }"
                        )
                        db_lines = [f"SQLite Database: {fname}", f"Size: {size_str}", f"Tables: {len(tables)}", ""]
                        for t in tables[:30]:
                            try:
                                cursor.execute(f'SELECT COUNT(*) FROM "{t}"')
                                count = cursor.fetchone()[0]
                                db_lines.append(f"  {t}  ({count} rows)")
                                cursor.execute(f'SELECT * FROM "{t}" LIMIT 3')
                                cols = [desc[0] for desc in cursor.description]
                                db_lines.append(f"    Columns: {', '.join(cols)}")
                                for row in cursor.fetchall():
                                    db_lines.append(f"    {row}")
                                db_lines.append("")
                            except Exception:
                                db_lines.append(f"  {t}  (unreadable)")
                        conn.close()
                        db_text.setPlainText("\n".join(db_lines))
                        content_layout.addWidget(db_text, 1)
                        status_text.setText(f"{remote_path}  |  SQLite  |  {len(tables)} tables  |  {size_str}")
                    except Exception as e:
                        image_label.setText(f"Cannot read database: {e}")

                elif is_pdf:
                    info_lines = [
                        f"PDF DOCUMENT",
                        f"",
                        f"Name:    {fname}",
                        f"Size:    {size_str}",
                        f"Path:    {remote_path}",
                        f"Local:   {path}",
                        f"",
                        f"Click 'Open in System' to view in PDF reader",
                    ]
                    image_label.setText("\n".join(info_lines))
                    image_label.setStyleSheet(
                        "color: #e2e8f0; font-size: 13px; background: #0d1117; border-radius: 12px; "
                        "border: 1px solid #1e293b; padding: 30px; font-family: 'Consolas', monospace;"
                    )
                    status_text.setText(f"{remote_path}  |  PDF  |  {size_str}")

                else:
                    try:
                        with open(path, 'rb') as f:
                            raw = f.read(2000)
                        hex_lines = []
                        for i in range(0, min(len(raw), 512), 16):
                            chunk = raw[i:i+16]
                            hex_part = ' '.join(f'{b:02x}' for b in chunk)
                            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                            hex_lines.append(f"{i:08x}  {hex_part:<48s}  {ascii_part}")
                        content_layout.removeWidget(image_label)
                        image_label.deleteLater()

                        hex_view = QTextEdit()
                        hex_view.setReadOnly(True)
                        hex_view.setStyleSheet(
                            "QTextEdit { background: #0d1117; color: #22c55e; border: 1px solid #1e293b; "
                            "border-radius: 10px; padding: 16px; font-family: 'Consolas', monospace; font-size: 11px; }"
                        )
                        hex_view.setPlainText("\n".join(hex_lines))
                        content_layout.addWidget(hex_view, 1)
                        status_text.setText(f"{remote_path}  |  Hex View  |  {size_str}")
                    except Exception as e:
                        image_label.setText(f"Cannot read file: {e}")

            except Exception as e:
                image_label.setText(f"Error: {e}")

        puller.done.connect(on_pulled)
        puller.start()

    def _show_deleted_files(self):
        self.conv_title.setText("Deleted Files — Recovery Viewer")
        items = [a for a in self.all_artifacts if a.get("category") == "Deleted Files"]
        self.conv_count.setText(f"{len(items)} recoverable files")

        self._clear_layout(self.conv_list_layout)

        if not items:
            empty = QLabel("No deleted/recoverable files found on device.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        type_groups = {}
        for item in items:
            ftype = item.get("type", "Deleted File")
            if ftype not in type_groups:
                type_groups[ftype] = []
            type_groups[ftype].append(item)

        ordered_types = ["Deleted Image", "Deleted Video", "Deleted Audio", "Deleted Document", "DB Journal", "Temp/Backup", "Deleted File"]
        for ftype in ordered_types:
            group = type_groups.get(ftype, [])
            if not group:
                continue

            header = QLabel(f"  {ftype} ({len(group)})")
            header.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)

            for item in group[:500]:
                item_frame = QFrame()
                item_frame.setStyleSheet("""
                    QFrame { background: #1a0d0d; border-radius: 8px; border: 1px solid #3b1515; }
                    QFrame:hover { background: #2e1515; border: 1px solid #ef4444; }
                """)
                item_frame.setFixedHeight(48)
                item_layout = QHBoxLayout(item_frame)
                item_layout.setContentsMargins(12, 6, 12, 6)

                fname = item.get("_data", {}).get("filename", "")
                status = item.get("_data", {}).get("status", "Deleted")
                ext = item.get("_data", {}).get("extension", "")

                status_lbl = QLabel(status)
                status_lbl.setFixedWidth(160)
                status_lbl.setStyleSheet("color: #f59e0b; font-size: 10px; font-weight: 600; background: transparent; border: none;")
                item_layout.addWidget(status_lbl)

                name_lbl = QLabel(fname[:50])
                name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                item_layout.addWidget(name_lbl, 1)

                remote_path = item.get("_data", {}).get("path", "")
                is_viewable = ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.mp4', '.3gp', '.mkv', '.avi', '.mov')
                is_media = ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
                is_video = ext in ('.mp4', '.3gp', '.mkv', '.avi', '.mov')

                if is_media or is_video:
                    view_btn = QPushButton("View")
                    view_btn.setFixedSize(50, 26)
                    if is_media:
                        view_btn.setStyleSheet("QPushButton { background: #25d366; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #1da851; }")
                    else:
                        view_btn.setStyleSheet("QPushButton { background: #3b82f6; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #2563eb; }")
                    media_type = "Image" if is_media else "Video"
                    view_btn.clicked.connect(lambda checked, p=remote_path, t=media_type: self._open_media_viewer(p, t))
                    item_layout.addWidget(view_btn)

                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _show_system(self):
        self.conv_title.setText("System Info — Device Forensics")
        items = [a for a in self.all_artifacts if a.get("category") == "System Info"]
        self.conv_count.setText(f"{len(items)} properties")

        self._clear_layout(self.conv_list_layout)

        if not items:
            empty = QLabel("No system info found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        for item in items:
            data = item.get("_data", {})
            is_header = data.get("is_header", "false") == "true"

            if is_header:
                section_name = data.get("section", "")
                header = QLabel(f"  {section_name}")
                header.setStyleSheet("color: #3b82f6; font-size: 13px; font-weight: 700; padding: 12px 0 6px 0; background: transparent; border: none;")
                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)
                continue

            item_frame = QFrame()
            item_frame.setStyleSheet("""
                QFrame { background: #111827; border-radius: 8px; border: 1px solid #1e293b; }
                QFrame:hover { background: #1a2234; border: 1px solid #334155; }
            """)
            item_frame.setFixedHeight(40)
            item_layout = QHBoxLayout(item_frame)
            item_layout.setContentsMargins(14, 6, 14, 6)

            label = data.get("label", item.get("type", ""))
            value = data.get("value", item.get("summary", ""))

            label_lbl = QLabel(label)
            label_lbl.setFixedWidth(180)
            label_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600; background: transparent; border: none;")
            item_layout.addWidget(label_lbl)

            value_lbl = QLabel(value[:80])
            value_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
            value_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            item_layout.addWidget(value_lbl, 1)

            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _show_emails(self):
        self.conv_title.setText("Emails — Inbox Viewer")
        items = [a for a in self.all_artifacts if a.get("category") == "Emails & Accounts"]
        self.conv_count.setText(f"{len(items)} email entries")

        self._clear_layout(self.conv_list_layout)

        if not items:
            empty = QLabel("No email data found on device.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        accounts = [i for i in items if i.get("type") == "Email Account"]
        emails = [i for i in items if i.get("type", "").startswith("Email (")]
        dbs = [i for i in items if i.get("type", "").startswith("Email DB")]
        other = [i for i in items if i.get("type") not in ("Email Account",) and not i.get("type", "").startswith("Email (") and not i.get("type", "").startswith("Email DB")]

        if accounts:
            header = QLabel(f"  Email Accounts ({len(accounts)})")
            header.setStyleSheet("color: #3b82f6; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)

            for item in accounts:
                item_frame = QFrame()
                item_frame.setStyleSheet("""
                    QFrame { background: #0d1f33; border-radius: 8px; border: 1px solid #1e3a5f; }
                    QFrame:hover { background: #162d4a; border: 1px solid #3b82f6; }
                """)
                item_frame.setFixedHeight(48)
                item_layout = QHBoxLayout(item_frame)
                item_layout.setContentsMargins(12, 6, 12, 6)

                provider = item.get("_data", {}).get("provider", "")
                provider_lbl = QLabel(provider)
                provider_lbl.setFixedWidth(80)
                provider_lbl.setStyleSheet("color: #3b82f6; font-size: 11px; font-weight: 600; background: transparent; border: none;")
                item_layout.addWidget(provider_lbl)

                name_lbl = QLabel(item.get("summary", "")[:60])
                name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                item_layout.addWidget(name_lbl, 1)

                status_lbl = QLabel("Active")
                status_lbl.setStyleSheet("color: #22c55e; font-size: 10px; background: transparent; border: none;")
                item_layout.addWidget(status_lbl)

                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        if emails:
            header2 = QLabel(f"  Email Messages ({len(emails)})")
            header2.setStyleSheet("color: #22c55e; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header2)

            provider_groups = {}
            for item in emails:
                prov = item.get("_data", {}).get("provider", "Unknown")
                if prov not in provider_groups:
                    provider_groups[prov] = []
                provider_groups[prov].append(item)

            for prov, items in provider_groups.items():
                prov_header = QLabel(f"    {prov} ({len(items)})")
                prov_header.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600; padding: 4px 0 2px 0; background: transparent; border: none;")
                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, prov_header)

                for item in items[:200]:
                    item_frame = QFrame()
                    item_frame.setStyleSheet("""
                        QFrame { background: #111827; border-radius: 8px; border: 1px solid #1e293b; }
                        QFrame:hover { background: #1a2234; border: 1px solid #22c55e; }
                    """)
                    item_frame.setFixedHeight(56)
                    item_layout = QHBoxLayout(item_frame)
                    item_layout.setContentsMargins(12, 6, 12, 6)

                    sender = item.get("_data", {}).get("sender", "")
                    subject = item.get("_data", {}).get("subject", "")
                    preview = item.get("_data", {}).get("preview", "")
                    date = item.get("date", "")

                    left_col = QVBoxLayout()
                    left_col.setSpacing(2)

                    sender_lbl = QLabel(sender[:40] if sender else "Unknown Sender")
                    sender_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; font-weight: 600; background: transparent; border: none;")
                    left_col.addWidget(sender_lbl)

                    subject_text = subject if subject else preview[:60]
                    subj_lbl = QLabel(subject_text[:55])
                    subj_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; background: transparent; border: none;")
                    left_col.addWidget(subj_lbl)

                    item_layout.addLayout(left_col, 1)

                    if date:
                        date_lbl = QLabel(date)
                        date_lbl.setStyleSheet("color: #64748b; font-size: 10px; background: transparent; border: none;")
                        item_layout.addWidget(date_lbl)

                    view_btn = QPushButton("View")
                    view_btn.setFixedSize(50, 26)
                    view_btn.setStyleSheet("QPushButton { background: #22c55e; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #16a34a; }")
                    view_btn.clicked.connect(lambda checked, i=item: self._open_email_viewer(i))
                    item_layout.addWidget(view_btn)

                    self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        if dbs:
            header3 = QLabel(f"  Email Databases ({len(dbs)})")
            header3.setStyleSheet("color: #f59e0b; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header3)

            for item in dbs:
                item_frame = QFrame()
                item_frame.setStyleSheet("""
                    QFrame { background: #1a1f2e; border-radius: 8px; border: 1px solid #334155; }
                    QFrame:hover { background: #1e293b; border: 1px solid #f59e0b; }
                """)
                item_frame.setFixedHeight(44)
                item_layout = QHBoxLayout(item_frame)
                item_layout.setContentsMargins(12, 6, 12, 6)

                name_lbl = QLabel(item.get("summary", "")[:60])
                name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                item_layout.addWidget(name_lbl, 1)

                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _open_email_viewer(self, item):
        """Open full email viewer dialog."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        from PyQt6.QtCore import Qt

        data = item.get("_data", {})
        sender = data.get("sender", "Unknown")
        recipient = data.get("recipient", "")
        subject = data.get("subject", "No Subject")
        preview = data.get("preview", "")
        body = data.get("body", preview)
        date = item.get("date", "Unknown date")
        provider = data.get("provider", "Email")

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Email — {subject[:50]}")
        dialog.setMinimumSize(750, 600)
        dialog.setStyleSheet("QDialog { background: #0a0e17; }")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        provider_lbl = QLabel(f"  {provider}")
        provider_lbl.setStyleSheet(f"color: #3b82f6; font-size: 11px; font-weight: 600; padding: 4px 8px; background: #0d1f33; border-radius: 4px; border: 1px solid #1e3a5f;")
        provider_lbl.setFixedWidth(120)
        layout.addWidget(provider_lbl)

        subject_lbl = QLabel(subject)
        subject_lbl.setStyleSheet("color: #e2e8f0; font-size: 18px; font-weight: 700; background: transparent; border: none;")
        subject_lbl.setWordWrap(True)
        layout.addWidget(subject_lbl)

        meta_frame = QFrame()
        meta_frame.setStyleSheet("QFrame { background: #111827; border-radius: 8px; border: 1px solid #1e293b; }")
        meta_layout = QVBoxLayout(meta_frame)
        meta_layout.setContentsMargins(16, 12, 16, 12)
        meta_layout.setSpacing(6)

        sender_row = QHBoxLayout()
        sender_row.addWidget(QLabel("From:"))
        sender_val = QLabel(sender)
        sender_val.setStyleSheet("color: #e2e8f0; font-weight: 600;")
        sender_row.addWidget(sender_val)
        sender_row.addStretch()
        meta_layout.addLayout(sender_row)

        if recipient:
            to_row = QHBoxLayout()
            to_row.addWidget(QLabel("To:"))
            to_val = QLabel(recipient)
            to_val.setStyleSheet("color: #e2e8f0;")
            to_row.addWidget(to_val)
            to_row.addStretch()
            meta_layout.addLayout(to_row)

        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Date:"))
        date_val = QLabel(date)
        date_val.setStyleSheet("color: #94a3b8;")
        date_row.addWidget(date_val)
        date_row.addStretch()
        meta_layout.addLayout(date_row)

        layout.addWidget(meta_frame)

        body_view = QTextEdit()
        body_view.setReadOnly(True)
        body_view.setStyleSheet("""
            QTextEdit { background: #111827; color: #e2e8f0; border: 1px solid #1e293b;
                        border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; }
        """)
        body_view.setText(body if body else "(No body content available)")
        layout.addWidget(body_view, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedSize(100, 36)
        close_btn.setStyleSheet("QPushButton { background: #334155; color: white; border-radius: 8px; } QPushButton:hover { background: #475569; }")
        close_btn.clicked.connect(dialog.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.exec()

    def _show_documents(self):
        self.conv_title.setText("Documents — Recovery Viewer")
        items = [a for a in self.all_artifacts if a.get("category") == "Documents"]
        self.conv_count.setText(f"{len(items)} documents")

        self._clear_layout(self.conv_list_layout)

        if not items:
            empty = QLabel("No documents found on device.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 40px; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, empty)
            self.stacked.setCurrentIndex(1)
            return

        type_groups = {}
        for item in items:
            ftype = item.get("type", "Document")
            if ftype not in type_groups:
                type_groups[ftype] = []
            type_groups[ftype].append(item)

        ordered = ["PDF Document", "Word Document", "Word (Legacy)", "Excel Spreadsheet", "Excel (Legacy)",
                    "PowerPoint", "PowerPoint (Legacy)", "Text File", "CSV Spreadsheet",
                    "E-Book", "E-Book (Kindle)", "Rich Text",
                    "JSON Data", "XML Data", "Markdown", "Web Page",
                    "Log File", "Config File", "YAML Config",
                    "Android Package", "Android Package (Bundle)", "Android OBB",
                    "Contact Card", "Calendar File",
                    "ZIP Archive", "RAR Archive", "7-Zip Archive", "TAR Archive", "GZ Archive",
                    "SQLite Database", "LibreOffice Doc", "LibreOffice Sheet", "LibreOffice Presentation",
                    "Document"]

        for ftype in ordered:
            group = type_groups.get(ftype, [])
            if not group:
                continue

            color = "#3b82f6"
            if "PDF" in ftype:
                color = "#ef4444"
            elif "Word" in ftype:
                color = "#2563eb"
            elif "Excel" in ftype or "Spreadsheet" in ftype:
                color = "#16a34a"
            elif "PowerPoint" in ftype or "Presentation" in ftype:
                color = "#ea580c"
            elif "Text" in ftype or "CSV" in ftype:
                color = "#64748b"
            elif "E-Book" in ftype:
                color = "#8b5cf6"
            elif "Archive" in ftype:
                color = "#f59e0b"
            elif "Database" in ftype:
                color = "#06b6d4"
            elif "Android" in ftype:
                color = "#10b981"
            elif "Contact" in ftype or "Calendar" in ftype:
                color = "#f59e0b"

            header = QLabel(f"  {ftype} ({len(group)})")
            header.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 700; padding: 8px 0 4px 0; background: transparent; border: none;")
            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, header)

            for item in group[:2000]:
                item_frame = QFrame()
                item_frame.setStyleSheet(f"""
                    QFrame {{ background: #111827; border-radius: 8px; border: 1px solid #1e293b; }}
                    QFrame:hover {{ background: #1a2234; border: 1px solid {color}; }}
                """)
                item_frame.setFixedHeight(48)
                item_layout = QHBoxLayout(item_frame)
                item_layout.setContentsMargins(12, 6, 12, 6)

                fname = item.get("_data", {}).get("filename", "")
                ext = item.get("_data", {}).get("extension", "")
                folder = item.get("_data", {}).get("folder", "")

                folder_lbl = QLabel(folder[:15] if folder else "")
                folder_lbl.setFixedWidth(100)
                folder_lbl.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 600; background: transparent; border: none;")
                item_layout.addWidget(folder_lbl)

                name_lbl = QLabel(fname[:55])
                name_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
                name_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                item_layout.addWidget(name_lbl, 1)

                remote_path = item.get("_data", {}).get("path", "")
                is_previewable = ext in ('.txt', '.csv', '.json', '.xml', '.md', '.log', '.html', '.htm', '.ini', '.conf', '.yaml', '.yml', '.vcf', '.ics')
                is_viewable_img = ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')
                is_pdf = ext == '.pdf'

                if is_previewable:
                    preview_btn = QPushButton("Preview")
                    preview_btn.setFixedSize(65, 26)
                    preview_btn.setStyleSheet("QPushButton { background: #8b5cf6; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #7c3aed; }")
                    preview_btn.clicked.connect(lambda checked, p=remote_path, t=ftype: self._open_text_preview(p, t))
                    item_layout.addWidget(preview_btn)
                elif is_pdf:
                    view_btn = QPushButton("View")
                    view_btn.setFixedSize(50, 26)
                    view_btn.setStyleSheet("QPushButton { background: #ef4444; color: white; border-radius: 6px; font-size: 10px; font-weight: 600; } QPushButton:hover { background: #dc2626; }")
                    view_btn.clicked.connect(lambda checked, p=remote_path, t="PDF": self._open_media_viewer(p, t))
                    item_layout.addWidget(view_btn)

                self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _open_text_preview(self, remote_path, doc_type):
        """Pull and preview text-based documents inline."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit
        from PyQt6.QtCore import Qt, QThread, pyqtSignal

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Preview — {Path(remote_path).name}")
        dialog.setMinimumSize(750, 600)
        dialog.setStyleSheet("QDialog { background: #0a0e17; }")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        info_label = QLabel(f"Loading: {remote_path}")
        info_label.setStyleSheet("color: #94a3b8; font-size: 12px; background: transparent; border: none;")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setStyleSheet("""
            QTextEdit { background: #111827; color: #e2e8f0; border: 1px solid #1e293b;
                        border-radius: 8px; padding: 12px; font-family: 'Consolas', monospace; font-size: 12px; }
        """)
        text_view.setText("Pulling file from device...")
        layout.addWidget(text_view, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedSize(100, 36)
        close_btn.setStyleSheet("QPushButton { background: #334155; color: white; border-radius: 8px; } QPushButton:hover { background: #475569; }")
        close_btn.clicked.connect(dialog.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dialog.show()

        serial = self.device_bar.get_first_serial()
        if not serial:
            text_view.setText("No device connected")
            return

        import subprocess, tempfile
        ext = Path(remote_path).suffix.lower()

        class PullWorker(QThread):
            done = pyqtSignal(str, str)
            def __init__(self, serial, remote, local):
                super().__init__()
                self.serial = serial
                self.remote = remote
                self.local = local
            def run(self):
                try:
                    adb_path = adb.ADB_PATH
                    result = subprocess.run(
                        [adb_path, "-s", self.serial, "pull", self.remote, self.local],
                        capture_output=True, text=True, timeout=30,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                    self.done.emit(self.local, result.stdout if result.returncode == 0 else "")
                except Exception as e:
                    self.done.emit("", str(e))

        local_file = tempfile.mktemp(suffix=ext or ".tmp")
        puller = PullWorker(serial, remote_path, local_file)
        self._active_text_puller = puller

        def on_pulled(path, msg):
            if not path or not Path(path).exists():
                text_view.setText(f"Failed to pull: {msg[:200]}")
                return
            info_label.setText(f"{Path(remote_path).name}")
            try:
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read(50000)
                text_view.setText(content)
            except Exception as e:
                text_view.setText(f"Cannot read file: {e}")

        puller.done.connect(on_pulled)
        puller.start()

    def _show_generic_list(self, category, app_def):
        self.conv_title.setText(app_def["name"])
        items = [a for a in self.all_artifacts if a.get("category") == category]
        self.conv_count.setText(f"{len(items)} items")

        self._clear_layout(self.conv_list_layout)

        for item_data in items:
            item_frame = QFrame()
            item_frame.setStyleSheet("""
                QFrame { background: #111827; border-radius: 8px; border: 1px solid #1e293b; }
                QFrame:hover { background: #1a2234; border: 1px solid #334155; }
            """)
            item_frame.setFixedHeight(48)
            item_layout = QHBoxLayout(item_frame)
            item_layout.setContentsMargins(14, 8, 14, 8)

            type_lbl = QLabel(item_data.get("type", ""))
            type_lbl.setFixedWidth(100)
            type_lbl.setStyleSheet(f"color: {app_def['color']}; font-size: 11px; font-weight: 600; background: transparent; border: none;")
            item_layout.addWidget(type_lbl)

            summary = item_data.get("summary", "")
            summary_lbl = QLabel(summary[:80])
            summary_lbl.setStyleSheet("color: #e2e8f0; font-size: 12px; background: transparent; border: none;")
            summary_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            item_layout.addWidget(summary_lbl, 1)

            self.conv_list_layout.insertWidget(self.conv_list_layout.count() - 1, item_frame)

        self.stacked.setCurrentIndex(1)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def _on_conversation_clicked(self, conversation):
        contact = conversation.get("contact_name", "")
        phone = conversation.get("phone", "")
        display_name = contact if contact else phone if phone else "Unknown"
        messages = conversation.get("messages", [])

        self.chat_contact_name.setText(display_name)
        self.chat_msg_count.setText(f"{len(messages)} messages")

        self._clear_layout(self.chat_list_layout)

        sorted_msgs = sorted(messages, key=lambda m: m.get("_data", {}).get("date", "") or m.get("date", ""))
        prev_date = ""
        for msg in sorted_msgs:
            is_out = msg.get("_data", {}).get("type", "") == "Outgoing"
            msg_date = (msg.get("_data", {}).get("date", "") or msg.get("date", "")).split(" ")[0] if msg.get("_data", {}).get("date", "") or msg.get("date", "") else ""
            show_date = msg_date != prev_date and msg_date != ""
            prev_date = msg_date
            bubble = MessageBubbleWidget(msg, is_outgoing=is_out, show_date=show_date)
            self.chat_list_layout.insertWidget(self.chat_list_layout.count() - 1, bubble)

        self.stacked.setCurrentIndex(2)
        QTimer.singleShot(50, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self):
        sb = self.chat_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _go_back_to_grid(self):
        self.stacked.setCurrentIndex(0)

    def _go_back_to_conversations(self):
        self.stacked.setCurrentIndex(1)

    def showEvent(self, event):
        super().showEvent(event)
        self.device_bar.auto_scan()
        if not self._data_loaded:
            serial = self.device_bar.get_first_serial()
            if serial:
                self._start_load(serial, "All")
                self._data_loaded = True

    def _on_device_connected(self, info):
        serial = info.get("serial", "")
        if serial and not self._data_loaded:
            self._start_load(serial, "All")
            self._data_loaded = True

    def _on_refresh(self):
        serial = self.device_bar.get_first_serial()
        if serial:
            self._data_loaded = True
            self._start_load(serial, "All")

    def _start_load(self, serial, category):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        self.progress_frame.setVisible(True)
        self.progress_label.setText("Extracting all artifacts from device...")
        self.refresh_btn.setEnabled(False)
        self._worker = LoadWorker(serial, category)
        self._worker.progress.connect(lambda m: self.progress_label.setText(m))
        self._worker.counts_updated.connect(self._on_counts_updated)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_counts_updated(self, counts):
        for app_def in APP_DEFS:
            cat = app_def["category"]
            count = counts.get(cat, 0)
            if cat in self.app_icon_widgets:
                self.app_icon_widgets[cat].update_count(count)
        total = sum(counts.values())
        self.progress_label.setText(f"Extracting... {total} artifacts found so far")

    def _on_finished(self, results):
        self.all_artifacts = results
        self.progress_frame.setVisible(False)
        self.refresh_btn.setEnabled(True)

        self._contacts = [a for a in results if a.get("category") == "Contacts"]
        self._build_sms_conversations(results)

        for app_def in APP_DEFS:
            cat = app_def["category"]
            count = sum(1 for a in results if a.get("category") == cat)
            if cat in self.app_icon_widgets:
                self.app_icon_widgets[cat].update_count(count)

        total = len(results)
        sms_count = sum(1 for a in results if a.get("category") == "SMS")
        call_count = sum(1 for a in results if a.get("category") == "Calls")
        contact_count = sum(1 for a in results if a.get("category") == "Contacts")
        self.stats_label.setText(
            f"{total} total artifacts  |  {sms_count} SMS  |  {call_count} calls  |  {contact_count} contacts"
        )

        self._persist_artifacts(results)

    def _build_sms_conversations(self, results):
        sms_msgs = [a for a in results if a.get("category") == "SMS"]
        self._conversations = {}
        for msg in sms_msgs:
            address = msg.get("_data", {}).get("address", "")
            if not address:
                continue
            if address not in self._conversations:
                contact_name = _resolve_contact_name(self._contacts, address)
                self._conversations[address] = {
                    "phone": address,
                    "contact_name": contact_name,
                    "messages": [],
                }
            self._conversations[address]["messages"].append(msg)

    def _persist_artifacts(self, results):
        """Save what the viewer read into the active case, once.

        These rows used to be inserted with no ``created_at`` and an empty
        session, so every insert failed the NOT NULL constraint and the
        exception was swallowed into a log line: the viewer's findings never
        reached the case. Without a session there is nowhere to put them, so
        the write is skipped rather than creating orphan rows.
        """
        if not self._session_id:
            return
        import uuid
        try:
            from ...core.database import get_db
            conn = get_db()
            existing = {
                (row[0], row[1]) for row in conn.execute(
                    "SELECT name, source FROM evidence_artifacts WHERE session_id=?",
                    (self._session_id,))
            }
            now = datetime.now(timezone.utc).isoformat()
            for a in results[:5000]:
                name = (a.get("summary") or "")[:200]
                source = a.get("source", "")
                if (name, source) in existing:
                    continue
                existing.add((name, source))
                conn.execute(
                    "INSERT INTO evidence_artifacts (id, session_id, artifact_id, name, source, category,"
                    " mime_type, file_size, file_path, sha256_hash, metadata, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), self._session_id, f"art_{uuid.uuid4().hex[:12]}",
                     name, source, a.get("category", ""), a.get("type", ""),
                     0, source, "",
                     json.dumps(a.get("_data", {}), default=str)[:2000], now)
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Persist artifacts error: {e}")
