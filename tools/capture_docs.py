"""Regenerate the screenshots the README embeds, from the running application.

Run it with a phone plugged in::

    python tools/capture_docs.py --device

Without ``--device`` the shots that need live ADB (the artifact grid and the
SIM read) are skipped and everything else is still taken.

Two things this script is careful about, because both have bitten the docs
before:

* It does **not** use ``QT_QPA_PLATFORM=offscreen``. That plugin ships a stub
  font database, so every glyph comes out as a box. The window is shown for
  real, on a desktop that is allowed to be a little busy for a few seconds,
  and grabbed with ``QWidget.grab()`` -- client area only, no desktop pixels,
  so an overlapping window cannot leak into the shot.
* It does **not** write to your database. ``database.DB_PATH`` is pointed at a
  temporary directory that is seeded with one demonstrative case, one session,
  two SIM slots and a couple of thousand artifact rows, then deleted. The
  phone is read for real; the case file is fiction, and says so.

Usage::

    python tools/capture_docs.py                 # everything but the live shots
    python tools/capture_docs.py --device        # full set, phone connected
    python tools/capture_docs.py --only sim      # one shot
    python tools/capture_docs.py --tests LOG     # re-render tests.png from a log
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "docs" / "images"

CASE_ID = "1f0f5f3a-7d1e-4a51-9a0e-6c2a2f0b41d1"
SESSION_ID = "a4e2c2d8-51c9-4b6e-8f63-1d5f3c9b2a77"
DEVICE_ID = "c9cb0f26-3f61-4f7c-9b8e-2e0e5d6a1c33"
SERIAL = "13276374A7004073"

ARTIFACT_TOTAL = 12480


# --------------------------------------------------------------------------
# demo data
# --------------------------------------------------------------------------

def _seed(conn: sqlite3.Connection) -> None:
    """One case, the way a case looks halfway through an examination."""
    now = datetime(2026, 10, 24, 9, 12, tzinfo=timezone.utc)
    cases = [
        (CASE_ID, "CASE-2026-0141", "Seized handset - Osu armed robbery inquiry",
         "Naa Adjei", "Cybercrime Unit, CID", "Handset seized under warrant.",
         "open", now),
        (uuid.uuid4().hex[:32], "CASE-2026-0138", "Corporate data exfiltration - Arden Logistics",
         "Kwesi Boateng", "Digital Forensics Lab", "Insider exfiltration, three endpoints.",
         "open", now - timedelta(days=6)),
        (uuid.uuid4().hex[:32], "CASE-2026-0129", "Missing person - handset triage",
         "Adwoa Mensah", "CID, Accra Central", "Triage only, returned to family.",
         "closed", now - timedelta(days=25)),
    ]
    conn.executemany(
        "INSERT INTO cases (id, case_number, title, investigator, agency, description,"
        " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(c[0], c[1], c[2], c[3], c[4], c[5], c[6],
          c[7].isoformat(), c[7].isoformat()) for c in cases])

    conn.execute(
        "INSERT INTO devices (id, serial_number, manufacturer, model, android_version,"
        " api_level, security_patch, encryption_status, bootloader_state, first_seen,"
        " last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (DEVICE_ID, SERIAL, "TECNO", "TECNO_KL6", "13", 33, "2025-11-05",
         "file-based", "locked", now.isoformat(), now.isoformat()))

    conn.execute(
        "INSERT INTO forensic_sessions (id, case_id, device_id, status, current_stage,"
        " operator, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (SESSION_ID, CASE_ID, DEVICE_ID, "acquisition", 3, "Naa Adjei",
         "Isolation applied at 09:04, acquisition started 09:12.", now.isoformat(),
         now.isoformat()))

    conn.execute(
        "INSERT INTO acquisition_profiles (id, session_id, primary_mode, hash_algorithm,"
        " status, total_files, total_bytes, image_format, started_at, completed_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex[:32], SESSION_ID, "logical", "sha256", "completed",
         12480, 3_118_268_416, "raw", now.isoformat(), now.isoformat()))

    for slot, status, carrier, iccid, imsi, msisdn, country in (
            (1, "present", "Telecel Ghana", "8923301412345678901", "620071234567890",
             "+233201234567", "Ghana"),
            (2, "present", "MTN Ghana", "8923302698765432109", "620031234567891",
             "+233551234567", "Ghana"),
            (3, "absent", "", "", "", "", "")):
        conn.execute(
            "INSERT INTO sim_cards (id, session_id, slot, status, imsi, iccid, msisdn,"
            " carrier, country, extracted_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:32], SESSION_ID, slot, status, imsi, iccid, msisdn,
             carrier, country, now.isoformat()))

    for name, items, summary in (
            ("sms", 860, "860 messages across 74 conversations"),
            ("calls", 1403, "1403 call records, 12 missed"),
            ("contacts", 2249, "2249 contacts, 318 with email addresses"),
            ("deleted", 96, "96 deleted files recovered by signature")):
        conn.execute(
            "INSERT INTO examination_results (id, session_id, analysis_type, status,"
            " findings, summary, item_count, tools_used, completed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:32], SESSION_ID, name, "completed", "{}", summary,
             items, "[]", now.isoformat()))

    conn.execute(
        "INSERT INTO forensic_reports (id, session_id, title, format, case_number,"
        " examiner_name, examiner_badge, examiner_organization, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex[:32], SESSION_ID, "Interim findings", "pdf",
         "CASE-2026-0141", "Naa Adjei", "D-01178", "Cybercrime Unit, CID",
         "draft", now.isoformat()))

    # The dashboard counts artifacts per case, so the evidence has to be filed
    # against the session rather than faked into the label.
    rows = []
    stamp = now.isoformat()
    for i in range(ARTIFACT_TOTAL):
        rows.append((
            uuid.uuid4().hex[:32], SESSION_ID, f"demo-{i:06d}",
            f"artifact {i}", "/sdcard/", "SMS" if i % 3 else "Calls", "text/plain",
            512 + i, "", "", "{}", stamp))
    conn.executemany(
        "INSERT INTO evidence_artifacts (id, session_id, artifact_id, name, source,"
        " category, mime_type, file_size, file_path, sha256_hash, metadata, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


# --------------------------------------------------------------------------
# qt plumbing
# --------------------------------------------------------------------------

def _make_app():
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFont
    from desktop.ui.theme import theme

    app = QApplication(sys.argv[:1])
    # A distinct application name keeps the capture out of the operator's own
    # QSettings (sidebar visibility, theme mode, assistant transcripts).
    app.setApplicationName("DroidForenX-Screenshots")
    app.setOrganizationName("DroidForenX")
    from desktop import __version__
    app.setApplicationVersion(__version__)
    app.setFont(QFont("Segoe UI", 10))

    from desktop.ui import branding
    app.setWindowIcon(branding.app_icon())

    theme.install(app)
    theme.set_mode("dark")
    return app


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_for(app, predicate, timeout: float, label: str) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            pump(app, 0.6)          # let the last relayout finish
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {label}")


def save(widget, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    pixmap = widget.grab()
    pixmap.save(str(path))
    print(f"  {name}.png  {pixmap.width()}x{pixmap.height()}")
    return path


def scroll_top(widget) -> None:
    """Every page scroller back to the top, so a shot frames the heading."""
    from PyQt6.QtWidgets import QScrollArea
    for area in widget.findChildren(QScrollArea):
        area.verticalScrollBar().setValue(0)


# --------------------------------------------------------------------------
# shots
# --------------------------------------------------------------------------

def capture(window, app, wanted: set[str], with_device: bool, tests_log: Path | None) -> None:
    from PyQt6.QtCore import Qt
    from desktop.ui.dialogs import CreateImageDialog

    def go(index: int) -> None:
        window._navigate_to(index)
        pump(app, 1.2)
        scroll_top(window.stack.currentWidget())
        pump(app, 0.3)

    shots: dict[str, callable] = {}

    def shot_dashboard():
        window._on_case_selected(CASE_ID)
        go(0)
        window._refresh_stats()
        pump(app, 1.0)
        save(window, "dashboard")

    def shot_cases():
        go(1)
        window.case_page.refresh()
        pump(app, 0.5)
        save(window, "case-management")

    def shot_device():
        go(2)
        page = window.device_page
        page._scan_devices()
        wait_for(app, lambda: page.device_grid.count() > 0,
                 timeout=90, label="device scan")
        pump(app, 1.0)
        save(window, "device-connection")

    def shot_acquisition():
        go(5)
        page = window.acquire_page
        page.level_combo.setCurrentIndex(0)      # Level 1 - logical
        page.hash_combo.setCurrentIndex(0)       # SHA-256
        scroll_top(page)
        pump(app, 0.8)
        save(window, "acquisition")

    def shot_artifacts():
        go(9)
        page = window.artifacts_page
        wait_for(app, lambda: page.all_artifacts or not page._data_loaded,
                 timeout=300, label="artifact extraction")
        pump(app, 1.5)
        save(window, "artifact-viewer")

    def shot_sim():
        go(10)
        page = window.sim_page
        page._read_sim()
        wait_for(app, lambda: page._result is not None, timeout=180, label="SIM read")
        pump(app, 1.0)
        save(window, "sim-forensics")

    def shot_create_image():
        dialog = CreateImageDialog(window.acquire_page, serial=SERIAL,
                                   case_id="CASE-2026-0141", session_id=SESSION_ID)
        # The dialog wants to be taller than the 720 px of desktop this machine
        # leaves at 150% scaling, and a top-level resize is clamped to the
        # screen. WA_DontShowOnScreen lays it out without mapping it, so the
        # whole form can be grabbed in one frame.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dialog.show()
        pump(app, 1.0)
        wait_for(app, lambda: dialog.partition_combo.count() > 0,
                 timeout=90, label="partition table")
        for i in range(dialog.format_combo.count()):
            if "raw" in dialog.format_combo.itemText(i).lower():
                dialog.format_combo.setCurrentIndex(i)
                break
        dialog.source_tabs.setCurrentIndex(0)
        dialog.examiner_edit.setText("Naa Adjei")
        dialog.evidence_number_edit.setText("EV-2026-0141-01")
        dialog.description_edit.setText("Logical image of user data, seized handset")
        # Size to the form, not to a number picked by eye: the scroll area knows
        # how tall its content is, and everything around it is the dialog's
        # chrome (margins and the button row).
        from PyQt6.QtWidgets import QScrollArea
        area = dialog.findChild(QScrollArea)
        if area is not None and area.widget() is not None:
            chrome = dialog.height() - area.height()
            dialog.resize(1100, area.widget().sizeHint().height() + chrome + 8)
        pump(app, 1.0)
        save(dialog, "create-image")
        dialog.close()

    def shot_report():
        go(7)
        page = window.report_page
        page.case_input.setText("CASE-2026-0141")
        page.examiner_input.setText("Naa Adjei")
        page.badge_input.setText("D-01178")
        page.org_input.setText("Cybercrime Unit, CID")
        pump(app, 0.8)
        save(window, "reporting")

    shots.update({
        "dashboard": shot_dashboard,
        "cases": shot_cases,
        "device": shot_device,
        "acquisition": shot_acquisition,
        "artifacts": shot_artifacts,
        "sim": shot_sim,
        "create-image": shot_create_image,
        "report": shot_report,
    })

    needs_device = {"device", "acquisition", "artifacts", "sim", "create-image"}
    if not with_device:
        for name in needs_device:
            shots.pop(name, None)

    for name, fn in shots.items():
        if wanted and name not in wanted:
            continue
        print(f"capturing {name}...")
        fn()

    if tests_log is not None and (not wanted or "tests" in wanted):
        print("capturing tests...")
        render_tests(log_path=tests_log, out_path=OUT_DIR / "tests.png")


# --------------------------------------------------------------------------
# tests.png
# --------------------------------------------------------------------------

def render_tests(log_path: Path, out_path: Path) -> Path:
    """Turn a real test log into the terminal-ish image the README embeds."""
    from PIL import Image, ImageDraw, ImageFont

    lines = [ln.rstrip() for ln in log_path.read_text(encoding="utf-8",
                                                      errors="replace").splitlines()]
    body = [ln for ln in lines if ln.strip()][-18:]
    width = 1800
    height = 80 + 25 * len(body) + 40
    image = Image.new("RGB", (width, height), "#0b1220")
    draw = ImageDraw.Draw(image)

    def font(size: int):
        for name in ("consola.ttf", "cour.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    title = font(22)
    mono = font(21)
    draw.rectangle([0, 0, width, 54], fill="#111827")
    draw.text((28, 16), "DroidForenX  -  unit test suite", font=title, fill="#94a3b8")

    y = 80
    for line in body:
        colour = "#e2e8f0"
        if line.startswith("OK"):
            colour = "#34d399"
        elif line.startswith("Ran "):
            colour = "#93c5fd"
        elif "FAIL" in line or "ERROR" in line:
            colour = "#f87171"
        draw.text((28, y), line[:150], font=mono, fill=colour)
        y += 25

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    print(f"  tests.png  {width}x{height}")
    return out_path


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", action="store_true",
                        help="include the shots that read the connected handset")
    parser.add_argument("--only", nargs="*", default=[],
                        help="capture only these shots")
    parser.add_argument("--tests", type=Path, default=None,
                        help="test log to render as tests.png")
    args = parser.parse_args()

    if not OUT_DIR.is_dir():
        parser.error(f"{OUT_DIR} does not exist; run from a checkout")

    tmp = Path(tempfile.mkdtemp(prefix="dfx-docs-"))
    from desktop.core import database
    database.DB_PATH = tmp / "demo.db"
    database.init_db()

    conn = sqlite3.connect(str(database.DB_PATH))
    try:
        _seed(conn)
    finally:
        conn.close()

    app = _make_app()
    from desktop.ui.main_window import MainWindow

    window = MainWindow()
    window.resize(1280, 720)
    window.show()
    pump(app, 2.5)

    try:
        capture(window, app, set(args.only), args.device, args.tests)
    finally:
        window.close()
        pump(app, 0.3)
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
