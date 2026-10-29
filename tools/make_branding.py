"""Build the branding assets the application ships, from the source artwork.

Run once after the artwork changes; the outputs are committed so a build never
depends on Pillow being the right version:

    python tools/make_branding.py

What it produces, in ``desktop/assets/branding/``:

    logo.png       the wordmark on transparency, for the sidebar
    icon.png       the X emblem on its own plate, for the window and the taskbar
    icon.ico       the same emblem, multi-size, for the executable

The source images are dark-navy artwork with light lettering, not the other
way round, so the background is keyed out against the sampled corner colour
rather than against white. The maths is an un-premultiply:

    stored = colour * alpha + background * (1 - alpha)
    colour = (stored - background * (1 - alpha)) / alpha

which recovers the true colour of a half-covered edge pixel instead of leaving
the pale halo a threshold cut produces.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path.home() / "Desktop" / "DroidForenX"
OUT = ROOT / "desktop" / "assets" / "branding"

LOGO_SOURCE = SOURCE / "DroidForenX  logo.png"

#: The sidebar logo is 189 px wide at 1x; 2x keeps it sharp on HiDPI.
LOGO_WIDTH = 560

#: Icon plate rounding, as a fraction of the canvas.
ICON_RADIUS = 0.19


def background_colour(image: Image.Image) -> tuple:
    """The artwork's own background, sampled from a corner."""
    rgb = image.convert("RGB")
    corner = rgb.crop((0, 0, 8, 8))
    pixels = list(corner.getdata())
    return tuple(sum(channel) // len(pixels) for channel in zip(*pixels))


def key_out_background(image: Image.Image, background: tuple,
                       floor: int = 10) -> Image.Image:
    """Key ``background`` out to transparency, un-premultiplying the edges.

    ``floor`` is the smallest distance from the background that still counts as
    content -- below it the pixel is the background's own sensor noise, and
    forcing it to alpha 0 stops the exported JPEG-ish grain from turning into a
    grey film over the whole image.
    """
    rgb = image.convert("RGB")
    out = Image.new("RGBA", rgb.size, (0, 0, 0, 0))
    src = rgb.load()
    dst = out.load()
    br, bg, bb = background
    width, height = rgb.size

    for y in range(height):
        for x in range(width):
            r, g, b = src[x, y]
            # Distance from the background, normalised per channel by how far
            # that channel could travel before the background's edge.
            distance = max(abs(r - br), abs(g - bg), abs(b - bb))
            if distance <= floor:
                continue
            alpha = min(255, int(round(distance * 255 / max(1, 255 - max(background)))))
            if alpha <= 0:
                continue
            keep = alpha / 255.0
            inv = 1.0 - keep
            nr = (r - br * inv) / keep
            ng = (g - bg * inv) / keep
            nb = (b - bb * inv) / keep
            dst[x, y] = (max(0, min(255, int(round(nr)))),
                         max(0, min(255, int(round(ng)))),
                         max(0, min(255, int(round(nb)))),
                         alpha)
    return out


def content_box(image: Image.Image, background: tuple, tolerance: int = 24) -> tuple:
    """Bounding box of everything that differs from the background."""
    rgb = image.convert("RGB")
    br, bg, bb = background
    mask = Image.new("L", rgb.size, 0)
    src = rgb.load()
    dst = mask.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = src[x, y]
            if max(abs(r - br), abs(g - bg), abs(b - bb)) > tolerance:
                dst[x, y] = 255
    return mask.getbbox() or (0, 0, rgb.width, rgb.height)


def build_logo() -> Image.Image:
    """The wordmark alone, on transparency, at twice its display width."""
    source = Image.open(LOGO_SOURCE).convert("RGB")
    background = background_colour(source)
    box = content_box(source, background)
    # A couple of pixels of slack all round, so the glow is not sliced at the
    # crop and nothing sits flush against the edge of the displayed image.
    pad = max(4, source.height // 60)
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(source.width, box[2] + pad), min(source.height, box[3] + pad))
    cropped = source.crop(box)
    ratio = LOGO_WIDTH / cropped.width
    resized = cropped.resize((LOGO_WIDTH, max(1, round(cropped.height * ratio))),
                             Image.Resampling.LANCZOS)
    logo = key_out_background(resized, background)
    logo.save(OUT / "logo.png")
    return logo


#: The emblem's own box, as fractions of the wordmark. Measured off the
#: artwork rather than detected: the final "N" and the X are a dozen pixels
#: apart, and the HUD ticks run to the right edge, so a gap-finding heuristic
#: either swallows the N or splits the X. These numbers take the X and nothing
#: else.
EMBLEM_BOX = (0.7825, 0.302, 0.9563, 0.727)


def build_emblem() -> Image.Image:
    """The X emblem on its own plate, squared off.

    A 2400x993 wordmark in a 256 px icon is a grey smudge, so the executable takes
    the emblem -- the one part of the mark that still reads at 16 px. The navy
    plate is the artwork's own background, cropped rather than redrawn, so the
    drop shadow and the circuit traces sit on the surface they were designed for.
    """
    source = Image.open(LOGO_SOURCE).convert("RGB")
    width, height = source.size
    left, top, right, bottom = EMBLEM_BOX
    box = (int(width * left), int(height * top),
           int(width * right), int(height * bottom))

    # Square the crop up from the mark's top-left corner, growing right and down.
    # Growing from the centre instead pulls the box back over the "N" that ends
    # a dozen pixels to the left of the X.
    side = max(box[2] - box[0], box[3] - box[1])
    side += int(side * 0.08)
    box = (box[0], box[1],
           min(width, box[0] + side), min(height, box[1] + side))
    return source.crop(box)


def build_icon(emblem: Image.Image) -> None:
    """Resize the emblem to a 512 px icon with rounded corners, then make the .ico."""
    size = 512
    square = emblem.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1),
                                           radius=int(size * ICON_RADIUS), fill=255)
    square.putalpha(mask)
    square.save(OUT / "icon.png")
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    square.save(OUT / "icon.ico", sizes=sizes)


def keep_sources() -> None:
    """Copy the artwork in, so the derived files can be rebuilt by anyone."""
    folder = OUT / "source"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LOGO_SOURCE, folder / "droidforenx-logo.png")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    build_logo()
    build_icon(build_emblem())
    keep_sources()
    for path in sorted(OUT.glob("*.*")):
        with Image.open(path) as image:
            print(f"[+] {path.name:<16} {image.size[0]}x{image.size[1]} {image.mode} "
                  f"{path.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
