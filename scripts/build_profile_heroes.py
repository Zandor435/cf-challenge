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
coach-card treatment -- a deliberate difference in kind", and family is also
the group carrying the straight-tone profiles. Tearing a joke edge onto a
photograph of somebody's father is the same error the tone gate exists to
prevent. Family gets the same page, with its photograph presented straight.

BLAINE IS SKIPPED. His cut comes from a hand-painted source with a real brush
edge (output/personas/Fat friends/Fat/ripped blaine.png) via
scripts/build_ripped_hero.py, and that hand-made edge beats anything
synthesised here. Overwriting it would be a downgrade.

DETERMINISTIC. The tear is seeded from the manager id, so re-running produces
byte-identical output and a rebuild never silently reshuffles everyone's art.

    python scripts/build_profile_heroes.py [--force]
"""
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


def tear_alpha(w, h, seed):
    """Alpha for a right-edge paint tear. Straight everywhere else."""
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
    return (a * 255).astype(np.uint8)


def build(path, group, mid, force):
    stem = os.path.basename(path)[:-5]
    out = os.path.join(os.path.dirname(path), stem + "-ripped.webp")
    if os.path.exists(out) and not force:
        print("  skip (exists) %s" % os.path.basename(out))
        return False
    im = Image.open(path).convert("RGB")
    scale = LONG_EDGE / max(im.size)
    if scale < 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)),
                       Image.LANCZOS)
    w, h = im.size
    seed = int(hashlib.sha256(("%s/%s" % (group, stem)).encode()).hexdigest()[:8], 16)
    rgba = np.dstack([np.asarray(im), tear_alpha(w, h, seed)])
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


def main():
    force = "--force" in sys.argv
    made = 0
    for g in GROUPS:
        print("===", g)
        for path in sorted(glob.glob("docs/assets/profiles/%s/*.webp" % g)):
            stem = os.path.basename(path)[:-5]
            if stem.endswith("-ripped"):
                continue
            # todd_01 / todd_02 are variants of one manager; the id for the
            # skip check is the part before the variant suffix.
            mid = stem.rsplit("_", 1)[0] if stem.rsplit("_", 1)[-1].isdigit() else stem
            if (g, mid) in SKIP:
                print("  skip (hand-painted) %s" % stem)
                continue
            made += build(path, g, mid, force)
    print("\n%d file(s) written. family is excluded on purpose - see module docstring."
          % made)
    # Covers family's untorn art too — it uses the same flush treatment, just
    # without a tear, so it needs its sizes in the manifest all the same.
    write_manifest()


if __name__ == "__main__":
    main()
