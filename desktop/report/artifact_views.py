"""The Artifact Viewer's results, as the report needs to show them.

The viewer is a live screen. This module answers the question the report asks
instead: *for each class of artifact the tool claims to recover, what was
actually recovered, and where is it?*

Two sources, and the panel always says which one it used:

  * the case store -- ``logical/contacts.json``, ``app_databases/``,
    ``full_storage/`` and the rest of the tree an acquisition writes. This is
    the defensible source, because the files carry hashes and custody;
  * the handset -- the same content providers the viewer reads, used only for
    the logical categories and only when the store has no copy. A panel built
    this way is marked LIVE, because it describes the device at report time,
    not the device at acquisition time.

A category that was never acquired still gets a panel. Printing "not present
in this acquisition" is information; omitting the heading is not.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

#: The order follows the checklist the tool advertises, which is also the
#: order an examiner walks a handset.
CATEGORIES = (
    ("contacts", "Contacts", "#f59e0b", "contacts"),
    ("sms", "SMS / MMS Messages", "#10b981", "messages"),
    ("calls", "Call Log", "#3b82f6", "calls"),
    ("calendar", "Calendar Events", "#06b6d4", "events"),
    ("browser", "Browser History", "#4285f4", "visits"),
    ("app_databases", "Application Databases", "#8b5cf6", "databases"),
    ("media", "Media Files", "#ec4899", "files"),
    ("documents", "Documents and Downloads", "#64748b", "documents"),
    ("social", "Social Application Data", "#25d366", "files"),
    ("deleted", "Deleted and Trashed Files", "#ef4444", "files"),
    ("device", "Device Properties and SIM", "#94a3b8", "properties"),
    ("storage", "Full /sdcard/ Storage Image", "#a855f7", "bytes"),
    ("app_data", "Per-App External Data and Backups", "#0ea5e9", "files"),
    ("recovered", "Carving and Journal Recovery", "#f97316", "items"),
    ("emails", "Email Extraction", "#ef4444", "messages"),
    ("doc_metadata", "Document Metadata", "#64748b", "documents"),
    ("bookmarks", "Chrome Bookmarks", "#4285f4", "bookmarks"),
    ("packaging", "Evidence Packaging and Verification", "#22c55e", "artifacts"),
)

#: Where each class lands in an acquisition. Printed on a gap panel so the
#: reader knows what a complete acquisition would have produced, and on the
#: register page so a missing class is a located gap rather than a shrug.
STORE_PATHS = {
    "contacts": "logical/contacts.json",
    "sms": "logical/sms_messages.json",
    "calls": "logical/call_log.json",
    "calendar": "logical/calendar_events.json",
    "browser": "browser/",
    "app_databases": "app_databases/",
    "media": "filesystem/ (DCIM, Pictures, Movies, Music)",
    "documents": "filesystem/ (Download, Documents)",
    "social": "social_apps/ or filesystem/Android/media",
    "deleted": "deleted_files/",
    "device": "device/",
    "storage": "full_storage/ or physical/",
    "app_data": "app_data/",
    "recovered": "recovered/",
    "emails": "emails/",
    "doc_metadata": "document_metadata.json",
    "bookmarks": "browser/ (bookmarks)",
    "packaging": "ACQUISITION_MANIFEST.json and VERIFICATION.json",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | {".mp4", ".3gp", ".mkv", ".mov", ".mp3", ".m4a", ".ogg", ".wav", ".opus"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("records", "items", "data", "results", "messages", "contacts", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _size(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


def _stamp(value) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        number = int(text)
        if number > 10_000_000_000:
            number //= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return text
    return text[:19].replace("T", " ")


def _walk(root: Path, suffixes=None, limit=4000):
    if not root.is_dir():
        return []
    found = []
    for path in root.rglob("*"):
        if len(found) >= limit:
            break
        if not path.is_file():
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        try:
            found.append((path, path.stat().st_size))
        except OSError:
            continue
    return found


def _parse_content_rows(payload: str) -> list[dict]:
    rows = []
    for line in str(payload or "").splitlines():
        if "Row:" not in line:
            continue
        body = line.split("Row:", 1)[1].strip()
        entry = {}
        for pair in re.split(r",\s*(?=\w+=)", body):
            if "=" in pair:
                key, value = pair.split("=", 1)
                entry[key.strip()] = value.strip().strip('"')
        if entry:
            rows.append(entry)
    return rows


def _live(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except Exception:
        return None


# --------------------------------------------------------------------------
# store readers
# --------------------------------------------------------------------------

def _rows_from_records(records, columns, limit):
    """Turn dicts into the three-cell rows a panel draws."""
    rows = []
    for record in records[:limit]:
        cells = []
        for column in columns:
            value = record.get(column, "")
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(v) for v in value[:2])
            cells.append(str(value or ""))
        rows.append(tuple(cells))
    return rows


def _store_contacts(store, limit):
    if not store:
        return None
    records = _as_list(_load_json(store / "logical" / "contacts.json"))
    if not records:
        return None
    rows = []
    for record in records[:limit]:
        name = record.get("name") or record.get("display_name") or "(no name)"
        phones = record.get("phones") or record.get("numbers") or []
        emails = record.get("emails") or []
        if isinstance(phones, list) and phones and isinstance(phones[0], dict):
            phones = [p.get("number", "") for p in phones]
        if isinstance(emails, list) and emails and isinstance(emails[0], dict):
            emails = [e.get("address", "") for e in emails]
        rows.append((name, ", ".join(map(str, phones[:2])) or "—",
                     ", ".join(map(str, emails[:1])) or ""))
    return {"count": len(records), "rows": rows,
            "source": "logical/contacts.json", "status": "recovered"}


def _store_records(store, relative, columns, limit, unit):
    if not store:
        return None
    records = _as_list(_load_json(store / relative))
    if not records:
        return None
    return {"count": len(records), "rows": _rows_from_records(records, columns, limit),
            "source": relative, "status": "recovered"}


def _store_media(store, limit, folder_filter=None, suffixes=MEDIA_SUFFIXES,
                 include_ext=(), unit="files"):
    if not store:
        return None
    files = _walk(store / "filesystem", suffixes)
    if folder_filter:
        files = [(p, s) for p, s in files
                 if any(token in str(p).lower().replace("\\", "/") for token in folder_filter)]
    if not files:
        return None
    photos = [(p, s) for p, s in files if p.suffix.lower() in IMAGE_SUFFIXES]
    rows = [(p.name, p.parent.name, _size(s)) for p, s in files[:limit]]
    return {"count": len(files), "rows": rows, "unit": unit,
            "thumbs": [p for p, _ in photos[:6]],
            "source": "filesystem/", "status": "recovered"}


def _store_listing(store, relative, limit, unit="files", suffixes=None):
    if not store:
        return None
    files = _walk(store / relative, suffixes)
    if not files:
        return None
    rows = [(p.name, p.parent.name, _size(s)) for p, s in sorted(files, key=lambda f: -f[1])[:limit]]
    return {"count": len(files), "rows": rows, "unit": unit,
            "source": f"{relative}/", "status": "recovered"}


def _store_device(store, limit):
    if not store:
        return None
    properties = _load_json(store / "device" / "device_properties.json")
    rows = []
    if isinstance(properties, dict):
        for key, value in list(properties.items())[:limit]:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)[:60]
            rows.append((str(key).replace("_", " ").title(), str(value)[:70], ""))
    sim = validate = None
    for name in ("sim_info.txt", "battery_status.txt"):
        path = store / "device" / name
        if path.is_file():
            sim = sim or path
            validate = validate
    if sim and len(rows) < limit:
        lines = [l.strip() for l in sim.read_text(encoding="utf-8", errors="replace").splitlines() if ":" in l]
        for line in lines[:2]:
            key, _, value = line.partition(":")
            rows.append((key.strip().title(), value.strip()[:70], ""))
    if not rows:
        return None
    return {"count": len(rows), "rows": rows, "unit": "properties",
            "source": "device/", "status": "recovered"}


def _store_storage(store, limit):
    if not store:
        return None
    candidates = [store / "full_storage", store / "physical"]
    files = []
    for candidate in candidates:
        files = _walk(candidate)
        if files:
            break
    if not files:
        return None
    total = sum(size for _, size in files)
    rows = [(p.name, p.parent.name, _size(s)) for p, s in
            sorted(files, key=lambda f: -f[1])[:limit]]
    return {"count": total, "unit": "bytes", "rows": rows,
            "source": f"{files[0][0].parts[len(store.parts)]}/", "status": "recovered"}


def _store_packaging(store, limit):
    if not store:
        return None
    manifest = _load_json(store / "ACQUISITION_MANIFEST.json")
    verification = _load_json(store / "VERIFICATION.json")
    if not manifest and not verification:
        return None
    rows = []
    if isinstance(manifest, dict):
        for key in ("acquisition_id", "device_serial", "method", "started_at",
                    "completed_at", "total_files", "total_bytes", "hash_algorithm"):
            if key in manifest:
                rows.append((key.replace("_", " ").title(), str(manifest[key])[:70], ""))
    count = len(manifest.get("artifacts", []) if isinstance(manifest, dict) else [])
    if verification:
        summary = verification if isinstance(verification, dict) else {}
        passed = summary.get("verified") or summary.get("passed")
        rows.append(("Manifest verification", str(passed if passed is not None else "recorded"), ""))
    return {"count": count or None, "unit": "artifacts", "rows": rows[:limit],
            "source": "ACQUISITION_MANIFEST.json", "status": "recovered"}


# --------------------------------------------------------------------------
# live readers (content providers only)
# --------------------------------------------------------------------------

def _clean_value(value) -> str:
    """The content provider writes the four letters N-U-L-L for an absent column."""
    text = str(value or "").strip()
    return "" if text.upper() in ("NULL", "NONE", "EMPTY") else text


def _live_contacts(serial, limit):
    payload = _live(__import__("desktop.core.adb_manager", fromlist=["query_content"])
                    .query_content, serial, "content://com.android.contacts/data",
                    sort="display_name ASC")
    if not payload:
        return None
    people: dict[str, dict] = {}
    for row in _parse_content_rows(payload):
        contact_id = _clean_value(row.get("contact_id"))
        name = _clean_value(row.get("display_name"))
        mimetype = row.get("mimetype", "")
        value = _clean_value(row.get("data1"))
        # Rows without a contact id are loose provider entries; key them by
        # whatever identifying value they do carry so they are not all merged.
        key = contact_id or f"{name}|{value if 'phone' in mimetype else ''}"
        person = people.setdefault(key, {"name": "", "phones": [], "emails": []})
        if name and not person["name"]:
            person["name"] = name
        if "phone" in mimetype and value:
            person["phones"].append(value)
        elif "email" in mimetype and value:
            person["emails"].append(value)

    records = [p for p in people.values() if p["phones"] or p["name"]]
    if not records:
        return None
    # A contact the handset itself cannot name is still a contact, but a panel
    # is more useful when the rows it shows are the ones with a number attached.
    records.sort(key=lambda p: (not p["phones"], not p["name"],
                                p["name"] or (p["phones"][0] if p["phones"] else "")))
    rows = []
    for person in records[:limit]:
        name = person["name"] or (person["phones"][0] if person["phones"] else "(unnamed)")
        # The provider stores both the formatted and the raw number; showing
        # the same contact line twice wastes a row that could hold a second
        # number or an email.
        phones, seen_numbers = [], set()
        for number in person["phones"]:
            key = re.sub(r"\D", "", number)[-9:]
            if key and key not in seen_numbers:
                seen_numbers.add(key)
                phones.append(number)
        rows.append((name, ", ".join(phones[:2]) or "no number",
                     ", ".join(person["emails"][:1])))
    return {"count": len(records), "rows": rows, "source": "content://com.android.contacts",
            "status": "live"}


def _live_sms(serial, limit):
    payload = _live(__import__("desktop.core.adb_manager", fromlist=["query_content"])
                    .query_content, serial, "content://sms", sort="date DESC")
    rows = _parse_content_rows(payload or "")
    if not rows:
        return None
    out = []
    for row in rows[:limit]:
        kind = {"1": "in", "2": "out"}.get(row.get("type", ""), "?")
        out.append((_clean_value(row.get("address")) or "unknown",
                    " ".join(_clean_value(row.get("body")).split())[:64],
                    f"{_stamp(row.get('date'))} {kind}"))
    return {"count": len(rows), "rows": out, "source": "content://sms", "status": "live"}


def _live_calls(serial, limit):
    payload = _live(__import__("desktop.core.adb_manager", fromlist=["query_content"])
                    .query_content, serial, "content://call_log/calls", sort="date DESC")
    rows = _parse_content_rows(payload or "")
    if not rows:
        return None
    out = []
    for row in rows[:limit]:
        kind = {"1": "Incoming", "2": "Outgoing", "3": "Missed"}.get(row.get("type", ""), row.get("type", "?"))
        try:
            seconds = int(row.get("duration") or 0)
            duration = f"{seconds // 60}m {seconds % 60}s"
        except ValueError:
            duration = str(row.get("duration") or "")
        name = _clean_value(row.get("cached_name")) or _clean_value(row.get("name"))
        number = _clean_value(row.get("number")) or "unknown"
        out.append((f"{name} {number}".strip(), f"{kind} · {duration}", _stamp(row.get("date"))))
    return {"count": len(rows), "rows": out, "source": "content://call_log/calls", "status": "live"}


def _live_calendar(serial, limit):
    payload = _live(__import__("desktop.core.adb_manager", fromlist=["query_content"])
                    .query_content, serial, "content://com.android.calendar/events",
                    sort="dtstart DESC")
    rows = _parse_content_rows(payload or "")
    if not rows:
        return None
    out = []
    for row in rows[:limit]:
        out.append((_clean_value(row.get("title")) or "(no title)",
                    (_clean_value(row.get("eventLocation")) or _clean_value(row.get("organizer")))[:56],
                    _stamp(row.get("dtstart"))))
    return {"count": len(rows), "rows": out, "source": "content://com.android.calendar/events",
            "status": "live"}


def _live_device(serial, limit):
    adb = __import__("desktop.core.adb_manager", fromlist=["adb_shell"])
    keys = (("ro.product.manufacturer", "Manufacturer"), ("ro.product.model", "Model"),
            ("ro.build.version.release", "Android"), ("ro.build.version.sdk", "SDK"),
            ("ro.build.version.security_patch", "Security patch"), ("ro.serialno", "Serial"),
            ("gsm.sim.operator.alpha", "SIM operator"), ("gsm.sim.state", "SIM state"))
    rows = []
    for key, label in keys:
        ok, value = _live(adb.adb_shell, serial, f"getprop {key}", timeout=15) or (False, "")
        if ok and value.strip():
            rows.append((label, value.strip()[:70], key))
    if not rows:
        return None
    return {"count": len(rows), "unit": "properties", "rows": rows[:limit],
            "source": "getprop (live)", "status": "live"}


def _adb():
    return __import__("desktop.core.adb_manager", fromlist=["adb_shell"])


def _live_ls(serial, directory, timeout=25):
    """One directory over ADB, as ``[{name, size, is_dir, date}]``.

    The kind and the timestamp matter: a package folder reports the size of its
    directory inode (a few kilobytes) rather than its contents, and printing
    that as a file size would invent a number that means nothing.
    """
    adb = _adb()
    ok, output = _live(adb.adb_shell, serial, f"ls -l '{directory}' 2>/dev/null", timeout=timeout) or (False, "")
    if not ok:
        return []
    listing = []
    for line in str(output).splitlines():
        # The setuid/sticky bits ('s', 'S', 't', 'T') appear in the mode string
        # on /sdcard/Android/{data,media}, where most application media lives.
        match = re.match(r"^([-dl])[rwxstST-]{9}\s+\d+\s+\S+\s+\S+\s+(\d+)\s+"
                         r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$", line)
        if not match:
            continue
        listing.append({"name": match.group(4).strip(),
                        "size": int(match.group(2)),
                        "is_dir": match.group(1) == "d",
                        "date": match.group(3)})
    return listing


def _live_media(serial, directories, limit, unit="files", thumbs=0, depth=0,
                layout="files"):
    """Bounded live walk of the directories the artifact viewer opens.

    ``depth`` expands sub-directories once, which is how WhatsApp and Telegram
    keep their media (``Media/WhatsApp Images/…`` rather than files in
    ``Media`` itself). ``layout="packages"`` prints directory entries with
    their timestamp instead of a meaningless size.
    """
    files = []
    for directory in directories:
        for entry in _live_ls(serial, directory):
            files.append({**entry, "parent": directory})

    if depth:
        expanded = []
        for directory in directories:
            for entry in _live_ls(serial, directory):
                if not entry["is_dir"]:
                    continue
                child = directory.rstrip("/") + "/" + entry["name"]
                expanded.extend({**sub, "parent": child}
                                for sub in _live_ls(serial, child)[:40])
        if expanded:
            files = expanded

    if not files:
        return None

    if layout == "packages":
        rows = [(entry["name"], "directory" if entry["is_dir"] else "file", entry["date"])
                for entry in files[:limit]]
    else:
        rows = [(entry["name"], entry["parent"].rsplit("/", 1)[-1], _size(entry["size"]))
                for entry in files[:limit]]

    photos = []
    if thumbs:
        # WhatsApp keeps a few bytes of placeholder beside every real photo;
        # a 3 KB "image" makes a grey thumbnail, so pick the substantial ones.
        candidates = [entry for entry in files
                      if Path(entry["name"]).suffix.lower() in IMAGE_SUFFIXES
                      and entry["size"] > 20_000]
        candidates.sort(key=lambda entry: -entry["size"])
        photos = [entry["parent"].rstrip("/") + "/" + entry["name"]
                  for entry in candidates[:thumbs]]

    return {"count": len(files), "rows": rows, "unit": unit,
            "thumbs": _live_thumbs(serial, photos),
            "source": ", ".join(d.replace("/sdcard/", "/") for d in directories[:3]),
            "status": "live"}


def _live_thumbs(serial, remotes):
    """Pull a handful of previews into a scratch folder, never into the store."""
    import tempfile

    adb = _adb()
    folder = Path(tempfile.gettempdir()) / "droidforenx-report-thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    pulled = []
    for remote in remotes:
        local = folder / Path(remote).name
        try:
            if not local.exists():
                _live(adb.pull_file, serial, remote, str(local), timeout=30)
            if local.is_file() and local.stat().st_size > 4096:
                pulled.append(local)
        except Exception:
            continue
    return pulled


def _live_deleted(serial, limit):
    adb = _adb()
    ok, output = _live(adb.adb_shell, serial,
                       "find /sdcard -maxdepth 6 -name '*.trashed-*' -type f 2>/dev/null",
                       timeout=60) or (False, "")
    if not ok:
        return None
    names = [line.strip() for line in str(output).splitlines() if line.strip()]
    if not names:
        return None
    rows = [(Path(name).name, str(Path(name).parent).replace("/sdcard/", "/"), "")
            for name in names[:limit]]
    return {"count": len(names), "rows": rows, "unit": "files",
            "source": "/sdcard/**/*.trashed-*", "status": "live"}


def _live_storage(serial, limit):
    adb = __import__("desktop.core.adb_manager", fromlist=["adb_shell"])
    ok, value = _live(adb.adb_shell, serial, "df -h /sdcard | tail -1", timeout=20) or (False, "")
    if not ok or not value.strip():
        return None
    parts = value.split()
    rows = [("Filesystem", parts[0], ""), ("Size", parts[1], ""),
            ("Used", parts[2], ""), ("Available", parts[3], ""), ("Use", parts[4], "")]
    return {"count": None, "unit": "storage", "rows": rows, "source": "df -h /sdcard (live)",
            "status": "live"}


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

def _panel(key, title, colour, unit, body, store, stamp, live_note):
    view = {"key": key, "title": title, "colour": colour, "unit": unit}
    if body:
        view.update(body)
        if str(view.get("status", "")).lower() == "live":
            view["note"] = live_note
    else:
        expected = STORE_PATHS.get(key, "the case store")
        view.update({"count": None, "rows": [], "status": "missing",
                     "source": expected,
                     "note": f"Not present in {store.name if store else 'this case'}. "
                             f"The acquisition phase that writes {expected} did not run, or "
                             "recorded nothing."})
    view.setdefault("stamp", stamp)
    return view


def collect(store: Path | None = None, serial: str | None = None, *,
            allow_live: bool = True, limit: int = 8, generated_at: str = "") -> list[dict]:
    """One panel per advertised artifact class, in checklist order."""
    store = Path(store) if store else None
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    live_note = ("Read live from the handset at report time; not the acquired state.")

    builders = {
        "contacts": lambda: (_store_contacts(store, limit) if store else None)
                            or (_live_contacts(serial, limit) if allow_live and serial else None),
        "sms": lambda: (_store_records(store, "logical/sms_messages.json",
                                       ("address", "body", "date_readable"), limit, "messages")
                        if store else None)
                       or (_live_sms(serial, limit) if allow_live and serial else None),
        "calls": lambda: (_store_records(store, "logical/call_log.json",
                                         ("number", "type", "date_readable"), limit, "calls")
                          if store else None)
                         or (_live_calls(serial, limit) if allow_live and serial else None),
        "calendar": lambda: (_store_records(store, "logical/calendar_events.json",
                                            ("title", "dtstart_readable", "eventLocation"), limit, "events")
                             if store else None)
                            or (_live_calendar(serial, limit) if allow_live and serial else None),
        "browser": lambda: _store_listing(store, "browser", limit, "records"),
        "app_databases": lambda: _store_listing(store, "app_databases", limit, "databases",
                                                suffixes={".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}),
        "media": lambda: _store_media(store, limit, folder_filter=("dcim", "pictures", "movies", "music"))
                           or (_live_media(serial, ("/sdcard/DCIM/Camera", "/sdcard/Pictures",
                                                    "/sdcard/Movies", "/sdcard/Music"),
                                           limit, thumbs=6) if allow_live and serial else None),
        "documents": lambda: _store_media(store, limit, suffixes=DOC_SUFFIXES,
                                          folder_filter=("download", "document"), unit="documents")
                             or (_live_media(serial, ("/sdcard/Download", "/sdcard/Documents"),
                                             limit, unit="documents") if allow_live and serial else None),
        "social": lambda: _store_media(store, limit, folder_filter=("android/media", "social_apps"),
                                       unit="files")
                          or (_live_media(serial, ("/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
                                                   "/sdcard/Android/media/org.telegram.messenger",
                                                   "/sdcard/Android/media/com.facebook.katana"),
                                          limit, thumbs=3, depth=1) if allow_live and serial else None),
        "deleted": lambda: _store_listing(store, "deleted_files", limit, "files")
                           or (_live_deleted(serial, limit) if allow_live and serial else None),
        "device": lambda: (_store_device(store, limit) if store else None)
                          or (_live_device(serial, limit) if allow_live and serial else None),
        "storage": lambda: (_store_storage(store, limit) if store else None)
                           or (_live_storage(serial, limit) if allow_live and serial else None),
        "app_data": lambda: _store_listing(store, "app_data", limit, "files")
                            or (_live_media(serial, ("/sdcard/Android/data",), limit,
                                            unit="packages", layout="packages")
                                if allow_live and serial else None),
        "recovered": lambda: _store_listing(store, "recovered", limit, "items"),
        "emails": lambda: _store_listing(store, "emails", limit, "messages", suffixes={".json", ".eml", ".mbox"}),
        "doc_metadata": lambda: _store_records(store, "document_metadata.json",
                                               ("file_name", "author", "created"), limit, "documents")
                               if store else None,
        "bookmarks": lambda: _store_listing(store, "browser", limit, "bookmarks",
                                            suffixes={".json"}),
        "packaging": lambda: _store_packaging(store, limit) if store else None,
    }

    views = []
    # The providers are independent, and ADB is the slow part, so the classes
    # are gathered concurrently. Sequentially this took 26 seconds on a handset
    # with 8,000 messages; the wait is paid by whoever clicked Generate.
    from concurrent.futures import ThreadPoolExecutor

    def build(entry):
        key, title, colour, unit = entry
        try:
            return entry, builders[key]()
        except Exception:
            return entry, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = dict((entry[0], body) for entry, body in pool.map(build, CATEGORIES))

    for key, title, colour, unit in CATEGORIES:
        views.append(_panel(key, title, colour, unit, fetched.get(key), store, stamp, live_note))
    return views


def summarise(views) -> dict:
    recovered = [v for v in views if str(v.get("status")).lower() == "recovered"]
    live = [v for v in views if str(v.get("status")).lower() == "live"]
    missing = [v for v in views if str(v.get("status")).lower() == "missing"]
    return {"total": len(views), "recovered": len(recovered), "live": len(live),
            "missing": len(missing),
            "missing_titles": [v["title"] for v in missing]}
