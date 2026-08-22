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
        --ref "blaine=output/personas/panel/panel_blaine_fat_01.png:Oklahoma State" \
        --ref "chris=output/personas/panel/panel_chris_fat_01.png:Colorado" \
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
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_owner_images import (  # noqa: E402
    _post_with_retries, bump_budget, load_budget,
)
from recolor_personas import color_name, team_colors  # noqa: E402
import banner_batch  # noqa: E402  -- pure data, no side effects
import gemini_image  # noqa: E402
import style_spec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = gemini_image.DEFAULT_MODEL

# Shared across every style. The group shot has the same non-negotiable as the
# solo recolors -- these are real people and the joke only works if they are
# recognizably themselves.
# A --ref may carry a literal colour instead of a canonical team name.
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

GROUP_LOCK = (
    " CRITICAL: each man must be clearly recognizable as the specific individual "
    "from his reference image -- same face, same facial features, same hair, same "
    "heavy-set body shape and build. Do NOT slim any of them, do NOT merge or "
    "blend their faces, do NOT substitute generic people, do NOT make them look "
    "like each other. Every man is large and heavy-set and must stay that way. "
    "All {n} men appear together in ONE scene, side by side, full body or "
    "three-quarter length, roughly equal prominence, each clearly distinguishable."
)

# The same invariant with the fat-coach joke removed. GROUP_LOCK asserts every
# subject is "large and heavy-set", which is the whole premise for panel,
# browns and church -- and false for family, whose art is real photographs of
# a grandmother, a boy and several adults. Asserting a body type about real
# people who do not have it is not a style choice, it is being wrong about
# them. Everything load-bearing is kept: same face, no merging, no generic
# substitutes, no slimming, all N present and distinguishable.
GROUP_LOCK_PLAIN = (
    " CRITICAL: each person must be clearly recognizable as the specific "
    "individual from their reference image -- same face, same facial features, "
    "same hair, same age, same body shape and build. Do NOT slim anyone, do "
    "NOT alter anyone's age, do NOT merge or blend their faces, do NOT "
    "substitute generic people, do NOT make them look like each other. All "
    "{n} people appear together in ONE scene, side by side, full body or "
    "three-quarter length, roughly equal prominence, each clearly "
    "distinguishable."
)

LOCKS = {"heavyset": GROUP_LOCK, "plain": GROUP_LOCK_PLAIN}

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


def show(path):
    """Repo-relative when it is inside the repo, absolute when it is not.

    --out accepts any path, including a scratch directory outside the repo, and
    Path.relative_to() RAISES on that rather than returning something. Printing
    a filename must never be able to fail a run that has already spent money.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


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
    ap.add_argument("--style-spec", action="append", default=[], metavar="PATH",
                    help="a derived style fragment (repeatable). Opts into the "
                         "decomposed path: composition, style, wardrobe and "
                         "locks are separate and only the fragment varies.")
    ap.add_argument("--composition", default=None,
                    choices=[e["slug"] for e in banner_batch.BANNERS],
                    help="required with --style-spec; supplies pose/framing/"
                         "setting, which the legacy STYLES bake in themselves")
    ap.add_argument("--n", type=int, default=3, help="variants per style")
    ap.add_argument("--aspect", default=None,
                    help="default 21:9, or the composition's own")
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
        # A raw #RRGGBB is accepted in place of a team name, for groups whose
        # identity is not a college allegiance. Browns is the first: its
        # premise is "Cleveland Browns fans first" with one Wake Forest
        # outlier, and two of its five managers have no stated college at all.
        # Filling `team` in personas.json to satisfy this parser would invent a
        # public claim about a real person -- sync_personas.py publishes that
        # file to the live site. Canonical validation is untouched for anyone
        # who does pass a team name.
        if HEX_RE.match(team):
            hexc, team = team.lower(), None
        elif team not in colors:
            print(f"ERROR: team {team!r} (for {mid}) not in teams_canonical.json"
                  f" -- pass a canonical team name or a literal #RRGGBB",
                  file=sys.stderr)
            return 1
        else:
            hexc = colors[team].get("color")
        refs.append((src, mid, team, hexc))
        # Ordinal, NOT manager_id: the model sees an ordered list of
        # reference images and has no way to map a name onto one. The
        # index is the only handle that actually binds a face to a color.
        desc.append((refs, team, hexc))
    if not refs:
        ap.error("at least one --ref ID=PATH:TEAM is required")

    # Character-consistency slots are finite; naming the ceiling beats a 400.
    if len(refs) > 5:
        print(f"ERROR: {len(refs)} references exceeds the 5 character slots on "
              f"{args.model}. Split the group or composite instead.", file=sys.stderr)
        return 1

    # ---- part 1: STYLE. Derived fragments load and validate before anything
    # is spent; an invalid one stops the run naming which part it reached into.
    specs = {}
    for raw in args.style_spec:
        spec = style_spec.load(Path(raw) if Path(raw).is_absolute()
                               else ROOT / raw)
        if spec["style_id"] in STYLES:
            print(f"ERROR: spec {spec['style_id']!r} shadows a built-in style. "
                  f"Rename it -- the built-ins are frozen.", file=sys.stderr)
            return 1
        specs[spec["style_id"]] = spec
    if specs and not args.composition:
        print("ERROR: --style-spec needs --composition. A fragment says only "
              "HOW the picture is made; something has to say what is in it. "
              f"Choose from: {', '.join(e['slug'] for e in banner_batch.BANNERS)}",
              file=sys.stderr)
        return 1

    available = dict(STYLES)
    available.update({k: v["fragment"] for k, v in specs.items()})
    style_names = list(available) if args.styles == "all" else \
        [s.strip() for s in args.styles.split(",") if s.strip()]
    unknown = [s for s in style_names if s not in available]
    if unknown:
        print(f"ERROR: unknown style(s): {unknown}. Available: "
              f"{', '.join(available)}", file=sys.stderr)
        return 1

    n_people = len(refs)
    comp = banner_batch.BY_SLUG.get(args.composition)
    # The subject NOUN belongs to the composition: "man" is right for the three
    # coach line-ups and wrong for family, whose roster includes a grandmother
    # and a boy. Ordinal still does the binding work -- the model has no way to
    # map a name onto a reference image, so the index is the only handle.
    noun = (comp or {}).get("subject", banner_batch.DEFAULT_SUBJECT)
    colors_str = "; ".join(
        f"the {noun} from reference image #{i} wears "
        + (f"{team} " if team else "") + f"{color_name(hexc)} ({hexc})"
        for i, (_r, team, hexc) in enumerate(desc, start=1))
    lock = LOCKS[(comp or {}).get("lock", banner_batch.DEFAULT_LOCK)]

    def assemble(s):
        """Concatenate the five parts in a fixed order. Authors nothing.

        LEGACY path (no spec): STYLES[s] is a whole prompt that already bakes
        in composition, setting and subject count, so it is used as-is. Those
        four strings produced the approved panel art and browns_comic_02 and
        are frozen.

        DECOMPOSED path (spec): composition, style, wardrobe and locks are
        separate strings. Holding composition + refs constant and varying only
        the fragment yields the same picture in a different medium -- the thing
        the legacy path cannot express, because switching `comic` for
        `vintage70s` also switches the pose and the setting.
        """
        spec = specs.get(s)
        if spec is None:
            head = STYLES[s].format(n=n_people, colors=colors_str)
        else:
            head = (comp["text"].format(n=n_people)
                    + " " + available[s].strip().rstrip(".") + "."
                    + f" Each {noun} wears their own color: " + colors_str + ".")
        tail = (GROUP_LOCK if spec is None else lock).format(n=n_people)
        # NO_TEXT is suppressed only by an explicit typography policy; the
        # default and every legacy style keep it.
        if (spec or {}).get("text_policy") != "typography":
            tail += NO_TEXT
        return head + tail

    prompts = {s: assemble(s) for s in style_names}

    # Aspect: explicit flag > the chosen composition's own > the 21:9 default.
    # COMPOSITION owns aspect, not style -- "a row of five, end to end" is 21:9
    # because of the row, not because of the ink. A spec's `aspect` records what
    # the style was derived AT and is advisory, so a disagreement is announced
    # rather than silently resolved; silence here is how you ship a poster style
    # squeezed into a masthead and spend an hour blaming the prompt.
    if args.aspect is None:
        args.aspect = (comp or {}).get("aspect") or "21:9"
    for sid, sp in specs.items():
        if sp.get("aspect") and sp["aspect"] != args.aspect and sid in style_names:
            print(f"  note: spec {sid!r} was derived at {sp['aspect']}; "
                  f"rendering at {args.aspect} "
                  f"({'--aspect' if comp is None else 'composition ' + comp['slug']})"
                  f". Pass --aspect {sp['aspect']} to match the source.")

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
                print(f"  skip (exists): {show(out_file)}")
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
            print(f"  wrote {show(out_file)} ({len(img)//1024} KB)")

    req = budget["requests"]
    print(f"\ndone: {made} generated, {skipped} skipped, {failed} failed. "
          f"Today (UTC): gemini {req.get('gemini',0)}, openai {req.get('openai',0)}.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
