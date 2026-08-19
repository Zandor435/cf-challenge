#!/usr/bin/env python3
"""Crop the approved persona art into the circular avatars the site serves.

NOT part of the live weekly pipeline. Run by hand when a manager's portrait
changes. Reads the kept recolor out of output/personas/recolor/ (gitignored,
~800KB each) and writes small web derivatives into docs/img/avatars/:

  <manager_id>_56.webp    1x — .avatar-lg is 56px, the largest on-page avatar
  <manager_id>_112.webp   2x — same crop, retina

The ~1MB source PNGs are NEVER copied into docs/. This repo is served from
GitHub Pages; only the derivatives ship.

REGENERATING output (CLAUDE.md rule 5): every run rewrites both sizes from the
source. Nothing here accumulates, so re-running is always safe -- there is no
state to clobber. It makes no network calls and touches no paid API.

Framing. The crop is a SQUARE lifted out of a portrait-orientation source and
scaled down, never squeezed, so faces are never distorted. Geometry is derived
from three measured anchors per manager (see ANCHORS) rather than hardcoded
pixel rects, so re-measuring a new portrait is three numbers, not a rect:

    head_top  top of the hair          crop starts HEADROOM head-heights above
    chin      bottom of the jaw        head height = chin - head_top
    cx        horizontal face center   crop is centered on this

    side = CROP_HEADS x head height  ->  the cut lands about one head-height
    below the chin, i.e. mid-chest, so the team garment color reads in the
    circle. Head sits near the top; the eyes land above center.

Playbook compliance (CLAUDE.md):
  - rule 1/4: the source is resolved from what is actually ON DISK via the
    REF_PREFERENCE ladder (the resolve_reference() pattern in
    scripts/generate_scenes.py). No filename convention is assumed -- Chris's
    kept variant is the jacket, not the accent, and this finds it. A manager
    with no source, no anchors, or an implausible crop FAILS LOUD and is
    NAMED; it is never silently skipped or center-cropped.
  - rule 12: the site only displays these. All framing logic lives here.

Usage:
    python scripts/build_avatars.py
    python scripts/build_avatars.py --only chris --contact review.png
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "output" / "personas" / "recolor"
OUT_DIR = ROOT / "docs" / "img" / "avatars"

# Widths to emit. 1x is the largest avatar the site actually renders
# (.avatar-lg, 56px, minus the 1px ring); 2x covers retina. Shipping a 256px
# asset for a 56px circle is the mistake this list exists to prevent.
SIZES = (56, 112)

# Mirrors scripts/generate_scenes.py: the kept recolor is whatever survived the
# selection review ON DISK, and the preference ladder picks among the survivors.
# Chris's accent variant was archived, so his jacket is what resolves here.
REF_PREFERENCE = ["accent", "jacket", "quarterzip", "polo"]

# Escape hatch for a manager whose kept art does not live under the recolor
# tree at all. Values are repo-relative paths. Empty is the normal state --
# prefer fixing what is on disk over adding an entry here.
OVERRIDES: dict[str, str] = {}

# Measured off the source images as fractions of width (cx) / height (the
# rest). Re-measure these when a portrait is replaced; everything else is
# derived. `eye_y` is not used to place the crop -- it is the assertion that
# the derived framing actually put the eyes above center.
ANCHORS = {
    # head_top is the top of the HAIR MASS, not stray strands: Blaine's mullet
    # and Jonathan's spikes otherwise inflate the head-height proxy and pull the
    # whole crop wide, shrinking the face inside the circle.
    "blaine":   {"head_top": 0.050, "chin": 0.305, "cx": 0.530, "eye_y": 0.205},
    "chris":    {"head_top": 0.080, "chin": 0.285, "cx": 0.520, "eye_y": 0.175},
    "jonathan": {"head_top": 0.055, "chin": 0.340, "cx": 0.475, "eye_y": 0.205},
    "zach":     {"head_top": 0.135, "chin": 0.310, "cx": 0.490, "eye_y": 0.220},
}

# Breathing room above the hair, and total crop height, both in head-heights.
HEADROOM = 0.10
CROP_HEADS = 2.05


def resolve_reference(mid):
    """Return (path, slug) of the kept recolor for `mid`, or (None, None).

    The resolve_reference() pattern from generate_scenes.py: glob what is
    actually on disk and rank it, rather than assuming a `_accent_` filename.
    """
    override = OVERRIDES.get(mid)
    if override:
        p = ROOT / override
        return (p, "override") if p.exists() else (None, None)
    have = {f.stem.split("_")[1]: f for f in (SRC_ROOT / mid).glob(f"{mid}_*_gemini.png")}
    for slug in REF_PREFERENCE:
        if slug in have:
            return have[slug], slug
    return None, None


def crop_box(size, a, mid):
    """Square head-and-shoulders box (left, top, right, bottom) for one source.

    Clamped into the image on every edge. Because the box is square and the
    resize below is uniform, this is a crop-and-scale -- the aspect ratio of
    the face is preserved exactly.
    """
    w, h = size
    head = (a["chin"] - a["head_top"]) * h
    if head <= 0:
        raise SystemExit(f"ERROR: {mid}: chin must sit below head_top in ANCHORS.")
    side = min(round(head * CROP_HEADS), w, h)
    top = round(a["head_top"] * h - head * HEADROOM)
    left = round(a["cx"] * w - side / 2)
    # Clamp rather than fail: a face near an edge is a framing compromise, not
    # a broken build. The eye-line check below still has to pass.
    left = max(0, min(left, w - side))
    top = max(0, min(top, h - side))
    return left, top, left + side, top + side


def build(mid, contact_rows):
    """Write both sizes for one manager. Returns a list of (path, bytes)."""
    src_path, slug = resolve_reference(mid)
    if src_path is None:
        raise SystemExit(
            f"ERROR: no kept recolor on disk for {mid!r} "
            f"(looked in {SRC_ROOT / mid} for {mid}_<variant>_gemini.png).")
    a = ANCHORS.get(mid)
    if a is None:
        raise SystemExit(
            f"ERROR: no ANCHORS entry for {mid!r}. Measure head_top/chin/cx/eye_y "
            f"off {src_path.name} and add it -- refusing to guess a crop.")

    with Image.open(src_path) as im:
        im = im.convert("RGB")
        box = crop_box(im.size, a, mid)
        face = im.crop(box)

    side = box[2] - box[0]
    eye_frac = (a["eye_y"] * im.height - box[1]) / side
    # Rule 4, applied to framing: a crop that puts the eyes at or below the
    # circle's center is a mis-measured anchor, and shipping it silently is
    # exactly the failure mode we refuse. 0.20-0.45 is "above center" without
    # being so high the chin falls out of the circle.
    if not 0.20 <= eye_frac <= 0.45:
        raise SystemExit(
            f"ERROR: {mid}: eyes land at {eye_frac:.2f} of the crop "
            f"(want 0.20-0.45, above center). Re-measure ANCHORS[{mid!r}].")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for px in SIZES:
        out = OUT_DIR / f"{mid}_{px}.webp"
        face.resize((px, px), Image.LANCZOS).save(out, "WEBP", quality=88, method=6)
        written.append((out, out.stat().st_size))
    print(f"  {mid:<9} src={src_path.name} ({slug})  crop={box} side={side}px  "
          f"eyes@{eye_frac:.2f}  -> " +
          ", ".join(f"{p.name} {n:,}B" for p, n in written))
    contact_rows.append((mid, face))
    return written


def write_contact(rows, path):
    """Round-crop preview sheet at 2x, for eyeballing framing before commit."""
    px, pad = 112, 12
    sheet = Image.new("RGB", (len(rows) * (px + pad) + pad, px + 2 * pad), "white")
    mask = Image.new("L", (px * 4, px * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, px * 4 - 1, px * 4 - 1), fill=255)
    mask = mask.resize((px, px), Image.LANCZOS)
    for i, (_mid, face) in enumerate(rows):
        sheet.paste(face.resize((px, px), Image.LANCZOS),
                    (pad + i * (px + pad), pad), mask)
    sheet.save(path)
    print(f"  contact sheet -> {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", action="append", metavar="MANAGER",
                    help="build just these managers (repeatable)")
    ap.add_argument("--contact", metavar="PATH",
                    help="also write a round-cropped preview sheet here")
    args = ap.parse_args(argv)

    targets = args.only or sorted(ANCHORS)
    unknown = [m for m in targets if m not in ANCHORS]
    if unknown:
        raise SystemExit(f"ERROR: no ANCHORS entry for {unknown}.")

    print(f"build_avatars: {len(targets)} manager(s) -> {OUT_DIR.relative_to(ROOT)}")
    rows, total = [], 0
    for mid in targets:
        total += sum(n for _p, n in build(mid, rows))
    if args.contact:
        write_contact(rows, args.contact)
    print(f"  {len(targets) * len(SIZES)} files, {total:,} bytes total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
