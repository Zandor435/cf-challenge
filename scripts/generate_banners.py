#!/usr/bin/env python3
"""Season-kickoff group banners — all four managers in one 21:9 image.

NOT part of the live weekly pipeline. Run by hand.

Feeds every manager's approved persona poster in as a CHARACTER-CONSISTENCY
reference in a single call, so the result is a real group render rather than
four separate images composited together. Gemini 3 image models carry dedicated
character slots for exactly this (Pro: 5, 3.1 Flash: 4), which is why a
four-man lineup fits without the faces blending.

Output is a wide banner for the site's hero strip. Several styles, several
variants each, all written to a gitignored folder for review -- the point is a
pile to choose from, not one precious render.

Usage:
    python scripts/generate_banners.py --group panel \
        --ref "blaine=output/personas/Fat friends/Fat/fat blaine.png:Oklahoma State" \
        --ref "chris=output/personas/Fat friends/Fat/fat beck.png:Colorado" \
        --styles all --n 3 --preview

    # review the resolved prompts, then drop --preview
    python scripts/generate_banners.py --group panel --ref ... --styles all --n 3

Playbook compliance (CLAUDE.md):
  - rule 2: reuses _post_with_retries from generate_owner_images.
  - rule 6: shared per-provider UTC-daily budget tally.
  - rule 7: --skip-if-exists by DEFAULT; --force is required to re-bill.

Privacy: sources are gitignored; a chosen banner is published only by passing it
through scripts/prepare_portraits.py --banner.
"""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_owner_images import (  # noqa: E402
    _post_with_retries, bump_budget, load_budget,
)
from recolor_personas import color_name, team_colors  # noqa: E402
import gemini_image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = gemini_image.DEFAULT_MODEL

# Shared across every style. The group shot has the same non-negotiable as the
# solo recolors -- these are real people and the joke only works if they are
# recognizably themselves.
GROUP_LOCK = (
    " CRITICAL: each man must be clearly recognizable as the specific individual "
    "from his reference image -- same face, same facial features, same hair, same "
    "heavy-set body shape and build. Do NOT slim any of them, do NOT merge or "
    "blend their faces, do NOT substitute generic people, do NOT make them look "
    "like each other. Every man is large and heavy-set and must stay that way. "
    "All {n} men appear together in ONE scene, side by side, full body or "
    "three-quarter length, roughly equal prominence, each clearly distinguishable."
)

# No lettering: the site's own type layer supplies the headline over the banner,
# and generated text renders unreliably at 21:9 anyway.
NO_TEXT = (
    " Do NOT render any words, letters, numbers, team names, or watermarks "
    "anywhere in the image -- artwork only, no typography."
)

STYLES = {
    "comic": (
        "A COMIC BOOK SPLASH PAGE of {n} heavy-set college football head coaches "
        "standing together as a team, in the style of a classic superhero comic: "
        "bold black ink outlines, heavy halftone dot shading, flat saturated cel "
        "colors, dramatic low-angle heroic framing, dynamic speed lines and "
        "action rays radiating behind them, stadium and floodlights in the "
        "background. Each coach wears his own team's colors: {colors}."
    ),
    "vintage70s": (
        "A VINTAGE 1970s TEAM PHOTOGRAPH of {n} heavy-set college football head "
        "coaches posed stiffly in a row on a practice field, shot on faded "
        "Kodachrome film: heavy grain, washed-out sepia-orange color cast, slight "
        "lens vignette, flat midday light, deadpan formal poses with hands on "
        "hips. Period-accurate 1970s coaching apparel in each man's team colors: "
        "{colors}."
    ),
    "tradingcard": (
        "A PAINTED SPORTS TRADING-CARD POSTER of {n} heavy-set college football "
        "head coaches together, rendered as a premium illustrated hero poster: "
        "rich painterly brushwork, dramatic stadium floodlight rim-lighting, deep "
        "shadows, heroic three-quarter poses, glowing atmospheric haze, saturated "
        "team color throughout. Each coach in his own team's colors: {colors}."
    ),
    "boxart": (
        "A GLOSSY VIDEO-GAME COVER ART composition of {n} heavy-set college "
        "football head coaches, in the style of a big-budget sports video game "
        "box: high-gloss digital illustration, heavy drop shadows, motion streaks "
        "and light trails sweeping behind the figures, saturated gradient "
        "background, dramatic rim lighting, confident hero poses. Each coach in "
        "his own team's colors: {colors}."
    ),
}


def gen_banner(api_key, model, refs, prompt, aspect):
    """Thin delegate — the payload/retry path lives in gemini_image.generate()."""
    return gemini_image.generate(api_key, model, refs, prompt, aspect, timeout=420)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--ref", action="append", default=[], metavar="ID=PATH:TEAM",
                    help="manager_id=poster_path:Team Name (repeatable)")
    ap.add_argument("--styles", default="all",
                    help=f"comma-separated or 'all' ({', '.join(STYLES)})")
    ap.add_argument("--n", type=int, default=3, help="variants per style")
    ap.add_argument("--aspect", default="21:9")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=str(ROOT / "output" / "banners"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--preview", action="store_true",
                    help="print resolved prompts, write NOTHING, bill NOTHING")
    args = ap.parse_args()

    colors = team_colors()
    refs, desc = [], []
    for entry in args.ref:
        if "=" not in entry or ":" not in entry.split("=", 1)[1]:
            ap.error(f"--ref needs ID=PATH:TEAM, got {entry!r}")
        mid, rest = entry.split("=", 1)
        raw_path, team = rest.rsplit(":", 1)
        src = Path(raw_path) if Path(raw_path).is_absolute() else ROOT / raw_path
        if not src.exists():
            print(f"ERROR: poster not found for {mid!r}: {src}", file=sys.stderr)
            return 1
        team = team.strip()
        if team not in colors:
            print(f"ERROR: team {team!r} (for {mid}) not in teams_canonical.json",
                  file=sys.stderr)
            return 1
        hexc = colors[team].get("color")
        refs.append((src, mid, team, hexc))
        # Ordinal, NOT manager_id: the model sees an ordered list of
        # reference images and has no way to map a name onto one. The
        # index is the only handle that actually binds a face to a color.
        desc.append(f"the man from reference image #{len(refs)} wears "
                    f"{team} {color_name(hexc)} ({hexc})")
    if not refs:
        ap.error("at least one --ref ID=PATH:TEAM is required")

    # Character-consistency slots are finite; naming the ceiling beats a 400.
    if len(refs) > 5:
        print(f"ERROR: {len(refs)} references exceeds the 5 character slots on "
              f"{args.model}. Split the group or composite instead.", file=sys.stderr)
        return 1

    style_names = list(STYLES) if args.styles == "all" else \
        [s.strip() for s in args.styles.split(",") if s.strip()]
    unknown = [s for s in style_names if s not in STYLES]
    if unknown:
        print(f"ERROR: unknown style(s): {unknown}. Available: {', '.join(STYLES)}",
              file=sys.stderr)
        return 1

    n_people = len(refs)
    colors_str = "; ".join(desc)
    prompts = {
        s: STYLES[s].format(n=n_people, colors=colors_str)
        + GROUP_LOCK.format(n=n_people) + NO_TEXT
        for s in style_names
    }

    print(f"model={args.model} aspect={args.aspect} people={n_people}")
    print(f"styles={', '.join(style_names)} x {args.n} = "
          f"{len(style_names) * args.n} planned paid calls\n")
    for s in style_names:
        print(f"  [{s}]")
    if args.preview:
        first = style_names[0]
        print(f"\n--- prompt sample ({first}) ---\n{prompts[first]}")
        print("\npreview only; nothing written, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    import os
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("ERROR: GEMINI_API_KEY missing from .env", file=sys.stderr)
        return 1

    ref_bytes = [(p.read_bytes(), "image/png") for p, *_ in refs]
    out_dir = Path(args.out) / args.group
    out_dir.mkdir(parents=True, exist_ok=True)

    budget = load_budget()
    made, skipped, failed = 0, 0, 0
    for style in style_names:
        for i in range(1, args.n + 1):
            out_file = out_dir / f"{args.group}_{style}_{i:02d}.png"
            if out_file.exists() and not args.force:
                print(f"  skip (exists): {out_file.relative_to(ROOT)}")
                skipped += 1
                continue
            print(f"  {style} #{i} ...")
            total = bump_budget(budget, "gemini")
            if total > args.daily_warn:
                print(f"  ::warning:: image-API daily tally {total} -- past the "
                      f"{args.daily_warn} threshold.")
            try:
                img = gen_banner(key, args.model, ref_bytes, prompts[style], args.aspect)
            except Exception as e:
                print(f"  FAILED {style} #{i}: {e}", file=sys.stderr)
                failed += 1
                continue
            out_file.write_bytes(img)
            made += 1
            print(f"  wrote {out_file.relative_to(ROOT)} ({len(img)//1024} KB)")

    req = budget["requests"]
    print(f"\ndone: {made} generated, {skipped} skipped, {failed} failed. "
          f"Today (UTC): gemini {req.get('gemini',0)}, openai {req.get('openai',0)}.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
