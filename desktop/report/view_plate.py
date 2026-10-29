"""Draw one artifact-view result panel as an image.

The Artifact Viewer is a live screen: dark cards, a coloured category badge and
a scrolling list. A report cannot scroll, but it can reproduce the *result* --
the heading, the record count, the first rows and, where the category holds
files, thumbnails of the files themselves.

Rendering that as an image rather than drawing it in the PDF buys two things
the report needs:

  * the panel can be laid out with the same primitives the viewer uses
    (fixed-width record numbers, ellipsised cells, thumbnail crops) without
    dragging a single new dependency into the frozen build -- Pillow is
    already there for the artifact viewer;
  * a panel is a plate. It carries a figure number, a provenance line and a
    timestamp, and it can be dropped if the category was never acquired,
    rather than quietly printing an empty table.

Nothing here invents data: the caller hands over rows that were read from the
case store or, where the store has none, from the handset, and each panel
states which of the two it is.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

# Palette copied from the application so a panel reads as the same product.
INK = (226, 232, 240)
MUTED = (148, 163, 184)
QUIET = (100, 116, 139)
BG = (11, 18, 32)
CARD = (17, 26, 44)
BAND = (21, 32, 52)
RULE = (38, 52, 77)

_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/System/Library/Fonts"),
)


def _font(names, size):
    """First font of ``names`` that exists, else Pillow's bitmap default."""
    from PIL import ImageFont

    for name in names:
        for folder in _FONT_DIRS:
            path = folder / name
            try:
                if path.is_file():
                    return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _faces(size_body=21, size_small=17):
    return {
        "title": _font(("segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"), 44),
        "label": _font(("segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"), 17),
        "body": _font(("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"), size_body),
        "small": _font(("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"), size_small),
        "mono": _font(("consola.ttf", "DejaVuSansMono.ttf", "Courier New.ttf"), size_small),
        "mono_bold": _font(("consolab.ttf", "DejaVuSansMono-Bold.ttf", "Courier New Bold.ttf"), size_small),
    }


def _ellipsis(draw, text, font, width):
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    if draw.textlength(text, font=font) <= width:
        return text
    cut = text
    while cut and draw.textlength(cut + "…", font=font) > width:
        cut = cut[:-1]
    return (cut + "…") if cut else ""


def _cover(image, box_w, box_h):
    """Crop-to-fill, so a portrait photo and a landscape shot share a cell."""
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image).convert("RGB")
    scale = max(box_w / image.width, box_h / image.height)
    resized = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                           resample=3)
    left = (resized.width - box_w) // 2
    top = (resized.height - box_h) // 2
    return resized.crop((left, top, left + box_w, top + box_h))


#: Emoji and variation selectors. Segoe UI has no glyph for them, so Pillow
#: draws a hollow box -- which then sits in a forensic report next to a
#: contact's real name. The name is the evidence; the pictogram is not.
_UNDRAWABLE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U0000FE00-\U0000FE0F\u200d\u20e3]"
)


def _plain(text) -> str:
    return _UNDRAWABLE.sub("", str(text or "")).strip()


def _hex(value, fallback):
    value = str(value or "").lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _count_text(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value or "0")
    return f"{number:,}"


def render(view: dict, *, width: int = 1240, max_thumbs: int = 6) -> tuple[bytes, tuple[int, int]]:
    """Return ``(jpeg_bytes, (width_px, height_px))`` for one category panel."""
    from PIL import Image, ImageDraw

    accent = _hex(view.get("colour"), (249, 115, 22))
    faces = _faces()
    rows = [tuple(_plain(cell) for cell in row)[:3] for row in (view.get("rows") or [])[:8]]
    thumbs = [Path(p) for p in (view.get("thumbs") or [])][:max_thumbs]
    title = _plain(view.get("title", ""))
    source = _plain(view.get("source", ""))
    note = _plain(view.get("note", ""))
    stamp = _plain(view.get("stamp", ""))

    pad = 44
    header_h = 176
    row_h = 46
    thumb_w, thumb_h = 356, 232
    gap = 20
    thumb_cols = 3
    thumb_rows = (len(thumbs) + thumb_cols - 1) // thumb_cols if thumbs else 0
    thumb_block = thumb_rows * (thumb_h + gap) if thumbs else 0
    footer_h = 92
    height = header_h + thumb_block + (len(rows) * row_h if rows else 0) + footer_h + pad

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    # Frame and header band
    draw.rectangle((0, 0, width - 1, header_h), fill=CARD)
    draw.rectangle((0, 0, width - 1, 6), fill=accent)
    draw.rectangle((pad, header_h - 1, width - pad, header_h - 1), fill=RULE)

    draw.text((pad, 34), "ARTIFACT VIEWER", font=faces["label"], fill=accent)
    title = _ellipsis(draw, title, faces["title"], width - pad * 2 - 300)
    draw.text((pad, 62), title, font=faces["title"], fill=INK)

    count = view.get("count")
    count_label = f"{_count_text(count)} {view.get('unit') or 'records'}" if count is not None else ""
    if count_label:
        tw = draw.textlength(count_label, font=faces["mono_bold"])
        x0 = width - pad - tw - 34
        draw.rounded_rectangle((x0, 74, width - pad, 118), radius=22, fill=BAND, outline=RULE)
        draw.text((x0 + 17, 85), count_label, font=faces["mono_bold"], fill=INK)

    source = _ellipsis(draw, source, faces["mono"], width - pad * 2 - 250)
    draw.text((pad, 126), source, font=faces["mono"], fill=MUTED)

    status = (view.get("status") or "").upper()
    if status:
        colour = {"RECOVERED": (52, 211, 153), "LIVE": (251, 191, 36),
                  "PARTIAL": (251, 191, 36)}.get(status, QUIET)
        sw = draw.textlength(status, font=faces["label"])
        draw.text((width - pad - sw, 128), status, font=faces["label"], fill=colour)

    y = header_h + 26

    # Thumbnails
    for index, path in enumerate(thumbs):
        column = index % thumb_cols
        row = index // thumb_cols
        x = pad + column * (thumb_w + gap)
        ty = y + row * (thumb_h + gap)
        draw.rounded_rectangle((x, ty, x + thumb_w, ty + thumb_h), radius=8, fill=BAND)
        try:
            with Image.open(path) as raw:
                cell = _cover(raw, thumb_w - 4, thumb_h - 4)
            image.paste(cell, (x + 2, ty + 2))
        except Exception:
            draw.text((x + 16, ty + thumb_h // 2 - 10), "preview unavailable",
                      font=faces["small"], fill=QUIET)
        draw.rounded_rectangle((x, ty, x + thumb_w, ty + thumb_h), radius=8, outline=RULE)
        caption = _ellipsis(draw, path.name, faces["mono"], thumb_w - 16)
        draw.rectangle((x, ty + thumb_h - 30, x + thumb_w, ty + thumb_h), fill=(8, 13, 24))
        draw.text((x + 8, ty + thumb_h - 26), caption, font=faces["mono"], fill=MUTED)
    if thumbs:
        y += thumb_block + 6

    # Result rows
    for index, row in enumerate(rows):
        if index % 2 == 0:
            draw.rectangle((pad - 10, y - 4, width - pad + 10, y + row_h - 8), fill=BAND)
        cells = list(row) + ["", "", ""]
        primary, secondary, trailing = cells[0], cells[1], cells[2]
        p_width = int(width * 0.40)
        s_width = int(width * 0.36)
        draw.text((pad, y + 4), _ellipsis(draw, primary, faces["body"], p_width), font=faces["body"], fill=INK)
        draw.text((pad + p_width + 16, y + 4),
                  _ellipsis(draw, secondary, faces["body"], s_width), font=faces["body"], fill=MUTED)
        trail = _ellipsis(draw, trailing, faces["mono"], width - pad - (pad + p_width + s_width + 32))
        trailing_w = draw.textlength(trail, font=faces["mono"])
        draw.text((width - pad - trailing_w, y + 5), trail, font=faces["mono"], fill=QUIET)
        y += row_h

    if not rows and not thumbs:
        note = note or "No records were acquired for this category."
        draw.text((pad, y + 10), _ellipsis(draw, note, faces["body"], width - pad * 2),
                  font=faces["body"], fill=QUIET)
        y += 40

    # Footer
    footer_y = height - footer_h + 18
    draw.rectangle((pad, footer_y - 12, width - pad, footer_y - 11), fill=RULE)
    note = _ellipsis(draw, note, faces["small"], width - pad * 2)
    if note:
        draw.text((pad, footer_y), note, font=faces["small"], fill=MUTED)
    stamp = _ellipsis(draw, stamp, faces["mono"], width - pad * 2)
    draw.text((pad, footer_y + 30), stamp, font=faces["mono"], fill=QUIET)

    payload = io.BytesIO()
    image.save(payload, format="JPEG", quality=86, optimize=True)
    return payload.getvalue(), (width, height)
