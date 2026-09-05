#!/usr/bin/env python3
"""Push an already-published portrait further into the fat-coach treatment,
offline: a deterministic liquify pass plus painted forehead sweat.

    python scripts/fatten_portrait.py --group browns --manager hauck [--preview]

WHY THIS EXISTS. make_fatcoach.py --keep-scene is the online way to make a
published portrait heavier: it hands the approved frame back to the image model
with SCENE_KEEP pinning the backdrop. That path needs GEMINI_API_KEY and bills
the rule-6 budget, and it re-renders every pixel -- including the face, the
Kentucky lettering and the crowd -- so a "make him heavier" round can quietly
come back with a different man in a differently spelled stadium. That is the
failure the hauck batch already produced once (see TEAM_MARKS in
make_fatcoach.py).

This script is the other half of that pair: no network, no key, no budget, and
NOTHING is re-rendered. It moves pixels that are already his -- a smooth mesh
warp for the build and a painted specular layer for the sweat -- so the face,
the garment, the lettering and the backdrop survive the pass by construction
rather than by prompt. Use it when the ask is "more of the same person", and
use make_fatcoach.py when the ask is a new picture.

WHAT IT CHANGES
  build   row-wise horizontal magnification over the torso (`hwiden`), an
          elliptical belly bulge, and a small downward sag so the gut hangs
          instead of floating. hwiden is per-ROW, so vertical lines -- the
          zip, the lanyard, the seam -- stay vertical and the head, which sits
          above the profile's first stop, is not touched at all.
  jowls   the same widening, small, over the jaw and neck only.
  sweat   a wet specular sheen that lifts the highlights ALREADY on his
          forehead (never a flat brightening, which reads as a blown-out
          patch), then seeded beads with an up-left specular dot and a shaded
          underside, then an optional trickle. Painted through a skin mask, so
          it cannot land on hair, the headset or the crowd behind him.

DETERMINISTIC. Every bead position comes from a seeded numpy Generator held in
the recipe, so re-running writes byte-identical files and a rebuild never
reshuffles the sweat.

OUTPUT (rule 5: every one of these REGENERATES, nothing accumulates)
  docs/assets/profiles/<g>/<id>.webp        the profile-page hero frame
  docs/assets/portraits/<g>/<id>.webp       the roster poster (long edge 900)
  docs/assets/portraits/<g>/<id>-face.webp  the roster circle (256)
  docs/img/avatars/<g>/<id>_{56,112}.webp   the small avatars

The two torn cuts are NOT written here -- they are generated from the hero
frame, so run the repo's own builder afterwards and it re-tears the new art
with the same seeded edge:

  python scripts/build_profile_heroes.py --force --only <g>/<id>
  python scripts/build_profile_heroes.py --force --side left --only <g>/<id>

WHY THE DERIVATIVES ARE CUT HERE. build_avatars.py and prepare_portraits.py
read output/personas/, which is gitignored -- on a fresh clone the original
generation source is simply not on disk, so neither can re-cut a face from the
edited frame. The crop geometry they used is reproduced instead: the avatar box
is build_avatars.ANCHORS["browns/hauck"] run through its own HEADROOM /
CROP_HEADS formula (matched against the published avatar to within 1px), and
the face box was measured off the published face crop the same way. Framing is
therefore unchanged; only the pixels inside it are new.

NOT IDEMPOTENT, AND GUARDED FOR IT. The hero frame is both the input and an
output, so a second run would fatten an already-fattened man and keep going.
The written hero therefore carries an XMP stamp, and a source that already
bears one is REFUSED by name (rule 4: loud, not silent). To redo the pass,
restore the pristine frame from git first --
`git checkout <commit> -- docs/assets/profiles/<g>/<id>.webp` -- rather than
running twice.

--preview writes the same set under output/fatten/<g>/ (gitignored) and leaves
docs/ alone, which is what a pass held for review wants.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent


def rel(p):
    """Repo-relative for printing, absolute if it somehow lives outside."""
    try:
        return p.resolve().relative_to(ROOT)
    except ValueError:
        return p

# The frame every recipe below is measured in. A source of another size is
# scaled into it, so the numbers stay meaningful if the hero is ever republished
# at a different resolution.
REF_W, REF_H = 967, 1200

POSTER_PX = 900          # prepare_portraits.py --poster-px
FACE_PX = 256            # prepare_portraits.py --face-px
AVATAR_PX = (56, 112)    # build_avatars.py SIZES
Q_HERO = 88              # build_profile_heroes.py / build_avatars.py
Q_POSTER = 82            # prepare_portraits.py --quality

# Written into the hero's XMP and read back as the "already done" guard.
STAMP = b"cf-challenge:fatten"


# --------------------------------------------------------------- warp field
def _sample(arr, xs, ys):
    """Bilinear resample. Coordinates are CLAMPED, never wrapped: a warp that
    reaches past the frame edge repeats the edge pixel instead of folding the
    crowd back into the picture."""
    h, w = arr.shape[:2]
    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    fx = (xs - x0)[..., None]
    fy = (ys - y0)[..., None]
    return (arr[y0, x0] * (1 - fx) * (1 - fy) + arr[y0, x1] * fx * (1 - fy) +
            arr[y1, x0] * (1 - fx) * fy + arr[y1, x1] * fx * fy)


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _profile(v, stops):
    """Piecewise-smooth 1-D profile through (position, value) stops."""
    out = np.full_like(v, stops[0][1], dtype=np.float32)
    for (p0, v0), (p1, v1) in zip(stops, stops[1:]):
        seg = v0 + (v1 - v0) * _smoothstep((v - p0) / float(p1 - p0))
        out = np.where((v > p0) & (v <= p1), seg, out)
    return np.where(v > stops[-1][0], stops[-1][1], out)


def hwiden(X, Y, cx, amp_y, half, feather):
    """Row-wise horizontal magnification about cx.

    Per-row and horizontal-only, which is the whole reason this is not a plain
    bulge: a radial bulge big enough to read as a belly also fish-eyes the arm
    and bows every vertical seam in the quarter-zip. `half`/`feather` bound it
    in x so the play-call sheet at the frame edge and the crowd behind him stay
    exactly where they are.
    """
    a = _profile(Y, amp_y)
    taper = 1.0 - _smoothstep((np.abs(X - cx) - half) / float(feather))
    return -(X - cx) * a * taper, np.zeros_like(X)


def bulge(X, Y, cx, cy, rx, ry, power):
    """Elliptical liquify bulge -- content pushed outward from (cx, cy).

    Output at normalised radius t samples from t**power, so power > 1 magnifies
    the middle and walks the silhouette outward over the background. Monotonic
    in t, so it cannot fold.
    """
    u = (X - cx) / rx
    v = (Y - cy) / ry
    r = np.sqrt(u * u + v * v)
    scale = np.where(r < 1.0, np.clip(r, 1e-6, None) ** (power - 1.0), 1.0)
    return (X - cx) * (scale - 1.0), (Y - cy) * (scale - 1.0)


def sag(X, Y, cx, cy, rx, ry, amp):
    """Push content DOWN inside an ellipse. A widened belly with no sag reads
    as a barrel chest; the gut has to hang."""
    u = (X - cx) / rx
    v = (Y - cy) / ry
    k = _smoothstep(1.0 - np.sqrt(u * u + v * v))
    return np.zeros_like(X), -amp * k


WARPS = {"hwiden": hwiden, "bulge": bulge, "sag": sag}


def warp(im, ops):
    arr = np.asarray(im, dtype=np.float32)
    h, w = arr.shape[:2]
    X, Y = np.meshgrid(np.arange(w, dtype=np.float32),
                       np.arange(h, dtype=np.float32))
    sx, sy = X.copy(), Y.copy()
    # Composed by chaining SOURCE coordinates: each op is evaluated at the
    # coordinates the previous ops resolved to, so the belly bulge acts on the
    # already-widened torso rather than on the original one.
    for kind, params in ops:
        dx, dy = WARPS[kind](sx, sy, **params)
        sx, sy = sx + dx, sy + dy
    return Image.fromarray(np.clip(_sample(arr, sx, sy), 0, 255).astype(np.uint8))


# -------------------------------------------------------------------- sweat
def _blur(a, radius):
    return np.asarray(Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(radius)),
                      dtype=np.float32) / 255.0


def _skin(arr):
    """Loose RGB skin rule. It does not have to be a good skin detector -- it
    only has to keep the paint off his hair, the headset and the crowd, and it
    is multiplied by a hand-placed ellipse that has already excluded almost
    everything else."""
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    m = ((r > 95) & (g > 55) & (b > 40) & (r > g + 8) & (g >= b - 10) &
         (r - np.minimum(g, b) > 12))
    return m.astype(np.float32)


def _ellipse(shape, cx, cy, rx, ry, rot=0.0, feather=0.4):
    h, w = shape
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    ca, sa = np.cos(rot), np.sin(rot)
    u = ((X - cx) * ca + (Y - cy) * sa) / rx
    v = (-(X - cx) * sa + (Y - cy) * ca) / ry
    return _smoothstep((1.0 - np.sqrt(u * u + v * v)) / feather)


def sheen(arr, mask, strength):
    """Wet look. Lifts the highlights that are ALREADY on the skin (a power
    curve on luminance) instead of raising the whole patch -- brightening flat
    tone gives a matte grey smear, and the difference between those two is the
    difference between sweat and a bad clone-stamp."""
    lum = arr.mean(axis=2) / 255.0
    spec = np.clip((lum - 0.42) / 0.38, 0, 1) ** 1.6
    k = (mask * spec * strength)[..., None]
    return arr * (1 - k) + np.array([255.0, 250.0, 238.0]) * k


def beads(arr, mask, rng, n, rmin, rmax):
    """Individual drops: a bright specular dot up-left (the stadium key light),
    a soft body, and a shaded underside. Without the shaded side a drop is a
    white speck and reads as dust or a blemish, not as liquid."""
    h, w = arr.shape[:2]
    ys, xs = np.nonzero(mask > 0.55)
    if len(xs) == 0:
        raise SystemExit("ERROR: sweat mask covers no skin -- re-measure the "
                         "forehead ellipse for this portrait.")
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    hi = np.zeros((h, w), np.float32)
    lo = np.zeros((h, w), np.float32)
    for _ in range(n):
        i = rng.integers(len(xs))
        cx, cy = float(xs[i]), float(ys[i])
        r = rng.uniform(rmin, rmax)
        el = 1.0 + rng.uniform(0.0, 0.9)          # drops elongate downward
        body = np.clip(1.0 - np.sqrt(((X - cx) / r) ** 2 +
                                     ((Y - cy) / (r * el)) ** 2), 0, 1) ** 0.7
        spec = np.clip(1.0 - np.sqrt(((X - (cx - r * .32)) / (r * .42)) ** 2 +
                                     ((Y - (cy - r * .45)) / (r * .42)) ** 2),
                       0, 1) ** 0.6
        hi = np.maximum(hi, spec * rng.uniform(.75, 1.0))
        hi = np.maximum(hi, body * 0.18)
        under = np.clip(1.0 - np.sqrt(((X - (cx + r * .18)) / (r * .85)) ** 2 +
                                      ((Y - (cy + r * .55 * el)) / (r * .75 * el)) ** 2),
                        0, 1) ** 0.9
        lo = np.maximum(lo, under * 0.55 * (1 - body * .5))
    hi = _blur(hi, 0.7) * mask
    lo = _blur(lo, 0.9) * mask
    out = arr * (1 - 0.55 * lo[..., None]) + np.array([70., 55., 48.]) * (0.55 * lo)[..., None]
    return out * (1 - hi[..., None]) + np.array([255., 253., 246.]) * hi[..., None]


def trickle(arr, pts, width, mask, alpha):
    """A bead that has already run: a wet band with a bright leading edge."""
    h, w = arr.shape[:2]
    Y, X = np.mgrid[0:h, 0:w].astype(np.float32)
    band = np.zeros((h, w), np.float32)
    edge = np.zeros((h, w), np.float32)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        for t in np.linspace(0, 1, int(max(abs(x1 - x0), abs(y1 - y0))) + 1):
            cx, cy = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            band = np.maximum(band, np.clip(1 - np.sqrt(((X - cx) / width) ** 2 +
                                                        ((Y - cy) / width) ** 2), 0, 1))
            edge = np.maximum(edge, np.clip(1 - np.sqrt(
                ((X - (cx - width * .35)) / (width * .38)) ** 2 +
                ((Y - cy) / (width * .38)) ** 2), 0, 1))
    band = _blur(band, 0.8) * mask * alpha
    edge = _blur(edge, 0.6) * mask * alpha
    out = arr * (1 - 0.30 * band[..., None]) + np.array([90., 70., 60.]) * (0.30 * band)[..., None]
    return out * (1 - 0.75 * edge[..., None]) + np.array([255., 252., 245.]) * (0.75 * edge)[..., None]


# ------------------------------------------------------------------ recipes
# Every coordinate is in the REF_W x REF_H frame above and was measured off the
# published portrait. crop boxes are (x0, y0, side) in that same frame.
RECIPES = {
    "browns/hauck": {
        "warp": [
            # Torso. Zero above y=385 (the jaw), so the head is untouched.
            ("hwiden", dict(cx=445, half=250, feather=215,
                            amp_y=[(385, 0.0), (560, 0.16), (730, 0.32),
                                   (1000, 0.32), (1200, 0.272)])),
            # Belly. Centred on his near side, where the gut actually sits.
            ("bulge", dict(cx=372, cy=800, rx=340, ry=310, power=1.22)),
            ("sag", dict(cx=360, cy=830, rx=300, ry=250, amp=18)),
            # FACE. Amp is 0 at y=210, above the cheekbones, so the eyes,
            # nose and brow keep their spacing and their distances from each
            # other -- that is what carries the likeness. Everything below it
            # is fair game: the widening peaks across the jaw and runs on into
            # the neck, so the head does not sit on a collar it has outgrown.
            ("hwiden", dict(cx=400, half=118, feather=105,
                            amp_y=[(210, 0.0), (262, 0.16), (322, 0.32),
                                   (392, 0.36), (445, 0.26), (495, 0.0)])),
            # Jowls: rounds the jaw and lower cheek that the widening only
            # stretched. Widening alone reads as a squashed photo; the bulge
            # is what makes it read as weight.
            ("bulge", dict(cx=404, cy=328, rx=128, ry=104, power=1.28)),
            # The double chin -- the jaw line pushed down into the neck.
            ("sag", dict(cx=408, cy=376, rx=100, ry=70, amp=24)),
            # Cheek, small: fills the hollow under the cheekbone.
            ("bulge", dict(cx=392, cy=266, rx=92, ry=70, power=1.14)),
        ],
        # Forehead + the temple beside it. Deliberately stops above the eye:
        # a bead placed at the outer eye corner runs down the cheek and reads
        # as a tear, which is a different picture entirely.
        "mask": [dict(cx=396, cy=168, rx=80, ry=34, rot=-0.22, feather=0.5),
                 dict(cx=455, cy=192, rx=26, ry=30, feather=0.5)],
        "shine": dict(cx=386, cy=160, rx=62, ry=22, rot=-0.25, feather=0.95),
        "shine_alpha": 0.20,
        "sheen": 0.92,
        "seed": 11,
        "beads": dict(n=24, rmin=3.6, rmax=7.2),
        "trickles": [dict(pts=[(353, 188), (349, 212), (348, 236), (351, 256)],
                          width=2.3, alpha=0.9)],
        # build_avatars.ANCHORS["browns/hauck"] through its HEADROOM/CROP_HEADS
        # formula lands here; verified against the published avatar.
        "avatar_box": (168, 32, 624),
        # Measured off the published hauck-face.webp.
        "face_box": (248, 53, 480),
    },
}


def sweat(im, r):
    arr = np.asarray(im, dtype=np.float32)
    skin = _blur(_skin(arr), 1.6)
    shape = arr.shape[:2]
    mask = np.clip(sum(_ellipse(shape, **e) for e in r["mask"]), 0, 1)
    mask = mask * np.clip(skin * 1.35, 0, 1)
    out = sheen(arr, mask, r["sheen"])
    if r.get("shine"):
        sh = _ellipse(shape, **r["shine"]) * mask * r["shine_alpha"]
        out = out * (1 - sh[..., None]) + np.array([255., 251., 242.]) * sh[..., None]
    rng = np.random.default_rng(r["seed"])
    out = beads(out, mask, rng, **r["beads"])
    for t in r.get("trickles", []):
        out = trickle(out, t["pts"], t["width"], np.clip(skin * 1.2, 0, 1),
                      t["alpha"])
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def scaled(recipe, sx, sy):
    """Recipes are written in the REF frame; a source of another size gets the
    numbers scaled rather than a silently mis-placed forehead."""
    if abs(sx - 1) < 1e-9 and abs(sy - 1) < 1e-9:
        return recipe
    def s(d, keys):
        out = dict(d)
        for k, f in keys.items():
            if k in out:
                out[k] = out[k] * f
        return out
    r = dict(recipe)
    ops = []
    for kind, p in recipe["warp"]:
        p = s(p, {"cx": sx, "cy": sy, "rx": sx, "ry": sy, "half": sx,
                  "feather": sx, "amp": sy})
        if "amp_y" in p:
            p["amp_y"] = [(y * sy, a) for y, a in p["amp_y"]]
        ops.append((kind, p))
    r["warp"] = ops
    r["mask"] = [s(e, {"cx": sx, "cy": sy, "rx": sx, "ry": sy}) for e in recipe["mask"]]
    if recipe.get("shine"):
        r["shine"] = s(recipe["shine"], {"cx": sx, "cy": sy, "rx": sx, "ry": sy})
    r["beads"] = s(recipe["beads"], {"rmin": sx, "rmax": sx})
    r["trickles"] = [dict(t, width=t["width"] * sx,
                          pts=[(x * sx, y * sy) for x, y in t["pts"]])
                     for t in recipe.get("trickles", [])]
    r["avatar_box"] = tuple(v * f for v, f in zip(recipe["avatar_box"], (sx, sy, sx)))
    r["face_box"] = tuple(v * f for v, f in zip(recipe["face_box"], (sx, sy, sx)))
    return r


def square(im, box, px):
    x, y, side = (round(v) for v in box)
    return im.crop((x, y, x + side, y + side)).resize((px, px), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--manager", required=True)
    ap.add_argument("--source", default=None,
                    help="frame to edit; default is the published profile hero")
    ap.add_argument("--force", action="store_true",
                    help="run even though the source is already stamped as "
                         "edited; the warp COMPOUNDS. See the module docstring.")
    ap.add_argument("--preview", action="store_true",
                    help="write under output/fatten/<group>/ and leave docs/ "
                         "untouched")
    a = ap.parse_args()

    key = f"{a.group}/{a.manager}"
    if key not in RECIPES:
        raise SystemExit(f"ERROR: no recipe for {key}. Recipes are measured off "
                         f"one portrait each; add one rather than reusing "
                         f"another manager's numbers.")
    src = Path(a.source) if a.source else (
        ROOT / "docs" / "assets" / "profiles" / a.group / f"{a.manager}.webp")
    if not src.is_absolute():
        src = ROOT / src
    if not src.is_file():
        raise SystemExit(f"ERROR: source not found: {src}")

    with Image.open(src) as probe:
        stamped = STAMP in (probe.info.get("xmp") or b"")
    if stamped and not a.force:
        raise SystemExit(
            f"ERROR: {rel(src)} has already had this pass applied "
            f"(XMP stamp). Running again would compound the warp. Restore the "
            f"pristine frame from git first:\n"
            f"  git checkout <commit> -- {rel(src)}\n"
            f"or pass --force if compounding is genuinely what you want.")

    im = Image.open(src).convert("RGB")
    r = scaled(RECIPES[key], im.width / REF_W, im.height / REF_H)
    print(f"{key}: {rel(src)} {im.width}x{im.height}")
    out = sweat(warp(im, r["warp"]), r)

    if a.preview:
        hero_dir = ROOT / "output" / "fatten" / a.group
        poster_dir = face_dir = avatar_dir = hero_dir
    else:
        hero_dir = ROOT / "docs" / "assets" / "profiles" / a.group
        poster_dir = face_dir = ROOT / "docs" / "assets" / "portraits" / a.group
        avatar_dir = ROOT / "docs" / "img" / "avatars" / a.group
    for d in {hero_dir, poster_dir, face_dir, avatar_dir}:
        d.mkdir(parents=True, exist_ok=True)

    written = []
    hero = hero_dir / f"{a.manager}.webp"
    out.save(hero, "WEBP", quality=Q_HERO, method=6, xmp=STAMP)
    written.append(hero)

    poster = out.copy()
    poster.thumbnail((POSTER_PX, POSTER_PX), Image.LANCZOS)
    p = poster_dir / (f"{a.manager}-poster.webp" if a.preview
                      else f"{a.manager}.webp")
    poster.save(p, "WEBP", quality=Q_POSTER, method=6)
    written.append(p)

    f = face_dir / f"{a.manager}-face.webp"
    square(out, r["face_box"], FACE_PX).save(f, "WEBP", quality=Q_POSTER,
                                             method=6)
    written.append(f)

    av = square(out, r["avatar_box"], max(AVATAR_PX))
    for px in AVATAR_PX:
        p = avatar_dir / f"{a.manager}_{px}.webp"
        av.resize((px, px), Image.LANCZOS).save(p, "WEBP", quality=Q_HERO,
                                                method=6)
        written.append(p)

    for p in written:
        print(f"  wrote {rel(p)}  {p.stat().st_size:,}B")
    if a.preview:
        print("\npreview only; docs/ untouched.")
        return 0
    print("\nNow re-tear the hero (the cuts are built FROM it):")
    print(f"  python scripts/build_profile_heroes.py --force --only {key}")
    print(f"  python scripts/build_profile_heroes.py --force --side left "
          f"--only {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
