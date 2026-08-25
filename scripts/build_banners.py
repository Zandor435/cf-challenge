#!/usr/bin/env python3
"""Publish generated banner art to the site, and write the manifest it reads.

THE WORKFLOW, in three steps:

    1. drop images in   output/banners/<group>/
    2. run              python scripts/build_banners.py --group panel
    3. commit           docs/assets/banners/<group>/  and
                        docs/data/<group>/banners.json

output/ is gitignored (see the /output/ block in .gitignore) and GitHub Pages
serves from docs/, so nothing under output/ can ever reach a reader. This
script is the one edge between the two: it ENCODES output/banners/<group>/ into
docs/assets/banners/<group>/ as WEBP, then regenerates
docs/data/<group>/banners.json listing what it published.

BYTES ARE RE-ENCODED, and this is the deliberate reversal of what this script
used to do. It mirrored source bytes and source filenames verbatim, which had
two costs the masthead could not carry once the rotator went live:

  * a random-per-load banner means the reader may pull ANY published file, so
    the page's worst case is the largest one. The generator emits 0.9-3.3 MB
    stills; a rotation of those is a masthead that costs megabytes per view.
  * the generator also emits JPEG bytes under a .png name. Mirroring preserved
    the lie. Encoding to WEBP ends it: the published name states the published
    format because the encoder, not the generator, chose both.

Every source is resized to a long edge of LONG_EDGE (only downwards -- a small
source is never upscaled into blur) and encoded at the highest quality on the
QUALITY_LADDER that lands under MAX_BYTES. The ladder descends, so a busy image
pays in quality and a flat one does not; a file that cannot make the cap even
at the floor is FATAL and names itself, rather than shipping a banner heavier
than the budget the rotator was sized for. This is the same Pillow WEBP call
prepare_portraits.publish_banner() uses for the single per-group hero webp --
one encoder, two callers, not two encoders.

THE MANIFEST is what the frontend reads. It carries the PUBLISHED filename and
the PUBLISHED pixel width and height -- never the source's, which after the
resize above are usually a different number. The page reserves the banner box
from that ratio before the image arrives; an eager, above-the-fold banner that
reflows the boards under it is the failure this metadata prevents. Never
hand-edit it: it is regenerated here, and a filename in it that is not on disk
is a 404 in the masthead.

Per-image alt text is OPTIONAL. If output/banners/<group>/alt.json exists and
maps a SOURCE filename to a string, that string rides into the manifest as
"alt" on the published entry. Keys starting with "_" are notes, not filenames,
and are skipped.

Per-image FOCAL is optional in the same shape: output/banners/<group>/focal.json
maps a SOURCE filename to a CSS object-position ("50% 12%"), which rides into
the manifest as "focal" and tells the page where to crop that banner when the
masthead's height cap bites. It is not decoration -- the panel banners put
their four faces at a different height in every one of them, and one shared
crop is what made the band render torsos. A malformed value or a key naming no source is
FATAL, not skipped; see load_focals().

Playbook compliance (CLAUDE.md):
  - rule 4: an unreadable image FAILS LOUD and names the file, and so does one
    that cannot be encoded under the size cap. Neither is skipped into a
    manifest that then disagrees with what is on disk.
  - rule 5: an empty/missing source directory is a hard stop -- the existing
    published banners and manifest are left exactly as they were, never
    clobbered with an empty list. The manifest REGENERATES; the asset directory
    only ever gains files, and removing one is opt-in via --prune.
  - rule 7: no network, no paid API, no cost. Safe to re-run; every source is
    encoded to memory first and only written when the resulting bytes differ
    from what is already published, so an unchanged run touches no file.

Usage:
    python scripts/build_banners.py --group panel
    python scripts/build_banners.py --group panel --check   # dry run, no writes
    python scripts/build_banners.py --group panel --prune   # also delete
                                    # published files no longer in output/
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "output" / "banners"
PUB_ROOT = ROOT / "docs" / "assets" / "banners"
DATA_ROOT = ROOT / "docs" / "data"

# What counts as a banner. Deliberately a list of image suffixes rather than
# "every file", so alt.json and any stray .txt beside the art are not published.
EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}

ALT_SIDECAR = "alt.json"
FOCAL_SIDECAR = "focal.json"

# A CSS object-position value, and nothing else. This string is copied into the
# manifest, fetched by the page and written into a style attribute, so its
# shape is PINNED here rather than trusted downstream: two components, x then
# y, each a percentage or one of the box keywords. app.js re-checks the same
# shape on arrival -- the page must not trust a manifest either -- but the stop
# belongs here, where the sidecar can still be fixed.
_FOCAL_PART = r"(?:left|center|right|top|bottom|\d{1,3}(?:\.\d+)?%)"
FOCAL_RE = re.compile(rf"^{_FOCAL_PART} {_FOCAL_PART}$")

# Publish geometry and budget. LONG_EDGE is a ceiling, never a target: the
# masthead is capped at 260px tall on desktop and 150px on mobile (style.css
# .hero-banner img), so 1600 already covers a 2x display with room to crop.
LONG_EDGE = 1600
MAX_BYTES = 250 * 1024
# Descending: the first quality that fits the budget wins, so flat art keeps
# its detail and only the busiest images pay. Most of this set lands at q78-q90;
# the four-panel collage, which is halftone texture edge to edge and compresses
# worst, needs the low fifties to make the cap at 1600px. 50 is the floor --
# below that the halftone and speed-line art visibly bands, and shipping a
# banner that looks broken is worse than failing the build and being told which
# file needs a smaller crop. The rendered box is at most 260px tall (style.css
# .hero-banner img), so even the floor is oversampled on screen.
QUALITY_LADDER = (90, 86, 82, 78, 74, 70, 66, 62, 58, 54, 50)
WEBP_METHOD = 6         # slowest/densest encode; this runs offline, not in CI


def rel(path: Path) -> str:
    """Repo-relative path for messages, falling back to the absolute one.

    Every path this script touches is under ROOT in normal use, but
    Path.relative_to() RAISES on one that is not -- so a bare relative_to() in
    a fatal-error message turns a clean "here is what is wrong" exit into a
    ValueError traceback, precisely when the script is trying to fail loud.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def sources(src_dir: Path):
    """Image files in src_dir, sorted by name. Not recursive."""
    return sorted((p for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in EXTS),
                  key=lambda p: p.name)


def published_name(src: Path) -> str:
    """Source filename -> published filename. Always .webp, because that is
    what encode() writes; the extension is a statement about the bytes."""
    return src.stem + ".webp"


def load_alts(src_dir: Path) -> dict:
    """Optional filename -> alt-text map.

    A malformed sidecar is fatal rather than silently ignored: it was written
    on purpose, and dropping it would ship the generic fallback while looking
    like it worked.
    """
    p = src_dir / ALT_SIDECAR
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"FATAL: {rel(p)} is unreadable: {e}")
    if not isinstance(data, dict):
        sys.exit(f"FATAL: {rel(p)} must be a filename -> alt object")
    # "_"-prefixed keys are editorial notes to whoever maintains the sidecar,
    # not filenames. Skipped by key, so a note whose value is a list (rather
    # than a string) cannot be str()-ed into something that looks like alt text.
    return {str(k): str(v).strip() for k, v in data.items()
            if not str(k).startswith("_") and str(v).strip()}


def load_focals(src_dir: Path, srcs) -> dict:
    """Optional filename -> object-position map, e.g. "50% 12%".

    WHY THIS EXISTS: the banners are group portraits and every one of them puts
    the four faces in a different part of the frame. The page caps the band's
    height, so something has to be cropped, and without this the crop is the
    same blind `center 38%` for every one -- which is how a set of images
    with faces in the top third ended up rendering as a row of torsos.

    Two fatal cases, both rule 4. A value that is not an object-position is a
    stop, because a focal that silently drops is a banner that silently crops
    wrong -- the exact failure this file was added to end. And a key that
    names no source is a stop too: it is a typo or a renamed image, and either
    way somebody believes they have framed a banner that is still on the
    default. Unlike alt.json this map CAN be checked against the sources,
    because it is keyed by the same filenames the encoder is about to read.
    """
    p = src_dir / FOCAL_SIDECAR
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"FATAL: {rel(p)} is unreadable: {e}")
    if not isinstance(data, dict):
        sys.exit(f"FATAL: {rel(p)} must be a filename -> focal object")
    # "_"-prefixed keys are editorial notes, not filenames -- same convention
    # as alt.json, and skipped by key for the same reason.
    out = {}
    for k, v in data.items():
        k = str(k)
        if k.startswith("_"):
            continue
        val = str(v).strip()
        if not FOCAL_RE.match(val):
            sys.exit(f"FATAL: {rel(p)}: {k!r} -> {val!r} is not an "
                     f"object-position (want e.g. \"50% 12%\")")
        out[k] = val
    known = {s.name for s in srcs}
    unknown = sorted(k for k in out if k not in known)
    if unknown:
        sys.exit(f"FATAL: {rel(p)} frames images that are not in "
                 f"{rel(src_dir)}: {', '.join(unknown)}")
    return out


def encode(src: Path):
    """Resize to LONG_EDGE and encode to WEBP under MAX_BYTES.

    Returns (bytes, width, height, quality). Encodes to memory, never to disk:
    that is what lets --check report the exact published size and the exact
    changed/unchanged verdict without writing anything.
    """
    try:
        with Image.open(src) as im:
            im.load()
            img = im.convert("RGB")
    except Exception as e:                          # noqa: BLE001 -- any decode
        sys.exit(f"FATAL: cannot read image {rel(src)}: {e}")

    w, h = img.size
    if not w or not h:
        sys.exit(f"FATAL: {rel(src)} reports a zero dimension ({w}x{h})")

    # Only ever down. Upscaling a small source to hit LONG_EDGE would spend
    # bytes on interpolation and publish a blurrier image than the original.
    longest = max(w, h)
    if longest > LONG_EDGE:
        scale = LONG_EDGE / longest
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         Image.LANCZOS)

    last = None
    for q in QUALITY_LADDER:
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=q, method=WEBP_METHOD)
        data = buf.getvalue()
        last = (len(data), q)
        if len(data) <= MAX_BYTES:
            return data, img.width, img.height, q

    # Rule 4: name the offender rather than publishing over budget.
    sys.exit(f"FATAL: {rel(src)} cannot be encoded under "
             f"{MAX_BYTES // 1024} KB -- smallest was {last[0] // 1024} KB at "
             f"quality {last[1]} ({img.width}x{img.height}). Crop or simplify "
             f"the source, or lower the quality floor deliberately.")


def build(group: str, check: bool, prune: bool) -> int:
    src_dir = SRC_ROOT / group
    pub_dir = PUB_ROOT / group
    manifest = DATA_ROOT / group / "banners.json"

    # Rule 5. Nothing to publish is a STOP, not an empty manifest: the live
    # site keeps whatever it is already serving.
    if not src_dir.is_dir():
        sys.exit(f"FATAL: no source directory {rel(src_dir)} -- "
                 f"nothing published, {rel(manifest)} untouched.")
    srcs = sources(src_dir)
    if not srcs:
        sys.exit(f"FATAL: {rel(src_dir)} holds no images "
                 f"({' '.join(sorted(EXTS))}) -- nothing published, "
                 f"{rel(manifest)} untouched.")

    # Two sources whose stems collide would publish to one .webp and the second
    # would silently win. Catch it here, before either is encoded.
    by_out = {}
    for s in srcs:
        by_out.setdefault(published_name(s), []).append(s.name)
    clash = {k: v for k, v in by_out.items() if len(v) > 1}
    if clash:
        lines = "; ".join(f"{k} <- {', '.join(v)}" for k, v in sorted(clash.items()))
        sys.exit(f"FATAL: {rel(src_dir)} has sources that publish to the same "
                 f"name: {lines}")

    alts = load_alts(src_dir)
    focals = load_focals(src_dir, srcs)
    entries = []
    written = unchanged = 0

    for s in srcs:
        data, width, height, quality = encode(s)
        name = published_name(s)
        d = pub_dir / name

        # Compare the bytes we would write against the bytes already there.
        # A content compare, not a timestamp one: re-running the encoder on an
        # unchanged source is deterministic, so this is what makes a no-op run
        # actually touch nothing.
        same = d.exists() and d.read_bytes() == data
        if same:
            unchanged += 1
        else:
            if not check:
                pub_dir.mkdir(parents=True, exist_ok=True)
                d.write_bytes(data)
            written += 1
            print(f"  {'would write' if check else 'wrote'}  {name} "
                  f"({width}x{height}, q{quality}, {len(data) // 1024} KB "
                  f"<- {s.name} {s.stat().st_size // 1024} KB)")

        entry = {"file": name, "width": width, "height": height}
        if s.name in focals:
            entry["focal"] = focals[s.name]
        if s.name in alts:
            entry["alt"] = alts[s.name]
        entries.append(entry)

    # Published files with no source any more. Reported always; removed only on
    # --prune, because deleting a committed asset the site may still reference
    # is not something a routine rebuild should do behind your back.
    stale = []
    if pub_dir.is_dir():
        keep = {published_name(s) for s in srcs}
        stale = sorted(p.name for p in pub_dir.iterdir()
                       if p.is_file() and p.name not in keep)
    for name in stale:
        if prune and not check:
            (pub_dir / name).unlink()
            print(f"  pruned    {name}")
        else:
            hint = "--check" if check else "pass --prune to remove"
            print(f"  STALE     {name} (in docs/, not published by this run; {hint})")

    doc = {
        "$note": [
            "GENERATED by scripts/build_banners.py -- do not hand-edit.",
            "REGENERATES in full on every run (playbook rule 5).",
            "Read by app.js on the home page: assets/art_slots.json declares",
            "this group's hero_banner slot as mode 'rotate', which names this",
            "file as its candidate source, and bannerFor() picks ONE entry",
            "uniformly at random per page load for the masthead. A group whose",
            "slot is mode 'fixed' does not read this file at all.",
            "'file' is the PUBLISHED name and width/height are the PUBLISHED",
            "pixels -- both are what the encoder wrote, not what the source",
            "was -- so the page can reserve the box from the true aspect ratio",
            "before the image loads.",
            "'focal' is optional, comes from",
            "output/banners/<group>/focal.json, and is the CSS",
            "object-position the page crops this one image to when the",
            "band's height cap bites. Absent means the frontend default,",
            "which biases toward the top of the frame.",
            "'dir' is docs-relative; 'alt' is optional, comes from",
            "output/banners/<group>/alt.json, and overrides the frontend's",
            "generic fallback for that one image.",
        ],
        "group": group,
        "dir": f"assets/banners/{group}",
        "count": len(entries),
        "banners": entries,
    }
    if not check:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")

    print(f"\n{group}: {len(entries)} banner(s) -- {written} written, "
          f"{unchanged} unchanged, {len(stale)} stale")
    print(f"  assets:   {rel(pub_dir)}")
    tail = " (not written -- --check)" if check else ""
    print(f"  manifest: {rel(manifest)}{tail}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", default="panel",
                    help="group slug (default: panel). Only this group's "
                         "banner directory is read or written.")
    ap.add_argument("--check", action="store_true",
                    help="dry run: report what would change, write nothing")
    ap.add_argument("--prune", action="store_true",
                    help="delete published banners no longer in output/")
    a = ap.parse_args()
    return build(a.group, a.check, a.prune)


if __name__ == "__main__":
    sys.exit(main())
