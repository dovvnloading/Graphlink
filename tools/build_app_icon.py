"""Render the canonical Graphlink app mark into a multi-resolution .ico.

Run:  python tools/build_app_icon.py

WHY THIS EXISTS RATHER THAN A ONE-LINER
---------------------------------------
The previous assets/graphlink.ico was a single 1024x1024 bitmap in an .ico
container - no 16/24/32 frames at all - so every place Windows actually
shows an app icon (title bar at 16px, taskbar at 24-32px, Alt-Tab) was
brute-force downscaling a poster. Thin strokes and small details vanish
under that kind of reduction.

Real icon pipelines don't downscale one drawing; they redraw it per size.
This script does the same thing in miniature: the geometry below is the
canonical mark (assets/branding/icon-h2-branch-chevron.svg), but the
stroke weight and the margin are tuned PER TARGET SIZE so that:

  - at 16px the stroke lands on ~2 whole pixels instead of 1.75 (which
    would smear across three), and the mark fills more of the box; and
  - at 128px+ the stroke returns to the canonical 7/64 proportion, so the
    large sizes match the SVG exactly.

The renderer is deliberately Pillow-only. cairosvg is not usable on this
machine (libcairo-2.dll is absent) and adding a native dependency to the
build for one asset would be a poor trade. Round caps/joins are emulated
exactly the way SVG defines them - a disc of radius stroke/2 at every
vertex - so the raster output matches the vector source rather than
approximating it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
ICO_PATH = REPO_ROOT / "assets" / "graphlink.ico"
FAVICON_DIR = REPO_ROOT / "web_ui" / "src" / "app" / "public"

GREEN = "#45c46f"
BLUE = "#4a90e2"
EDGE = "#7d8590"

# The canonical mark, in the same 64-unit space as the SVG source. Each
# entry is (points, colour); every polyline is stroked with round caps and
# round joins, matching the SVG's stroke-linecap/linejoin="round".
# Chevron geometry is a balance between two failure modes, both of which
# were hit while tuning this:
#
#   too narrow / too heavy -> the aperture between the arms' inner edges
#     closes and the chevron renders as a solid arrowhead. A 12-unit arm
#     against a 7-unit stroke left under 1.5px of opening at 16px.
#   too wide  -> the arms splay past roughly 80 degrees and the pair stops
#     reading as two arrows, becoming a flat zigzag.
#
# 8 x 11 arms at a 6-unit stroke (about a 72 degree opening) sits between
# them: the V stays open down to 16px and each half still reads as an arrow.
STROKES: list[tuple[list[tuple[float, float]], str]] = [
    ([(32, 58), (32, 45)], EDGE),                 # stem
    ([(32, 45), (18, 32), (18, 21)], GREEN),      # left branch
    ([(10, 31), (18, 20), (26, 31)], GREEN),      # left chevron tip
    ([(32, 45), (46, 32), (46, 21)], BLUE),       # right branch
    ([(38, 31), (46, 20), (54, 31)], BLUE),       # right chevron tip
]

CANONICAL_STROKE = 6.0

# Per-size tuning. `margin` is the fraction of the target box left empty on
# each side; small sizes get a tighter margin so the mark fills more of a
# cramped box. This is the hand-tuning the old single-bitmap .ico had no way
# to express.
#
# Stroke stays CONSTANT across every size. The instinct is to fatten strokes
# at small sizes so they land on whole pixels, but that was tried and made
# things worse: here the binding constraint is the chevron aperture, and a
# heavier stroke closes the V. Legibility comes from preserving that opening,
# not from pixel-snapping the line.
SIZES: dict[int, dict[str, float]] = {
    16: {"stroke": CANONICAL_STROKE, "margin": 0.02},
    20: {"stroke": CANONICAL_STROKE, "margin": 0.03},
    24: {"stroke": CANONICAL_STROKE, "margin": 0.04},
    32: {"stroke": CANONICAL_STROKE, "margin": 0.05},
    48: {"stroke": CANONICAL_STROKE, "margin": 0.06},
    64: {"stroke": CANONICAL_STROKE, "margin": 0.07},
    128: {"stroke": CANONICAL_STROKE, "margin": 0.08},
    256: {"stroke": CANONICAL_STROKE, "margin": 0.08},
}

# Supersampling factor. The mark is drawn this many times larger than the
# target and then reduced with LANCZOS, which is what produces clean
# antialiased edges from plain polygon fills.
SUPERSAMPLE = 8


def _art_bounds(stroke: float) -> tuple[float, float, float, float]:
    """Tight bounds of the drawn mark INCLUDING the stroke's own width.

    Centring on the 64-unit viewBox would be wrong: the mark is not
    centred in it (it sits low, spanning y 18-58), so a naive centre would
    leave a visible weight imbalance at small sizes.
    """
    xs = [p[0] for pts, _ in STROKES for p in pts]
    ys = [p[1] for pts, _ in STROKES for p in pts]
    half = stroke / 2.0
    return min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half


def _draw_polyline(draw: ImageDraw.ImageDraw, pts, width: float, colour: str) -> None:
    w = max(1, int(round(width)))
    if len(pts) > 1:
        draw.line(pts, fill=colour, width=w, joint="curve")
    # Round caps and joins: SVG defines both as a disc of radius stroke/2
    # centred on the vertex, so drawing one at every point reproduces the
    # vector result rather than approximating it.
    r = w / 2.0
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)


def render(size: int, stroke: float, margin: float) -> Image.Image:
    ss = size * SUPERSAMPLE
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x0, y0, x1, y1 = _art_bounds(stroke)
    art_w, art_h = x1 - x0, y1 - y0

    content = ss * (1.0 - 2.0 * margin)
    scale = content / max(art_w, art_h)
    # Centre the mark's true bounding box in the target box.
    off_x = (ss - art_w * scale) / 2.0 - x0 * scale
    off_y = (ss - art_h * scale) / 2.0 - y0 * scale

    def tx(p):
        return (p[0] * scale + off_x, p[1] * scale + off_y)

    for pts, colour in STROKES:
        _draw_polyline(draw, [tx(p) for p in pts], stroke * scale, colour)

    return img.resize((size, size), Image.LANCZOS)


def build_svg() -> str:
    """Emit the canonical SVG from the SAME geometry the raster uses.

    The vector source and the .ico are generated from one definition on
    purpose: hand-maintaining both is how they silently drift, and a brand
    mark whose vector and bitmap disagree is worse than having only one.
    """
    paths = []
    for pts, colour in STROKES:
        d = f"M{pts[0][0]} {pts[0][1]}" + "".join(f"L{x} {y}" for x, y in pts[1:])
        paths.append(f'    <path d="{d}" stroke="{colour}"/>')
    body = "\n".join(paths)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"\n'
        '     role="img" aria-labelledby="t d">\n'
        "  <title id=\"t\">Graphlink</title>\n"
        '  <desc id="d">A stem forking into two directed branches - the Graphlink app mark.</desc>\n'
        f'  <g fill="none" stroke-width="{CANONICAL_STROKE:g}" stroke-linecap="round" stroke-linejoin="round">\n'
        f"{body}\n"
        "  </g>\n</svg>\n"
    )


def main() -> int:
    frames = {size: render(size, cfg["stroke"], cfg["margin"]) for size, cfg in SIZES.items()}

    svg = build_svg()
    canonical_svg = REPO_ROOT / "assets" / "branding" / "icon-h2-branch-chevron.svg"
    canonical_svg.write_text(svg, encoding="utf-8")
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    (FAVICON_DIR / "favicon.svg").write_text(svg, encoding="utf-8")

    ordered = [frames[s] for s in sorted(frames, reverse=True)]
    base, rest = ordered[0], ordered[1:]
    ICO_PATH.parent.mkdir(parents=True, exist_ok=True)
    # append_images embeds each hand-tuned render as its own frame. Without
    # it Pillow would resize `base` down for every size, which is exactly
    # the single-bitmap problem this script exists to fix.
    base.save(ICO_PATH, format="ICO", sizes=[im.size for im in ordered], append_images=rest)

    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    fav = [frames[s] for s in (16, 32, 48) if s in frames]
    fav[-1].save(FAVICON_DIR / "favicon.ico", format="ICO",
                 sizes=[im.size for im in fav], append_images=fav[:-1])

    print(f"wrote {ICO_PATH.relative_to(REPO_ROOT)}  ({ICO_PATH.stat().st_size:,} bytes)")
    print(f"      frames: {', '.join(f'{s}x{s}' for s in sorted(frames))}")
    for p in (FAVICON_DIR / "favicon.ico", FAVICON_DIR / "favicon.svg", canonical_svg):
        print(f"wrote {p.relative_to(REPO_ROOT)}  ({p.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
