"""The Investigation Report PDF: the document a lawyer, a magistrate or a
case officer actually reads.

This is the narrative companion to :mod:`desktop.report.evidence_report`. The
evidence report is the ledger -- every artifact, every hash, every loader status.
This document is the *case file*: what device was examined, who examined it, what
was recovered, in what quantity, and what it means. Same house rules:

  * Colour carries meaning. Green / amber / red are acquisition outcomes; amber
    is also the structural accent the application already uses. Nothing is
    coloured for decoration.
  * SHA-256 values, timestamps and build strings are set in Courier, so they
    align in a column and can be compared by eye.
  * Every body page carries the case number, the tool version and
    "Page n of m". Front matter uses roman numerals, as a bound report does.
  * What could not be acquired is stated where it was expected, not omitted.

Layout model
------------
The document is composed up to four times. Pass one draws the table of
contents with placeholder page numbers and records where each section landed;
pass two prints those numbers; the loop stops when a pass reproduces the
previous pass exactly. The contents page therefore never lies and never shifts
the pages it is numbering -- which is why the number of contents pages is
computed from the *planned* section list, not from what the renderer meets.

Usage:
    python -m desktop.report.investigation_report <data.json> out.pdf
"""

from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path
from fpdf import FPDF

try:  # the version is the tool's, and it is printed on every page
    from desktop import __version__ as TOOL_VERSION
except Exception:  # pragma: no cover - direct execution outside the package
    TOOL_VERSION = "1.0"

TOOL_NAME = "DroidForenX"

# --- palette -------------------------------------------------------------
# Shares the evidence report's palette on purpose: two documents about one case
# should not look like they came from two different laboratories.
INK = (18, 24, 33)
MUTED = (94, 106, 122)
QUIET = (133, 143, 156)
RULE = (214, 219, 226)
RULE_SOFT = (233, 236, 241)
ACCENT = (201, 138, 20)          # amber, the application accent
BAND = (245, 247, 249)
BAND_ALT = (250, 251, 252)
PAPER = (255, 255, 255)

OK = (5, 150, 105)
WARN = (217, 119, 6)
BAD = (220, 38, 38)

#: Core fonts are latin-1, so typographic characters are folded before encoding
#: rather than allowed to raise FPDFUnicodeEncodingException mid-report.
FOLD = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
    "\u200b": "", "\u2022": "-", "\u2192": "->", "\u2265": ">=",
    "\u2264": "<=", "\u00d7": "x", "\u2011": "-", "\u2044": "/",
    "\u2713": "OK", "\u00b7": "-",
}

STATUS_COLOUR = {
    "completed": OK,
    "complete": OK,
    "success": OK,
    "verified": OK,
    "running": WARN,
    "partial": WARN,
    "skipped": QUIET,
    "failed": BAD,
    "error": BAD,
}

PAGE_W, PAGE_H = 210.0, 297.0
MARGIN_L, MARGIN_R = 18.0, 18.0
MARGIN_T, MARGIN_B = 26.0, 24.0

TOC_X = MARGIN_L
TOC_TOP = 58.0
TOC_ROW = 7.4
TOC_ROWS = 27


# --------------------------------------------------------------------------
# small formatters
# --------------------------------------------------------------------------

def human_bytes(count) -> str:
    """1024-based, because Android reports storage in 1024-based units."""
    try:
        value = float(count or 0)
    except (TypeError, ValueError):
        return "not recorded"
    if value <= 0:
        return "not recorded"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def count_text(count) -> str:
    try:
        return f"{int(count or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _roman(number: int) -> str:
    numerals = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    out = []
    for value, glyph in numerals:
        while number >= value:
            out.append(glyph)
            number -= value
    return "".join(out) or "i"


def _timestamp(raw) -> str:
    """ISO timestamps become the minute, in UTC, on one readable line."""
    text = str(raw or "").strip()
    if not text:
        return "not recorded"
    text = text.replace("T", " ")
    return text[:16]


def _clean(text, limit: int | None = None) -> str:
    if isinstance(text, float):
        # A latitude parsed from a dump arrives as 5.6076999999999995; six
        # significant figures is more precision than any of these fields has.
        text = f"{text:.6g}"
    folded = "".join(FOLD.get(ch, ch) for ch in str(text if text is not None else ""))
    folded = folded.encode("latin-1", "replace").decode("latin-1")
    folded = " ".join(folded.split())
    if limit and len(folded) > limit:
        folded = folded[: max(0, limit - 3)].rstrip(" ,;.") + "..."
    return folded


MODULE_TITLES = {
    "sms_mms": "SMS and MMS",
    "call_log": "Call Log",
    "contacts": "Contacts",
    "browser": "Browser History",
    "gps": "GPS and Location",
    "wifi": "Wi-Fi History",
    "bluetooth": "Bluetooth Pairings",
    "apps": "Application Artifacts",
    "installed_apps": "Installed Applications",
    "app_permissions": "App Permissions",
    "calendar": "Calendar Events",
    "deleted": "Deleted Data Recovery",
    "notes": "Notes and Memos",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "instagram": "Instagram",
    "snapchat": "Snapchat",
    "tiktok": "TikTok",
    "facebook": "Facebook Messenger",
    "transactions": "Financial Transactions",
    "wifi_passwords": "Wi-Fi Passwords",
    "clipboard": "Clipboard Contents",
    "notifications": "Notification History",
    "usage_stats": "App Usage Statistics",
    "media": "Media Files",
    "documents": "Documents",
    "emails_accounts": "Email Accounts",
    "social_media": "Social Media",
}


def _title_of(key: str) -> str:
    text = str(key)
    return MODULE_TITLES.get(text, _clean(text.replace("_", " ").strip().title()))


def _split_rows(count: int, first_cap: int, rest_cap: int) -> list[int]:
    """How many rows go on each sheet, so the last one is never a scrap.

    A table that ends with five lonely rows on a page of its own reads as a
    layout accident. The page count is decided up front, then the rows are
    handed out evenly enough that the final sheet carries at least a few.
    """
    if count <= first_cap:
        return [count]
    pages = 1 + -(-(count - first_cap) // max(1, rest_cap))
    sizes: list[int] = []
    index = 0
    while index < count:
        left = count - index
        pages_left = pages - len(sizes)
        cap = first_cap if not sizes else rest_cap
        take = min(cap, left)
        if pages_left == 2 and left - take < 8:
            take = max(1, min(cap, left - 8))
        index += take
        sizes.append(take)
    return sizes


def _bytes_label(size) -> str:
    if size is None:
        return "size not recorded"
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "size not recorded"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _preview(path, box: int = 980):
    """Decode an image for the plate, shrunk and re-encoded as a JPEG.

    Returns ``(payload, (width_px, height_px), reason)``. Pillow is already a
    dependency of the artifact viewer, so it is reused rather than pulling in
    anything new. The shrinking matters: twelve full-resolution camera frames
    took the document from 130 KB to 13 MB, which is not a file anyone emails
    to a prosecutor. A format Pillow cannot read still gets a plate -- with the
    reason printed instead of a picture, which is more honest than dropping
    the row.
    """
    try:
        from PIL import Image, ImageOps
    except Exception:
        return None, (0, 0), "no decoder available"

    try:
        with Image.open(path) as raw:
            raw.load()
            raw = ImageOps.exif_transpose(raw)
            if raw.mode in ("RGBA", "LA", "P"):
                raw = raw.convert("RGBA")
                canvas = Image.new("RGB", raw.size, (255, 255, 255))
                canvas.paste(raw, mask=raw.split()[-1])
                raw = canvas
            elif raw.mode != "RGB":
                raw = raw.convert("RGB")
            raw.thumbnail((box, box), Image.LANCZOS)
            payload = io.BytesIO()
            raw.save(payload, format="JPEG", quality=80, optimize=True)
            return payload.getvalue(), raw.size, ""
    except Exception as exc:  # unreadable, truncated, unsupported codec
        return None, (0, 0), type(exc).__name__


def _logo_path() -> Path | None:
    """The wordmark, from a checkout or from a one-file build."""
    candidates = []
    frozen = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and frozen:
        candidates += [
            Path(frozen) / "desktop" / "assets" / "branding" / "logo.png",
            Path(frozen) / "assets" / "branding" / "logo.png",
        ]
    candidates.append(Path(__file__).resolve().parents[1] / "assets" / "branding" / "logo.png")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------

class InvestigationReport(FPDF):
    """One PDF, composed in as many passes as it takes to number itself."""

    def __init__(self, data: dict, *, toc=(), total_pages: int = 0):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.data = data or {}
        self.toc = list(toc)
        self.total_pages = total_pages or 0

        self.set_margins(MARGIN_L, MARGIN_T, MARGIN_R)
        self.set_auto_page_break(auto=True, margin=MARGIN_B)
        self.set_title(f"Investigation Report - {_clean(data.get('case_number', ''))}")
        self.set_author(_clean(data.get("examiner", TOOL_NAME)))
        self.set_creator(f"{TOOL_NAME} {TOOL_VERSION}")
        self.set_producer(f"{TOOL_NAME} {TOOL_VERSION}")

        self.front_pages = 1        # cover only, until the contents is drawn
        self.toc_entries: list[tuple[int, str, int]] = []
        self._number = 0

        # Plates are resolved once: a file that has gone missing between the
        # data being gathered and the report being composed must not be listed
        # as if it were still in the store.
        self._plates = []
        for image in (self.data.get("images") or []):
            try:
                path = Path(str(image.get("path") or ""))
                if path.is_file():
                    self._plates.append({**image, "path": path})
            except OSError:
                continue

        # Artifact-view panels, rendered once and kept as JPEG bytes so the
        # contents pass does not decode them three more times.
        self._view_panels: list[dict] = []
        self._view_gaps: list[dict] = []
        for view in (self.data.get("artifact_views") or []):
            status = str(view.get("status") or "").lower()
            if status == "missing":
                self._view_gaps.append(view)
                continue
            try:
                from . import view_plate

                payload, size = view_plate.render(view)
            except Exception:
                self._view_gaps.append({**view, "note": "panel could not be rendered"})
                continue
            self._view_panels.append({"view": view, "payload": payload, "size": size})

    # -- text encoding ----------------------------------------------------
    def normalize_text(self, text: str) -> str:
        folded = "".join(FOLD.get(ch, ch) for ch in str(text))
        return folded.encode("latin-1", "replace").decode("latin-1")

    # -- chrome -----------------------------------------------------------
    def header(self):
        page = self.page_no()
        if page <= self.front_pages:
            return

        width = self.epw
        y = 12.0
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.8)
        self.line(self.l_margin, y + 3.4, self.l_margin + 9, y + 3.4)

        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*INK)
        self.set_xy(self.l_margin + 12, y)
        self.cell(64, 7, _clean(self.data.get("case_number", "no case number"), 34).upper())

        self.set_font("Helvetica", "", 6.8)
        self.set_text_color(*QUIET)
        self.set_xy(self.l_margin + 12, y)
        self.set_xy(self.l_margin + 76, y)
        self.cell(70, 7, "DIGITAL FORENSIC EXAMINATION REPORT")

        self.set_font("Helvetica", "B", 6.8)
        self.set_text_color(*BAD)
        self.set_xy(self.w - self.r_margin - 60, y)
        self.cell(60, 7, "CONFIDENTIAL", align="R")

        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, y + 7.4, self.w - self.r_margin, y + 7.4)

        # header() runs after _beginpage has set the cursor to the top margin,
        # so it must put the cursor back where the page content expects it.
        self.set_xy(self.l_margin, MARGIN_T)

    def footer(self):
        page = self.page_no()
        if page == 1:
            return

        width = self.epw
        top = PAGE_H - 17.0
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, top, self.w - self.r_margin, top)

        self.set_font("Helvetica", "", 6.6)
        self.set_text_color(*MUTED)
        self.set_xy(self.l_margin, top + 2.2)
        self.cell(width / 2, 4, f"{TOOL_NAME} {TOOL_VERSION}")

        if page <= self.front_pages:
            label = _roman(page - 1)
        elif self.total_pages:
            body_total = max(1, self.total_pages - self.front_pages)
            label = f"Page {page - self.front_pages} of {body_total}"
        else:
            label = f"Page {page - self.front_pages}"

        self.set_xy(self.l_margin, top + 2.2)
        self.cell(width, 4, label, align="R")

    # -- flow helpers -----------------------------------------------------
    def _ensure(self, height: float):
        if self.get_y() + height > PAGE_H - MARGIN_B - 2:
            self.add_page()
            return True
        return False

    def _fit(self, text, width: float, size: float, style: str = "") -> str:
        """Truncate to what the column can hold, with an ellipsis and no crumbs."""
        self.set_font("Helvetica", style, size)
        text = _clean(text, 400)
        if not text:
            return ""
        if self.get_string_width(text) <= width - 2.0:
            return text
        while text and self.get_string_width(text + "...") > width - 2.0:
            text = text[:-1]
        return text.rstrip(" ,;.") + "..."

    # -- structural blocks ------------------------------------------------
    def section(self, title: str, number: str | None = None, subtitle: str | None = None,
                *, toc_label: str | None = None):
        """A numbered section: chip, title, optional standfirst, hairline rule."""
        self._ensure(58)
        if self.get_y() > MARGIN_T + 4:
            self.ln(5)

        y = self.get_y()
        if number is None:
            self._number += 1
            number = str(self._number)

        chip = 10.0
        self.set_fill_color(*ACCENT)
        self.rect(self.l_margin, y + 0.6, chip, chip, style="F")
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(255, 255, 255)
        self.set_xy(self.l_margin, y + 0.6 + chip / 2 - 3.1)
        self.cell(chip, 6.2, number, align="C")

        self.set_font("Helvetica", "B", 17)
        self.set_text_color(*INK)
        self.set_xy(self.l_margin + chip + 5, y)
        self.cell(self.epw - chip - 5, 7.2, _clean(title, 64))

        cursor = y + 12.2
        if subtitle:
            self.set_font("Helvetica", "", 8.4)
            self.set_text_color(*MUTED)
            self.set_xy(self.l_margin, cursor)
            self.multi_cell(self.epw, 4.6, _clean(subtitle), align="L",
                            new_x="LMARGIN", new_y="NEXT")
            cursor = self.get_y() + 1.6
        else:
            cursor = y + 12.4

        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(self.l_margin, cursor, self.w - self.r_margin, cursor)
        self.set_y(cursor + 5)

        self.toc_entries.append((0, str(number), _clean(toc_label or title, 70), self._body_page()))

    def subsection(self, title: str, note: str | None = None, *, toc: bool = True):
        self._ensure(26)
        if self.get_y() > MARGIN_T + 6:
            self.ln(1.6)
        y = self.get_y()
        heading = _clean(title, 78)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(*INK)
        self.set_xy(self.l_margin, y)
        self.cell(0, 6.4, heading)
        if note:
            heading_w = self.get_string_width(heading)
            self.set_font("Helvetica", "", 7.6)
            self.set_text_color(*QUIET)
            self.set_xy(self.l_margin + heading_w + 3.0, y + 0.6)
            self.cell(self.epw - heading_w - 3.0, 5.2, _clean(note, 60))
        self.set_y(y + 8.2)
        if toc:
            self.toc_entries.append((1, "", heading, self._body_page()))

    def paragraph(self, text: str, *, size: float = 8.8, colour=MUTED, space: float = 3.0):
        self.set_font("Helvetica", "", size)
        self.set_text_color(*colour)
        self.multi_cell(self.epw, size * 0.52, _clean(text), align="L",
                        new_x="LMARGIN", new_y="NEXT")
        self.ln(space)

    def _body_page(self) -> int:
        return max(1, self.page_no() - self.front_pages)

    # -- components -------------------------------------------------------
    def kv_block(self, x: float, y: float, width: float, title: str, rows, *,
                 label_w: float = 27.0, row_h: float = 6.2) -> float:
        """A titled card of label/value rows with hairline separators."""
        band_h = 7.4
        height = band_h + row_h * len(rows)

        self.set_fill_color(*BAND)
        self.rect(x, y, width, band_h, style="F")
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*MUTED)
        self.set_xy(x + 3.4, y + band_h / 2 - 1.9)
        self.cell(width - 6, 3.8, title.upper())

        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.rect(x, y, width, height, style="D")

        cursor = y + band_h
        for label, value in rows:
            self.set_font("Helvetica", "", 7.4)
            self.set_text_color(*QUIET)
            self.set_xy(x + 3.4, cursor + row_h / 2 - 1.9)
            self.cell(label_w, 3.8, self._fit(label, label_w, 7.4))

            value_w = width - label_w - 6.8
            text = self._fit(value, value_w, 7.4, "B")
            self.set_font("Helvetica", "B", 7.4)
            self.set_text_color(*INK)
            self.set_xy(x + 3.4 + label_w, cursor + row_h / 2 - 1.9)
            self.cell(value_w, 3.8, text)

            self.set_draw_color(*RULE_SOFT)
            self.set_line_width(0.15)
            self.line(x, cursor + row_h, x + width, cursor + row_h)
            cursor += row_h
        return y + height

    def stat_tiles(self, items, *, per_row: int = 4, height: float = 20.0,
                   gap: float = 4.0) -> float:
        """Big numbers on a quiet band. One row per ``per_row`` figures."""
        tile_w = (self.epw - gap * (per_row - 1)) / per_row
        for start in range(0, len(items), per_row):
            self._ensure(height + 4)
            y = self.get_y()
            for index, (value, label) in enumerate(items[start:start + per_row]):
                x = self.l_margin + index * (tile_w + gap)
                self.set_fill_color(*BAND)
                self.rect(x, y, tile_w, height, style="F")

                self.set_fill_color(*ACCENT)
                self.rect(x, y, 1.6, height, style="F")

                self.set_font("Helvetica", "B", 15)
                self.set_text_color(*INK)
                self.set_xy(x + 4.6, y + 3.4)
                self.cell(tile_w - 6, 7, self._fit(value, tile_w - 6, 15, "B"))

                self.set_font("Helvetica", "", 6.4)
                self.set_text_color(*MUTED)
                self.set_xy(x + 4.6, y + 11.4)
                self.cell(tile_w - 6, 3.4, self._fit(label.upper(), tile_w - 6, 6.4))
            self.set_y(y + height + gap)
        return self.get_y()

    def bar_chart(self, items, *, label_w: float = 46.0, row_h: float = 6.0,
                  emphasis: int = 1) -> float:
        """Horizontal bars. The largest category carries the accent colour."""
        if not items:
            return self.get_y()
        peak = max(value for _, value in items) or 1
        value_w = 16.0
        track_w = self.epw - label_w - value_w - 4.0

        for position, (label, value) in enumerate(items):
            self._ensure(row_h + 2)
            y = self.get_y()
            self.set_font("Helvetica", "", 7.8)
            self.set_text_color(*INK)
            self.set_xy(self.l_margin, y + 0.6)
            self.cell(label_w - 2, 4.4, self._fit(label, label_w - 2, 7.8))

            self.set_fill_color(*RULE_SOFT)
            self.rect(self.l_margin + label_w, y + 1.1, track_w, 3.4, style="F")
            span = max(0.6, track_w * (value / peak))
            self.set_fill_color(*(ACCENT if position < emphasis else INK))
            self.rect(self.l_margin + label_w, y + 1.1, span, 3.4, style="F")

            self.set_font("Helvetica", "B", 7.8)
            self.set_text_color(*INK)
            self.set_xy(self.l_margin + label_w + track_w + 2.0, y + 0.6)
            self.cell(value_w, 4.4, count_text(value), align="R")
            self.set_y(y + row_h)
        return self.get_y()

    def table(self, headers, rows, widths, *, aligns=None, mono_cols=(),
              row_h: float = 6.0, size: float = 7.6, header_size: float = 6.6,
              repeat_header: bool = True, colours=None, caption: str | None = None):
        """Zebra table with a repeated header and a closing rule.

        ``colours`` maps ``(row_index, column_index)`` to a text colour, so a
        failed status can be red without colouring the row it sits in.

        ``caption`` names the table so that a table running over a page break
        says what it is on the sheets that carry no heading of their own.
        """
        aligns = aligns or ["L"] * len(headers)
        colours = colours or {}
        bottom = PAGE_H - MARGIN_B - 2

        # A short table that would split into one or two orphan rows reads as a
        # mistake; move the whole thing instead. Long tables still break.
        needed = (row_h + 1.4) + min(len(rows), 8) * row_h
        if self.get_y() + needed > bottom and self.get_y() > MARGIN_T + 30:
            self.add_page()

        def draw_header():
            y = self.get_y()
            self.set_fill_color(*INK)
            self.rect(self.l_margin, y, sum(widths), row_h + 1.4, style="F")
            self.set_font("Helvetica", "B", header_size)
            self.set_text_color(255, 255, 255)
            x = self.l_margin
            for width, label, align in zip(widths, headers, aligns):
                pad = 2.4
                self.set_xy(x + pad, y + 0.7)
                self.cell(width - pad * 2, row_h, _clean(label).upper(), align=align)
                x += width
            self.set_y(y + row_h + 1.4)

        self._ensure(row_h * 3 + 2)
        first_capacity = max(1, int((bottom - self.get_y() - (row_h + 1.4)) // row_h))
        rest_capacity = max(1, int((bottom - MARGIN_T - (row_h + 1.4) - 4.2) // row_h))
        sizes = _split_rows(len(rows), first_capacity, rest_capacity)

        offset = 0
        for page_index, take in enumerate(sizes):
            if page_index == 0:
                draw_header()
            elif repeat_header:
                self.add_page()
                if caption and self.get_y() <= MARGIN_T + 1:
                    self.set_font("Helvetica", "I", 6.6)
                    self.set_text_color(*MUTED)
                    self.set_xy(self.l_margin, self.get_y())
                    self.cell(0, 4.2, _clean(f"{caption}, continued", 96))
                    self.set_xy(self.l_margin, self.get_y() + 4.2)
                draw_header()
            else:
                self.add_page()

            for index in range(offset, offset + take):
                row = rows[index]
                y = self.get_y()
                if index % 2 == 1:
                    self.set_fill_color(*BAND_ALT)
                    self.rect(self.l_margin, y, sum(widths), row_h, style="F")

                x = self.l_margin
                for column, (width, align) in enumerate(zip(widths, aligns)):
                    value = row[column] if column < len(row) else ""
                    tint = colours.get((index, column))
                    if column in mono_cols:
                        self.set_font("Courier", "", size - 0.6)
                        text = _clean(value, 60)
                    elif tint:
                        self.set_font("Helvetica", "B", size)
                        text = self._fit(value, width, size, "B")
                    else:
                        self.set_font("Helvetica", "", size)
                        text = self._fit(value, width, size)
                    self.set_text_color(*(tint or INK))
                    self.set_xy(x + 2.4, y + row_h / 2 - 1.9)
                    self.cell(width - 4.8, 3.8, text, align=align)
                    x += width
                self.set_y(y + row_h)
            offset += take

        self.set_draw_color(*RULE)
        self.set_line_width(0.25)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3.5)

    # -- cover ------------------------------------------------------------
    def _cover(self):
        # The cover is a fixed canvas: a page break here would push one line of
        # furniture onto an otherwise blank sheet.
        self.set_auto_page_break(False)
        self.add_page()
        data = self.data
        device = self.data.get("device") or {}
        session = self.data.get("session") or {}

        self.set_fill_color(*ACCENT)
        self.rect(0, 0, PAGE_W, 3.2, style="F")

        logo = _logo_path()
        if logo is not None:
            self.image(str(logo), x=MARGIN_L, y=13, w=58)
        else:
            self.set_font("Helvetica", "B", 22)
            self.set_text_color(*INK)
            self.set_xy(MARGIN_L, 15)
            self.cell(90, 10, "DROIDFORENX")

        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*BAD)
        self.set_xy(self.w - MARGIN_R - 80, 15)
        self.cell(80, 4, "CONFIDENTIAL", align="R")
        self.set_font("Helvetica", "", 6.4)
        self.set_text_color(*QUIET)
        self.set_xy(self.w - MARGIN_R - 80, 19.4)
        self.cell(80, 3.4, "Law enforcement sensitive - handle under case protocol", align="R")

        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(MARGIN_L, 40, self.w - MARGIN_R, 40)

        self.set_font("Helvetica", "B", 7.4)
        self.set_text_color(*MUTED)
        self.set_char_spacing(0.9)
        self.set_xy(MARGIN_L, 52)
        self.cell(0, 4, "DIGITAL FORENSIC EXAMINATION REPORT")
        self.set_char_spacing(0)

        case = _clean(data.get("case_number", "unreferenced case"), 60)
        self.set_font("Helvetica", "B", 30)
        self.set_text_color(*INK)
        self.set_xy(MARGIN_L, 60)
        self.multi_cell(self.epw, 12.4, case, align="L", new_x="LMARGIN", new_y="NEXT")

        self.set_draw_color(*ACCENT)
        self.set_line_width(1.1)
        self.set_y(self.get_y() + 2.5)
        y = self.get_y()
        self.line(MARGIN_L, y, MARGIN_L + 34, y)

        self.set_font("Helvetica", "", 9.6)
        self.set_text_color(*MUTED)
        self.set_xy(MARGIN_L, y + 4.5)
        self.cell(0, 5, "Mobile device acquisition, examination and analysis")

        declared = _clean(data.get("abstract") or
                          ("This document records the forensic examination conducted under "
                           f"case {case}. It sets out the device examined, the method used, "
                           "the artifacts recovered and the limitations of the acquisition."), 320)
        self.set_font("Helvetica", "", 8.6)
        self.set_text_color(*MUTED)
        self.set_xy(MARGIN_L, y + 13)
        self.multi_cell(self.epw - 6, 4.5, declared, align="L", new_x="LMARGIN", new_y="NEXT")

        counts = self._counts()
        self.set_y(136)
        self.stat_tiles([
            (count_text(counts["total"]), "Artifacts recovered"),
            (count_text(counts["modules"]), "Analysis modules"),
            (count_text(len(data.get("audit_log") or [])), "Custody events"),
            (count_text(counts["sources"]), "Evidence sources"),
        ], per_row=4, height=19, gap=3.4)

        top = self.get_y() + 4
        card_w = (self.epw - 5) / 2
        device_rows = [
            ("Manufacturer", device.get("manufacturer") or "not recorded"),
            ("Model", device.get("model") or "not recorded"),
            ("Android", device.get("android_version") or "not recorded"),
            ("Serial", data.get("serial") or session.get("device_id") or "not recorded"),
        ]
        self.kv_block(MARGIN_L, top, card_w, "Device examined", device_rows, label_w=21)
        case_rows = [
            ("Examiner", data.get("examiner") or "not recorded"),
            ("Badge / ID", data.get("badge") or "not recorded"),
            ("Agency", data.get("org") or "not recorded"),
            ("Generated", _timestamp(data.get("generated_at"))),
        ]
        self.kv_block(MARGIN_L + card_w + 5, top, card_w, "Examination record", case_rows, label_w=21)

        # Examiner attestation: a report that may be tendered needs a place for
        # the examiner's own hand, and the cover is where that belongs.
        attest = max(self.get_y(), top + 32) + 12
        self.set_font("Helvetica", "B", 6.6)
        self.set_text_color(*MUTED)
        self.set_char_spacing(0.7)
        self.set_xy(MARGIN_L, attest)
        self.cell(0, 4, "EXAMINER'S ATTESTATION")
        self.set_char_spacing(0)

        self.set_font("Helvetica", "I", 7.4)
        self.set_text_color(*MUTED)
        self.set_xy(MARGIN_L, attest + 5)
        self.multi_cell(self.epw - 40, 3.9,
                        "I certify that the examination recorded here was carried out by me or "
                        "under my direction, that the method described in section 4 was "
                        "followed, and that the figures in this report are those the tool "
                        "wrote at the time of acquisition.", align="L",
                        new_x="LMARGIN", new_y="NEXT")

        line_y = attest + 26
        self.set_draw_color(*QUIET)
        self.set_line_width(0.3)
        self.line(MARGIN_L, line_y, MARGIN_L + 66, line_y)
        self.line(MARGIN_L + 104, line_y, MARGIN_L + 150, line_y)

        self.set_font("Helvetica", "B", 7.4)
        self.set_text_color(*INK)
        self.set_xy(MARGIN_L, line_y + 1.6)
        self.cell(66, 3.6, _clean(data.get("examiner") or "examiner", 40))
        self.set_xy(MARGIN_L + 104, line_y + 1.6)
        self.cell(46, 3.6, _clean(_timestamp(data.get("generated_at"))[:10], 20))

        self.set_font("Helvetica", "", 6.4)
        self.set_text_color(*QUIET)
        self.set_xy(MARGIN_L, line_y + 5.4)
        self.cell(66, 3.4, _clean(f"Signature - {data.get('badge') or 'badge not recorded'}", 44))
        self.set_xy(MARGIN_L + 104, line_y + 5.4)
        self.cell(46, 3.4, "Date")

        band_y = PAGE_H - 42
        self.set_fill_color(*BAND)
        self.rect(MARGIN_L, band_y, self.epw, 20, style="F")
        self.set_font("Helvetica", "B", 6.6)
        self.set_text_color(*INK)
        self.set_xy(MARGIN_L + 4.5, band_y + 3.4)
        self.cell(self.epw - 9, 3.6, "THIS REPORT IS CONFIDENTIAL AND PRIVILEGED")
        self.set_font("Helvetica", "", 6.6)
        self.set_text_color(*MUTED)
        self.set_xy(MARGIN_L + 4.5, band_y + 7.8)
        self.multi_cell(self.epw - 9, 3.4,
                        "It is prepared for the case file and for the parties entitled to it. "
                        "Its SHA-256 digest is written to a sidecar file of the same name at "
                        "the moment of generation; that sidecar, not the document, is the "
                        "integrity record.", align="L", new_x="LMARGIN", new_y="NEXT")

        self.set_font("Helvetica", "", 6.4)
        self.set_text_color(*QUIET)
        self.set_xy(MARGIN_L, PAGE_H - 13)
        self.cell(self.epw, 4, f"Produced with {TOOL_NAME} {TOOL_VERSION}")
        self.set_auto_page_break(True, margin=MARGIN_B)

    # -- contents ---------------------------------------------------------
    def _contents(self):
        # The count comes from the planned entry list, so adding the contents
        # never shifts the page numbers printed on it.
        planned = self._plan()
        pages = max(1, math.ceil(sum(1 + len(s["subs"]) for s in planned) / TOC_ROWS))
        self.front_pages = 1 + pages

        for page in range(pages):
            self.add_page()
            if page == 0:
                self.set_font("Helvetica", "B", 7.4)
                self.set_text_color(*ACCENT)
                self.set_char_spacing(0.9)
                self.set_xy(MARGIN_L, 40)
                self.cell(0, 4, "CONTENTS")
                self.set_char_spacing(0)

                self.set_font("Helvetica", "B", 20)
                self.set_text_color(*INK)
                self.set_xy(MARGIN_L, 46)
                self.cell(0, 9, "Table of contents")

            entries = self.toc[page * TOC_ROWS:(page + 1) * TOC_ROWS]
            y = TOC_TOP
            for level, number, label, target in entries:
                if level == 0:
                    self.set_font("Helvetica", "B", 9.2)
                    self.set_text_color(*INK)
                else:
                    self.set_font("Helvetica", "", 8.2)
                    self.set_text_color(*MUTED)

                indent = 9 if level == 0 else 19
                if number:
                    self.set_font("Helvetica", "B", 8.6)
                    self.set_text_color(*ACCENT)
                    self.set_xy(MARGIN_L, y)
                    self.cell(8, 5.2, number)

                self.set_xy(MARGIN_L + indent, y)
                if level == 0:
                    self.set_font("Helvetica", "B", 9.2)
                    self.set_text_color(*INK)
                else:
                    self.set_font("Helvetica", "", 8.2)
                    self.set_text_color(*MUTED)
                self.cell(0, 5.2, _clean(label, 84))

                page_text = str(target) if target else "-"
                self.set_font("Helvetica", "B" if level == 0 else "", 8.4)
                self.set_text_color(*INK if level == 0 else MUTED)
                self.set_xy(self.w - MARGIN_R - 18, y)
                self.cell(18, 5.2, page_text, align="R")

                title_w = self.get_string_width(_clean(label, 84))
                leader_from = MARGIN_L + indent + title_w + 4
                self.set_draw_color(186, 194, 205)
                self.set_line_width(0.15)
                self.set_dash_pattern(dash=0.15, gap=1.5)
                self.line(min(leader_from, self.w - MARGIN_R - 24), y + 3.6,
                          self.w - MARGIN_R - 20, y + 3.6)
                self.set_dash_pattern()
                y += TOC_ROW

            self.set_font("Helvetica", "I", 7)
            self.set_text_color(*QUIET)
            self.set_xy(MARGIN_L, PAGE_H - 30)
            self.cell(self.epw, 4, "Page numbers refer to the body of the report; "
                                   "front matter is numbered in roman numerals.", align="L")

    # -- data shaping -----------------------------------------------------
    def _modules(self):
        modules = []
        for result in self.data.get("results") or []:
            findings = result.get("findings")
            if not isinstance(findings, dict):
                findings = {}
            key = str(result.get("analysis_type") or result.get("name") or "analysis")
            modules.append({
                "key": key,
                "title": _title_of(key),
                "status": str(result.get("status") or "recorded").lower(),
                "count": int(result.get("item_count") or 0),
                "findings": findings,
                "tools": result.get("tools_used") or "",
                "completed": result.get("completed_at") or "",
                "error": findings.get("error") if isinstance(findings.get("error"), str) else "",
            })
        modules.sort(key=lambda m: (-m["count"], m["title"]))
        return modules

    def _counts(self):
        modules = self._modules()
        total = sum(m["count"] for m in modules)
        sources = 0
        for module in modules:
            for value in module["findings"].values():
                if isinstance(value, list) and value:
                    sources += 1
                    break
        return {
            "total": total,
            "modules": len(modules),
            "sources": sources,
            "failed": sum(1 for m in modules if m["status"] == "failed"),
        }

    def _plan(self):
        """Sections, and the appendices that will follow them.

        Built before anything is drawn so the contents can reserve the right
        number of pages. Section titles here must match the ones passed to
        ``section()`` in ``_compose``.
        """
        modules = self._modules()
        findings_subs = [m["title"] for m in modules if m["count"] > 0][:10]
        appendices = [m for m in modules if m["count"] > 0][:4]
        self._appendices = appendices

        plan = [
            {"title": "Executive Summary", "subs": []},
            {"title": "Case and Device Profile", "subs": []},
            {"title": "Chain of Custody", "subs": []},
            {"title": "Methodology", "subs": []},
            {"title": "Examination Results", "subs": []},
            {"title": "Evidence Findings", "subs": findings_subs},
            {"title": "Conclusions and Recommendations", "subs": []},
            {"title": "Integrity and Limitations", "subs": []},
        ] + [
            {"title": f"Appendix {chr(65 + index)} - {module['title']}", "subs": []}
            for index, module in enumerate(appendices)
        ]

        self._image_letter = None
        self._view_letter = None
        letter_index = len(appendices)

        if self._view_panels or self._view_gaps:
            self._view_letter = chr(65 + letter_index)
            letter_index += 1
            plan.append({"title": f"Appendix {self._view_letter} - Artifact View Results",
                         "subs": []})

        if self._plates:
            self._image_letter = chr(65 + letter_index)
            plan.append({"title": f"Appendix {self._image_letter} - Recovered Images",
                         "subs": []})
        return plan

    # -- sections ---------------------------------------------------------
    def _compose(self):
        plan = self._plan()
        counts = self._counts()

        self._cover()
        self._contents()

        # The contents needed a placeholder count before it could be drawn, so
        # entries are registered as sections are; front matter is now known.
        self._section_summary(plan[0]["title"], counts)
        self._section_case(plan[1]["title"])
        self._section_custody(plan[2]["title"])
        self._section_method(plan[3]["title"])
        self._section_results(plan[4]["title"])
        self._section_findings(plan[5]["title"])
        self._section_conclusions(plan[6]["title"])
        self._section_integrity(plan[7]["title"])
        for index, module in enumerate(self._appendices):
            self._section_appendix(module, index)
        if self._view_letter:
            self._section_views(self._view_letter)
        if self._image_letter:
            self._section_images(self._image_letter)

    # --- 1. summary ------------------------------------------------------
    def _section_summary(self, title, counts):
        data = self.data
        device = data.get("device") or {}
        case = _clean(data.get("case_number", "this case"))

        self.add_page()
        self.section(title, subtitle="What was examined, what came back, and how much of it.")

        top = self._modules()[:1]
        lead = (
            f"This report sets out the forensic examination of "
            f"{_clean(device.get('manufacturer', 'a mobile device'))} "
            f"{_clean(device.get('model', ''))}".rstrip()
            + (f", running Android {_clean(device.get('android_version'))}"
               if device.get("android_version") else "")
            + f", examined under case {case}. {count_text(counts['total'])} digital artifacts "
            f"were recovered across {counts['modules']} analysis modules. "
        )
        if counts["failed"]:
            lead += (f"{counts['failed']} module{'s' if counts['failed'] != 1 else ''} did not "
                     "complete; the reason is recorded against it in section 5 and the "
                     "artifacts it covers are not claimed here. ")
        else:
            lead += "Every module selected for the examination completed. "
        lead += ("Counts are of items written by the analysis modules; each is evidenced in the "
                 "appendices, and every artifact is hashed in the case evidence store.")
        self.paragraph(lead)

        modules = self._modules()
        chart_items = [(m["title"], m["count"]) for m in modules if m["count"] > 0][:8]
        self.subsection("Artifact profile", "largest recoveries first", toc=False)
        if chart_items:
            self.bar_chart(chart_items, emphasis=1)
            self.ln(3)
        else:
            self.paragraph("No analysis module returned artifacts for this session.", space=1)

        self._ensure(30)
        self.subsection("Examination at a glance", toc=False)
        self.stat_tiles([
            (count_text(counts["total"]), "Artifacts recovered"),
            (count_text(counts["modules"]), "Modules run"),
            (count_text(len(data.get("audit_log") or [])), "Custody events"),
            (count_text(sum(1 for m in modules if m["count"])), "Sources with data"),
        ], per_row=4, height=18, gap=3.4)
        self.ln(1)
        self.paragraph(
            "Figures describe what this tool recovered and recorded. They are not an estimate "
            "of what the device holds: app-private storage on a non-rooted handset is outside "
            "the reach of any logical acquisition, and is reported as such in section 8.",
            size=7.8)

    # --- 2. case and device ---------------------------------------------
    def _section_case(self, title):
        data = self.data
        device = data.get("device") or {}
        session = data.get("session") or {}

        self.section(title, subtitle="The record that ties this document to a case and a handset.")

        card_w = (self.epw - 5) / 2
        y = self.get_y()
        case_rows = [
            ("Case number", data.get("case_number") or "not recorded"),
            ("Examiner", data.get("examiner") or "not recorded"),
            ("Badge / ID", data.get("badge") or "not recorded"),
            ("Agency", data.get("org") or "not recorded"),
            ("Session", str(session.get("id") or "not recorded")[:18]),
            ("Opened", _timestamp(session.get("created_at"))),
            ("Status", str(session.get("status") or "not recorded").title()),
        ]
        left_bottom = self.kv_block(MARGIN_L, y, card_w, "Case register", case_rows, label_w=24)

        dev_rows = [
            ("Manufacturer", device.get("manufacturer") or "not recorded"),
            ("Model", device.get("model") or "not recorded"),
            ("Device name", device.get("device_name") or "not recorded"),
            ("Android", device.get("android_version") or "not recorded"),
            ("API level", device.get("api_level") or "not recorded"),
            ("Security patch", device.get("security_patch") or "not recorded"),
            ("Encryption", str(device.get("encryption_status") or "not recorded").title()),
            ("Screen lock", str(device.get("lock_type") or "not recorded")),
        ]
        right_bottom = self.kv_block(MARGIN_L + card_w + 5, y, card_w, "Device identification",
                                     dev_rows, label_w=26)
        self.set_y(max(left_bottom, right_bottom) + 6)

        self.subsection("Storage", toc=False)
        total = device.get("storage_total_bytes") or 0
        free = device.get("storage_free_bytes") or 0
        used = max(0, (total or 0) - (free or 0))
        self.set_font("Helvetica", "", 7.8)
        self.set_text_color(*MUTED)
        self.set_xy(MARGIN_L, self.get_y())
        self.cell(self.epw, 4.6,
                  f"{human_bytes(used)} used of {human_bytes(total)} - {human_bytes(free)} free")

        bar_y = self.get_y() + 5.4
        self.set_fill_color(*RULE_SOFT)
        self.rect(MARGIN_L, bar_y, self.epw, 5.0, style="F")
        if total:
            self.set_fill_color(*INK)
            self.rect(MARGIN_L, bar_y, max(0.8, self.epw * (used / total)), 5.0, style="F")
            self.set_fill_color(*ACCENT)
            self.rect(MARGIN_L, bar_y, max(0.8, self.epw * (used / total)), 1.0, style="F")
        self.set_y(bar_y + 9)

        self.subsection("Build strings", toc=False)
        build_rows = [
            ("Fingerprint", device.get("build_fingerprint") or "not recorded"),
            ("Kernel", device.get("kernel_version") or "not recorded"),
            ("SIM cards", str(device.get("sim_count") if device.get("sim_count") is not None
                              else "not recorded")),
            ("Identified", _timestamp(device.get("identified_at"))),
        ]
        for label, value in build_rows:
            y = self.get_y()
            if self._ensure(7):
                y = self.get_y()
            self.set_font("Helvetica", "", 7.2)
            self.set_text_color(*QUIET)
            self.set_xy(MARGIN_L, y)
            self.cell(26, 5, label)
            self.set_font("Courier", "", 6.8)
            self.set_text_color(*INK)
            self.set_xy(MARGIN_L + 26, y)
            self.multi_cell(self.epw - 26, 4.0, _clean(value, 150), align="L",
                            new_x="LMARGIN", new_y="NEXT")
            self.ln(1.2)

        self.ln(2)
        self.paragraph(
            "Identification is read from the device through the platform's own properties; it "
            "is a statement of what the handset reported, not of what the label on the case "
            "envelope says. Where the two disagree, the envelope prevails and the difference "
            "belongs in the examiner's notes.", size=7.8)

    # --- 3. custody ------------------------------------------------------
    def _section_custody(self, title):
        audit = self.data.get("audit_log") or []
        self.section(title, subtitle="Every action the tool recorded against this session, in order.")

        if not audit:
            self.paragraph("No audit entries were recorded for this session. That is itself a "
                           "finding: it means the session was not driven through the tool's "
                           "logged workflow, and custody must be evidenced from the examiner's "
                           "own records.")
            return

        self.paragraph(
            f"{len(audit)} entries. The log is written by the application as each action "
            "completes, and is not edited by report generation. All times are UTC.",
            size=8.2)

        rail_x = MARGIN_L + 2.6
        stamp_w = 30.0
        text_x = MARGIN_L + stamp_w + 8.0
        text_w = self.epw - stamp_w - 8.0

        for index, entry in enumerate(audit):
            stamp = _timestamp(entry.get("timestamp"))
            action = _clean(entry.get("action") or "action", 46)
            actor = _clean(entry.get("actor") or "", 30)
            detail = _clean(entry.get("details") or "", 220)

            self._ensure(12)
            y = self.get_y()

            if index < len(audit) - 1:
                self.set_draw_color(*RULE)
                self.set_line_width(0.3)
                self.line(rail_x, y + 3.0, rail_x, y + 11.0)

            self.set_fill_color(*ACCENT)
            self.circle(rail_x, y + 2.4, 1.15, style="F")

            self.set_font("Courier", "", 6.6)
            self.set_text_color(*MUTED)
            self.set_xy(MARGIN_L + 7, y)
            self.cell(stamp_w, 4.6, stamp)

            self.set_font("Helvetica", "B", 7.8)
            self.set_text_color(*INK)
            self.set_xy(text_x, y)
            self.cell(text_w - 34, 4.6, action)

            if actor:
                self.set_font("Helvetica", "", 6.8)
                self.set_text_color(*QUIET)
                self.set_xy(self.w - MARGIN_R - 34, y)
                self.cell(34, 4.6, self._fit(actor, 34, 6.8), align="R")

            if detail:
                self.set_font("Helvetica", "", 7.2)
                self.set_text_color(*MUTED)
                self.set_xy(text_x, y + 4.4)
                self.multi_cell(text_w, 3.6, detail, align="L",
                                new_x="LMARGIN", new_y="NEXT")
            else:
                self.set_y(y + 5.0)
            self.ln(1.6)

    # --- 4. methodology --------------------------------------------------
    def _section_method(self, title):
        self.section(title, subtitle="The four-stage workflow, and the standard it follows.")

        self.paragraph(
            "The examination follows NIST SP 800-101 Rev. 2 and SWGDE best practice. The "
            "workflow is fixed: each stage hands the next a documented artifact, and nothing "
            "is analysed that was not first acquired and hashed.")

        stages = [
            ("Identification",
             "Manufacturer, model, build, API level, patch level and encryption state are read "
             "from the platform properties. This fixes the device identity used on every page "
             "of this report."),
            ("Acquisition",
             "Artifacts are read through the platform's content providers, service dumps and "
             "accessible storage. Where a source refuses access - app-private data on a "
             "non-rooted device, for instance - the refusal is recorded as NOT_ACCESSIBLE "
             "rather than as an empty result."),
            ("Examination and analysis",
             "Recovered data is parsed, ordered and counted by module. Each module's output is "
             "written once and never re-dated; counts in this report come from that output."),
            ("Reporting",
             "Findings are assembled into this document. The file's SHA-256 is computed as it "
             "leaves the tool and written to a sidecar, so the report can be shown unaltered."),
        ]
        y = self.get_y() + 1
        for index, (stage, body) in enumerate(stages, 1):
            self._ensure(19)
            y = self.get_y()
            self.set_fill_color(*INK)
            self.circle(MARGIN_L + 3.2, y + 3.4, 3.1, style="F")
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(255, 255, 255)
            self.set_xy(MARGIN_L + 0.1, y + 1.4)
            self.cell(6.2, 4, str(index), align="C")

            self.set_font("Helvetica", "B", 8.8)
            self.set_text_color(*INK)
            self.set_xy(MARGIN_L + 10, y)
            self.cell(self.epw - 10, 5.2, stage)

            self.set_font("Helvetica", "", 7.6)
            self.set_text_color(*MUTED)
            self.set_xy(MARGIN_L + 10, y + 5.4)
            self.multi_cell(self.epw - 10, 3.9, _clean(body), align="L",
                            new_x="LMARGIN", new_y="NEXT")
            self.ln(3.4)

        self.subsection("What this method cannot do", toc=False)
        self.paragraph(
            "Logical acquisition reads what the operating system will show an ordinary "
            "application. Deleted data that the filesystem has already unlinked, unallocated "
            "space, and app-private databases are not visible without root or a physical "
            "image. Their absence from this report is not evidence of their absence from the "
            "device.", size=8.0)

    # --- 5. results ------------------------------------------------------
    def _section_results(self, title):
        modules = self._modules()
        self.section(title, subtitle="One row per module, with what it recovered and whether it finished.")

        if not modules:
            self.paragraph("No examination results were recorded for this session.")
            return

        widths = [8, self.epw - 8 - 26 - 22 - 36, 26, 22, 36]
        rows = []
        colours = {}
        for index, module in enumerate(modules, 1):
            rows.append((
                str(index),
                module["title"],
                count_text(module["count"]),
                module["status"].title(),
                _timestamp(module["completed"]) if module["completed"] else "-",
            ))
            if module["status"] in ("failed", "error"):
                colours[(index - 1, 3)] = BAD
            elif module["status"] in ("partial", "skipped"):
                colours[(index - 1, 3)] = WARN
        self.table(["#", "Analysis module", "Items", "Status", "Completed (UTC)"],
                   rows, widths, aligns=["R", "L", "R", "L", "L"], colours=colours,
                   caption="Module summary")

        total = sum(m["count"] for m in modules)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*INK)
        self.set_xy(MARGIN_L, self.get_y())
        self.cell(self.epw, 6, f"{count_text(total)} items across {len(modules)} modules")
        self.ln(6)

        failed = [m for m in modules if m["status"] == "failed" or m["error"]]
        if failed:
            self.subsection("Modules that did not complete", toc=False)
            for module in failed:
                self._ensure(14)
                self.set_font("Helvetica", "B", 8)
                self.set_text_color(*BAD)
                self.set_xy(MARGIN_L, self.get_y())
                self.cell(self.epw, 4.6, module["title"])
                self.ln(4.8)
                self.set_font("Helvetica", "", 7.6)
                self.set_text_color(*MUTED)
                self.set_xy(MARGIN_L, self.get_y())
                self.multi_cell(self.epw, 3.9,
                                _clean(module["error"] or "The module reported a failure "
                                       "without a message.", 300),
                                align="L", new_x="LMARGIN", new_y="NEXT")
                self.ln(3.4)

    # --- 6. findings -----------------------------------------------------
    def _section_findings(self, title):
        modules = [m for m in self._modules() if m["count"] > 0]
        self.section(title, subtitle="What each module recovered, and the shape of it.")

        if not modules:
            self.paragraph("No module returned artifacts, so there are no findings to set out. "
                           "Section 8 records the limitations that applied.")
            return

        for module in modules:
            self._ensure(62)
            self.subsection(module["title"],
                            f"{count_text(module['count'])} items"
                            + (f" - {_clean(module['tools'], 40)}" if module.get("tools") else ""))

            stats = module["findings"].get("stats")
            if isinstance(stats, dict) and stats:
                pairs = [(str(k).replace("_", " ").title(), v) for k, v in list(stats.items())[:6]]
                self.stat_tiles([(count_text(v) if isinstance(v, (int, float)) else _clean(v, 12),
                                  k) for k, v in pairs],
                                per_row=min(4, len(pairs)), height=15, gap=3.0)
                self.ln(1.5)

            rendered = 0
            for key, value in module["findings"].items():
                if rendered >= 2:
                    break
                if not isinstance(value, list) or not value or not isinstance(value[0], dict):
                    continue
                rows = value[:5]
                columns = [c for c in rows[0].keys()][:5]
                headers = [_title_of(c) for c in columns]
                widths = self._column_widths(headers, columns, rows)
                body = [[_clean(item.get(c, ""), 60) for c in columns] for item in rows]
                self.table(headers, body, widths,
                           mono_cols=tuple(i for i, c in enumerate(columns)
                                           if any(t in c.lower() for t in ("date", "time", "number", "hash"))),
                           caption=module["title"])
                if len(value) > len(rows):
                    appendix = next((chr(65 + i) for i, m in enumerate(self._appendices)
                                     if m["key"] == module["key"]), "")
                    self.set_font("Helvetica", "I", 6.8)
                    self.set_text_color(*QUIET)
                    self.set_xy(MARGIN_L, self.get_y() - 2)
                    self.cell(0, 3.6, f"First {len(rows)} of {count_text(len(value))} rows - "
                                      + (f"the full table is in Appendix {appendix}."
                                         if appendix else
                                         "the remainder is in the case evidence store."))
                    self.ln(4.4)
                rendered += 1

            if not rendered:
                error = module.get("error")
                self.paragraph(_clean(error) if error else
                               "This module recorded a count but no tabular rows to display.",
                               size=7.8, space=2)

    def _column_widths(self, headers, columns, rows):
        """Column widths that follow the content, within the text width."""
        weights = []
        for index, column in enumerate(columns):
            longest = max([len(_clean(headers[index], 40))]
                          + [len(_clean(row.get(column, ""), 60)) for row in rows])
            weights.append(max(6, min(longest, 42)))
        total = sum(weights)
        return [max(14, self.epw * w / total) for w in weights]

    # --- 7. conclusions --------------------------------------------------
    def _section_conclusions(self, title):
        data = self.data
        counts = self._counts()
        modules = self._modules()
        top = [m for m in modules if m["count"] > 0][:3]

        self.section(title, subtitle="What the recovered material supports, stated plainly.")

        headline = (
            f"{count_text(counts['total'])} artifacts were recovered from the device and are "
            f"evidenced in this report. "
        )
        if top:
            headline += ("The largest recoveries were "
                         + ", ".join(f"{m['title']} ({count_text(m['count'])})" for m in top)
                         + ". ")
        headline += ("Each item can be traced from this document to a hash in the case evidence "
                     "store, and from there to the acquisition that produced it.")
        self.paragraph(headline)

        self.paragraph(
            "No conclusion in this report rests on data the tool could not acquire. Where a "
            "source refused access, the count is zero and the refusal is recorded; a zero here "
            "means 'not obtained', not 'not present'.")

        self.subsection("Findings of fact", toc=False)
        facts = [
            f"The device identified itself as {_clean((data.get('device') or {}).get('manufacturer', 'an unknown make'))} "
            f"{_clean((data.get('device') or {}).get('model', ''))}, running Android "
            f"{_clean((data.get('device') or {}).get('android_version', 'of unknown version'))}.",
            f"{counts['modules']} analysis modules were run; {counts['modules'] - counts['failed']} "
            f"completed and {counts['failed']} failed or were refused.",
            f"{count_text(counts['total'])} artifacts were written to the case store and counted here.",
            f"{len(data.get('audit_log') or [])} custody events were recorded against the session.",
        ]
        for fact in facts:
            self._ensure(9)
            y = self.get_y()
            self.set_fill_color(*ACCENT)
            self.circle(MARGIN_L + 1.6, y + 2.2, 0.9, style="F")
            self.set_font("Helvetica", "", 8.2)
            self.set_text_color(*INK)
            self.set_xy(MARGIN_L + 5, y)
            self.multi_cell(self.epw - 5, 4.4, _clean(fact), align="L",
                            new_x="LMARGIN", new_y="NEXT")
            self.ln(1.6)

        self.subsection("Recommendations", toc=False)
        recommendations = [
            "Retain the acquired evidence and this report together, unaltered, for the life of "
            "the case; separating them breaks the chain the report describes.",
            "Re-verify the SHA-256 values in the evidence store before any material is put "
            "before a court, and record the verification.",
            "Where a module was refused for want of root, obtain a rooted or physical image "
            "before drawing any negative conclusion about that class of data.",
            "Read this report alongside the evidence report, which carries the per-artifact "
            "hashes and the per-loader statuses that this document summarises.",
        ]
        for index, text in enumerate(recommendations, 1):
            self._ensure(12)
            y = self.get_y()
            self.set_font("Helvetica", "B", 8.2)
            self.set_text_color(*ACCENT)
            self.set_xy(MARGIN_L, y)
            self.cell(7, 4.4, f"{index}.")
            self.set_font("Helvetica", "", 8.2)
            self.set_text_color(*INK)
            self.set_xy(MARGIN_L + 7, y)
            self.multi_cell(self.epw - 7, 4.4, _clean(text), align="L",
                            new_x="LMARGIN", new_y="NEXT")
            self.ln(2.4)

    # --- 8. integrity ----------------------------------------------------
    def _section_integrity(self, title):
        data = self.data
        self.section(title, subtitle="How to check this document, and what it does not claim.")

        self.subsection("Report integrity", toc=False)
        self.paragraph(
            "A report cannot print its own hash: writing the digest into the document changes "
            "the digest. The SHA-256 of this file is therefore computed as the file is closed "
            "and written to a sidecar of the same name, with the suffix .sha256. That sidecar "
            "must travel with the report. To verify, recompute SHA-256 over the PDF and compare "
            "it with the value in the sidecar.")

        y = self.get_y()
        self.set_fill_color(*BAND)
        self.rect(MARGIN_L, y, self.epw, 13, style="F")
        self.set_font("Helvetica", "B", 6.6)
        self.set_text_color(*MUTED)
        self.set_xy(MARGIN_L + 4, y + 2.6)
        self.cell(self.epw - 8, 3.4, "VERIFY ON WINDOWS")
        self.set_font("Courier", "", 7.2)
        self.set_text_color(*INK)
        self.set_xy(MARGIN_L + 4, y + 6.6)
        self.cell(self.epw - 8, 4, 'certutil -hashfile "report.pdf" SHA256')
        self.set_y(y + 17)

        self.subsection("Limitations", toc=False)
        limitations = [
            "This was a logical acquisition. Unallocated space, deleted records the filesystem "
            "has already reclaimed, and data never written to accessible storage were not read.",
            "App-private data under /data/data is readable only by the owning application and "
            "by root. On the handset examined, those paths were refused, and the modules that "
            "depend on them report zero recovered items.",
            "Device file-based encryption was in force. Artifacts stored encrypted and not "
            "opened during the session may not be recoverable from the acquisition.",
            "Counts are of items the analysis modules emitted. A parser that cannot interpret a "
            "record does not invent one, so a count may understate what is present.",
            "Nothing in this report is machine opinion. The document contains no AI-generated "
            "narrative: every statement is a description of recorded data or of the method.",
        ]
        for item in limitations:
            self._ensure(10)
            y = self.get_y()
            self.set_font("Helvetica", "", 7.6)
            self.set_text_color(*MUTED)
            self.set_xy(MARGIN_L, y)
            self.cell(4, 4, "-")
            self.set_xy(MARGIN_L + 4, y)
            self.multi_cell(self.epw - 4, 4.0, _clean(item), align="L",
                            new_x="LMARGIN", new_y="NEXT")
            self.ln(1.8)

        self.ln(3)
        self.paragraph(
            f"Generated by {TOOL_NAME} {TOOL_VERSION} on "
            f"{_timestamp(data.get('generated_at'))} UTC, for case "
            f"{_clean(data.get('case_number', 'unreferenced'))}. This document is confidential, "
            "is prepared for the case file, and is not to be distributed outside the parties "
            "entitled to it.", size=7.6)

    # --- appendices ------------------------------------------------------
    def _section_appendix(self, module, index):
        self.add_page()
        letter = chr(65 + index)
        title = f"Appendix {letter} - {module['title']}"
        self.section(title, number=letter,
                     subtitle=f"{count_text(module['count'])} items recovered by this module.",
                     toc_label=module["title"])

        lists = [(key, value) for key, value in module["findings"].items()
                 if isinstance(value, list) and value and isinstance(value[0], dict)]
        if not lists:
            self.paragraph("This module recorded a count but no tabular rows, so there is no "
                           "table to reproduce here.")
            return

        for key, value in lists[:1]:
            rows = value[:120]
            columns = list(rows[0].keys())[:6]
            headers = [_title_of(c) for c in columns]
            widths = self._column_widths(headers, columns, rows)
            body = [[_clean(item.get(c, ""), 70) for c in columns] for item in rows]
            self.table(headers, body, widths, row_h=5.0, size=7.2, header_size=6.4,
                       mono_cols=tuple(i for i, c in enumerate(columns)
                                       if any(t in c.lower() for t in ("date", "time", "number", "hash"))),
                       caption=f"Appendix {letter} - {module['title']}")
            if len(value) > len(rows):
                self.set_font("Helvetica", "I", 7)
                self.set_text_color(*QUIET)
                self.set_xy(MARGIN_L, self.get_y() - 1)
                self.cell(0, 4, f"Showing the first {len(rows)} of {count_text(len(value))} rows. "
                                "The remainder is in the case evidence store.", align="L")

    def _section_views(self, letter):
        """Plates of the Artifact Viewer's own results, one category per sheet.

        This is the appendix a case officer actually flips to: it answers "did
        you get the messages, the calls, the photographs?" without asking them
        to read a table of counts. Each panel is rendered from the records
        printed beneath it, and states whether those records came from the case
        store or were read live from the handset.
        """
        total = len(self._view_panels) + len(self._view_gaps)
        live = sum(1 for panel in self._view_panels
                   if str(panel["view"].get("status", "")).lower() == "live")

        self.add_page()
        self.section(
            f"Appendix {letter} - Artifact View Results",
            number=letter,
            subtitle=f"{len(self._view_panels)} of {total} advertised artifact classes are "
                     "reproduced below, each with the record count and provenance.",
            toc_label="Artifact View Results",
        )

        self.paragraph(
            "The Artifact Viewer presents these classes as live screens, one card per "
            "category. A report cannot scroll, so each sheet below reproduces the same "
            "result: the heading, the number of records, the first rows in full and, where "
            "the class holds files, thumbnails of the files themselves. Records marked "
            "RECOVERED were read from the case store and carry its custody; records marked "
            "LIVE were read from the handset when this report was generated, which is stated "
            "on the panel because it describes the device at report time rather than at "
            "acquisition time." + (f" {live} of the panels are live." if live else ""),
            size=7.8, space=3.0)

        rows = []
        for index, panel in enumerate(self._view_panels, 1):
            view = panel["view"]
            rows.append((f"{letter}.{index}", _clean(view.get("title", ""), 46),
                         str(view.get("count")) if view.get("count") is not None else "-",
                         str(view.get("unit") or ""), str(view.get("status") or "").upper()))
        if rows:
            self.table(("Fig.", "Artifact class", "Records", "Unit", "Source"),
                       rows, (18.0, 80.0, 26.0, 30.0, 36.0),
                       aligns=("L", "L", "R", "L", "L"), row_h=5.4, size=7.2, header_size=6.4,
                       mono_cols=(0, 2), caption=f"Appendix {letter} - Artifact View Results")

        for index, panel in enumerate(self._view_panels, 1):
            self.add_page()
            view = panel["view"]
            width_px, height_px = panel["size"]
            draw_w = self.epw
            draw_h = draw_w * height_px / float(width_px)
            top = self.get_y()

            self.set_font("Courier", "B", 7.2)
            self.set_text_color(*ACCENT)
            self.set_xy(self.l_margin, top)
            self.cell(20, 4.6, f"FIGURE {letter}.{index}")
            self.set_font("Helvetica", "B", 8.4)
            self.set_text_color(*INK)
            self.set_xy(self.l_margin + 21, top - 0.4)
            self.cell(0, 5.4, _clean(view.get("title", ""), 80))

            self.image(panel["payload"], x=self.l_margin, y=top + 7.0, w=draw_w, h=draw_h)

            note_y = top + 7.0 + draw_h + 3.0
            self.set_font("Helvetica", "", 7.2)
            self.set_text_color(*MUTED)
            self.set_xy(self.l_margin, note_y)
            source = _clean(view.get("source") or "not recorded", 90)
            status = str(view.get("status") or "").upper()
            self.multi_cell(self.epw, 3.8,
                            _clean(f"{status.title()} - source: {source}. "
                                   f"{view.get('note') or ''}", 320),
                            align="L", new_x="LMARGIN", new_y="NEXT")

        if self._view_gaps:
            self.add_page()
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*INK)
            self.set_xy(self.l_margin, self.get_y())
            self.cell(0, 6, f"Appendix {letter} - Categories with no records in this case")
            self.set_y(self.get_y() + 8)

            self.paragraph(
                "These classes are advertised by the tool and absent from this case. They are "
                "listed rather than omitted so that the gap is a stated finding: a reader can "
                "tell the difference between a handset that held no such data and an examiner "
                "who did not look. Each is acquired by the corresponding acquisition phase, "
                "and a report generated after that phase will carry its panel.",
                size=7.6, space=3.0)

            rows = [(_clean(gap.get("title", ""), 40),
                     _clean(gap.get("source") or "case store", 56), "not acquired")
                    for gap in self._view_gaps]
            self.table(("Artifact class", "Expected in the case store", "Status"),
                       rows, (58.0, 100.0, 30.0), row_h=5.8, size=7.2, header_size=6.4,
                       caption=f"Appendix {letter} - Categories with no records")

    def _section_images(self, letter):
        """Plates of the images the artifact viewer showed for this handset.

        A picture is the most persuasive thing in a report and the least
        verifiable, so every plate carries the store path it came from and the
        SHA-256 of the file on disk. Nothing here is a screenshot of the tool:
        these are the acquired images themselves.
        """
        self.add_page()
        self.section(
            f"Appendix {letter} - Recovered Images",
            number=letter,
            subtitle=f"{count_text(len(self._plates))} images reproduced from the acquisition, "
                     "each with its store path and SHA-256.",
            toc_label="Recovered Images",
        )

        categories = {}
        for plate in self._plates:
            categories[plate.get("category") or "Other"] = categories.get(plate.get("category") or "Other", 0) + 1
        spread = ", ".join(f"{count_text(value)} {key.lower()}" for key, value in categories.items())

        self.paragraph(
            "The artifact viewer presents these images off the handset as camera photographs, "
            "screenshots and application media. The sample below is spread across the whole "
            f"acquisition rather than taken from its start: {spread}. Each plate is reproduced from "
            "the stored copy, not re-read from the device, so the file a reader verifies is the "
            "file that was examined. The complete set remains in the case evidence store.",
            size=7.8, space=3.5)

        columns = 2
        gap = 6.0
        cell_w = (self.epw - gap) / columns
        image_h = 47.0
        stride = 64.5

        row_top = self.get_y()
        for index, plate in enumerate(self._plates, 1):
            column = (index - 1) % columns
            if column == 0:
                if self._ensure(stride):
                    # This sheet carries no heading of its own, so it has to
                    # say what the plates belong to.
                    self.set_font("Helvetica", "I", 6.6)
                    self.set_text_color(*MUTED)
                    self.set_xy(self.l_margin, self.get_y())
                    self.cell(0, 4.2, _clean(f"Appendix {letter} - Recovered Images, "
                                             "continued", 96))
                    self.set_xy(self.l_margin, self.get_y() + 4.2)
                row_top = self.get_y()
            self._plate(MARGIN_L + column * (cell_w + gap), row_top,
                        cell_w, image_h, plate, index)
            if column == columns - 1 or index == len(self._plates):
                self.set_y(row_top + stride)

        self.set_font("Helvetica", "I", 6.8)
        self.set_text_color(*QUIET)
        self.set_xy(MARGIN_L, self.get_y() + 1)
        self.multi_cell(self.epw, 3.6,
                        "Plate order follows the store path. A file whose format this document "
                        "cannot render is still listed, with its path and hash, so it can be "
                        "opened from the evidence store.", align="L", new_x="LMARGIN", new_y="NEXT")

    def _plate(self, x, y, width, image_h, plate, number):
        self.set_fill_color(*BAND)
        self.rect(x, y, width, image_h, style="F")

        preview, size, reason = _preview(plate["path"])
        if preview is not None:
            pixel_w, pixel_h = size
            box_w, box_h = width - 3.0, image_h - 3.0
            scale = min(box_w / pixel_w, box_h / pixel_h)
            draw_w, draw_h = pixel_w * scale, pixel_h * scale
            self.image(preview, x=x + (width - draw_w) / 2, y=y + (image_h - draw_h) / 2,
                       w=draw_w, h=draw_h)
        else:
            self.set_font("Helvetica", "B", 6.4)
            self.set_text_color(*QUIET)
            self.set_xy(x, y + image_h / 2 - 5)
            self.cell(width, 4, "PREVIEW NOT AVAILABLE", align="C")
            self.set_font("Helvetica", "", 6.0)
            self.set_xy(x, y + image_h / 2 - 1)
            self.cell(width, 4, _clean(reason or "unreadable file", 60), align="C")

        self.set_draw_color(*QUIET)
        self.set_line_width(0.2)
        self.rect(x, y, width, image_h)

        caption_y = y + image_h + 2.2
        self.set_font("Courier", "B", 6.0)
        self.set_text_color(*ACCENT)
        self.set_xy(x, caption_y)
        self.cell(24, 3.4, f"PLATE {number:02d}")

        self.set_font("Helvetica", "", 6.0)
        self.set_text_color(*QUIET)
        self.set_xy(x + 24, caption_y)
        self.cell(width - 24, 3.4, _clean(str(plate.get("category") or "").upper(), 26), align="R")

        self.set_font("Courier", "", 6.6)
        self.set_text_color(*INK)
        self.set_xy(x, caption_y + 3.8)
        self.cell(width, 3.6, _clean(plate.get("name") or plate["path"].name, 58))

        self.set_font("Helvetica", "", 6.2)
        self.set_text_color(*MUTED)
        self.set_xy(x, caption_y + 7.6)
        self.cell(width, 3.4, self._fit(plate.get("source") or "", width, 6.2))

        size_text = _bytes_label(plate.get("size") or 0)
        digest = plate.get("sha256") or "not recorded"
        self.set_font("Courier", "", 5.8)
        self.set_text_color(*QUIET)
        self.set_xy(x, caption_y + 11.2)
        self.cell(width, 3.2, _clean(f"{size_text}  sha256 {digest[:28]}", 64))


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def build(data: dict, output) -> Path:
    """Render the investigation report and its integrity sidecar.

    The document is composed until a pass reproduces the page numbers printed
    by the previous one. In practice that is two passes; the loop is bounded so
    a pathological layout cannot spin.
    """
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)

    toc: list = []
    total = 0
    blob = b""
    for _ in range(4):
        pdf = InvestigationReport(data, toc=toc, total_pages=total)
        pdf._compose()
        blob = bytes(pdf.output())
        if list(pdf.toc_entries) == list(toc) and pdf.pages_count == total:
            break
        toc, total = pdf.toc_entries, pdf.pages_count

    out.write_bytes(blob)

    import hashlib

    digest = hashlib.sha256(blob).hexdigest()
    out.with_suffix(out.suffix + ".sha256").write_text(
        f"{digest}  {out.name}\n"
        f"# {TOOL_NAME} report integrity record\n"
        f"# case={_clean(data.get('case_number'))}\n"
        f"# generated={_timestamp(data.get('generated_at'))}\n"
        f'# verify: certutil -hashfile "{out.name}" SHA256\n',
        encoding="utf-8",
    )
    return out


def main(argv):
    if len(argv) < 3:
        print("Usage: python -m desktop.report.investigation_report <data.json> <out.pdf>")
        return 2
    data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    path = build(data, argv[2])
    print(f"written: {path}")
    print(f"size   : {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
