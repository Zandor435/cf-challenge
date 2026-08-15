#!/usr/bin/env python3
"""
make_avatars.py — turn coach-profile art into site avatars (ARCHITECTURE §12).

The generated profiles live OUTSIDE this repo (Zach's Windows box, under
`output\\personas\\...`). This script is the copy-in step: it reads a source
folder, crops each image square, shrinks it, writes it to
`docs/assets/portraits/<group_id>/<manager_id>.png`, and refreshes that group's
block in `docs/assets/portraits/manifest.json` so the site picks it up with no
hand-editing.

It is a LOCAL UTILITY, not part of the pipeline. Nothing in .github/workflows
calls it, it never touches data/ or docs/data/, and the output-contract write
surface (three JSON files per group) is unchanged. Run it by hand when new art
lands; commit the PNGs it writes.

MAPPING IS EXPLICIT OR IT FAILS (playbook rule 4, the "DR Congo bug" rule).
Filenames are matched to manager_id / display_name by normalized substring. A
manager that matches zero files, or more than one, is NOT guessed at — the run
fails, prints every source file it saw, and hands you a ready-to-paste --map
line. Silently pinning the wrong face to the wrong manager is exactly the class
of quiet-wrong-answer bug that gate exists to prevent, and source filenames
("Fat friends", persona-generator output names) are not reliable enough to
trust blind.

PARTIAL INPUT DOES NOT CLOBBER (playbook rule 5). Every manager must resolve
before anything is written, so a half-populated source folder leaves the
committed avatars untouched. --allow-partial opts out when you genuinely only
have some of them; it writes what matched and leaves the rest alone.

CROPPING matches what the site does: a square cover-crop, anchored high, then
resized. The site's CSS is `object-fit: cover; object-position: center top` on
a circular disc, so a pre-squared image passes through it untouched and what
you see here is what renders. --anchor controls how much headroom is kept above
the crop: `upper` (default, 15% down — right for a 5:7 headshot where the face
sits in the top third), `top` (0%), or `center` (50%).

SIZE defaults to 256px. The disc renders at 56/40/28 CSS px, so 256 covers a 4x
display with room to spare and stays a ~50-100KB PNG. These ship in the Pages
build — there is no image host and no resizing at request time, so the file you
commit is the file every visitor downloads. Don't commit 4MB originals.

Usage:
  python scripts/make_avatars.py --src "C:\\Users\\zacha\\Claude Code\\cf-challenge\\output\\personas\\Fat friends\\Fat"
  python scripts/make_avatars.py --src ./art --group panel --dry-run
  python scripts/make_avatars.py --src ./art --map blaine=coach_1.png --map chris=coach_2.png

Exit codes: 0 written (or dry-run clean) / 1 mapping or input failure.
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTRAIT_ROOT = REPO / "docs" / "assets" / "portraits"
MANIFEST = PORTRAIT_ROOT / "manifest.json"

# Pillow's decoders cover everything the persona generators emit. Kept as a
# lazy import with a real message because this is a utility — a missing Pillow
# must not read as "the script is broken."
try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit(
        "make_avatars.py needs Pillow.\n"
        "  pip install pillow      (or: pip install -r requirements.txt)"
    )

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ANCHORS = {"top": 0.0, "upper": 0.15, "center": 0.5}


def norm(s):
    """Lowercase, alphanumerics only — 'Coach_Blaine (1).png' -> 'coachblaine1'."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def load_managers(group_id):
    cfg_path = REPO / "groups" / group_id / "config.json"
    if not cfg_path.exists():
        sys.exit(f"No such group: {cfg_path} does not exist.")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    managers = cfg.get("managers") or []
    if not managers:
        sys.exit(f"{cfg_path} lists no managers.")
    return [(m["manager_id"], m.get("display_name", m["manager_id"])) for m in managers]


def source_images(src):
    if not src.is_dir():
        sys.exit(f"--src is not a directory: {src}")
    files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        sys.exit(f"No images found in {src} (looked for {', '.join(sorted(IMAGE_SUFFIXES))}).")
    return files


def auto_match(manager_id, display_name, files):
    """Every file whose stem plausibly names this manager. Substring both ways so
    'blaine.png' and 'panel-blaine-final-v2.png' both hit, and a bare '1.png'
    hits nothing rather than hitting everyone."""
    keys = {norm(manager_id)}
    first = str(display_name).split()[0] if str(display_name).split() else ""
    if first:
        keys.add(norm(first))
    keys = {k for k in keys if k}
    hits = []
    for f in files:
        stem = norm(f.stem)
        if any(k and (k in stem or stem in k) for k in keys):
            hits.append(f)
    return hits


def resolve(managers, files, overrides, allow_partial):
    """manager_id -> Path. Fails loud on ambiguity, on a miss, and on double-use."""
    by_name = {f.name: f for f in files}
    by_norm = {}
    for f in files:
        by_norm.setdefault(norm(f.name), f)
        by_norm.setdefault(norm(f.stem), f)

    chosen, problems = {}, []
    for manager_id, display_name in managers:
        if manager_id in overrides:
            raw = overrides[manager_id]
            hit = by_name.get(raw) or by_norm.get(norm(raw)) or by_norm.get(norm(Path(raw).stem))
            if hit is None:
                problems.append(f"  {manager_id}: --map named '{raw}', which is not in --src")
            else:
                chosen[manager_id] = hit
            continue

        hits = auto_match(manager_id, display_name, files)
        if len(hits) == 1:
            chosen[manager_id] = hits[0]
        elif not hits:
            problems.append(f"  {manager_id} ({display_name}): no source filename matches")
        else:
            names = ", ".join(h.name for h in hits)
            problems.append(f"  {manager_id} ({display_name}): ambiguous — matches {names}")

    # Two managers sharing one file is always an error, never a partial-run case.
    seen = {}
    for manager_id, path in chosen.items():
        seen.setdefault(path, []).append(manager_id)
    for path, owners in seen.items():
        if len(owners) > 1:
            problems.append(f"  {path.name}: claimed by {' and '.join(sorted(owners))}")
            for o in owners:
                chosen.pop(o, None)

    if problems and not (allow_partial and chosen and all("claimed by" not in p for p in problems)):
        print("Could not map every manager to a source image:\n", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(f"\nSource files in that folder ({len(files)}):", file=sys.stderr)
        for f in files:
            print(f"  {f.name}", file=sys.stderr)
        print("\nSay which is which explicitly, e.g.:", file=sys.stderr)
        pairs = " ".join(
            f'--map {mid}="{files[i].name}"' if i < len(files) else f"--map {mid}=..."
            for i, (mid, _) in enumerate(managers)
        )
        print(f"  python scripts/make_avatars.py --src ... {pairs}", file=sys.stderr)
        print("\n(Or pass --allow-partial to write only the ones that did match.)", file=sys.stderr)
        sys.exit(1)

    if problems:
        print("Proceeding with a partial set (--allow-partial):", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print("", file=sys.stderr)
    return chosen


def make_avatar(src_path, dest_path, size, anchor, dry_run):
    with Image.open(src_path) as im:
        im = ImageOps.exif_transpose(im)          # phone/camera originals carry rotation
        im = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
        w, h = im.size
        side = min(w, h)
        left = round((w - side) / 2)               # always horizontally centred
        top = round((h - side) * ANCHORS[anchor])  # vertical bias keeps the face in frame
        im = im.crop((left, top, left + side, top + side))
        im = im.resize((size, size), Image.LANCZOS)
        if dry_run:
            return (w, h), None
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest_path, "PNG", optimize=True)
        return (w, h), dest_path.stat().st_size


def update_manifest(group_id, written, dry_run):
    """Refresh this group's block, preserving the `_note` keys and every other
    group. Entries are paths relative to the portraits folder, which is exactly
    what buildPortraitIndex() in docs/app.js expects."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    portraits = manifest.setdefault("portraits", {})
    block = portraits.setdefault(group_id, {})
    for manager_id in sorted(written):
        block[manager_id] = f"{group_id}/{manager_id}.png"
    if not dry_run:
        # ensure_ascii=False or the rewrite mangles the em-dashes in the `_note`
        # keys into — escapes every time this runs.
        MANIFEST.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return block


def main():
    ap = argparse.ArgumentParser(description="Crop + shrink coach profiles into site avatars.")
    ap.add_argument("--src", required=True, help="folder holding the source profile images")
    ap.add_argument("--group", default="panel", help="group_id to write avatars for (default: panel)")
    ap.add_argument("--size", type=int, default=256, help="output square size in px (default: 256)")
    ap.add_argument("--anchor", choices=sorted(ANCHORS), default="upper",
                    help="vertical crop bias: top / upper (default) / center")
    ap.add_argument("--map", action="append", default=[], metavar="ID=FILE",
                    help="explicit manager_id=filename, repeatable; overrides auto-matching")
    ap.add_argument("--allow-partial", action="store_true",
                    help="write the managers that did resolve instead of failing on the rest")
    ap.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    args = ap.parse_args()

    if args.size < 32:
        sys.exit("--size below 32px is smaller than the smallest avatar renders; refusing.")

    overrides = {}
    for pair in args.map:
        if "=" not in pair:
            sys.exit(f"--map expects manager_id=filename, got: {pair}")
        key, val = pair.split("=", 1)
        overrides[key.strip()] = val.strip()

    managers = load_managers(args.group)
    known = {m for m, _ in managers}
    for key in overrides:
        if key not in known:
            sys.exit(
                f"--map names '{key}', which is not a manager_id in groups/{args.group}/config.json.\n"
                f"Valid ids: {', '.join(sorted(known))}"
            )

    files = source_images(Path(args.src).expanduser())
    chosen = resolve(managers, files, overrides, args.allow_partial)

    out_dir = PORTRAIT_ROOT / args.group
    label = "Would write" if args.dry_run else "Wrote"
    print(f"{args.group}: {len(chosen)} of {len(managers)} managers, "
          f"{args.size}x{args.size} px, anchor={args.anchor}\n")
    for manager_id, _ in managers:
        src_path = chosen.get(manager_id)
        if src_path is None:
            print(f"  {manager_id:<10} (skipped — no source)")
            continue
        dims, nbytes = make_avatar(src_path, out_dir / f"{manager_id}.png", args.size, args.anchor, args.dry_run)
        size_note = "" if nbytes is None else f", {nbytes / 1024:.0f} KB"
        print(f"  {manager_id:<10} {src_path.name}  [{dims[0]}x{dims[1]}]"
              f"  ->  docs/assets/portraits/{args.group}/{manager_id}.png{size_note}")

    if not chosen:
        sys.exit("\nNothing resolved — no files written, manifest untouched.")

    update_manifest(args.group, chosen, args.dry_run)
    refreshed = "would be refreshed" if args.dry_run else "refreshed"
    print(f"\n{label} {len(chosen)} avatar(s); manifest.json entries for '{args.group}' {refreshed}.")
    if args.dry_run:
        print("Dry run — nothing on disk changed.")
    else:
        print("Now commit them:")
        print(f"  git add docs/assets/portraits && git commit -m 'feat: {args.group} coach avatars'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
