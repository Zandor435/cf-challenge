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
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_portraits import detect_face  # noqa: E402

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

    # NON-PANEL managers are keyed "<group>/<manager_id>", because `zach` is on
    # all four rosters with different art on each. Detection handles the other
    # seventeen; an entry here is the documented escape hatch for a portrait
    # the cascade cannot read.
    #
    # family/gunner: novelty joke glasses with thick round lenses defeat the
    # frontal cascade completely -- it found no face on him at all and matched
    # a small face in a framed Christmas photo on the fridge behind him
    # instead, which the MIN_FACE_FRAC floor now rejects. His head genuinely
    # fills the frame; it just cannot be detected.
    "family/gunner": {"head_top": 0.005, "chin": 0.490, "cx": 0.467,
                      "eye_y": 0.280},

    # browns/hauck: a sideline three-quarter profile -- he is looking down the
    # field, not at the camera, and the frontal cascade cannot read a head at
    # that yaw. It matched a 76px face in the crowd instead (6.6% of height),
    # which MIN_FACE_FRAC correctly rejected. His head is not small: measured
    # head_top-to-chin is 25.4% of frame height, inside the 21-27% band panel's
    # four sit in. Measured by hand off the 928x1152 source.
    "browns/hauck": {"head_top": 0.052, "chin": 0.306, "cx": 0.496,
                     "eye_y": 0.171},
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


# ---------------------------------------------------------------- detection
# Panel's four anchors were measured by hand. Eighteen more managers across
# browns/church/family is 54 numbers, and hand-measuring them is both tedious
# and the kind of thing that rots when a portrait is replaced. So derive them.
#
# CALIBRATED against panel's hand-measured ANCHORS. The OpenCV cascade box is
# very good at two of the four numbers and bad at the others:
#
#     cx     within 0.015 of measured on all four   -> trust it
#     eye_y  within 0.013 on all four               -> trust it
#     head_top  off by 0.05-0.10, inconsistently    -> do NOT trust it
#     chin      off by ~0.05                        -> do NOT trust it
#
# The reason is hair. The measured head_top is the top of the HAIR MASS, and
# hair above the cascade box ranged from 0.01x face height (Chris, cap) to
# 0.23x (Blaine, mullet). No single multiplier recovers that, which is the same
# observation the ANCHORS comment already makes.
#
# So the derived path does not estimate head_top/chin at all. It sizes the
# square off the detected face height and positions it so the EYE LINE lands at
# a fixed fraction of the crop -- built from the two numbers detection actually
# measures well. Panel keeps its hand-measured anchors; nothing here touches it.
FACE_TO_SIDE = 2.0      # square side, in detected face heights
EYE_IN_CROP = 0.33      # where the eye line sits down the finished square
EYE_IN_FACE = 0.42      # eye line down the cascade box
# The floor separates "a face in the background" from "a small but real
# subject", and both exist here. Calibrated against the two ends actually on
# disk: gunner's false positive on a photo pinned to a fridge was 4.5% of image
# height, while gayden is a CORRECT detection at 11.8% -- his source is a
# full-page catalogue scan where he is kneeling, so his face is genuinely
# small. 8% sits between them. Panel's four, for scale, run 21-27%.
MIN_FACE_FRAC = 0.08


def derive_box(im, mid):
    """Square crop box from a detected face, or raise naming the manager."""
    box = detect_face(im)
    if box is None:
        raise SystemExit(
            f"ERROR: {mid}: no frontal face detected in the source. Rule 4 -- "
            f"a center crop of someone's torso shipped silently is exactly "
            f"what this refuses. Add an ANCHORS entry by hand, or pick a "
            f"different source portrait.")
    x, y, fw, fh = box
    # PLAUSIBILITY. detect_face returns the LARGEST match, which is not the
    # same as the subject: on a snapshot with framed pictures behind the
    # subject, the only frontal match the cascade finds can be a face in a
    # photo on the wall. That is how Gunner's first avatar came out a
    # washed-out blur of somebody else's kitchen.
    #
    # A portrait's subject fills the frame. Panel's four sources put the face
    # at 0.21-0.27 of image height; anything under MIN_FACE_FRAC is not the
    # person this avatar is for, and cropping to it silently is the exact
    # failure rule 4 exists to stop.
    if fh < im.height * MIN_FACE_FRAC:
        raise SystemExit(
            f"ERROR: {mid}: the only face found is {fh}px tall in a "
            f"{im.width}x{im.height} source ({fh / im.height:.1%} of height, "
            f"floor {MIN_FACE_FRAC:.0%}). That is a background face, not the "
            f"subject. Crop the source to the subject, pick a different "
            f"portrait, or add a hand-measured ANCHORS entry.")
    side = min(round(fh * FACE_TO_SIDE), im.width, im.height)
    eye_abs = y + fh * EYE_IN_FACE
    left = round(x + fw / 2 - side / 2)
    top = round(eye_abs - side * EYE_IN_CROP)
    left = max(0, min(left, im.width - side))
    top = max(0, min(top, im.height - side))
    # Clamping is allowed -- a face near an edge is a framing compromise, not a
    # broken build -- but if it pushed the eye line out of the band the crop is
    # no longer head-and-shoulders and must not ship.
    eye_frac = (eye_abs - top) / side
    if not 0.20 <= eye_frac <= 0.45:
        raise SystemExit(
            f"ERROR: {mid}: after clamping, eyes land at {eye_frac:.2f} of the "
            f"crop (want 0.20-0.45). The face sits too near an edge of "
            f"{im.width}x{im.height} for a square head-and-shoulders cut.")
    return (left, top, left + side, top + side), eye_frac


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


def group_source(group, mid):
    """Portrait source for a non-panel group: the lowest-numbered variant.

    output/personas/<group>/<group>_<mid>_<treatment>_<nn>.<ext> -- the same
    path art_gaps.py reports on, so READY there means resolvable here.
    """
    d = ROOT / "output" / "personas" / group
    if not d.is_dir():
        return None, None
    hits = sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in
                  {".png", ".jpg", ".jpeg", ".webp"}
                  and p.stem.startswith(f"{group}_{mid}_"))
    return (hits[0], hits[0].stem.split("_")[-2]) if hits else (None, None)


def build(mid, contact_rows, group="panel"):
    """Write both sizes for one manager. Returns a list of (path, bytes)."""
    if group == "panel":
        src_path, slug = resolve_reference(mid)
        if src_path is None:
            raise SystemExit(
                f"ERROR: no kept recolor on disk for {mid!r} "
                f"(looked in {SRC_ROOT / mid} for {mid}_<variant>_gemini.png).")
    else:
        src_path, slug = group_source(group, mid)
        if src_path is None:
            raise SystemExit(
                f"ERROR: no portrait on disk for {group}/{mid!r} (looked in "
                f"{ROOT / 'output' / 'personas' / group} for "
                f"{group}_{mid}_*). Run scripts/art_gaps.py --group {group}.")

    with Image.open(src_path) as im:
        im = im.convert("RGB")
        a = ANCHORS.get(mid if group == "panel" else f"{group}/{mid}")
        if a is not None:
            # Hand-measured wins wherever it exists: panel's four anchors put
            # the hair mass where detection cannot see it.
            box = crop_box(im.size, a, f"{group}/{mid}")
            side = box[2] - box[0]
            eye_frac = (a["eye_y"] * im.height - box[1]) / side
            how = "measured"
            # Rule 4, applied to framing: a crop that puts the eyes at or below
            # the circle's center is a mis-measured anchor, and shipping it
            # silently is exactly the failure mode we refuse.
            if not 0.20 <= eye_frac <= 0.45:
                raise SystemExit(
                    f"ERROR: {mid}: eyes land at {eye_frac:.2f} of the crop "
                    f"(want 0.20-0.45, above center). Re-measure "
                    f"ANCHORS[{mid!r}].")
        else:
            box, eye_frac = derive_box(im, f"{group}/{mid}")
            side = box[2] - box[0]
            how = "detected"
        face = im.crop(box)

    out_dir = OUT_DIR if group == "panel" else OUT_DIR / group
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for px in SIZES:
        out = out_dir / f"{mid}_{px}.webp"
        face.resize((px, px), Image.LANCZOS).save(out, "WEBP", quality=88,
                                                  method=6)
        written.append((out, out.stat().st_size))
    print(f"  {mid:<11} {how:<9} src={src_path.name[:34]:<34} side={side:>4}px  "
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
    ap.add_argument("--group", default="panel",
                    help="which group's roster to build (default panel). "
                         "panel writes to docs/img/avatars/ and uses the "
                         "hand-measured ANCHORS; every other group writes to "
                         "docs/img/avatars/<group>/ and derives its crop by "
                         "face detection.")
    args = ap.parse_args(argv)

    if args.group == "panel":
        targets = args.only or sorted(ANCHORS)
        unknown = [m for m in targets if m not in ANCHORS]
        if unknown:
            raise SystemExit(f"ERROR: no ANCHORS entry for {unknown}.")
    else:
        cfg = ROOT / "groups" / args.group / "config.json"
        if not cfg.is_file():
            raise SystemExit(f"ERROR: no group {args.group!r} at {cfg}")
        roster = [m["manager_id"] for m in
                  json.loads(cfg.read_text(encoding="utf-8"))["managers"]]
        targets = args.only or roster
        unknown = [m for m in targets if m not in roster]
        if unknown:
            raise SystemExit(f"ERROR: {unknown} not on the {args.group} roster.")

    dest = OUT_DIR if args.group == "panel" else OUT_DIR / args.group
    print(f"build_avatars[{args.group}]: {len(targets)} manager(s) -> "
          f"{dest.relative_to(ROOT).as_posix()}")
    rows, total = [], 0
    for mid in targets:
        total += sum(n for _p, n in build(mid, rows, args.group))
    if args.contact:
        write_contact(rows, args.contact)
    print(f"  {len(targets) * len(SIZES)} files, {total:,} bytes total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
