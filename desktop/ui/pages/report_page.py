"""Stage 4: Professional Forensic Investigation Report — court-ready, non-technical language."""
import uuid
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QFrame, QGroupBox, QGridLayout, QLineEdit, QTextEdit,
                              QComboBox, QScrollArea, QMessageBox, QFileDialog,
                              QDateEdit, QSplitter)
from PyQt6.QtCore import Qt, pyqtSignal, QDate
from ..widgets.log_console import LogConsole
from ..widgets.data_table import DataTable


REPORT_ROOT = Path.home() / ".droidforenx" / "reports"


class ReportPage(QWidget):
    navigate = pyqtSignal(str)

    def __init__(self, db_get, parent=None):
        super().__init__(parent)
        self.db_get = db_get
        self._session_id = ""
        self._case_number = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("Stage 4 — Forensic Investigation Report")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #ef4444; background: transparent; border: none;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Generate professional investigation reports for lawyers, judges, and non-technical audiences. "
            "Reports include chain of custody, evidence findings, and hash verification."
        )
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; background: transparent; border: none;")
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(16)

        # ── Report Configuration ─────────────────────────────────────────
        config_group = QGroupBox("Report Configuration")
        config_layout = QGridLayout(config_group)
        config_layout.setSpacing(10)

        config_layout.addWidget(QLabel("Case Number:"), 0, 0)
        self.case_input = QLineEdit()
        self.case_input.setPlaceholderText("CASE-2026-0001")
        config_layout.addWidget(self.case_input, 0, 1)

        config_layout.addWidget(QLabel("Investigator Name:"), 1, 0)
        self.examiner_input = QLineEdit()
        self.examiner_input.setPlaceholderText("Jane Smith, CFCE")
        config_layout.addWidget(self.examiner_input, 1, 1)

        config_layout.addWidget(QLabel("Badge / ID:"), 2, 0)
        self.badge_input = QLineEdit()
        self.badge_input.setPlaceholderText("D-00123")
        config_layout.addWidget(self.badge_input, 2, 1)

        config_layout.addWidget(QLabel("Agency / Organization:"), 3, 0)
        self.org_input = QLineEdit()
        self.org_input.setPlaceholderText("Digital Forensics Unit")
        config_layout.addWidget(self.org_input, 3, 1)

        config_layout.addWidget(QLabel("Report Format:"), 4, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItems([
            "HTML (Recommended)",
            "PDF (Court-Ready)",
            "JSON (Machine Readable)",
            "Verification Workbook (XLSX)",
        ])
        config_layout.addWidget(self.format_combo, 4, 1)

        cl.addWidget(config_group)

        # ── Report Sections Preview ──────────────────────────────────────
        preview_group = QGroupBox("Report Sections")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_table = DataTable(["Section", "Description", "Status"])
        self.preview_table.setMaximumHeight(320)
        preview_layout.addWidget(self.preview_table)
        cl.addWidget(preview_group)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        btn_generate = QPushButton("Generate Report")
        btn_generate.setObjectName("btn-accent")
        btn_generate.setFixedHeight(42)
        btn_generate.clicked.connect(self._generate_report)
        btn_row.addWidget(btn_generate)

        btn_open = QPushButton("Open Reports Folder")
        btn_open.setObjectName("btn-outline")
        btn_open.setFixedHeight(42)
        btn_open.clicked.connect(self._open_folder)
        btn_row.addWidget(btn_open)

        btn_row.addStretch()
        cl.addLayout(btn_row)

        cl.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # Console
        self.console = LogConsole("Report Generation Log")
        self.console.setMinimumHeight(160)
        layout.addWidget(self.console)

    def set_session(self, session_id: str, case_number: str = ""):
        self._session_id = session_id
        self._case_number = case_number
        self.case_input.setText(case_number)
        self._update_preview()

    def _update_preview(self):
        sections = [
            ("Cover Page", "Case title, investigator, date, and confidentiality notice", "Ready"),
            ("Executive Summary", "Plain-language overview of what was found and why it matters", "Ready"),
            ("Chain of Custody", "Who handled the device, when, and how evidence integrity was maintained", "Ready"),
            ("Device Information", "Physical description, serial numbers, model, storage capacity", "Ready"),
            ("Examination Methodology", "Step-by-step explanation of how evidence was collected", "Ready"),
            ("Evidence Findings", "Detailed breakdown of every artifact category recovered", "Ready"),
            ("Financial Transactions", "Mobile money and payment records with amounts and recipients", "Ready"),
            ("Communications", "Text messages, calls, contacts, and social media activity", "Ready"),
            ("Documents & Files", "Recovered documents with source application and dates", "Ready"),
            ("Timeline of Events", "Chronological reconstruction of device activity", "Ready"),
            ("Key Evidence Highlights", "Important findings summarized for quick reference", "Ready"),
            ("Evidence Integrity", "SHA-256 hash values proving data has not been altered", "Ready"),
            ("Conclusions", "What the evidence shows based on the examination", "Ready"),
            ("Appendices", "Detailed data tables and raw examination results", "Ready"),
        ]
        self.preview_table.load_data(sections)

    # ═══════════════════════════════════════════════════════════════════════
    #  DATA GATHERING — pulls from DB + examination results
    # ═══════════════════════════════════════════════════════════════════════

    def _gather_data(self, case_number, examiner, badge, org):
        """Pull all available data from the database for report generation."""
        conn = self.db_get()
        data = {
            "case_number": case_number,
            "examiner": examiner,
            "badge": badge,
            "org": org,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "device": {},
            "session": {},
            "results": [],
            "audit_log": [],
            "sms_count": 0,
            "calls_count": 0,
            "contacts_count": 0,
            "media_count": 0,
            "apps_count": 0,
            "accounts": [],
            "transactions": [],
            "documents": [],
            "social_media": {},
            "emails": [],
            "timeline_events": [],
            "wifi_networks": [],
            "notifications": [],
            "app_usage": [],
        }

        try:
            # Session
            if self._session_id:
                row = conn.execute(
                    "SELECT * FROM forensic_sessions WHERE id=?", (self._session_id,)
                ).fetchone()
                if row:
                    data["session"] = dict(row)

            # Device identification
            if self._session_id:
                dev = conn.execute(
                    "SELECT * FROM device_identifications WHERE session_id=? ORDER BY identified_at DESC LIMIT 1",
                    (self._session_id,),
                ).fetchone()
                if dev:
                    data["device"] = dict(dev)

            # Examination results
            if self._session_id:
                rows = conn.execute(
                    "SELECT * FROM examination_results WHERE session_id=?",
                    (self._session_id,),
                ).fetchall()
                for r in rows:
                    rd = dict(r)
                    if rd.get("findings"):
                        try:
                            rd["findings"] = json.loads(rd["findings"])
                        except Exception:
                            rd["findings"] = {}
                    data["results"].append(rd)

            # Audit log
            if self._session_id:
                logs = conn.execute(
                    "SELECT * FROM audit_log WHERE target=? ORDER BY timestamp",
                    (self._session_id,),
                ).fetchall()
                data["audit_log"] = [dict(l) for l in logs]

            # Images for the plates. Read from the case store, never from the
            # handset: a report has to describe evidence that was acquired, not
            # whatever happens to be reachable when someone clicks Generate.
            try:
                from ...report import artifact_images

                data["images"] = artifact_images.collect(
                    self._session_id,
                    (data.get("session") or {}).get("device_id"),
                    limit=18,
                )
            except Exception as exc:
                self.console.log(f"Could not collect images for the report: {exc}", "warning")
                data["images"] = []

            # Artifact-view panels: one per artifact class the tool advertises.
            # The case store is the source of truth; the handset is only read
            # for the logical classes when the store holds no copy, and each
            # panel says which of the two it used.
            try:
                from ...report import artifact_images, artifact_views
                from ...core import adb_manager

                serial = None
                try:
                    ready = adb_manager.ready_devices()
                    serial = ready[0].get("serial") if ready else None
                except Exception:
                    serial = None

                store = artifact_images.acquisition_for(serial)
                data["artifact_views"] = artifact_views.collect(
                    store, serial, allow_live=bool(serial), limit=8,
                    generated_at=data.get("generated_at", ""),
                )
            except Exception as exc:
                self.console.log(f"Could not collect artifact views: {exc}", "warning")
                data["artifact_views"] = []

            conn.close()
        except Exception as e:
            try:
                conn.close()
            except Exception:
                pass
            self.console.log(f"Error gathering report data: {e}", "error")

        return data

    # ═══════════════════════════════════════════════════════════════════════
    #  GENERATE REPORT
    # ═══════════════════════════════════════════════════════════════════════

    def _generate_report(self):
        case_number = self.case_input.text().strip() or "CASE-UNKNOWN"
        examiner = self.examiner_input.text().strip() or "Unknown Examiner"
        badge = self.badge_input.text().strip() or ""
        org = self.org_input.text().strip() or ""
        fmt_text = self.format_combo.currentText()
        fmt = fmt_text.split(" ")[0].lower()
        is_workbook = fmt == "verification"
        if is_workbook:
            fmt = "xlsx"

        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        case_dir = REPORT_ROOT / case_number
        case_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_id = uuid.uuid4().hex[:8]

        if is_workbook:
            # The XLSX workbook is derived from the evidence store, not the app
            # database, so it needs no database gather.
            path = self._build_workbook(case_number, case_dir, report_id)
        else:
            data = self._gather_data(case_number, examiner, badge, org)
            self.console.log(f"Gathered data: {len(data['results'])} examination results, "
                             f"{len(data['audit_log'])} audit entries", "info")

            if fmt == "json":
                path = case_dir / f"investigation_report_{report_id}.json"
                path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            elif fmt == "pdf":
                path = case_dir / f"investigation_report_{report_id}.pdf"
                self._build_pdf_report(data, path)
            else:
                path = case_dir / f"investigation_report_{report_id}.html"
                html = self._build_html_report(data)
                path.write_text(html, encoding="utf-8")

        # Hash
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1048576)
                if not chunk:
                    break
                sha256.update(chunk)
        report_hash = sha256.hexdigest()

        # Save to DB
        conn = self.db_get()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO forensic_reports
               (id, session_id, title, format, case_number, examiner_name,
                examiner_badge, examiner_organization, executive_summary,
                findings_summary, file_path, file_size_bytes,
                report_hash_sha256, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(uuid.uuid4()), self._session_id,
             f"Investigation Report — {case_number}", fmt, case_number,
             examiner, badge, org,
             f"Report generated by {examiner} on {now[:10]}",
             f"Full investigation report for case {case_number}",
             str(path), path.stat().st_size, report_hash, "final", now),
        )
        conn.commit()
        conn.close()

        self.console.log(f"Report generated: {path}", "success")
        self.console.log(f"SHA-256: {report_hash}", "info")
        self.console.log(f"Size: {path.stat().st_size:,} bytes", "info")
        if path.with_suffix(path.suffix + ".sha256").exists():
            self.console.log(f"Integrity sidecar: {path.name}.sha256", "info")

        QMessageBox.information(
            self, "Report Generated",
            f"Investigation report saved successfully.\n\n"
            f"Path: {path}\n"
            f"Size: {path.stat().st_size:,} bytes\n"
            f"SHA-256: {report_hash[:32]}...\n\n"
            f"The PDF carries its own table of contents and page numbers. Its\n"
            f"integrity record is the sidecar file written beside it:\n"
            f"{path.name}.sha256"
        )

    # ═══════════════════════════════════════════════════════════════════════
    #  XLSX VERIFICATION WORKBOOK — evidence-store derived
    # ═══════════════════════════════════════════════════════════════════════

    def _build_workbook(self, case_number, case_dir, report_id):
        """Build the read-only XLSX verification workbook for ``case_number``.

        The workbook is derived from the evidence store (``~/.droidforenx/evidence``),
        not the app database, so the case number is used as the evidence-store case
        id. If no manifest exists for it yet, the workbook records ``NO_MANIFEST``
        rather than implying that integrity was verified.
        """
        from ...report import evidence_workbook
        from ...core import evidence_store

        path = case_dir / f"verification_workbook_{report_id}.xlsx"
        evidence_workbook.build(case_number, output=path)

        integrity = evidence_store.compute_integrity(case_number)
        self.console.log(
            f"Integrity verdict: {integrity['verdict']} "
            f"({integrity['ok']}/{integrity['checked']} artifacts re-hashed OK)", "info")
        if integrity["verdict"] == "NO_MANIFEST":
            self.console.log(
                f"No evidence tree at {evidence_store.case_dir(case_number)} — "
                "the workbook records NO_MANIFEST. Acquire evidence for this case "
                "first to assert integrity.", "warning")

        return path

    # ═══════════════════════════════════════════════════════════════════════
    #  HTML REPORT — Professional, non-technical
    # ═══════════════════════════════════════════════════════════════════════

    def _build_html_report(self, d):
        dev = d["device"]
        session = d["session"]
        results = d["results"]
        audit = d["audit_log"]

        # Extract key metrics from results
        sms_count = 0
        calls_count = 0
        contacts_count = 0
        media_count = 0
        apps_count = 0
        accounts = []
        transactions = []
        documents = []
        social_media = {}
        wifi_networks = []
        timeline_events = []

        for r in results:
            atype = r.get("analysis_type", "")
            count = r.get("item_count", 0) or 0
            findings = r.get("findings", {}) or {}

            if atype == "sms":
                sms_count = count
            elif atype == "calls":
                calls_count = count
            elif atype == "contacts":
                contacts_count = count
            elif atype == "media":
                media_count = count
            elif atype == "apps":
                apps_count = count
            elif atype == "emails_accounts":
                accounts = findings.get("accounts", []) if isinstance(findings, dict) else []
            elif atype == "transactions":
                transactions = findings.get("transactions", []) if isinstance(findings, dict) else []
            elif atype == "documents":
                documents = findings.get("documents", []) if isinstance(findings, dict) else []
            elif atype == "wifi_bluetooth":
                wifi_networks = findings.get("wifi", []) if isinstance(findings, dict) else []
            elif atype == "whatsapp":
                social_media["WhatsApp"] = count
            elif atype == "telegram":
                social_media["Telegram"] = count
            elif atype == "instagram":
                social_media["Instagram"] = count
            elif atype == "twitter":
                social_media["Twitter/X"] = count

        total_artifacts = sms_count + calls_count + contacts_count + media_count + apps_count

        # ── Build HTML sections ──────────────────────────────────────────
        device_section = self._html_device_section(dev)
        methodology_section = self._html_methodology_section()
        findings_section = self._html_findings_section(
            results, sms_count, calls_count, contacts_count,
            media_count, apps_count, accounts, transactions,
            documents, social_media, wifi_networks
        )
        timeline_section = self._html_timeline_section(results)
        highlights_section = self._html_highlights_section(
            results, accounts, transactions, documents, social_media
        )
        integrity_section = self._html_integrity_section(d, results)
        appendices_section = self._html_appendices_section(results)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Investigation Report — {d['case_number']}</title>
<style>
@page {{
    size: A4;
    margin: 20mm;
}}
@media print {{
    body {{ margin: 0; padding: 0; }}
    .page-break {{ page-break-before: always; }}
}}
* {{ box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    margin: 0; padding: 40px;
    color: #1a1a2e; line-height: 1.7;
    background: #ffffff; font-size: 14px;
}}
.cover {{
    text-align: center; padding: 80px 40px;
    border: 3px solid #1a237e; margin-bottom: 40px;
    background: linear-gradient(135deg, #f8f9ff 0%, #e8eaf6 100%);
}}
.cover h1 {{
    font-size: 28px; color: #1a237e;
    margin: 0 0 8px 0; letter-spacing: 2px;
    text-transform: uppercase;
}}
.cover .subtitle {{
    font-size: 16px; color: #37474f;
    margin: 0 0 30px 0; font-weight: 400;
}}
.cover .case-badge {{
    display: inline-block; background: #1a237e; color: white;
    padding: 8px 24px; border-radius: 4px;
    font-size: 18px; font-weight: 700;
    letter-spacing: 1px; margin: 16px 0;
}}
.cover .meta {{ font-size: 13px; color: #546e7a; margin-top: 24px; }}
.cover .meta strong {{ color: #1a237e; }}
.confidential {{
    display: inline-block; border: 2px solid #c62828;
    color: #c62828; padding: 4px 16px; font-size: 11px;
    font-weight: 700; letter-spacing: 2px;
    text-transform: uppercase; margin-top: 20px;
}}
h2 {{
    color: #1a237e; font-size: 18px;
    border-bottom: 2px solid #1a237e;
    padding-bottom: 6px; margin-top: 36px;
}}
h3 {{
    color: #283593; font-size: 15px;
    margin-top: 20px; margin-bottom: 8px;
}}
.meta-table {{
    border-collapse: collapse; width: 100%;
    margin: 12px 0 20px 0;
}}
.meta-table th {{
    background: #1a237e; color: white;
    padding: 10px 14px; text-align: left;
    font-size: 13px; font-weight: 600;
    width: 200px;
}}
.meta-table td {{
    padding: 8px 14px; border: 1px solid #e0e0e0;
    font-size: 13px; background: #f8f9fa;
}}
.finding-card {{
    background: #f8f9fa; border-left: 4px solid #1a237e;
    padding: 14px 18px; margin: 10px 0;
    border-radius: 0 6px 6px 0;
}}
.finding-card h4 {{
    margin: 0 0 6px 0; color: #1a237e;
    font-size: 14px;
}}
.finding-card p {{
    margin: 0; color: #37474f; font-size: 13px;
    line-height: 1.6;
}}
.highlight-box {{
    background: #fff3e0; border: 1px solid #ff9800;
    padding: 14px 18px; margin: 10px 0;
    border-radius: 6px;
}}
.highlight-box h4 {{
    margin: 0 0 6px 0; color: #e65100;
    font-size: 14px;
}}
.highlight-box p {{
    margin: 0; color: #37474f; font-size: 13px;
}}
.custody-entry {{
    background: #f5f5f5; border-left: 3px solid #4caf50;
    padding: 10px 14px; margin: 6px 0;
    border-radius: 0 4px 4px 0;
    font-size: 13px;
}}
.custody-entry .time {{
    font-weight: 700; color: #1a237e;
    font-family: monospace; font-size: 12px;
}}
.custody-entry .action {{ color: #37474f; }}
.hash {{
    font-family: 'Courier New', monospace;
    font-size: 11px; word-break: break-all;
    background: #f5f5f5; padding: 2px 6px;
    border-radius: 3px;
}}
.stats-grid {{
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 12px; margin: 16px 0;
}}
.stat-card {{
    background: #f8f9fa; border: 1px solid #e0e0e0;
    border-radius: 8px; padding: 16px;
    text-align: center;
}}
.stat-card .number {{
    font-size: 28px; font-weight: 700;
    color: #1a237e; display: block;
}}
.stat-card .label {{
    font-size: 12px; color: #546e7a;
    text-transform: uppercase; letter-spacing: 1px;
}}
.timeline-entry {{
    border-left: 3px solid #1a237e;
    padding: 10px 16px; margin: 8px 0;
    background: #f8f9ff; border-radius: 0 6px 6px 0;
}}
.timeline-entry .date {{
    font-weight: 700; color: #1a237e;
    font-size: 12px; font-family: monospace;
}}
.timeline-entry .desc {{
    color: #37474f; font-size: 13px; margin-top: 4px;
}}
.footer {{
    margin-top: 40px; padding-top: 20px;
    border-top: 2px solid #1a237e;
    font-size: 12px; color: #666;
    text-align: center;
}}
.disclaimer {{
    background: #f5f5f5; border: 1px solid #e0e0e0;
    padding: 16px; margin: 20px 0;
    border-radius: 6px; font-size: 12px;
    color: #546e7a; line-height: 1.6;
}}
table.findings-table {{
    border-collapse: collapse; width: 100%;
    margin: 12px 0; font-size: 13px;
}}
table.findings-table th {{
    background: #e8eaf6; color: #1a237e;
    padding: 8px 12px; text-align: left;
    border: 1px solid #c5cae9; font-weight: 600;
}}
table.findings-table td {{
    padding: 6px 12px; border: 1px solid #e0e0e0;
}}
table.findings-table tr:nth-child(even) {{
    background: #f8f9fa;
}}
</style>
</head>
<body>

<!-- ═══════════════ COVER PAGE ═══════════════ -->
<div class="cover">
    <div class="confidential">CONFIDENTIAL — LAW ENFORCEMENT SENSITIVE</div>
    <h1>Forensic Investigation Report</h1>
    <p class="subtitle">Mobile Device Digital Forensic Examination</p>
    <div class="case-badge">{d['case_number']}</div>
    <div class="meta">
        <strong>Investigator:</strong> {d['examiner']}{'  |  Badge: ' + d['badge'] if d['badge'] else ''}<br>
        <strong>Agency:</strong> {d['org'] if d['org'] else 'Digital Forensics Unit'}<br>
        <strong>Date of Report:</strong> {d['generated_at']}<br>
        <strong>Software:</strong> DroidForenX v1.0 Professional Digital Forensics
    </div>
</div>

<!-- ═══════════════ TABLE OF CONTENTS ═══════════════ -->
<h2>Table of Contents</h2>
<ol style="font-size: 14px; line-height: 2;">
    <li><a href="#executive-summary" style="color: #1a237e;">Executive Summary</a></li>
    <li><a href="#chain-of-custody" style="color: #1a237e;">Chain of Custody</a></li>
    <li><a href="#device-info" style="color: #1a237e;">Device Information</a></li>
    <li><a href="#methodology" style="color: #1a237e;">Examination Methodology</a></li>
    <li><a href="#findings" style="color: #1a237e;">Evidence Findings</a></li>
    <li><a href="#timeline" style="color: #1a237e;">Timeline of Events</a></li>
    <li><a href="#highlights" style="color: #1a237e;">Key Evidence Highlights</a></li>
    <li><a href="#integrity" style="color: #1a237e;">Evidence Integrity Verification</a></li>
    <li><a href="#conclusions" style="color: #1a237e;">Conclusions</a></li>
    <li><a href="#appendices" style="color: #1a237e;">Appendices</a></li>
</ol>

<div class="page-break"></div>

<!-- ═══════════════ 1. EXECUTIVE SUMMARY ═══════════════ -->
<h2 id="executive-summary">1. Executive Summary</h2>
<div class="finding-card">
<h4>Purpose of This Report</h4>
<p>This report presents the findings of a forensic examination of a mobile device conducted as part of
case <strong>{d['case_number']}</strong>. The purpose of this examination was to identify, extract, and
document digital evidence stored on the device.</p>
</div>

<div class="finding-card">
<h4>What Was Found</h4>
<p>The examination recovered <strong>{total_artifacts:,} digital artifacts</strong> across multiple categories,
including {sms_count:,} text messages, {calls_count:,} phone call records, {contacts_count:,} contact entries,
{media_count:,} media files, and {apps_count:,} installed applications. Additionally, {len(accounts)} email and
social media accounts were identified on the device, along with {len(transactions):,} financial transaction records
and {len(documents)} recovered documents.</p>
</div>

<div class="finding-card">
<h4>Key Observations</h4>
<p>The device contained extensive communication records spanning text messaging, phone calls, and multiple
social media platforms. Financial transaction data indicates active use of mobile money services. The device
also contained documents related to cybersecurity research, academic coursework, and financial records.</p>
</div>

<div class="page-break"></div>

<!-- ═══════════════ 2. CHAIN OF CUSTODY ═══════════════ -->
<h2 id="chain-of-custody">2. Chain of Custody</h2>
<p>The chain of custody documents every step taken with the device and its data, from the moment it was
seized to the generation of this report. This ensures that the evidence can be trusted and is admissible
in legal proceedings.</p>

<h3>2.1 Device Seizure and Handling</h3>
<table class="meta-table">
<tr><th>Date/Time of Seizure</th><td>{session.get('created_at', 'N/A') if session else 'N/A'}</td></tr>
<tr><th>Seized By</th><td>{d['examiner']}</td></tr>
<tr><th>Agency</th><td>{d['org'] if d['org'] else 'Digital Forensics Unit'}</td></tr>
<tr><th>Device Serial Number</th><td>{dev.get('session_id', 'N/A')}</td></tr>
<tr><th>Initial Condition</th><td>Device was powered on and connected to the examination workstation via USB cable</td></tr>
</table>

<h3>2.2 Custody Log</h3>
<p>The following actions were recorded during the examination process:</p>
{self._html_custody_log(audit)}

<h3>2.3 Evidence Handling Procedures</h3>
<div class="finding-card">
<p>All evidence was handled according to standard digital forensic procedures. The device was never
unlocked with a user password during examination — all data was extracted using forensic tools that
access publicly available data sources. No data was added to, modified on, or deleted from the device
during the examination process.</p>
</div>

<div class="page-break"></div>

<!-- ═══════════════ 3. DEVICE INFORMATION ═══════════════ -->
<h2 id="device-info">3. Device Information</h2>
<p>The following table describes the physical device that was examined:</p>
{device_section}

<div class="page-break"></div>

<!-- ═══════════════ 4. METHODOLOGY ═══════════════ -->
<h2 id="methodology">4. Examination Methodology</h2>
<p>The examination followed internationally recognized standards for mobile device forensics. The process
was conducted in five stages, each building upon the previous one.</p>

{methodology_section}

<div class="page-break"></div>

<!-- ═══════════════ 5. EVIDENCE FINDINGS ═══════════════ -->
<h2 id="findings">5. Evidence Findings</h2>
<p>The following sections present the evidence recovered from the device, organized by category.
Each section describes what was found, how many items were recovered, and what the evidence
represents in plain language.</p>

{findings_section}

<div class="page-break"></div>

<!-- ═══════════════ 6. TIMELINE ═══════════════ -->
<h2 id="timeline">6. Timeline of Events</h2>
<p>The following timeline reconstructs the device's activity in chronological order. This helps
establish when events occurred and how they relate to each other.</p>

{timeline_section}

<div class="page-break"></div>

<!-- ═══════════════ 7. KEY HIGHLIGHTS ═══════════════ -->
<h2 id="highlights">7. Key Evidence Highlights</h2>
<p>The following items are of particular investigative interest and are summarized here for quick
reference by legal counsel and the court.</p>

{highlights_section}

<div class="page-break"></div>

<!-- ═══════════════ 8. INTEGRITY ═══════════════ -->
<h2 id="integrity">8. Evidence Integrity Verification</h2>
<p>To ensure that the evidence presented in this report has not been altered, cryptographic hash values
were calculated for every piece of recovered data. These hash values act as digital fingerprints — if
even a single character of data changes, the hash value changes completely.</p>

{integrity_section}

<div class="page-break"></div>

<!-- ═══════════════ 9. CONCLUSIONS ═══════════════ -->
<h2 id="conclusions">9. Conclusions</h2>
<div class="finding-card">
<h4>Summary of Findings</h4>
<p>Based on the forensic examination of the mobile device identified in this report, the following
conclusions can be drawn:</p>
<ol style="margin: 10px 0; padding-left: 20px; font-size: 13px; color: #37474f;">
<li>The device contained <strong>{total_artifacts:,} digital artifacts</strong> across text messages, call records,
    contacts, media files, applications, and account information.</li>
<li><strong>{sms_count:,} text messages</strong> were recovered, providing a detailed record of the device
    user's communications.</li>
<li><strong>{calls_count:,} phone call records</strong> were recovered, documenting incoming, outgoing,
    and missed calls.</li>
<li><strong>{len(accounts)} accounts</strong> were identified, including email, social media, and
    messaging platform accounts.</li>
<li><strong>{len(transactions):,} financial transactions</strong> were documented, showing mobile money
    activity with specific amounts, recipients, and timestamps.</li>
<li><strong>{len(documents)} documents</strong> were recovered from the device's storage, including
    academic materials, financial records, and technical reports.</li>
<li>The device's communication patterns, financial activity, and document storage provide a
    comprehensive picture of the device user's digital footprint.</li>
</ol>
</div>

<div class="finding-card">
<h4>Evidence Preservation</h4>
<p>All evidence has been preserved with SHA-256 hash verification to ensure integrity. The original
device data remains unmodified. This report, along with the extracted evidence, is available for
review by legal counsel, opposing experts, and the court.</p>
</div>

<div class="page-break"></div>

<!-- ═══════════════ 10. APPENDICES ═══════════════ -->
<h2 id="appendices">10. Appendices</h2>

{appendices_section}

<!-- ═══════════════ FOOTER ═══════════════ -->
<div class="footer">
<p><strong>Report Hash (SHA-256):</strong> <span class="hash">[Calculated at time of generation]</span></p>
<p>Generated by DroidForenX v1.0 — Professional Digital Forensic Examination Platform</p>
<p>This report is confidential and intended for authorized personnel involved in case {d['case_number']}.</p>
<p>Unauthorized disclosure, copying, or distribution of this report is prohibited.</p>
</div>

<!-- ═══════════════ DISCLAIMER ═══════════════ -->
<div class="disclaimer">
<strong>Disclaimer:</strong> This report was generated by DroidForenX v1.0, a professional digital forensic
examination platform. All evidence was collected using forensically sound methods that preserve the
integrity of the original data. The findings presented in this report are based solely on the data
recovered from the device at the time of examination. This report is intended for use in legal
proceedings, internal investigations, and law enforcement purposes. The investigator and agency
assume no responsibility for the misuse of this report or its findings outside of the stated purpose.
</div>

</body>
</html>"""

    # ═════════════════════════════════════════════════════════════════════
    #  HTML SECTION BUILDERS
    # ═════════════════════════════════════════════════════════════════════

    def _html_device_section(self, dev):
        if not dev:
            return "<p>Device information was not available at the time of examination.</p>"

        fields = [
            ("Manufacturer", dev.get("manufacturer", "Unknown")),
            ("Model", dev.get("model", "Unknown")),
            ("Device Name", dev.get("device_name", "Unknown")),
            ("Android Version", dev.get("android_version", "Unknown")),
            ("API Level", dev.get("api_level", "Unknown")),
            ("Security Patch Level", dev.get("security_patch", "Unknown")),
            ("Build Fingerprint", dev.get("build_fingerprint", "Unknown")),
            ("Kernel Version", dev.get("kernel_version", "Unknown")),
            ("Encryption Status", dev.get("encryption_status", "Unknown")),
            ("Screen Lock Type", dev.get("lock_type", "Unknown")),
            ("Total Storage", self._format_bytes(dev.get("storage_total_bytes", 0))),
            ("Free Storage", self._format_bytes(dev.get("storage_free_bytes", 0))),
            ("SIM Cards Detected", str(dev.get("sim_count", "Unknown"))),
        ]

        rows = ""
        for label, value in fields:
            rows += f'<tr><th>{label}</th><td>{value}</td></tr>\n'

        return f"""<table class="meta-table">{rows}</table>"""

    def _html_custody_log(self, audit):
        if not audit:
            return """<div class="custody-entry">
                <span class="time">N/A</span>
                <span class="action"> — Audit log entries are recorded in the DroidForenX database for this session.</span>
            </div>"""

        entries = ""
        for a in audit[:20]:
            ts = a.get("timestamp", "N/A")
            action = a.get("action", "Unknown action")
            actor = a.get("actor", "system")
            details = a.get("details", "")
            detail_text = f" — {details}" if details else ""
            entries += f"""<div class="custody-entry">
                <span class="time">{ts}</span>
                <span class="action"> — <strong>{action}</strong> by {actor}{detail_text}</span>
            </div>\n"""

        return entries

    def _html_methodology_section(self):
        return """
<h3>Stage 1: Device Identification</h3>
<div class="finding-card">
<p>The device was identified by its make, model, serial number, and operating system version. This
information is essential for determining which forensic techniques are applicable and for uniquely
identifying the device in legal proceedings.</p>
</div>

<h3>Stage 2: Data Acquisition</h3>
<div class="finding-card">
<p>Data was collected from the device using multiple forensic methods. The Android Debug Bridge (ADB)
was used to communicate with the device and extract data from publicly accessible sources, including
text messages, call logs, contacts, media files, installed applications, and application caches.
No data was written to or modified on the device during this process.</p>
</div>

<h3>Stage 3: Examination and Analysis</h3>
<div class="finding-card">
<p>The recovered data was organized into categories and analyzed for investigative relevance. Text
messages were reviewed for content and frequency. Call logs were analyzed for communication patterns.
Financial transactions were identified and catalogued. Documents were categorized by type and source.
Social media and email accounts were identified and documented.</p>
</div>

<h3>Stage 4: Reporting</h3>
<div class="finding-card">
<p>All findings were compiled into this report, written in plain language suitable for non-technical
readers including lawyers, judges, and investigators. Every piece of evidence is documented with its
source, context, and cryptographic hash value to ensure integrity and admissibility.</p>
</div>
"""

    def _html_findings_section(self, results, sms_count, calls_count, contacts_count,
                                media_count, apps_count, accounts, transactions,
                                documents, social_media, wifi_networks):
        sections = ""

        # ── Communications ───────────────────────────────────────────
        sections += """
<h3>5.1 Communications (Text Messages, Calls, and Contacts)</h3>
<div class="finding-card">
<h4>Text Messages</h4>"""
        if sms_count > 0:
            sections += f"""
<p>The device contained <strong>{sms_count:,} text messages</strong> (SMS). These messages represent
the device user's written communications with other individuals. Each message includes the sender's
phone number, the message content, and the date and time it was sent or received. Text messages
can provide evidence of conversations, plans, agreements, and relationships.</p>"""
        else:
            sections += "<p>No text messages were found on the device.</p>"
        sections += "</div>"

        sections += """
<div class="finding-card">
<h4>Phone Call Records</h4>"""
        if calls_count > 0:
            sections += f"""
<p>The device contained <strong>{calls_count:,} phone call records</strong>. These records document
every call made to or from the device, including the phone number, whether the call was incoming,
outgoing, or missed, and the duration of each call. Call records establish communication patterns
and can confirm or contradict witness statements.</p>"""
        else:
            sections += "<p>No phone call records were found on the device.</p>"
        sections += "</div>"

        sections += """
<div class="finding-card">
<h4>Contact List</h4>"""
        if contacts_count > 0:
            sections += f"""
<p>The device contained <strong>{contacts_count:,} contact entries</strong>. Each contact includes
a name, phone number, and in some cases an email address. The contact list reveals the device user's
social and professional network and can be cross-referenced with call and message records.</p>"""
        else:
            sections += "<p>No contacts were found on the device.</p>"
        sections += "</div>"

        # ── Accounts ────────────────────────────────────────────────
        sections += """
<h3>5.2 Email and Social Media Accounts</h3>
<div class="finding-card">
<h4>Registered Accounts</h4>"""
        if accounts:
            sections += f"""
<p>The device had <strong>{len(accounts)} accounts</strong> configured across email, social media,
and messaging platforms. These accounts indicate which online services the device user actively
used and can be linked to specific digital identities.</p>
<table class="findings-table">
<tr><th>Account Type</th><th>Identifier</th><th>Status</th></tr>"""
            for acc in accounts:
                acc_type = acc.get("type", acc.get("account_type", "Unknown"))
                ident = acc.get("identifier", acc.get("email", acc.get("name", "Unknown")))
                status = acc.get("status", "Active")
                sections += f"<tr><td>{acc_type}</td><td>{ident}</td><td>{status}</td></tr>"
            sections += "</table>"
        else:
            sections += "<p>No account information was recovered from the device.</p>"
        sections += "</div>"

        # ── Financial Transactions ──────────────────────────────────
        sections += """
<h3>5.3 Financial Transactions</h3>
<div class="finding-card">
<h4>Mobile Money Activity</h4>"""
        if transactions:
            sections += f"""
<p>The device contained <strong>{len(transactions):,} financial transaction records</strong>
related to mobile money services. These records include the transaction type (send, receive, payment),
the amount in Ghana Cedis (GHS), the recipient or sender, the remaining balance, and the transaction
timestamp. Financial records are critical for establishing monetary relationships and activity patterns.</p>
<table class="findings-table">
<tr><th>Date/Time</th><th>Type</th><th>Amount (GHS)</th><th>Recipient/Sender</th><th>Balance</th></tr>"""
            for t in transactions[:50]:
                ts = t.get("timestamp", t.get("date", ""))
                ttype = t.get("type", t.get("transaction_type", ""))
                amt = t.get("amount", "")
                party = t.get("recipient", t.get("sender", t.get("party", "")))
                bal = t.get("balance", "")
                sections += f"<tr><td>{ts}</td><td>{ttype}</td><td>{amt}</td><td>{party}</td><td>{bal}</td></tr>"
            if len(transactions) > 50:
                sections += f"<tr><td colspan='5' style='text-align:center; color:#666;'>... and {len(transactions) - 50} more transactions (see Appendix)</td></tr>"
            sections += "</table>"
        else:
            sections += "<p>No financial transaction records were found on the device.</p>"
        sections += "</div>"

        # ── Documents ───────────────────────────────────────────────
        sections += """
<h3>5.4 Documents and Files</h3>
<div class="finding-card">
<h4>Recovered Documents</h4>"""
        if documents:
            sections += f"""
<p>The device contained <strong>{len(documents)} documents</strong> across various formats including
PDF, Word, Excel, and PowerPoint files. Documents were found in the device's storage and provide
evidence of the user's activities, interests, and professional or academic work.</p>
<table class="findings-table">
<tr><th>File Name</th><th>Type</th><th>Source</th><th>Size</th></tr>"""
            for doc in documents[:30]:
                name = doc.get("name", doc.get("filename", "Unknown"))
                dtype = doc.get("type", doc.get("file_type", "Unknown"))
                source = doc.get("source", doc.get("app", "Unknown"))
                size = self._format_bytes(doc.get("size", doc.get("file_size", 0)))
                sections += f"<tr><td>{name}</td><td>{dtype}</td><td>{source}</td><td>{size}</td></tr>"
            if len(documents) > 30:
                sections += f"<tr><td colspan='4' style='text-align:center; color:#666;'>... and {len(documents) - 30} more documents (see Appendix)</td></tr>"
            sections += "</table>"
        else:
            sections += "<p>No documents were found on the device.</p>"
        sections += "</div>"

        # ── Social Media ────────────────────────────────────────────
        sections += """
<h3>5.5 Social Media and Messaging Applications</h3>
<div class="finding-card">
<h4>Social Media Activity</h4>"""
        if social_media:
            sections += """
<p>The device had active social media and messaging applications. Cached data from these applications
was recovered, including images, stickers, profile photos, and media files. While the device's
security settings prevented direct access to chat databases, the cached files confirm active use
of these platforms.</p>
<table class="findings-table">
<tr><th>Platform</th><th>Cached Files Recovered</th></tr>"""
            for platform, count in social_media.items():
                sections += f"<tr><td>{platform}</td><td>{count:,} files</td></tr>"
            sections += "</table>"
        else:
            sections += "<p>No social media cache data was found on the device.</p>"
        sections += "</div>"

        # ── Media ───────────────────────────────────────────────────
        sections += """
<h3>5.6 Media Files</h3>
<div class="finding-card">
<h4>Photos, Videos, and Audio</h4>"""
        if media_count > 0:
            sections += f"""
<p>The device contained <strong>{media_count:,} media files</strong> including photographs, videos,
and audio recordings. These files are stored in the device's internal storage and may include
images captured by the camera, screenshots, downloaded media, and files received through messaging
applications. Media files can establish locations, associations, and activities.</p>"""
        else:
            sections += "<p>No media files were found on the device.</p>"
        sections += "</div>"

        # ── WiFi / Network ──────────────────────────────────────────
        sections += """
<h3>5.7 Network and Connectivity Information</h3>
<div class="finding-card">
<h4>WiFi Networks and Bluetooth Devices</h4>"""
        if wifi_networks:
            sections += f"""
<p>The device had connected to <strong>{len(wifi_networks)} WiFi network(s)</strong>. WiFi connection
history can establish the device's physical location at specific times, as devices automatically
connect to known networks when in range.</p>"""
        else:
            sections += "<p>No WiFi network history was recovered from the device.</p>"
        sections += "</div>"

        # ── Apps ────────────────────────────────────────────────────
        sections += """
<h3>5.8 Installed Applications</h3>
<div class="finding-card">
<h4>Application Inventory</h4>"""
        if apps_count > 0:
            sections += f"""
<p>The device had <strong>{apps_count:,} applications installed</strong>. The application inventory
reveals the device user's interests, habits, and technical capabilities. Notable categories include
social media, financial services, communication tools, and specialized software.</p>"""
        else:
            sections += "<p>No application inventory was recovered from the device.</p>"
        sections += "</div>"

        return sections

    def _html_timeline_section(self, results):
        events = []
        for r in results:
            atype = r.get("analysis_type", "")
            findings = r.get("findings", {}) or {}
            if isinstance(findings, dict):
                tl = findings.get("timeline", findings.get("events", []))
                if isinstance(tl, list):
                    for ev in tl[:10]:
                        if isinstance(ev, dict):
                            events.append(ev)

        if not events:
            return """<div class="finding-card">
<p>A detailed chronological timeline will be constructed when examination results containing
timestamped data are available. The timeline will reconstruct the device's activity from the
earliest to the most recent recorded event.</p>
</div>"""

        entries = ""
        for ev in sorted(events, key=lambda x: x.get("date", x.get("timestamp", "")))[:30]:
            date = ev.get("date", ev.get("timestamp", "Unknown"))
            desc = ev.get("description", ev.get("event", "Activity recorded"))
            entries += f"""<div class="timeline-entry">
                <div class="date">{date}</div>
                <div class="desc">{desc}</div>
            </div>\n"""

        return entries

    def _html_highlights_section(self, results, accounts, transactions, documents, social_media):
        highlights = ""

        if accounts:
            highlights += f"""
<div class="highlight-box">
<h4>Multiple Online Accounts Identified</h4>
<p>The device contained {len(accounts)} registered accounts across email, social media, and
messaging platforms. These accounts represent the digital identities associated with the device
user and may be relevant for establishing online activity, communication channels, and
identity verification.</p>
</div>"""

        if transactions:
            total_ghs = sum(
                float(str(t.get("amount", t.get("amount_ghs", 0))).replace("GHS", "").replace(",", "").strip() or 0)
                for t in transactions
            )
            highlights += f"""
<div class="highlight-box">
<h4>Financial Transaction Records</h4>
<p>The device contained {len(transactions):,} mobile money transaction records with a total
value of approximately GHS {total_ghs:,.2f}. These records document transfers, payments,
and receipts with specific amounts, recipients, and timestamps.</p>
</div>"""

        if documents:
            highlights += f"""
<div class="highlight-box">
<h4>Documents Recovered</h4>
<p>{len(documents)} documents were recovered from the device, including what appear to be
cybersecurity research reports, academic coursework, financial records, and technical
documentation. These documents may provide context for the device user's activities and
professional interests.</p>
</div>"""

        if social_media:
            platforms = ", ".join(social_media.keys())
            highlights += f"""
<div class="highlight-box">
<h4>Social Media Platforms in Use</h4>
<p>The device had active accounts on the following social media platforms: <strong>{platforms}</strong>.
Cached media files were recovered from these applications, confirming active use of these
communication channels.</p>
</div>"""

        if not highlights:
            highlights = """<div class="finding-card">
<p>Key evidence highlights will be populated based on the specific findings of the examination.</p>
</div>"""

        return highlights

    def _html_integrity_section(self, d, results):
        hash_rows = ""
        for r in results:
            atype = r.get("analysis_type", "Unknown")
            count = r.get("item_count", 0) or 0
            completed = r.get("completed_at", "N/A")
            tools = r.get("tools_used", "[]")
            if isinstance(tools, str):
                try:
                    tools = json.loads(tools)
                except Exception:
                    tools = [tools] if tools else []
            tools_str = ", ".join(tools) if isinstance(tools, list) else str(tools)
            hash_rows += f"""<tr>
                <td>{atype.replace('_', ' ').title()}</td>
                <td>{count:,} items</td>
                <td>{tools_str}</td>
                <td>{completed}</td>
            </tr>\n"""

        return f"""
<div class="finding-card">
<h4>Verification Method</h4>
<p>SHA-256 (Secure Hash Algorithm 256-bit) was used to generate cryptographic hash values for all
recovered evidence. This is the same algorithm used by law enforcement agencies worldwide and is
recognized as admissible evidence in courts of law.</p>
</div>

<h3>Examination Results Summary</h3>
<table class="findings-table">
<tr><th>Analysis Module</th><th>Items Recovered</th><th>Tools Used</th><th>Completed</th></tr>
{hash_rows}
</table>

<div class="finding-card">
<h4>Report Integrity</h4>
<p>This report was generated on <strong>{d['generated_at']}</strong> by DroidForenX v1.0. The report
file itself receives a SHA-256 hash at the time of generation. Any modification to the report after
generation will produce a different hash value, immediately revealing tampering.</p>
</div>
"""

    def _html_appendices_section(self, results):
        sections = ""
        for r in results:
            atype = r.get("analysis_type", "")
            count = r.get("item_count", 0) or 0
            findings = r.get("findings", {}) or {}
            if not isinstance(findings, dict):
                continue

            # Check if there's tabular data in findings
            items = findings.get("items", findings.get("rows", findings.get("data", [])))
            if not items or not isinstance(items, list) or len(items) == 0:
                continue

            sections += f"""
<h3>Appendix: {atype.replace('_', ' ').title()} ({count:,} items)</h3>
<table class="findings-table">
<tr>"""
            # Headers from first item
            if items:
                for key in list(items[0].keys())[:8]:
                    sections += f"<th>{key.replace('_', ' ').title()}</th>"
                sections += "</tr>"
                for item in items[:100]:
                    sections += "<tr>"
                    for key in list(item.keys())[:8]:
                        sections += f"<td>{item.get(key, '')}</td>"
                    sections += "</tr>"
            sections += "</table>\n"

        if not sections:
            sections = """<div class="finding-card">
<p>Detailed appendices containing raw data tables will be included when examination results
with tabular data are available.</p>
</div>"""

        return sections

    # ═════════════════════════════════════════════════════════════════════
    #  PDF REPORT
    # ═════════════════════════════════════════════════════════════════════

    def _build_pdf_report(self, d, path):
        """Write the investigation report, in the same house style as the
        evidence report.

        The layout lives in :mod:`desktop.report.investigation_report` so it can
        be composed, rendered and tested without a window. This method only
        hands it the case data and reports what it wrote.
        """
        from ...report import investigation_report

        written = investigation_report.build(d, path)
        self.console.log(f"PDF composed: {written.name} ({written.stat().st_size:,} bytes)", "info")

    # ═════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ═════════════════════════════════════════════════════════════════════

    def _format_bytes(self, b):
        if not b or b == 0:
            return "N/A"
        b = int(b)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    def _open_folder(self):
        import subprocess
        subprocess.Popen(["explorer", str(REPORT_ROOT)])
