#!/usr/bin/env python3
"""Pair half-card plates into head-to-head matchup composites.

NOT part of the live weekly pipeline. Run by hand.

    output/halfcards/panel/panel_half_<manager>_<facing>_<style>_<nn>.png
        -> output/matchups/panel/panel_vs_<a>_<b>_<style>_<seam>.png

NO KEYING. The plates came back on flat near-black with rim light along the
profile edge, which keys badly: the lit fringe halos and the shadow side is
swallowed. Woodcut and noir put large parts of the subject at background value,
so no threshold separates them at all. Each plate therefore stays an OPAQUE
RECTANGLE and the two butt together at a hard vertical seam, which is what real
fight bills do anyway. There is deliberately no alpha, no chroma key, no
matting and no background replacement anywhere in this file. If you are about
to add a threshold, don't.

Alignment is the only hard part. The plates were generated independently, so
head scale and eye height drift despite the brief specifying identical framing.
Trusting the crop puts one man visibly larger than the other, so everything is
normalized on the EYE LINE instead:

  - inter-eye distance sets scale (both plates scale to the SMALLER of the two,
    so nothing is ever upsampled)
  - eye line sets vertical placement, landing slightly above centre
  - bottoms fall where they fall; the composite is cut to the shorter of the
    two, measured from the top. No padding, no stretching.

Eye lines come from data/halfcard_eyelines.json. A value present in that file
ALWAYS wins over detection -- that is the escape hatch, so a bad detection is
fixable by editing one number instead of touching code. A plate that cannot be
detected is written to that file as null and its pairings are skipped and
named, never silently defaulted.

Usage:
    python scripts/composite_halfcards.py
    python scripts/composite_halfcards.py --styles noir --seams rule
    python scripts/composite_halfcards.py --redetect     # ignore cached autos
"""

import argparse
import hashlib
import itertools
import json
import math
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
PLATES = ROOT / "output" / "halfcards" / "panel"
OUT = ROOT / "output" / "matchups" / "panel"
EYELINES = ROOT / "data" / "halfcard_eyelines.json"

MANAGERS = ["blaine", "chris", "jonathan", "zach"]
SEAMS = ["rule", "split", "starburst"]

# panel_half_<manager>_<facing>_<style>_<nn>.png
PLATE_RE = re.compile(
    r"^panel_half_(?P<mid>[a-z]+)_(?P<facing>right|left)_"
    r"(?P<style>[a-z0-9]+)_(?P<nn>\d+)\.png$")

EYE_LINE_FRACTION = 0.42   # eye line sits slightly above the vertical centre
BAND_FRACTION = 1 / 3      # flat dark band across the lower third
BAND_COLOR = (14, 14, 16)

def rel(path: Path) -> str:
    """Repo-relative when possible. --plates/--out may point anywhere (a
    scratch dir during verification), and relative_to raises on those."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


CASCADES = ["haarcascade_frontalface_default.xml",
            "haarcascade_frontalface_alt2.xml",
            "haarcascade_profileface.xml"]


# ---------- eye detection ---------------------------------------------------
def _cascade(name):
    return cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / name))


def detect_eyes(path: Path):
    """Return {"eye_y", "eye_dx"} in plate pixels, or None if undetectable.

    Two eyes are required: inter-eye distance is what sets scale, and a single
    eye gives no distance. A face box alone is NOT accepted as a fallback --
    face-box width tracks head yaw as much as head size, which is exactly the
    silent mis-scale this whole approach exists to avoid.
    """
    bgr = cv2.imread(str(path))
    if bgr is None:
        return None
    gray = cv2.equalizeHist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    h, w = gray.shape

    faces = []
    for name in CASCADES:
        c = _cascade(name)
        if c.empty():
            continue
        found = c.detectMultiScale(gray, scaleFactor=1.06, minNeighbors=5,
                                   minSize=(int(w * 0.15), int(h * 0.12)))
        faces.extend(found)
        # profileface only matches one direction; mirror to catch the other.
        if "profile" in name:
            flipped = c.detectMultiScale(cv2.flip(gray, 1), scaleFactor=1.06,
                                         minNeighbors=5,
                                         minSize=(int(w * 0.15), int(h * 0.12)))
            faces.extend([(w - x - fw, y, fw, fh) for x, y, fw, fh in flipped])
    if not faces:
        return None

    fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
    # Eyes live in the upper half of a face box; searching lower invites
    # nostrils and mouth corners.
    roi = gray[fy:fy + int(fh * 0.6), fx:fx + fw]
    if roi.size == 0:
        return None
    eyes = _cascade("haarcascade_eye.xml").detectMultiScale(
        roi, scaleFactor=1.05, minNeighbors=6,
        minSize=(max(8, int(fw * 0.08)), max(8, int(fw * 0.08))))
    if len(eyes) < 2:
        return None

    # Take the two largest, then require they sit side by side rather than
    # stacked -- a stacked pair is one eye detected twice at two scales.
    eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
    cx = [fx + ex + ew / 2 for ex, ey, ew, eh in eyes]
    cy = [fy + ey + eh / 2 for ex, ey, ew, eh in eyes]
    dx = abs(cx[0] - cx[1])
    dy = abs(cy[0] - cy[1])
    if dx < fw * 0.12 or dy > dx * 0.6:
        return None
    return {"eye_y": float(sum(cy) / 2), "eye_dx": float(dx)}


def load_eyelines():
    if EYELINES.exists():
        try:
            return json.loads(EYELINES.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_eyelines(data):
    EYELINES.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_note": (
        "Eye line and inter-eye distance per half-card plate, in plate pixels. "
        "A non-null entry here ALWAYS wins over automatic detection, so a bad "
        "detection is fixed by editing numbers, never by touching code. A null "
        "entry means detection failed: fill in eye_y (height of the eye line "
        "from the top of the plate) and eye_dx (horizontal distance between "
        "the two pupils) by eye, and that pairing starts compositing. "
        "source=detected was found automatically; source=manual is yours."),
        **{k: v for k, v in sorted(data.items()) if k != "_note"}}
    EYELINES.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def eyeline_for(path: Path, cache: dict, redetect: bool):
    """Cached value wins; otherwise detect and record. None => unusable."""
    key = path.stem
    if not redetect and key in cache:
        return cache[key]          # may be None, meaning "awaiting manual"
    got = detect_eyes(path)
    cache[key] = dict(got, source="detected") if got else None
    return cache[key]


# ---------- geometry --------------------------------------------------------
def scaled(img: Image.Image, eye: dict, target_dx: float):
    """Scale a plate so its inter-eye distance equals target_dx."""
    f = target_dx / eye["eye_dx"]
    if abs(f - 1.0) < 1e-6:
        return img, eye["eye_y"]
    size = (max(1, int(round(img.width * f))), max(1, int(round(img.height * f))))
    return img.resize(size, Image.LANCZOS), eye["eye_y"] * f


def assemble(left_img, left_eye_y, right_img, right_eye_y, overlap_frac=0.0):
    """Place two plates with their eye lines on one horizontal.

    Returns (layer_left, layer_right, size). Both layers are FULL canvas size
    with one plate placed on it, so a seam can choose between them per pixel.
    overlap_frac > 0 pulls the plates toward each other, creating a vertical
    band where BOTH layers have real content -- that overlap is what lets the
    jagged seam interlock instead of stretching a half to fit.
    """
    half_w = min(left_img.width, right_img.width)

    def hcrop(im, keep_right_edge):
        if im.width == half_w:
            return im
        x = im.width - half_w if keep_right_edge else 0
        x = max(0, min(x, im.width - half_w))
        return im.crop((x, 0, x + half_w, im.height))

    left_img = hcrop(left_img, keep_right_edge=False)
    right_img = hcrop(right_img, keep_right_edge=True)

    overlap = int(half_w * overlap_frac)
    W = half_w * 2 - overlap

    # Eye line lands at E; a plate's top edge lands at E - its own eye_y, so E
    # must clear the deepest eye line or that plate would leave a gap at top.
    E = max(left_eye_y, right_eye_y)
    top_l, top_r = E - left_eye_y, E - right_eye_y
    H = int(min(top_l + left_img.height, top_r + right_img.height))

    layer_l = Image.new("RGB", (W, H), BAND_COLOR)
    layer_r = Image.new("RGB", (W, H), BAND_COLOR)
    layer_l.paste(left_img, (0, int(round(top_l))))
    layer_r.paste(right_img, (W - half_w, int(round(top_r))))

    # Raise the eye line to sit slightly above centre by trimming from the TOP
    # only. Never pad -- if it is already high enough, leave it alone.
    c = (E - EYE_LINE_FRACTION * H) / (1 - EYE_LINE_FRACTION)
    c = int(max(0, min(c, H * 0.4)))
    if c:
        layer_l = layer_l.crop((0, c, W, H))
        layer_r = layer_r.crop((0, c, W, H))
    return layer_l, layer_r


def flatten(layer_l, layer_r):
    """Straight vertical join down the centre."""
    W, H = layer_l.size
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rectangle([0, 0, W // 2, H], fill=255)
    return Image.composite(layer_l, layer_r, mask)



# ---------- seams -----------------------------------------------------------
def seam_rule(canvas):
    """A hard bar with a thin bright inner line on each side."""
    d = ImageDraw.Draw(canvas)
    W, H = canvas.size
    bw = max(6, int(W * 0.028))
    x0, x1 = W // 2 - bw // 2, W // 2 + bw // 2
    d.rectangle([x0, 0, x1, H], fill=(8, 8, 10))
    lw = max(1, bw // 10)
    d.rectangle([x0, 0, x0 + lw, H], fill=(236, 231, 214))
    d.rectangle([x1 - lw, 0, x1, H], fill=(236, 231, 214))
    return canvas


def seam_split(layer_l, layer_r, seed):
    """A jagged lightning break: the two plates genuinely interlock.

    The zigzag is used as the compositing MASK across the overlap band, so each
    side of the break is real plate content. An earlier version stretched each
    half to full width and composited those, which slammed both heads into the
    seam and left one side reading empty.
    """
    W, H = layer_l.size
    rng = np.random.default_rng(seed)
    steps = 16
    ys = np.linspace(0, H, steps + 1)
    amp = W * 0.05
    xs = W / 2 + rng.uniform(-amp, amp, steps + 1)
    xs[0] = xs[-1] = W / 2

    mask = Image.new("L", (W, H), 0)
    pts = [(float(xs[i]), float(ys[i])) for i in range(steps + 1)]
    ImageDraw.Draw(mask).polygon([(0, 0)] + pts + [(0, H)], fill=255)
    out = Image.composite(layer_l, layer_r, mask)
    ImageDraw.Draw(out).line(pts, fill=(236, 231, 214),
                             width=max(2, int(W * 0.0035)))
    return out


def seam_starburst(canvas):
    """Plates meet flush; rays radiate over the join from the centre point."""
    W, H = canvas.size
    cx, cy = W / 2, H * EYE_LINE_FRACTION
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    rays, R = 28, math.hypot(W, H)
    for i in range(rays):
        a0 = (2 * math.pi / rays) * i
        a1 = a0 + (2 * math.pi / rays) * 0.46
        pts = [(cx, cy),
               (cx + R * math.cos(a0), cy + R * math.sin(a0)),
               (cx + R * math.cos(a1), cy + R * math.sin(a1))]
        od.polygon(pts, fill=(255, 240, 200, 26 if i % 2 else 52))
    od.ellipse([cx - W * 0.035, cy - W * 0.035, cx + W * 0.035, cy + W * 0.035],
               fill=(255, 248, 224, 220))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def draw_band(canvas):
    """Flat dark band across the lower third. Nothing is baked into it -- the
    site overlays names and live numbers there in HTML."""
    W, H = canvas.size
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, int(H * (1 - BAND_FRACTION)), W, H], fill=BAND_COLOR)
    return canvas


# ---------- main ------------------------------------------------------------
def discover(plate_dir: Path):
    """Index plates as {(mid, facing, style): path}, styles discovered from
    the filenames rather than hardcoded."""
    index, styles = {}, set()
    for f in sorted(plate_dir.glob("panel_half_*.png")):
        m = PLATE_RE.match(f.name)
        if not m:
            continue
        index[(m["mid"], m["facing"], m["style"])] = f
        styles.add(m["style"])
    return index, sorted(styles)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plates", default=str(PLATES))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--styles", default=None, help="default: all found on disk")
    ap.add_argument("--seams", default=",".join(SEAMS))
    ap.add_argument("--redetect", action="store_true",
                    help="ignore cached auto-detections (manual entries still win)")
    args = ap.parse_args()

    plate_dir = Path(args.plates)
    out_dir = Path(args.out)
    index, found_styles = discover(plate_dir)
    if not index:
        print(f"No plates found in {plate_dir}", file=sys.stderr)
        print("Nothing to composite. Generate the half-card plates first.")
        return 1

    styles = ([s.strip() for s in args.styles.split(",") if s.strip()]
              if args.styles else found_styles)
    seams = [s.strip() for s in args.seams.split(",") if s.strip()]
    bad = [s for s in seams if s not in SEAMS]
    if bad:
        print(f"ERROR: unknown seam(s) {bad}. Available: {', '.join(SEAMS)}",
              file=sys.stderr)
        return 1

    cache = load_eyelines()
    cache.pop("_note", None)
    out_dir.mkdir(parents=True, exist_ok=True)

    pairings = list(itertools.combinations(sorted(MANAGERS), 2))
    written, skipped, undetected = 0, [], set()

    for style in styles:
        for a, b in pairings:
            # Alphabetical order fixes BOTH the filename and the frame
            # position, and each slot demands a specific facing. Two men facing
            # the same way is a bug, not a variant -- assert rather than hope.
            assert a < b, f"pairing {a},{b} is not alphabetical"
            left_key, right_key = (a, "right", style), (b, "left", style)
            assert left_key[1] == "right" and right_key[1] == "left"

            missing = [f"panel_half_{k[0]}_{k[1]}_{k[2]}_01.png"
                       for k in (left_key, right_key) if k not in index]
            if missing:
                skipped.append((f"{a}_{b}_{style}", "missing plate(s): "
                                + ", ".join(missing)))
                continue

            lp, rp = index[left_key], index[right_key]
            le, re_ = eyeline_for(lp, cache, args.redetect), \
                eyeline_for(rp, cache, args.redetect)
            bad_plates = [p.name for p, e in ((lp, le), (rp, re_)) if not e]
            if bad_plates:
                undetected.update(bad_plates)
                skipped.append((f"{a}_{b}_{style}",
                                "eye detection failed: " + ", ".join(bad_plates)))
                continue

            li, ri = Image.open(lp).convert("RGB"), Image.open(rp).convert("RGB")
            target_dx = min(le["eye_dx"], re_["eye_dx"])   # downscale only
            li_s, l_eye = scaled(li, le, target_dx)
            ri_s, r_eye = scaled(ri, re_, target_dx)
            # Flush layers for rule/starburst; overlapped layers for split, so
            # the jagged break has real plate content on both sides of the tear.
            flush = assemble(li_s, l_eye, ri_s, r_eye)
            torn = assemble(li_s, l_eye, ri_s, r_eye, overlap_frac=0.14)

            for seam in seams:
                if seam == "split":
                    # Deterministic per pairing. hash() is salted per process,
                    # so the same input would tear differently between runs.
                    seed = int.from_bytes(
                        hashlib.sha1(f"{a}{b}{style}".encode()).digest()[:4], "big")
                    img = seam_split(torn[0], torn[1], seed)
                elif seam == "starburst":
                    img = seam_starburst(flatten(*flush))
                else:
                    img = seam_rule(flatten(*flush))
                img = draw_band(img)
                path = out_dir / f"panel_vs_{a}_{b}_{style}_{seam}.png"
                img.save(path)
                written += 1
                print(f"  wrote {rel(path)} ({img.width}x{img.height})")

    save_eyelines(cache)

    print(f"\ncomposites written: {written}")
    print(f"pairings skipped:   {len(skipped)}")
    for name, why in skipped:
        print(f"  {name}: {why}")
    if undetected:
        print(f"\nplates needing a manual eye line ({len(undetected)}) -- "
              f"fill them in at {rel(EYELINES)}:")
        for n in sorted(undetected):
            print(f"  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
