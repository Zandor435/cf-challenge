#!/usr/bin/env python3
"""Depth-staged scene library + trophy object plate for the panel group.

NOT part of the live weekly pipeline. Run by hand, in gated phases.

Every call goes through gemini_image.generate() — the same path the banner
generator uses. Character consistency comes from REFERENCE IMAGES, never from
text description of a face; the text only names a distinguishing cue so a
figure still reads correctly when small or in silhouette.

Naming:
    output/scenes/panel/panel_<setup>_<lead>_<style>_<nn>.png
    output/objects/trophy_<angle>_<nn>.png

<lead> is the manager in the foreground — the key the site will use to pick a
per-manager scene.

Hard rules enforced here, not left to the prompt author:
  - ILLUSTRATED ONLY. STYLE_SET carries no photographic, cinematic or
    film-stock language, and --check-prompts fails the run if a banned term
    appears in any assembled prompt.
  - BUILD_LOCK restates body shape in every scene prompt. Phase 2 proved
    reference images alone do not carry build: without it, the manager whose
    reference is a podium crop came back slimmed in 4/4 renders.
  - Traits come from groups/panel/personas.json. A manager with no recorded
    cue gets no invented one.

Usage:
    python scripts/generate_scenes.py --phase trophy --dry-run
    python scripts/generate_scenes.py --phase trophy
    python scripts/generate_scenes.py --phase tunnel --styles comic,painted

Playbook compliance (CLAUDE.md):
  - rule 2: retry/backoff via the shared _post_with_retries shell.
  - rule 6: shared per-provider UTC-daily budget tally + threshold warning.
  - rule 7: --skip-if-exists by DEFAULT; --force is required to re-bill.
"""

import argparse
import json
import re
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gemini_image  # noqa: E402
from recolor_personas import color_name, team_colors  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PERSONAS = ROOT / "groups" / "panel" / "personas.json"

# Illustrated only. Anything evoking a camera, a film stock or a photograph is
# banned -- photoreal staging is what breaks likeness once a body is in motion.
STYLE_SET = {
    "comic": ("rendered as COMIC BOOK ART: bold black ink linework, heavy "
              "halftone dot shading, saturated flat color fills, strong "
              "graphic blacks"),
    "painted": ("rendered as a GOUACHE SPORTS ILLUSTRATION: visible brush "
                "strokes, painterly edges, rich layered pigment, the look of "
                "a hand-painted sports magazine plate"),
    "screenprint": ("rendered as a THREE-COLOR SCREENPRINTED POSTER: heavy "
                    "flat shapes, hard edges, limited ink palette, visible "
                    "registration, no gradients"),
    "woodcut": ("rendered as a SCRATCHBOARD WOODCUT ENGRAVING: extreme "
                "black-and-white contrast, carved directional line texture, "
                "chiseled shapes, sparse accent color"),
}

# Assembled prompts are scanned for these before a single call is made.
# ILLUSTRATED-ONLY ONLY. This guard is technical, not editorial: photoreal
# staging is what breaks likeness once a body is in motion. Body/food/weight
# terms were removed at Z's instruction on 2026-08-18 -- this is a private site,
# the subjects are the author's own friends, and the body-shape wording is
# load-bearing for build fidelity (see BUILD_LOCK).
BANNED_TERMS = [
    "photo", "photograph", "photorealistic", "photoreal", "cinematic",
    "kodachrome", "film grain", "film stock", "35mm", "dslr", "lens flare",
    "hyperrealistic", "realistic render",
]

ILLUSTRATION_LOCK = (
    " This is an ILLUSTRATION, not a photograph. Keep the drawn, hand-made "
    "quality throughout."
)

# The wording the approved 28 relied on to hold body shape. Reference images
# alone did NOT carry it: without this clause Zach came back visibly slimmed in
# all four Phase 2 tunnel renders, because his source poster is a podium crop
# that shows very little of his body. Text and reference do different jobs --
# the reference carries the face, this carries the build.
BUILD_LOCK = (
    " Every one of these men is large, heavy-set and big-bodied. Keep each "
    "man's body shape, size and build exactly as it appears in his reference "
    "image. Do NOT slim anyone down, do NOT shrink anyone, do NOT give anyone "
    "an athletic or average build."
)

NO_TEXT = (
    " Do not render any words, letters, numbers, team names or watermarks "
    "anywhere in the image -- artwork only, no typography."
)

# ---------------------------------------------------------------------------
# Phase 1 -- trophy object plate
# ---------------------------------------------------------------------------
# Deliberately differentiated from the real championship trophy: this is a
# parody league object and must not be mistakable for the actual award. The
# etched OVER / UNDER wording, the tri-color refraction and the squared matte
# plinth are the differentiators, and they are stated as requirements.
TROPHY_BASE = (
    "A fictional fantasy-league championship trophy, {style}. A faceted "
    "crystal glass American football, mounted upright with its long axis "
    "vertical, on a squared matte black pedestal. The faceted glass refracts "
    "and scatters warm orange, gold and deep red light through its interior. "
    "The two words OVER and UNDER are etched into the front face of the "
    "pedestal, stacked one above the other, separated by a horizontal etched "
    "rule. Flat neutral studio-grey background, no environment, no people, no "
    "figures, no logos, no team marks. This is an invented parody-league "
    "object: give it its own identity -- a distinctly squared, blocky plinth "
    "and heavy visible faceting -- and do NOT reproduce or closely imitate any "
    "real, existing championship award."
)
TROPHY_ANGLES = {
    "front": ("Straight-on view at eye level, perfectly symmetrical, "
              "centered composition, even frontal lighting."),
    "low": ("Dramatic low angle looking steeply up at the trophy, heroic and "
            "imposing, strong warm rim light raking across the facets from "
            "behind."),
    "three_qtr": ("Three-quarter turned view showing the depth and thickness "
                  "of the pedestal, gentle side lighting revealing the "
                  "form."),
}

# ---------------------------------------------------------------------------
# Phase 2 / 3 -- scene setups
# ---------------------------------------------------------------------------
SETUPS = {
    "tunnel": (
        "A stadium tunnel scene, {style}. THE LEAD FIGURE is in the "
        "foreground, large in frame, emerging into hard bright light at the "
        "tunnel mouth -- his face fully lit and rendered in sharp, complete "
        "detail. The three other men are ranked behind him at receding "
        "depths: one at midground in three-quarter view and softer detail, "
        "and two far back in the tunnel darkness, small and low-detail, "
        "close to silhouette. Shafts of light full of drifting dust and haze. "
        "Viewpoint is low, near ground level, looking up at the lead figure."
    ),
    "sideline": (
        "A football sideline scene, {style}. THE LEAD FIGURE is in profile at "
        "the edge of the frame, wearing a coaching headset, one hand cupped "
        "over his ear, mouth open mid-shout. A second man stands at midground "
        "gesturing. Two more are small and indistinct further upfield. The "
        "horizon is tilted for a canted, off-kilter composition."
    ),
    "shoulder": (
        "A meeting-room scene viewed from behind and above THE LEAD FIGURE's "
        "shoulder, {style}. The lead is a large dark out-of-focus mass "
        "occupying one corner of the frame, seen from behind. The three other "
        "men sit across a table in bright light, sharply detailed, caught "
        "mid-argument with animated expressions. Wide-angle framing that "
        "exaggerates the depth between foreground and background."
    ),
    "huddle": (
        "A tight football huddle, {style}. All four men lean in over a play "
        "sheet, heads nearly touching, forming a ring. {angle} THE LEAD "
        "FIGURE's face catches the key light and is the most clearly rendered "
        "of the four."
    ),
    "trophy": (
        "A trophy presentation scene, {style}. The championship trophy from "
        "the final reference image stands on a pedestal at midground, lit and "
        "clearly visible -- reproduce that trophy's design faithfully. THE "
        "LEAD FIGURE is in the foreground looking toward it, turned partly "
        "away from the viewer with his back three-quarters to us. Two more "
        "men flank the trophy further back at depth."
    ),
    "bench": (
        "A dejected sideline bench scene, {style}. The men are staggered at "
        "clearly different distances -- NOT a flat row. THE LEAD FIGURE is "
        "closest to the viewer, slumped forward, elbows on knees, helmet held "
        "in his hands, head down. The others recede behind him at uneven "
        "intervals, each showing a different degree of dejection. Compressed "
        "long-lens perspective flattening the stack of figures."
    ),
}
HUDDLE_ANGLES = {
    "up": "Viewed from low inside the huddle looking up at the ring of faces.",
    "down": "Viewed from directly overhead looking down into the ring.",
}

# Fidelity budget, stated explicitly so the model spends detail where it counts.
def fidelity_clause(lead_cue, other_cues):
    cues = "; ".join(c for c in other_cues if c) or "their general build"
    lead_bit = f" The lead is identifiable by {lead_cue}." if lead_cue else ""
    return (
        " FIDELITY: the LEAD FIGURE carries full likeness -- he must be "
        "clearly and unmistakably recognizable as the specific individual in "
        "reference image #1, with the same face, same features and same hair, "
        "rendered in full detail." + lead_bit +
        " The background figures do NOT need facial detail and should not be "
        "given invented faces; they need only read as the correct silhouettes, "
        f"identifiable by their distinguishing cues: {cues}. Do not blend or "
        "merge any of the men together, and do not substitute generic people."
    )


# Preference order for a manager's character reference. "accent" is the
# approved pick for three of the four, but Chris's accent was archived after
# review and only his gold jacket survives, so the reference MUST be resolved
# from what is actually on disk. Hardcoding a variant silently breaks the whole
# batch the moment a selection changes.
REF_PREFERENCE = ["accent", "jacket", "quarterzip", "polo"]


def resolve_reference(mid):
    """Return the kept recolor to use as this manager's character reference."""
    d = ROOT / "output" / "personas" / "recolor" / mid
    have = {f.stem.split("_")[1]: f for f in d.glob(f"{mid}_*_gemini.png")}
    for slug in REF_PREFERENCE:
        if slug in have:
            return have[slug], slug
    return None, None


def load_personas():
    data = json.loads(PERSONAS.read_text(encoding="utf-8"))
    return data["managers"]


def check_prompt(text, label):
    """Fail loud BEFORE spending a call if a banned term slipped in.

    Two subtleties, both found by this guard misfiring on its own output:
      - Match on WORD BOUNDARIES, not substrings. Plain `"eat" in text` fires
        on "great" and "features"; "fat" would fire on any word containing it.
      - Scan only the VARIABLE content. The fixed lock clauses are reviewed
        constants that legitimately contain "photograph" inside a NEGATIVE
        instruction ("this is an ILLUSTRATION, not a photograph"). Strip them
        first, or the guard rejects its own safety wording.
    """
    scanned = text
    for constant in (ILLUSTRATION_LOCK, NO_TEXT, BUILD_LOCK):
        scanned = scanned.replace(constant, " ")
    low = scanned.lower()
    hits = sorted({t for t in BANNED_TERMS
                   if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low)})
    if hits:
        raise SystemExit(
            f"ERROR: prompt for {label} contains banned term(s): {', '.join(hits)}\n"
            f"       Illustrated-only and no-body-comedy rules are enforced "
            f"before any paid call.")


def build_trophy_prompts(styles):
    out = {}
    for angle, angle_txt in TROPHY_ANGLES.items():
        for style in styles:
            p = (TROPHY_BASE.format(style=STYLE_SET[style]) + " " + angle_txt
                 + ILLUSTRATION_LOCK)
            # NO_TEXT is deliberately omitted: the etched OVER / UNDER wording
            # is a required part of this object's design.
            out[(angle, style)] = p
    return out


def build_scene_prompt(setup, lead, styles_key, personas, colors, huddle_angle=None):
    lead_p = personas[lead]
    others = [m for m in personas if m != lead]
    body = SETUPS[setup]
    fmt = {"style": STYLE_SET[styles_key]}
    if setup == "huddle":
        fmt["angle"] = HUDDLE_ANGLES[huddle_angle or "up"]
    text = body.format(**fmt)

    wardrobe = []
    for i, mid in enumerate([lead] + others, start=1):
        team = personas[mid]["team"]
        hexc = colors[team]["color"]
        wardrobe.append(f"the man from reference image #{i} wears "
                        f"{team} {color_name(hexc)} ({hexc})")
    text += " Team colors: " + "; ".join(wardrobe) + "."
    text += fidelity_clause(lead_p.get("silhouette_cue"),
                            [personas[m].get("silhouette_cue") for m in others])
    text += BUILD_LOCK + ILLUSTRATION_LOCK + NO_TEXT
    return text, [lead] + others


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # "trophy_plate" is the Phase 1 OBJECT; "trophy" is the Phase 3 SCENE that
    # uses it. Distinct names -- sharing one would make the scene unreachable.
    ap.add_argument("--phase", required=True,
                    choices=["trophy_plate"] + list(SETUPS))
    ap.add_argument("--styles", default="painted",
                    help=f"comma-separated ({', '.join(STYLE_SET)})")
    ap.add_argument("--leads", default="blaine,chris,jonathan,zach")
    ap.add_argument("--n", type=int, default=2, help="variants (minimum 2)")
    ap.add_argument("--aspect", default=None,
                    help="default 3:4 for trophy, 16:9 for scenes")
    ap.add_argument("--model", default=gemini_image.DEFAULT_MODEL)
    ap.add_argument("--trophy-plate", default=None,
                    help="path to the canonical trophy plate (trophy setup only)")
    ap.add_argument("--huddle-angle", default="up", choices=list(HUDDLE_ANGLES))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble and validate prompts, write NOTHING, bill NOTHING")
    args = ap.parse_args()

    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    unknown = [s for s in styles if s not in STYLE_SET]
    if unknown:
        print(f"ERROR: unknown style(s) {unknown}. Available: "
              f"{', '.join(STYLE_SET)}", file=sys.stderr)
        return 1
    n = max(2, args.n)   # "every generation produces 2 variants minimum"

    personas = load_personas()
    colors = team_colors()

    jobs = []   # (out_path, prompt, ref_paths, aspect)
    if args.phase == "trophy_plate":
        aspect = args.aspect or "3:4"
        prompts = build_trophy_prompts(styles)
        for (angle, style), prompt in prompts.items():
            check_prompt(prompt, f"trophy/{angle}/{style}")
            for i in range(1, n + 1):
                jobs.append((ROOT / "output" / "objects" /
                             f"trophy_{angle}_{i:02d}.png", prompt, [], aspect))
    else:
        aspect = args.aspect or "16:9"
        leads = [l.strip() for l in args.leads.split(",") if l.strip()]
        bad = [l for l in leads if l not in personas]
        if bad:
            print(f"ERROR: unknown lead(s) {bad}", file=sys.stderr)
            return 1
        # The trophy SCENE must carry the chosen plate as an extra reference;
        # generating it from a text description instead would produce a
        # different trophy in every frame.
        plate = None
        if args.trophy_plate:
            plate = Path(args.trophy_plate)
            if not plate.is_absolute():
                plate = ROOT / args.trophy_plate
            if not plate.exists():
                print(f"ERROR: trophy plate not found: {plate}", file=sys.stderr)
                return 1
        elif args.phase == "trophy":
            print("ERROR: the trophy scene requires --trophy-plate PATH "
                  "(the canonical plate selected in Phase 1).", file=sys.stderr)
            return 1
        for lead in leads:
            for style in styles:
                prompt, order = build_scene_prompt(
                    args.phase, lead, style, personas, colors, args.huddle_angle)
                check_prompt(prompt, f"{args.phase}/{lead}/{style}")
                refs = []
                for m in order:
                    rp, _slug = resolve_reference(m)
                    if rp is None:
                        print(f"ERROR: no kept recolor on disk for {m!r} — "
                              f"every variant appears to be archived. Restore "
                              f"one from output/archive/recolor/{m}/ or pass a "
                              f"different --leads set.", file=sys.stderr)
                        return 1
                    refs.append(rp)
                if plate:
                    refs = refs + [plate]
                for i in range(1, n + 1):
                    jobs.append((ROOT / "output" / "scenes" / "panel" /
                                 f"panel_{args.phase}_{lead}_{style}_{i:02d}.png",
                                 prompt, refs, aspect))

    limit = gemini_image.ref_limit(args.model)
    over = [j for j in jobs if len(j[2]) > limit]
    if over:
        print(f"ERROR: {len(over)} job(s) exceed the {limit} reference slots on "
              f"{args.model}.", file=sys.stderr)
        return 1

    print(f"phase={args.phase} model={args.model} aspect={aspect} "
          f"styles={','.join(styles)} variants={n}")
    print(f"planned paid calls: {len(jobs)}")
    if args.dry_run:
        first = jobs[0]
        print(f"\nrefs/call: {len(first[2])}   banned-term scan: PASSED")
        print(f"\nsample output path: {first[0].relative_to(ROOT)}")
        print(f"\n--- sample prompt ---\n{first[1]}")
        print("\ndry run; nothing written, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("ERROR: GEMINI_API_KEY missing from .env", file=sys.stderr)
        return 1

    budget = gemini_image.load_budget()
    made, skipped, failed, failures = 0, 0, 0, []
    for out_path, prompt, ref_paths, asp in jobs:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not args.force:
            print(f"  skip (exists): {out_path.relative_to(ROOT)}")
            skipped += 1
            continue
        missing = [p for p in ref_paths if not p.exists()]
        if missing:
            msg = f"missing reference(s): {[str(m) for m in missing]}"
            print(f"  FAILED {out_path.name}: {msg}", file=sys.stderr)
            failures.append((out_path.name, "MissingReference", msg))
            failed += 1
            continue
        refs = [(p.read_bytes(), "image/png") for p in ref_paths]
        print(f"  {out_path.name} ...")
        total = gemini_image.bump_budget(budget, "gemini")
        if total > args.daily_warn:
            print(f"  ::warning:: image-API daily tally {total} -- past the "
                  f"{args.daily_warn} threshold.")
        try:
            img = gemini_image.generate(key, args.model, refs, prompt, asp)
        except Exception as e:
            print(f"  FAILED {out_path.name}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            failures.append((out_path.name, type(e).__name__, str(e)[:200]))
            failed += 1
            continue
        out_path.write_bytes(img)
        made += 1
        print(f"  wrote {out_path.relative_to(ROOT)} ({len(img)//1024} KB)")

    req = budget["requests"]
    print(f"\ndone: {made} generated, {skipped} skipped, {failed} failed.")
    if failures:
        print("failures by error class:")
        for name, cls, msg in failures:
            print(f"  {name}: {cls}: {msg}")
    print(f"Today (UTC): gemini {req.get('gemini', 0)}, "
          f"openai {req.get('openai', 0)}  (warn at {args.daily_warn})")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
