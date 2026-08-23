#!/usr/bin/env python3
"""Publish generated banner art to the site, and write the manifest it reads.

THE WORKFLOW, in three steps:

    1. drop images in   output/banners/<group>/
    2. run              python scripts/build_banners.py --group panel
    3. commit           docs/assets/banners/<group>/  and
                        docs/data/<group>/banners.json

output/ is gitignored (see the /output/ block in .gitignore) and GitHub Pages
serves from docs/, so nothing under output/ can ever reach a reader. This
script is the one edge between the two: it MIRRORS output/banners/<group>/ into
docs/assets/banners/<group>/ preserving filenames and bytes, then regenerates
docs/data/<group>/banners.json listing what it published.

BYTES ARE NOT TOUCHED. No resize, no re-encode, no format change -- the file
that lands in docs/ is byte-identical to the one in output/. Dimensions are
read, never rewritten. (Several of today's panel files are named .png but hold
JPEG bytes; that is how they were generated, browsers sniff image data rather
than trusting the extension, and re-encoding to "fix" a filename is exactly the
lossy step this script exists not to take.)

THE MANIFEST is what the frontend reads -- managers.js picks ONE entry from it
uniformly at random per page load. It carries each file's real pixel width and
height so the page can reserve the box from the true aspect ratio before the
image arrives; an eager, above-the-fold banner that reflows the roster under it
is the failure this metadata prevents. Never hand-edit it: it is regenerated
here, and a filename in it that is not on disk is a 404 in the masthead.

Per-image alt text is OPTIONAL. If output/banners/<group>/alt.json exists and
maps a filename to a string, that string rides into the manifest as "alt" and
the frontend uses it; absent, the page falls back to a generic league label.

Playbook compliance (CLAUDE.md):
  - rule 4: an unreadable image FAILS LOUD and names the file. It is never
    skipped into a manifest that then disagrees with what is on disk.
  - rule 5: an empty/missing source directory is a hard stop -- the existing
    published banners and manifest are left exactly as they were, never
    clobbered with an empty list. The manifest REGENERATES; the asset directory
    only ever gains files, and removing one is opt-in via --prune.
  - rule 7: no network, no paid API, no cost. Safe to re-run; unchanged files
    are compared by content hash and skipped.

Usage:
    python scripts/build_banners.py --group panel
    python scripts/build_banners.py --group panel --check   # dry run, no writes
    python scripts/build_banners.py --group panel --prune   # also delete
                                    # published files no longer in output/
"""

import argparse
import hashlib
import json
import shutil
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


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sources(src_dir: Path):
    """Image files in src_dir, sorted by name. Not recursive."""
    return sorted((p for p in src_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in EXTS),
                  key=lambda p: p.name)


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
    return {str(k): str(v) for k, v in data.items() if str(v).strip()}


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

    alts = load_alts(src_dir)
    entries = []
    copied = unchanged = 0

    for s in srcs:
        # Rule 4: read the real pixel size, and NAME the file if it cannot be
        # read. A banner that lands in docs/ with no dimensions in the manifest
        # is a banner the page cannot reserve a box for.
        try:
            with Image.open(s) as im:
                width, height = im.size
        except Exception as e:                      # noqa: BLE001 -- any decode
            sys.exit(f"FATAL: cannot read image {rel(s)}: {e}")
        if not width or not height:
            sys.exit(f"FATAL: {rel(s)} reports a zero dimension "
                     f"({width}x{height})")

        d = pub_dir / s.name
        same = (d.exists()
                and d.stat().st_size == s.stat().st_size
                and sha256(d) == sha256(s))
        if same:
            unchanged += 1
        else:
            if not check:
                pub_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)          # bytes preserved, no re-encode
            copied += 1
            print(f"  {'would copy' if check else 'copied'}  {s.name} "
                  f"({width}x{height}, {s.stat().st_size // 1024} KB)")

        entry = {"file": s.name, "width": width, "height": height}
        if s.name in alts:
            entry["alt"] = alts[s.name]
        entries.append(entry)

    # Published files with no source any more. Reported always; removed only on
    # --prune, because deleting a committed asset the site may still reference
    # is not something a routine rebuild should do behind your back.
    stale = []
    if pub_dir.is_dir():
        keep = {s.name for s in srcs}
        stale = sorted(p.name for p in pub_dir.iterdir()
                       if p.is_file() and p.name not in keep)
    for name in stale:
        if prune and not check:
            (pub_dir / name).unlink()
            print(f"  pruned    {name}")
        else:
            hint = "--check" if check else "pass --prune to remove"
            print(f"  STALE     {name} (in docs/, not in output/; {hint})")

    doc = {
        "$note": [
            "GENERATED by scripts/build_banners.py -- do not hand-edit.",
            "REGENERATES in full on every run (playbook rule 5).",
            "Read by managers.js, which picks ONE entry uniformly at random",
            "per page load and renders it above the page intro. width/height",
            "are the file's real pixels and exist so the box can be reserved",
            "from the true aspect ratio before the image loads.",
            "'dir' is docs-relative; 'alt' is optional and overrides the",
            "frontend's generic fallback for that one image.",
        ],
        "group": group,
        "dir": f"assets/banners/{group}",
        "count": len(entries),
        "banners": entries,
    }
    if not check:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")

    print(f"\n{group}: {len(entries)} banner(s) -- {copied} copied, "
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
