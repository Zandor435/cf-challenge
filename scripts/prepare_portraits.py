#!/usr/bin/env python3
"""Turn approved persona poster art into the two derivatives the site serves.

NOT part of the live weekly pipeline. Run by hand when an owner's art is
chosen. Reads a private source image (assets/source_photos/, output/personas/ —
both gitignored) and emits, per manager, into docs/assets/portraits/<group>/:

  <manager_id>.webp        poster  — full composition, long edge --poster-px.
                                     The baked-in art/text IS the joke; never
                                     cropped away.
  <manager_id>-face.webp   face    — square head-and-shoulders crop, --face-px,
                                     for the site's circular avatars.

The face crop is found with OpenCV's frontal-face cascade, then expanded to
--expand x the detected face width and nudged down so the face sits above the
square's center (standard portrait framing). Detection failure is LOUD and
skips that manager rather than silently shipping a crop of someone's torso.

Usage:
    python scripts/prepare_portraits.py --group panel \
        --map "blaine=output/personas/Fat friends/Fat/fat blaine.png" \
        --map "chris=output/personas/Fat friends/Fat/fat beck.png" \
        --preview

    # review the sheet it prints, then drop --preview to write real assets
    python scripts/prepare_portraits.py --group panel --map ... --force

Playbook compliance (CLAUDE.md):
  - rule 4: an undetectable face fails loud and NAMES the manager; it is never
    silently replaced with a center crop.
  - rule 5: --skip-if-exists is the DEFAULT; --force is required to overwrite
    an already-approved asset.
  - rule 7: no network calls, no paid API — safe to re-run.

Privacy: sources stay gitignored. Only the derivatives written under
docs/assets/portraits/ are committed, and only those reach the public site.
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "docs" / "assets" / "portraits"
CASCADE = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
# ACCUMULATES (rule 5): merged per group, never clobbered. app.js reads this
# to decide whether to emit an <img> at all, so a group with no art emits no
# 404s -- same "only render what we know exists" contract as team logos.
MANIFEST = OUT_ROOT / "index.json"


def detect_face(img: Image.Image):
    """Return the largest (x, y, w, h) frontal face, or None."""
    gray = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = cv2.CascadeClassifier(str(CASCADE)).detectMultiScale(
        gray, scaleFactor=1.08, minNeighbors=6,
        minSize=(max(40, img.width // 25), max(40, img.height // 25)))
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: f[2] * f[3])


def face_square(img: Image.Image, box, expand: float):
    """Expand a tight face box into a head-and-shoulders square, clamped."""
    x, y, w, h = box
    side = min(int(w * expand), img.width, img.height)
    # Face sits above the square's center -> shift the crop down past the chin.
    cx = x + w / 2
    cy = y + h / 2 + h * 0.28
    left = int(round(min(max(cx - side / 2, 0), img.width - side)))
    top = int(round(min(max(cy - side / 2, 0), img.height - side)))
    return (left, top, left + side, top + side)


def update_manifest(group: str, manager_ids) -> None:
    """Merge this run's managers into the manifest without dropping others."""
    data = {}
    if MANIFEST.exists():
        try:
            data = json.loads(MANIFEST.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    have = set(data.get(group, [])) | set(manager_ids)
    data[group] = sorted(have)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"  manifest: {group} -> {', '.join(data[group])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True, help="group slug, e.g. panel")
    ap.add_argument("--map", action="append", default=[], metavar="ID=PATH",
                    help="manager_id=source image path (repeatable)")
    ap.add_argument("--poster-px", type=int, default=900, help="poster long edge")
    ap.add_argument("--face-px", type=int, default=256, help="face crop side")
    ap.add_argument("--expand", type=float, default=2.3,
                    help="face-box multiplier for the head-and-shoulders square")
    ap.add_argument("--quality", type=int, default=82)
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing approved assets")
    ap.add_argument("--preview", action="store_true",
                    help="write a review sheet to --preview-out; write NO assets")
    ap.add_argument("--preview-out", default="portrait_preview.jpg")
    args = ap.parse_args()

    pairs = []
    for entry in args.map:
        if "=" not in entry:
            ap.error(f"--map needs ID=PATH, got: {entry!r}")
        mid, _, raw = entry.partition("=")
        src = Path(raw)
        if not src.is_absolute():
            src = ROOT / raw
        if not src.exists():
            print(f"ERROR: source not found for {mid!r}: {src}", file=sys.stderr)
            return 1
        pairs.append((mid.strip(), src))
    if not pairs:
        ap.error("at least one --map ID=PATH is required")

    out_dir = OUT_ROOT / args.group
    if not args.preview:
        out_dir.mkdir(parents=True, exist_ok=True)

    tiles, written_ids, made, skipped, failed = [], [], 0, 0, 0
    for mid, src in pairs:
        img = Image.open(src).convert("RGB")
        box = detect_face(img)
        if box is None:
            # rule 4: name the offender, never guess a crop.
            print(f"  FAILED {mid}: no face detected in {src.name} — pass an "
                  f"explicit crop or re-shoot the art.", file=sys.stderr)
            failed += 1
            continue

        crop = face_square(img, box, args.expand)
        face = img.crop(crop).resize((args.face_px, args.face_px), Image.LANCZOS)
        poster = img.copy()
        poster.thumbnail((args.poster_px, args.poster_px), Image.LANCZOS)

        if args.preview:
            tiles.append((mid, poster, face, box, crop))
            print(f"  {mid}: face at {tuple(int(v) for v in box)} -> crop {crop}")
            continue

        for path, im in ((out_dir / f"{mid}.webp", poster),
                         (out_dir / f"{mid}-face.webp", face)):
            if path.exists() and not args.force:
                print(f"  skip (exists): {path.relative_to(ROOT)}")
                skipped += 1
                continue
            im.save(path, "WEBP", quality=args.quality, method=6)
            made += 1
            print(f"  wrote {path.relative_to(ROOT)} "
                  f"({path.stat().st_size // 1024} KB)")
        written_ids.append(mid)

    if args.preview and tiles:
        CW, CH = 300, 380
        sheet = Image.new("RGB", (CW * len(tiles), CH + 300), (22, 22, 22))
        for i, (mid, poster, face, box, crop) in enumerate(tiles):
            thumb = poster.copy()
            thumb.thumbnail((CW - 16, CH - 16), Image.LANCZOS)
            sheet.paste(thumb, (i * CW + (CW - thumb.width) // 2,
                                (CH - thumb.height) // 2))
            sheet.paste(face.resize((256, 256), Image.LANCZOS),
                        (i * CW + (CW - 256) // 2, CH + 20))
        out = Path(args.preview_out)
        sheet.save(out, quality=90)
        print(f"\npreview -> {out}  (top row: poster, bottom row: face crop, "
              f"order: {', '.join(m for m, *_ in tiles)})")
        return 0 if failed == 0 else 2

    if written_ids:
        update_manifest(args.group, written_ids)
    print(f"\ndone: {made} written, {skipped} skipped, {failed} failed.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
