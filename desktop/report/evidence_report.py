"""
Evidence Report PDF.

This module produces the *authoritative* case document. It is built only from
what the evidence store recorded - artifacts, hashes, custody events, per-loader
statuses and earned verification tiers. It reads no AI output and contains none,
by design: AI narrative belongs in a separate document that is never merged
into the evidence chain.

Design rules, because a forensic report is read by people who will challenge
it:
  * Colour carries meaning and nothing else. Green/amber/grey/red map to the
    acquisition status vocabulary. No decorative colour.
  * SHA-256 values, serial numbers and paths are set in a monospace face so
    they align and can be compared by eye.
  * Every page carries case id, page x of y, tool version and generation time.
  * The report states plainly what could NOT be acquired, and why.

Usage:
    python -m desktop.report.evidence_report CASE-BASELINE-001 [out.pdf]
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

# Allow both `python -m desktop.report.evidence_report` and direct execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from desktop.core import evidence_store  # noqa: E402
from desktop.core.evidence_store import AcquisitionStatus, VerificationTier  # noqa: E402

# --- palette -------------------------------------------------------------
INK = (18, 24, 33)
MUTED = (94, 106, 122)
RULE = (214, 219, 226)
ACCENT = (201, 138, 20)        # amber, matches the application accent
BAND = (245, 247, 249)
PAPER = (255, 255, 255)

STATUS_COLOUR = {
    AcquisitionStatus.ACQUIRED: (16, 145, 105),
    AcquisitionStatus.PARTIAL: (217, 119, 6),
    AcquisitionStatus.RECOVERED: (16, 145, 105),
    AcquisitionStatus.DELETED_RECOVERABLE: (217, 119, 6),
    AcquisitionStatus.DELETED_UNRECOVERABLE: (107, 114, 128),
    AcquisitionStatus.NOT_ACCESSIBLE: (107, 114, 128),
    AcquisitionStatus.NOT_PRESENT: (107, 114, 128),
    AcquisitionStatus.FAILED: (220, 38, 38),
}


def _mono(text: str, size: float = 7.0) -> str:
    """Escape a hash/serial for the courier face."""
    return str(text).replace("(", r"\(").replace(")", r"\)").replace("\\", r"\\")


class EvidenceReportPDF(FPDF):
    def __init__(self, case_id: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.case_id = case_id
        self.set_auto_page_break(auto=True, margin=20)
        self.alias_nb_pages()
        self.generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    # -- chrome ----------------------------------------------------------
    def header(self):
        if self.page_no() == 1:
            return  # the cover carries its own masthead
        w = self.w - self.l_margin - self.r_margin
        self.set_font("Helvetica", "B", 7.5)
        self.set_text_color(*MUTED)
        self.cell(w, 6, "EVIDENCE REPORT", align="R")
        self.ln(6)
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        w = self.w - self.l_margin - self.r_margin
        self.set_y(-15)
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y() - 2, self.w - self.r_margin, self.get_y() - 2)
        # three explicit thirds - cell(0, ...) stacks them on one another
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MUTED)
        self.cell(w / 3, 5, self.case_id, align="L")
        self.cell(w / 3, 5, f"{evidence_store.TOOL_NAME} {evidence_store.TOOL_VERSION}", align="C")
        self.cell(w / 3, 5, f"Page {self.page_no()} of {{nb}}", align="R")
        self.set_y(-10)
        self.set_font("Helvetica", "", 6.5)
        self.cell(0, 4, f"Generated {self.generated} - no AI-generated content in this document", align="L")

    # -- building blocks --------------------------------------------------
    def section(self, number: str, title: str):
        self.ln(3)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*INK)
        self.cell(0, 7, f"{number}. {title}")
        self.ln(7)
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.5)
        self.line(self.l_margin, self.get_y(), self.l_margin + 18, self.get_y())
        self.ln(4)

    def kv_table(self, rows, key_w=42):
        """Two-column key/value table with hairline rules."""
        val_w = self.w - self.l_margin - self.r_margin - key_w
        for key, value in rows:
            # cell() does not advance x across a row, and multi_cell() parks x
            # at the right margin, so both halves are positioned explicitly.
            x0, y0 = self.get_x(), self.get_y()
            self.set_xy(x0, y0)
            self.set_font("Helvetica", "B", 8.5)
            self.set_text_color(*INK)
            self.cell(key_w, 5.2, str(key)[:34])
            row_bottom = y0 + 5.2
            self.set_xy(x0 + key_w, y0)
            self.set_font("Helvetica", "", 8.5)
            self.set_text_color(*MUTED)
            self.multi_cell(val_w, 5.2, str(value)[:110])
            # Row height is whatever the taller of the two cells needed.
            row_bottom = max(row_bottom, self.get_y())
            self.set_xy(x0, row_bottom)
            self.set_draw_color(*RULE)
            self.set_line_width(0.15)
            self.line(self.l_margin, row_bottom, self.w - self.r_margin, row_bottom)

    def table_header(self, widths, labels):
        x0, y0 = self.get_x(), self.get_y()
        self.set_font("Helvetica", "B", 7.5)
        self.set_fill_color(*BAND)
        self.set_text_color(*INK)
        for w, lab in zip(widths, labels):
            self.set_xy(x0, y0)
            self.cell(w, 6, lab, fill=True)
            x0 += w
        self.set_xy(self.l_margin, y0 + 6)
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())

    def table_rule(self):
        y = self.get_y()
        self.set_draw_color(*RULE)
        self.set_line_width(0.15)
        self.line(self.l_margin, y, self.w - self.r_margin, y)


def _load(case_id: str) -> dict:
    cdir = evidence_store.case_dir(case_id)
    meta_path = cdir / "case.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    sums_path = cdir / "manifests" / "SHA256SUMS"
    hashes = []
    if sums_path.exists():
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, _, path = line.partition("  ")
                hashes.append((digest.strip(), path.strip()))
    return {
        "meta": meta,
        "hashes": hashes,
        "custody": evidence_store.read_custody(case_id),
        # status.jsonl is append-only and holds one row per loader per run, so a
        # re-run appends rather than replaces. Report the current state of each
        # loader (latest row wins) instead of every historical row, otherwise the
        # counts and the status table describe the run count, not the device.
        "statuses": _latest_status_per_loader(evidence_store.read_status(case_id)),
        "verifications": evidence_store.read_verification(case_id),
    }


def _latest_status_per_loader(statuses: list[dict]) -> list[dict]:
    """Collapse re-appended status rows to one row per loader (most recent wins)."""
    latest: dict[str, dict] = {}
    for row in statuses:
        latest[row["loader"]] = row  # append-only file, so last write is current
    return sorted(latest.values(), key=lambda r: r["loader"])


def build(case_id: str, output: Path | None = None) -> Path:
    data = _load(case_id)
    meta = data["meta"]
    pdf = EvidenceReportPDF(case_id)

    width = pdf.w - pdf.l_margin - pdf.r_margin

    # ---------------- cover ----------------
    pdf.add_page()
    # cell() does not advance y (ln() does), and cell(0, ...) parks x at the
    # right margin, so every cover line gets an explicit x and an explicit ln.
    y = 26
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*INK)
    pdf.cell(width, 10, "EVIDENCE REPORT")
    y += 10

    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED)
    pdf.cell(width, 6, "Android logical acquisition and examination")
    y += 6 + 6  # one line height, then breathing room above the rule

    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(1.2)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_xy(pdf.l_margin, y + 10)

    pdf.kv_table([
        ("Case identifier", case_id),
        ("Examiner", meta.get("operator", "not recorded")),
        ("Case opened", meta.get("opened_at", "not recorded")),
        ("Device serial", meta.get("device_serial", "not recorded")),
        ("Report generated", pdf.generated),
        ("Tool", f"{evidence_store.TOOL_NAME} {evidence_store.TOOL_VERSION}"),
    ])
    pdf.ln(8)

    # honest scope box
    acquired = [s for s in data["statuses"] if s["status"] in AcquisitionStatus.SUCCESSFUL]
    denied = [s for s in data["statuses"] if s["status"] == AcquisitionStatus.NOT_ACCESSIBLE]
    verified = [v for v in data["verifications"]
                if v["tier"] == VerificationTier.T1_DEVICE_VERIFIED]
    loaders = {v["loader"] for v in data["verifications"]} | {s["loader"] for s in data["statuses"]}

    pdf.set_fill_color(*BAND)
    box_h = 34
    box_y = pdf.get_y()
    pdf.rect(pdf.l_margin, box_y, width, box_h, style="F")
    pdf.set_xy(pdf.l_margin + 5, box_y + 4)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*INK)
    # Explicit ln(6): cell() alone does not advance y, so the heading would
    # print on top of the paragraph below it.
    pdf.cell(width - 10, 6, "SCOPE OF THIS REPORT")
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    total = len(loaders) or 1
    pdf.multi_cell(width - 10, 4.4,
                   f"{len(verified)} of {total} loaders device-verified. "
                   f"{len(acquired)} reported {AcquisitionStatus.ACQUIRED}, "
                   f"{len(denied)} reported {AcquisitionStatus.NOT_ACCESSIBLE} - "
                   f"those artifacts were NOT acquired and no finding is claimed for them. "
                   f"This document contains no AI-generated content.")
    pdf.set_xy(pdf.l_margin, box_y + box_h + 4)

    # ---------------- 1. case ----------------
    pdf.add_page()
    pdf.section("1", "Case Identification")
    pdf.kv_table([
        ("Case identifier", case_id),
        ("Examiner", meta.get("operator", "not recorded")),
        ("Opened at", meta.get("opened_at", "not recorded")),
        ("Device serial", meta.get("device_serial", "not recorded")),
        ("Notes", meta.get("notes", "-")),
        ("Evidence root", evidence_store.EVIDENCE_ROOT),
    ])

    # ---------------- 2. acquisition status ----------------
    pdf.section("2", "Acquisition Status")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(width, 4.5,
                   "Every loader the tool attempted, and what it actually achieved. "
                   "A loader that recovered nothing is reported as NOT_PRESENT, never "
                   "as a success.")
    pdf.ln(3)

    widths = [7, 34, 30, 26, width - 97]
    pdf.table_header(widths, ["#", "Loader", "Status", "Tier", "Detail"])
    # Status rows written by a baseline run predate the verification ledger and
    # carry no tier, so fall back to the loader's earned tier.
    earned_tier = {v["loader"]: v["tier"] for v in data["verifications"]}
    for i, s in enumerate(data["statuses"], 1):
        colour = STATUS_COLOUR.get(s["status"], MUTED)
        tier = s.get("verification_tier") or earned_tier.get(s["loader"], "")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*INK)
        pdf.cell(widths[0], 5.4, str(i))
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(widths[1], 5.4, s["loader"][:24])
        pdf.set_text_color(*colour)
        pdf.set_font("Helvetica", "B", 7.6)
        pdf.cell(widths[2], 5.4, s["status"][:22])
        pdf.set_font("Courier", "", 6.6)
        pdf.set_text_color(*MUTED)
        pdf.cell(widths[3], 5.4, str(tier)[:20])
        pdf.set_font("Helvetica", "", 7.2)
        detail = (s.get("detail", "") or "")
        # Truncate to what the column can actually hold; a hard [:74] slice
        # ran past the right margin and printed a half-word.
        if len(detail) > 64:
            # ASCII only: the built-in core fonts are latin-1 encoded.
            detail = detail[:61].rstrip(" ,;.") + "..."
        pdf.cell(widths[4], 5.4, detail)
        pdf.ln(5.4)
        if i % 2 == 0:
            pdf.set_draw_color(*RULE)
            pdf.set_line_width(0.1)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.table_rule()

    # ---------------- 3. verification tiers ----------------
    if data["verifications"]:
        pdf.add_page()
        pdf.section("3", "Verification Tiers")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(width, 4.5,
                       "A tier is a claim that a loader works. Tiers are never declared "
                       "in a table - each one is earned and the evidence for the claim is "
                       "recorded with it. A loader with no record is T3_UNTESTED.")
        pdf.ln(3)
        vw = [34, 28, width - 62]
        pdf.table_header(vw, ["Loader", "Tier", "Evidence recorded with the claim"])
        for v in data["verifications"]:
            pdf.set_font("Helvetica", "B", 7.8)
            pdf.set_text_color(*INK)
            pdf.cell(vw[0], 5.2, v["loader"][:22])
            pdf.set_font("Courier", "B", 7)
            pdf.set_text_color(*ACCENT)
            pdf.cell(vw[1], 5.2, v["tier"][:18])
            pdf.set_font("Helvetica", "", 6.6)
            pdf.set_text_color(*MUTED)
            pdf.cell(vw[2], 5.2, (v.get("evidence", "") or "")[:120])
            pdf.ln(5.2)
        pdf.table_rule()

    # ---------------- 4. evidence integrity ----------------
    pdf.section("4", "Evidence Integrity")
    if data["hashes"]:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(width, 4.5,
                       "SHA-256 of every artifact, computed at the moment of acquisition. "
                       "Artifacts in acquired/ are never modified or deleted by analysis code.")
        pdf.ln(3)
        hw = [width - 52, 52]
        pdf.table_header(hw, ["Artifact", "SHA-256"])
        for digest, path in data["hashes"]:
            pdf.set_font("Helvetica", "", 7.2)
            pdf.set_text_color(*INK)
            pdf.cell(hw[0], 5, path[-58:])
            pdf.set_font("Courier", "", 6.6)
            pdf.set_text_color(*MUTED)
            pdf.cell(hw[1], 5, _mono(digest[:40]))
            pdf.ln(5)
        pdf.table_rule()
    else:
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(width, 5,
                       "No artifacts were acquired in this case, so no artifact manifest "
                       "exists. This is recorded rather than omitted: an empty manifest is "
                       "a fact about the examination.")

    # ---------------- 5. chain of custody ----------------
    pdf.add_page()
    pdf.section("5", "Chain of Custody")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(width, 4.5,
                   "Append-only. Every entry is written when the action occurs; no entry "
                   "is edited or removed.")
    pdf.ln(3)
    cw = [26, 20, width - 46, 0]
    cw[3] = 0
    widths = [26, 20, 22, width - 68]
    pdf.table_header(widths, ["Timestamp (UTC)", "Action", "Actor", "Detail"])
    for e in data["custody"]:
        pdf.set_font("Courier", "", 6.6)
        pdf.set_text_color(*MUTED)
        pdf.cell(widths[0], 5, e.get("timestamp", "")[:16])
        pdf.set_font("Helvetica", "B", 7.2)
        pdf.set_text_color(*INK)
        pdf.cell(widths[1], 5, e.get("action", "")[:14])
        pdf.set_font("Helvetica", "", 7.2)
        pdf.cell(widths[2], 5, e.get("actor", "")[:18])
        pdf.set_text_color(*MUTED)
        pdf.set_font("Helvetica", "", 6.8)
        pdf.cell(widths[3], 5, (e.get("detail", "") or "")[:96])
        pdf.ln(5)
    pdf.table_rule()

    # ---------------- 6. scope and limitations ----------------
    pdf.section("6", "Scope and Limitations")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*INK)
    limits = [
        "App-private application data was not acquired. Android restricts /data/data/<package> "
        "to the owning application and to root; the examined device was not rooted.",
        "Social media, messaging, browser history, saved Wi-Fi keys and mobile-money data were "
        "not acquired, for the reason above.",
        "This was a logical acquisition, not a full flash image. Data never written to "
        "accessible storage was not recovered.",
        "Modern messaging applications encrypt their local databases with a keystore-derived "
        "key; such data may remain unreadable even with root.",
        "Device file-based encryption was active. With the screen locked, encrypted user data "
        "yields little.",
    ]
    for item in limits:
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*INK)
        pdf.multi_cell(width - 6, 4.6, "-  " + item)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*INK)
    pdf.multi_cell(width, 5,
                   "No statement in this report should be read as asserting the presence or "
                   "absence of any artifact that the tool reported as NOT_ACCESSIBLE.")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*ACCENT)
    pdf.multi_cell(width, 5,
                   "This report contains no AI-generated content. Any machine-generated "
                   "assessment is issued as a separate document, labelled as such, and does "
                   "not form part of the evidence.")

    # ---------------- 7. report integrity ----------------
    pdf.section("7", "Report Integrity")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(width, 4.5,
                   "Once this document is written, its SHA-256 is computed and recorded "
                   "alongside it in a sidecar file of the same name. The hash cannot be "
                   "printed inside the report it describes, because altering the report to "
                   "carry the hash would change the hash. The sidecar is therefore the "
                   "integrity record, and it must be transferred with the report.")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*INK)
    pdf.multi_cell(width, 5,
                   "To verify: recompute SHA-256 over the exported PDF and compare it with "
                   "the value in the accompanying .sha256 file.")
    pdf.ln(4)
    pdf.set_font("Courier", "B", 8)
    pdf.set_text_color(*ACCENT)
    pdf.multi_cell(width, 5, _mono("certutil -hashfile <report>.pdf SHA256"))

    out = output or (Path.cwd() / "reports" / f"{case_id}_evidence_report.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out))

    # Sidecar integrity record - written after the PDF is final.
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    sidecar = out.with_suffix(out.suffix + ".sha256")
    sidecar.write_text(
        f"{digest}  {out.name}\n"
        f"# DroidForenX report integrity record\n"
        f"# case={case_id}\n"
        f"# generated={pdf.generated}\n"
        f"# verify: certutil -hashfile \"{out.name}\" SHA256\n",
        encoding="utf-8",
    )
    evidence_store.log_custody(case_id, "REPORT", evidence_store.TOOL_NAME,
                               f"evidence report exported: {out.name} sha256={digest[:16]}...")
    return out


def main(argv):
    if len(argv) < 2:
        print("Usage: python -m desktop.report.evidence_report <case_id> [output.pdf]")
        return 2
    case_id = argv[1]
    out = Path(argv[2]) if len(argv) > 2 else None
    path = build(case_id, out)
    print(f"Evidence report written: {path}")
    print(f"  size   : {path.stat().st_size:,} bytes")
    print(f"  sha256 : {hashlib.sha256(path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))