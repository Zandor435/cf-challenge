#!/usr/bin/env python3
"""Lay the flat lower-third overlay band onto generated scene art.

NOT part of the live weekly pipeline. Run by hand, after a scene batch.

    output/scenes/<group>/*.png  ->  same path, band composited in place
    raw pre-band original preserved under output/_source/scenes/<group>/

WHY THIS EXISTS AT ALL. The scene prompt already asks for a clean, flat lower
third and the model largely obliges -- but "largely" is the problem. A band the
site overlays live HTML type into has to be FLAT, and a generated one never
quite is: it carries scenery gradient, a stray branch, ink texture from the
style, a horizon that drifts a few pixels between variants. Type set against
that is legible in one render and mud in the next. So the prompt clause keeps
the composition clear of the strip and this lays down the actual surface.

NO KEYING, and that is deliberate and load-bearing -- the same rule
composite_halfcards.py states for its plates. The band is an OPAQUE RECTANGLE
pasted over the art, meeting it at a HARD SEAM. There is no alpha, no gradient
ramp, no feathered edge, no threshold and no background matting anywhere in
this file. A soft edge would put a few hundred rows of half-band between the
picture and the type, which is exactly the unpredictable surface the band
exists to remove. If you are about to add a fade, don't.

WHY THE SEAM IS DRAWN. Butting two opaque areas together leaves a boundary that
reads as an accident when the art happens to be dark at the meeting line. A
deliberate rule along the top edge makes it a decision instead -- the same
reason a real lower third has one.

COLOR IS READ, NEVER TYPED. Fill and seam come from teams_canonical.json's
`color` (the primary) and `alternateColor`, so the band cannot drift away from
the palette the batch was generated against.

Playbook compliance (CLAUDE.md):
  - rule 5: the raw original is copied to output/_source/ BEFORE the in-place
    write, and never overwritten once there -- so re-running is idempotent and
    a second pass bands the raw again rather than banding a banded image.
  - rule 7: --skip-if-exists is the DEFAULT via that same source copy; --force
    re-bands from the preserved raw. No network calls, no paid API.

    python scripts/composite_lower_band.py --group church [--team Duke]
    python scripts/composite_lower_band.py --group church --dry-run
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "docs" / "data" / "teams_canonical.json"

# Fraction of image height the band occupies. A third is what the brief asks
# for and what the prompt clause reserves; the two must agree or the band
# either crops the composition or leaves generated detail showing beneath it.
BAND_FRAC = 1.0 / 3.0

# Height of the hard rule along the band's top edge, in pixels at 1080p-ish
# scale, then scaled with the image so it reads the same on any output size.
SEAM_PX_AT_1080 = 4


def team_color(team, key="color"):
    data = json.loads(CANONICAL.read_text(encoding="utf-8"))
    for t in data.get("teams", []):
        if t.get("school") == team:
            return t.get(key)
    raise SystemExit(f"ERROR: team {team!r} not in {CANONICAL.name}")


def hex_to_rgb(h):
    h = (h or "").lstrip("#")
    if len(h) != 6:
        raise SystemExit(f"ERROR: not a #rrggbb color: {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def band(img, fill, seam):
    """Paste the opaque band. Returns a new image; the input is not mutated."""
    out = img.convert("RGB").copy()
    w, h = out.size
    top = h - int(round(h * BAND_FRAC))
    d = ImageDraw.Draw(out)
    # The band itself: one opaque rectangle, full width, hard edges.
    d.rectangle([0, top, w, h], fill=fill)
    # The seam: a deliberate rule, not a blend.
    seam_h = max(2, int(round(SEAM_PX_AT_1080 * h / 1080)))
    d.rectangle([0, top, w, top + seam_h - 1], fill=seam)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True, help="scene group, e.g. church")
    ap.add_argument("--team", default="Duke",
                    help="canonical team supplying the band colors "
                         "(default Duke)")
    ap.add_argument("--category", default="scenes")
    ap.add_argument("--force", action="store_true",
                    help="re-band from the preserved raw original")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be banded; write nothing")
    a = ap.parse_args()

    live = ROOT / "output" / a.category / a.group
    raw_dir = ROOT / "output" / "_source" / a.category / a.group
    if not live.is_dir():
        raise SystemExit(f"ERROR: nothing at {live}")

    fill = hex_to_rgb(team_color(a.team, "color"))
    seam_hex = team_color(a.team, "alternateColor") or "#ffffff"
    seam = hex_to_rgb(seam_hex)
    print(f"group={a.group} team={a.team} fill=#{'%02x%02x%02x' % fill} "
          f"seam=#{'%02x%02x%02x' % seam} band={BAND_FRAC:.3f} of height")

    done = skipped = 0
    for src in sorted(live.glob("*.png")):
        raw = raw_dir / src.name
        already = raw.exists()
        if already and not a.force:
            print(f"  skip (already banded): {src.name}")
            skipped += 1
            continue
        if a.dry_run:
            print(f"  would band: {src.name}")
            done += 1
            continue
        if not already:
            # Preserve BEFORE the destructive write, never after.
            raw_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, raw)
        with Image.open(raw) as im:
            out = band(im, fill, seam)
        out.save(src, "PNG")
        print(f"  banded {src.name} ({out.size[0]}x{out.size[1]})")
        done += 1

    verb = "would band" if a.dry_run else "banded"
    print(f"\n{verb} {done}, skipped {skipped}. "
          f"raws under {raw_dir.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
