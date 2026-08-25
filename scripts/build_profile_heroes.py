"""Build the single-manager page's bleed heroes: docs/assets/profiles/<g>/<id>-ripped.webp

WHAT THIS MAKES. profile.html shows one manager's art filling the left panel
edge-to-edge: straight on top, left and bottom so it bleeds into the card, and
torn on the RIGHT ONLY, where it breaks into the copy column. That cut is a
separate file from <id>.webp, which stays exactly as it is for the roster
cards on managers.html — there the art sits inside a bordered figure and a
ragged transparent edge reads as a rendering fault.

WHICH GROUPS. panel, church and browns only. Their art is the AI coach-poster
treatment, which is where a painted tear belongs. FAMILY IS DELIBERATELY
EXCLUDED and that is not an oversight: art_slots.json documents its art as
"real family photographs and period gag artifacts rather than the AI
coach-card treatment -- a deliberate difference in kind". A painted tear reads
as a joke edge, and a joke edge on a real photograph of somebody's father is
a different register from one on a generated coach poster. That distinction is
about the ART, not about the prose, so it survives the retirement of the tone
gate. Family gets the same page, with its photograph presented straight.

BLAINE IS SKIPPED. His cut comes from a hand-painted source with a real brush
edge (output/personas/panel/panel_blaine_ripped_01.png) via
scripts/build_ripped_hero.py, and that hand-made edge beats anything
synthesised here. Overwriting it would be a downgrade.

DETERMINISTIC. The tear is seeded from the manager id, so re-running produces
byte-identical output and a rebuild never silently reshuffles everyone's art.

WHICH EDGE. The tear opens toward the copy, so it follows the layout: a
portrait-LEFT profile tears on the RIGHT (the default, and every file published
today), and a portrait-RIGHT profile has to tear on the LEFT or the ragged edge
runs into the page margin instead of into the column it is supposed to break
into. Which managers sit on which side is profile_order in
groups/<g>/personas.json -- odd positions mirror. See docs/managers.js.

    python scripts/build_profile_heroes.py [--force]
    python scripts/build_profile_heroes.py --side left --only panel/chris         --out-root output/profile_heroes_left

THE LEFT TEAR IS THE RIGHT TEAR, MIRRORED -- same seed, same profile, same
flecks, flipped. Not a fresh random edge: the two cuts of one portrait have to
be the same treatment or the page reads as two different art directions, and a
second seed would also mean the before/after sheet is comparing two variables
at once. Only the ALPHA is flipped. The photograph is never mirrored -- that
would hand somebody a reversed face and a reversed jersey.

--out-root stages the build somewhere other than docs/ (repo-root output/ is
gitignored), which is what a pass that is held for review wants: nothing the
live page resolves changes until a file is copied into docs/ on purpose.
"""
import argparse
import glob
import hashlib
import os
import sys

import numpy as np
from PIL import Image

GROUPS = ("panel", "church", "browns")
SKIP = {("panel", "blaine")}          # hand-painted; see build_ripped_hero.py
LONG_EDGE = 1200
DEPTH_FRAC = 0.09                     # tear depth as a fraction of width


def smooth(x, sigma):
    """Gaussian blur of a 1-D signal, with edge-clamped padding."""
    radius = max(1, int(sigma * 3))
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(x, radius, mode="edge"), k, mode="valid")


def tear_alpha(w, h, seed, side="right"):
    """Alpha for a single-edge paint tear. Straight everywhere else.

    side="left" returns the same tear mirrored -- see the module docstring for
    why it is a flip rather than a second seed.
    """
    rng = np.random.default_rng(seed)
    depth = w * DEPTH_FRAC

    # Two octaves: a slow wander that gives the edge its shape, and a finer
    # one that keeps it from reading as a smooth curve. One octave alone looks
    # like a torn sheet OR like static; the pair looks like a brush.
    n = smooth(rng.standard_normal(h), h / 38.0)
    n = n / (np.abs(n).max() + 1e-9)
    f = smooth(rng.standard_normal(h), h / 260.0)
    f = f / (np.abs(f).max() + 1e-9)
    prof = 0.52 + 0.62 * n + 0.26 * f
    prof = np.clip(prof, 0.05, 1.35)

    edge_x = w - depth * prof                      # per-row cut position
    xs = np.arange(w)[None, :]
    # 1.5px ramp instead of a hard step, or the diagonal parts of the edge
    # alias into visible stair-steps.
    a = np.clip((edge_x[:, None] - xs) / 1.5 + 0.5, 0.0, 1.0)

    # Flecks thrown past the edge, thinning out with distance — the part that
    # actually reads as paint rather than as a cut.
    beyond = np.clip((xs - edge_x[:, None]) / depth, 0.0, 1.0)
    keep = (rng.random((h, w)) < 0.22 * np.exp(-3.4 * beyond)) & (beyond > 0)
    keep &= rng.random((h, w)) < 0.5
    a = np.maximum(a, keep.astype(np.float32))
    if side == "left":
        a = np.fliplr(a)
    return (a * 255).astype(np.uint8)


def build(path, group, mid, force, side="right", out_root=None):
    stem = os.path.basename(path)[:-5]
    # The left cut is a SECOND FILE, never a replacement: the right cut stays
    # exactly as it is for every portrait-left manager, and a portrait that
    # moves sides is a manifest edit rather than a re-render.
    suffix = "-ripped.webp" if side == "right" else "-ripped-left.webp"
    out_dir = os.path.dirname(path)
    if out_root:
        out_dir = os.path.join(out_root, group)
        os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, stem + suffix)
    if os.path.exists(out) and not force:
        print("  skip (exists) %s" % os.path.basename(out))
        return False
    im = Image.open(path).convert("RGB")
    scale = LONG_EDGE / max(im.size)
    if scale < 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)),
                       Image.LANCZOS)
    w, h = im.size
    # SEEDED WITHOUT THE SIDE. The left cut has to be the mirror of this
    # manager's own right cut, so it must draw the same tear -- folding `side`
    # into the seed would make them two unrelated edges.
    seed = int(hashlib.sha256(("%s/%s" % (group, stem)).encode()).hexdigest()[:8], 16)
    rgba = np.dstack([np.asarray(im), tear_alpha(w, h, seed, side)])
    Image.fromarray(rgba, "RGBA").save(out, "WEBP", quality=88, method=6)
    print("  wrote %-26s %dx%d ar=%.3f" % (os.path.basename(out), w, h, w / h))
    return True


def write_manifest():
    """docs/assets/profiles/heroes.json — every profile image's pixel size.

    WHY IT EXISTS: profile.html sizes the hero panel to the art's own aspect,
    so the picture bleeds flush without ever being cropped. Without the size
    up front the page has to wait for the image to decode before it knows how
    wide the panel goes, and every profile visibly reflows on load.

    KEYED BY PATH, NOT BY MANAGER ID, and that is not incidental: browns' todd
    has two art variants that rotate (todd_01 is 0.80, todd_02 is 0.63). One
    aspect per manager would be wrong for exactly the manager the rotation
    exists for. The page looks up whatever path resolveArt() handed it.

    OVERWRITE, regenerated from disk each run. A path that disappears leaves
    the manifest, and the page falls back to measuring the image on load.
    """
    out = {}
    for path in sorted(glob.glob("docs/assets/profiles/*/*.webp")):
        rel = path.replace("\\", "/").split("docs/", 1)[1]
        with Image.open(path) as im:
            out[rel] = [im.width, im.height]
    dest = "docs/assets/profiles/heroes.json"
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('{\n  "$note": [\n')
        fh.write('    "GENERATED by scripts/build_profile_heroes.py -- do not edit.",\n')
        fh.write('    "path (docs-relative) -> [width, height], for every profile image.",\n')
        fh.write('    "profile.html reads it to size the hero panel to the art\'s own",\n')
        fh.write('    "aspect before the image loads, so the picture bleeds flush without",\n')
        fh.write('    "being cropped and the page does not reflow. Keyed by PATH because",\n')
        fh.write('    "todd rotates two variants with different aspects.",\n')
        fh.write('    "A 404 here is survivable: the page measures the image on load."\n')
        fh.write('  ],\n  "$version": 1,\n  "sizes": {\n')
        rows = ['    "%s": [%d, %d]' % (k, v[0], v[1]) for k, v in out.items()]
        fh.write(",\n".join(rows))
        fh.write("\n  }\n}\n")
    print("\nmanifest: %s (%d images)" % (dest, len(out)))


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="re-render files that already exist")
    ap.add_argument("--side", choices=("right", "left"), default="right",
                    help="which edge tears. right (default) is the published "
                         "cut; left is for portrait-RIGHT profiles.")
    ap.add_argument("--only", action="append", default=None, metavar="GROUP/ID",
                    help="restrict to these managers, e.g. --only panel/chris. "
                         "Matches the file stem too, so browns/todd picks up "
                         "todd_01 and todd_02. Repeatable.")
    ap.add_argument("--out-root", default=None, metavar="DIR",
                    help="write under DIR/<group>/ instead of beside the source "
                         "in docs/. Use it to stage a pass held for review; the "
                         "manifest is then NOT rewritten, because nothing the "
                         "live page resolves has changed.")
    return ap.parse_args(argv)


def wanted(only, group, mid, stem):
    """--only matches a manager id OR a file stem, so browns/todd takes both of
    todd's variants without naming each one."""
    if not only:
        return True
    keys = {"%s/%s" % (group, mid), "%s/%s" % (group, stem), mid, stem}
    return any(k in keys for k in only)


def main():
    args = parse_args(sys.argv[1:])
    made = 0
    for g in GROUPS:
        print("===", g)
        for path in sorted(glob.glob("docs/assets/profiles/%s/*.webp" % g)):
            stem = os.path.basename(path)[:-5]
            if stem.endswith("-ripped") or stem.endswith("-ripped-left"):
                continue
            # todd_01 / todd_02 are variants of one manager; the id for the
            # skip check is the part before the variant suffix.
            mid = stem.rsplit("_", 1)[0] if stem.rsplit("_", 1)[-1].isdigit() else stem
            if not wanted(args.only, g, mid, stem):
                continue
            if (g, mid) in SKIP:
                # Blaine's cut is a hand-painted brush edge and there is no
                # synthesised tear worth putting next to it. He sits at persona
                # position 1 -- portrait LEFT -- so he wants the right tear he
                # already has. If he is ever reordered onto the right, the
                # honest fix is a new hand-painted source, not this generator.
                print("  skip (hand-painted) %s" % stem)
                continue
            made += build(path, g, mid, args.force, args.side, args.out_root)
    print("")
    print("%d file(s) written (side=%s). family is excluded on purpose - "
          "see module docstring." % (made, args.side))
    # Covers family's untorn art too — it uses the same flush treatment, just
    # without a tear, so it needs its sizes in the manifest all the same.
    #
    # NOT REWRITTEN FOR A STAGED PASS. The manifest is the live page's
    # declaration of what exists under docs/, and a build that wrote nowhere
    # near docs/ has changed nothing it should describe.
    if args.out_root:
        print("staged under %s -- manifest left alone (nothing published)."
              % args.out_root)
    else:
        write_manifest()


if __name__ == "__main__":
    main()
