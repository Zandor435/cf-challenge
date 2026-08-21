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
import base64
import json
import re
import os
import sys
from pathlib import Path

import requests

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gemini_image  # noqa: E402
from recolor_personas import color_name, team_colors  # noqa: E402
from generate_owner_images import _post_with_retries  # noqa: E402
from scene_batch import (BATCH, FULLCARD, MATCHUP_FULLCARD, MATCHUPS,  # noqa: E402
                         POSTERS, POSTER_NAMES)

ROOT = Path(__file__).resolve().parent.parent
PERSONAS = ROOT / "groups" / "panel" / "personas.json"

# Illustrated only. Anything evoking a camera, a film stock or a photograph is
# banned -- photoreal staging is what breaks likeness once a body is in motion.
STYLE_SET = {
    # comic predates the rest and is left as-is: the 50-image scene batch and
    # both poster sets were generated against this exact string.
    "comic": ("rendered as COMIC BOOK ART: bold black ink linework, heavy "
              "halftone dot shading, saturated flat color fills, strong "
              "graphic blacks"),
    # The six below are Z's own definitions, given 2026-08-18 when the style
    # vocabulary was set. painted/screenprint/woodcut were reworded to match;
    # the only prior use of "painted" was the trophy plate, already generated.
    "screenprint": ("rendered as a THREE-COLOR SCREENPRINTED POSTER: hard flat "
                    "shapes, heavy solid black, visible halftone dots, a "
                    "strictly limited ink palette, no gradients"),
    "woodcut": ("rendered as a SCRATCHBOARD ENGRAVING: carved white line on "
                "black, extreme contrast, no midtones at all, chiselled "
                "directional texture"),
    "airbrush": ("rendered as a 1980s VAN-MURAL AIRBRUSH painting: soft "
                 "glowing edges, gleaming chrome highlights, deeply saturated "
                 "gradients, that custom-van fantasy-art finish"),
    "painted": ("rendered as a GOUACHE SPORTS ILLUSTRATION: visible brushwork, "
                "warm pigment, painterly edges, the look of a hand-painted "
                "sports magazine plate"),
    "risograph": ("rendered as a TWO-COLOR RISOGRAPH PRINT: deliberate "
                  "misregistration between the two ink layers, heavy grain, "
                  "blotchy offset ink texture, flat spot colors"),
    "noir": ("rendered as HEAVY CHIAROSCURO INK: most of the face buried in "
             "solid black shadow, one hard blade of light cutting across the "
             "eyes, stark and high-contrast"),
}

# The six that make up a half-card plate set. comic is deliberately absent --
# it is the scene/poster style, not a plate style.
HALFCARD_STYLES = ["screenprint", "woodcut", "airbrush", "painted",
                   "risograph", "noir"]

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

STRICT_NO_TEXT = (
    " CRITICAL: the image must contain ZERO text. No letters, no words, no "
    "numbers, no symbols, no signage copy and no watermark anywhere in the "
    "frame. Any lettering at all makes this image unusable."
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


def recolor_dir(mid):
    """The directory a manager's kept recolors live in. Single source of truth
    for the path, so the resolver and its diagnosis can never disagree."""
    return ROOT / "output" / "personas" / "recolor" / mid


def resolve_reference(mid):
    """Return the kept recolor to use as this manager's character reference."""
    have = {f.stem.split("_")[1]: f
            for f in recolor_dir(mid).glob(f"{mid}_*_gemini.png")}
    for slug in REF_PREFERENCE:
        if slug in have:
            return have[slug], slug
    return None, None


def reference_failure(mid):
    """Say why resolve_reference() came up empty -- accurately.

    Four causes look identical to the caller and need four different fixes, so
    naming the wrong one sends the next person to the wrong place. This used to
    assert "every variant appears to be archived" unconditionally; that is only
    one of the four, and it was the wrong one the day the live directory went
    missing. Look at disk and report what is actually there.
    """
    d = recolor_dir(mid)
    archive = ROOT / "output" / "archive" / "recolor" / mid
    archived = (sorted(f.name for f in archive.glob(f"{mid}_*_gemini.png"))
                if archive.is_dir() else [])

    if not d.exists():
        why = f"the live recolor directory {d} DOES NOT EXIST"
    elif not any(d.glob(f"{mid}_*_gemini.png")):
        why = f"the live recolor directory {d} EXISTS BUT IS EMPTY"
    else:
        # Files are present; none of them carries a garment slug the
        # preference ladder knows. Name them -- the fix is a rename or a new
        # ladder entry, not a restore.
        found = sorted(f.name for f in d.glob(f"{mid}_*_gemini.png"))
        return (f"ERROR: no usable recolor for {mid!r}: {d} holds "
                f"{', '.join(found)}, but none matches the preference ladder "
                f"{REF_PREFERENCE}. Rename one or extend REF_PREFERENCE.")

    if archived:
        return (f"ERROR: no kept recolor on disk for {mid!r} — {why}. "
                f"{len(archived)} variant(s) are archived under {archive} "
                f"({', '.join(archived)}); restore one, or pass a different "
                f"--leads set.")
    return (f"ERROR: no kept recolor on disk for {mid!r} — {why}, and nothing "
            f"is archived under {archive} either. Regenerate with "
            f"scripts/recolor_personas.py, or pass a different --leads set.")


def build_batch_prompt(entry, style, personas, colors, plate):
    """Assemble one named-composition prompt and its ordered reference list."""
    who = entry.get("who") or []
    text = entry["text"].rstrip().rstrip(".") + ". " + STYLE_SET[style].capitalize() + "."
    if who:
        wardrobe = []
        for i, mid in enumerate(who, start=1):
            team = personas[mid]["team"]
            hexc = colors[team]["color"]
            wardrobe.append("the man from reference image #" + str(i) + " wears "
                            + team + " " + color_name(hexc) + " (" + hexc + ")")
        text += " Team colors: " + "; ".join(wardrobe) + "."
        text += (" Each man must be clearly recognizable as the specific "
                 "individual in his reference image -- same face, same "
                 "features, same hair. Do not blend or merge them together, "
                 "and do not substitute generic people.")
        cues = "; ".join(c for c in
                         (personas[m].get("silhouette_cue") for m in who) if c)
        if cues:
            text += " Their distinguishing cues: " + cues + "."
        text += BUILD_LOCK
    text += ILLUSTRATION_LOCK
    # milkcarton needs the single word MISSING; a blanket no-text clause
    # would fight the composition it is supposed to produce.
    if entry.get("strict_no_text"):
        text += STRICT_NO_TEXT
    elif not entry.get("allow_text"):
        text += NO_TEXT

    refs = []
    for mid in who:
        rp, _slug = resolve_reference(mid)
        if rp is None:
            raise SystemExit(reference_failure(mid)
                             + f" (needed by {entry['slug']}.)")
        refs.append(rp)
    if entry.get("trophy"):
        refs.append(plate)
    return text, refs


def build_poster_prompt(entry, style, personas, colors, plate):
    """A fight-poster prompt. Unlike scenes, lettering here is REQUIRED --
    surnames, a sanctioning wordmark and a promo line are the furniture. What
    must NOT appear is numerals of any kind, and anything at all inside the
    lower band: the site overlays live numbers into that strip in HTML, and
    baked stats are stale by Saturday."""
    who = entry["who"]
    names = [POSTER_NAMES[m] for m in who]

    # Textless bills keep the FORM of fight-poster furniture and drop its
    # content: ornament where lettering would go, so the layout still reads as
    # a bill at a glance. Garment lettering baked into the source portraits is
    # explicitly allowed through -- it reads as clothing, not as furniture.
    ORNAMENT = (
        " Where a fight bill would carry lettering, put ORNAMENT and nothing "
        "else: laurel scrollwork, ribbon banners whose interiors are "
        "completely empty, art-deco rules and bars, radiating starburst rays, "
        "and decorative frames enclosing empty space. The layout must read "
        "instantly as a fight bill while containing not one readable "
        "character.")
    NO_FURNITURE_TEXT = (
        " CRITICAL -- NO TEXT: no names, no surnames, no sanctioning-body "
        "wordmark, no promotional line, no numbers, no dates, no odds, no "
        "letters and no numerals anywhere in the poster furniture, the "
        "background, the frames, the banners or the lower band. The ONLY "
        "lettering permitted anywhere is whatever is already printed on the "
        "men's own clothing in the reference images. Every banner and frame "
        "must be left blank.")

    if entry.get("fullcard"):
        head = ("A gaudy 1980s closed-circuit boxing FULL CARD bill poster, "
                + STYLE_SET[style] + ". All FOUR men appear as the fight card: "
                "the two from reference images #1 and #2 large at the top as "
                "the main event, squared off in half-profile facing each other "
                "across the vertical centre; the two from reference images #3 "
                "and #4 smaller beneath them as the undercard, also facing off. "
                "Chins raised, hard rim light along every profile edge, deep "
                "shadow behind. A bold starburst radiates from the centre of "
                "the main event. Every man is fully lit and fully detailed.")
        lettering = (" LETTERING: bake in the poster furniture. The names "
                     + ", ".join(names[:2]) + " in heavy all-capital slab "
                     "lettering flanking the main event, and " 
                     + ", ".join(names[2:]) + " in smaller all-capital slab "
                     "lettering flanking the undercard. A sanctioning-body "
                     "wordmark across the very top. The promotional line \""
                     + entry.get("promo", "") + "\" in bold display lettering.")
    else:
        head = ("A gaudy 1980s closed-circuit boxing title-fight poster, "
                + STYLE_SET[style] + ". TWO men in half-profile facing each "
                "other across the vertical centre of the poster, head and "
                "shoulders, large, filling the upper two thirds of the frame. "
                "The man from reference image #1 is on the LEFT and the man "
                "from reference image #2 is on the RIGHT. Both chins are "
                "raised. Hard rim light traces the edge of each profile with "
                "deep shadow behind. A bold starburst radiates from the centre "
                "point between the two heads. Both men are fully lit and fully "
                "detailed -- neither is a background figure.")
        lettering = (" LETTERING: bake in the poster furniture. The name "
                     + names[0] + " in heavy all-capital slab lettering on the "
                     "LEFT side and " + names[1] + " in the same treatment on "
                     "the RIGHT side. A sanctioning-body wordmark across the "
                     "very top. The promotional line \"" + entry.get("promo", "")
                     + "\" in bold display lettering.")

    trophy = (" Between the figures at chest height, smaller than any of their "
              "heads, stands the faceted crystal championship trophy from the "
              "final reference image -- reproduce its design faithfully.")

    split = ""
    if entry.get("split_ground"):
        split = (" These two men wear very similar gold; give each his own "
                 "distinctly different dark background field on his own half "
                 "of the poster, so the two figures never merge into one.")

    wardrobe = []
    for i, mid in enumerate(who, start=1):
        team = personas[mid]["team"]
        hexc = colors[team]["color"]
        wardrobe.append("the man from reference image #" + str(i) + " wears "
                        + team + " " + color_name(hexc) + " (" + hexc + ")")
    colors_txt = " Team colors: " + "; ".join(wardrobe) + "."

    likeness = (" Each man must be clearly recognizable as the specific "
                "individual in his reference image -- same face, same "
                "features, same hair. Do not blend or merge them together and "
                "do not substitute generic people.")
    cues = "; ".join(c for c in
                     (personas[m].get("silhouette_cue") for m in who) if c)
    if cues:
        likeness += " Their distinguishing cues: " + cues + "."

    numbers = (" Bake NO numbers anywhere in the image: no records, no scores, "
               "no dates, no odds, no numerals of any kind.")
    band = (" Across the LOWER THIRD of the poster leave a completely clean, "
            "flat, solid dark horizontal band spanning the full width. That "
            "band must contain nothing at all -- no lettering, no numbers, no "
            "ornament, no texture, no pattern, no imagery. It must stay "
            "entirely empty.")

    if entry.get("textless"):
        text = (head + trophy + split + colors_txt + likeness + BUILD_LOCK
                + ORNAMENT + band + NO_FURNITURE_TEXT + ILLUSTRATION_LOCK)
    else:
        text = (head + trophy + split + colors_txt + likeness + BUILD_LOCK
                + lettering + numbers + band + ILLUSTRATION_LOCK)

    refs = []
    for mid in who:
        rp, _slug = resolve_reference(mid)
        if rp is None:
            raise SystemExit(reference_failure(mid))
        refs.append(rp)
    refs.append(plate)
    return text, refs


# Half-card plates are composited side by side later, so FRAMING consistency
# across a style set outranks every other quality here. These clauses are
# identical on all 48 calls; only the man, the facing and the style change.
HALF_FRAMING = (
    " FRAMING, identical on every plate: head and shoulders only, the top of "
    "his head close to the upper edge of the frame, cropped off at mid-chest "
    "at the bottom. His head fills roughly the upper half of the frame height. "
    "His eye line sits a little above the vertical centre of the frame."
)
HALF_EMPTY = (
    " Nothing else appears in the frame at all: no trophy, no starburst, no "
    "other person, no crowd, no stadium, no field, no ornament, no border, no "
    "frame, no logos, no props. The background is a completely flat, uniform, "
    "unlit near-black field -- no gradient, no texture, no pattern, no "
    "vignette, no detail whatsoever -- so it can be keyed out cleanly."
)
HALF_NO_TEXT = (
    " No text of any kind anywhere: no names, no numbers, no wordmarks, no "
    "lettering. The ONLY exception is lettering already printed on the man's "
    "own clothing in the reference image, which is part of the garment."
)
FACING = {
    # facing = the direction he LOOKS. right-facing belongs on the LEFT of a
    # composite, which is why the lead room goes the same way as the gaze.
    "right": (" He is turned in half-profile facing and looking toward the "
              "RIGHT edge of the frame, never at the viewer. Place him "
              "slightly left of centre so there is lead room ahead of him on "
              "the right."),
    "left": (" He is turned in half-profile facing and looking toward the "
             "LEFT edge of the frame, never at the viewer. Place him slightly "
             "right of centre so there is lead room ahead of him on the "
             "left."),
}


OPENAI_URL = "https://api.openai.com/v1/images/edits"

# gpt-image only accepts this fixed size set, so an aspect string is mapped to
# the nearest one it will actually take. 3:4 lands on the portrait size -- the
# plates are eye-line normalised by the compositor afterwards, so an exact
# ratio matters far less than every plate in a set sharing ONE size.
OPENAI_SIZES = {"1:1": "1024x1024", "3:4": "1024x1536", "2:3": "1024x1536",
                "4:5": "1024x1536", "9:16": "1024x1536", "4:3": "1536x1024",
                "3:2": "1536x1024", "16:9": "1536x1024", "21:9": "1536x1024"}


def gen_openai(api_key, model, refs, prompt, aspect, quality="high"):
    """Return PNG bytes for one image from /v1/images/edits.

    Mirrors gemini_image.generate()'s contract -- same argument order, same
    return -- so the job loop can pick a provider and change nothing else.
    refs is the SAME ordered (bytes, mime) list; every entry is sent as a
    repeated image[] part, which keeps "reference image #N" pointing at the
    same picture it points at on the Gemini path.
    """
    files = [("image[]", (f"ref{i}.png", blob, mime))
             for i, (blob, mime) in enumerate(refs, start=1)]
    form = {"model": model, "prompt": prompt, "n": "1",
            "size": OPENAI_SIZES.get(aspect, "1024x1536"), "quality": quality}
    resp = _post_with_retries(lambda: requests.post(
        OPENAI_URL, data=form, files=files,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=420))
    data = resp.json()
    try:
        return base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError):
        raise RuntimeError(f"No image in response: {json.dumps(data)[:400]}")


def build_halfcard_prompt(mid, facing, style, personas, colors):
    team = personas[mid]["team"]
    hexc = colors[team]["color"]
    cue = personas[mid].get("silhouette_cue")
    text = ("A single man alone, head and shoulders, in the pose a boxer takes "
            "for a title-fight promotional plate: chin slightly raised, "
            "confident, unimpressed, faintly menacing. " + STYLE_SET[style]
            + ". Hard rim light traces the edge of his profile with deep "
            "shadow falling on the far side of his face.")
    text += FACING[facing]
    text += HALF_FRAMING
    text += (" He wears " + team + " " + color_name(hexc) + " (" + hexc + ").")
    text += (" He must be clearly recognizable as the specific individual in "
             "the reference image -- same face, same features, same hair. Do "
             "not substitute a generic person.")
    if cue:
        text += " He is identifiable by " + cue + "."
    text += BUILD_LOCK + HALF_EMPTY + HALF_NO_TEXT + ILLUSTRATION_LOCK

    rp, _slug = resolve_reference(mid)
    if rp is None:
        raise SystemExit(reference_failure(mid))
    return text, [rp]


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
                    choices=["trophy_plate", "batch", "posters", "matchups",
                             "halfcards"] + list(SETUPS))
    ap.add_argument("--styles", default=None,
                    help=f"comma-separated ({', '.join(STYLE_SET)})")
    ap.add_argument("--leads", default="blaine,chris,jonathan,zach")
    ap.add_argument("--n", type=int, default=2, help="variants (minimum 2)")
    ap.add_argument("--aspect", default=None,
                    help="default 3:4 for trophy, 16:9 for scenes")
    ap.add_argument("--model", default=None,
                    help=f"default {gemini_image.DEFAULT_MODEL} (gemini) "
                         f"/ gpt-image-2 (openai)")
    ap.add_argument("--provider", default="gemini", choices=["gemini", "openai"],
                    help="image backend. openai is the failover when the "
                         "Gemini spend cap is hit.")
    ap.add_argument("--quality", default="high",
                    choices=["low", "medium", "high"], help="openai only")
    ap.add_argument("--trophy-plate", default=None,
                    help="path to the canonical trophy plate (trophy setup only)")
    ap.add_argument("--huddle-angle", default="up", choices=list(HUDDLE_ANGLES))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble and validate prompts, write NOTHING, bill NOTHING")
    args = ap.parse_args()
    if not args.model:
        args.model = (gemini_image.DEFAULT_MODEL if args.provider == "gemini"
                      else "gpt-image-2")

    # None means "caller did not choose": scenes fall back to painted,
    # half-cards fall back to the full six-style plate set.
    styles = ([s.strip() for s in args.styles.split(",") if s.strip()]
              if args.styles else ["painted"])
    unknown = [s for s in styles if s not in STYLE_SET]
    if unknown:
        print(f"ERROR: unknown style(s) {unknown}. Available: "
              f"{', '.join(STYLE_SET)}", file=sys.stderr)
        return 1
    # The 2-variant floor was a rule from the scene-batch brief, not a
    # property of the generator. Half-card plates are 1 variant each, so
    # the floor is 1 and the DEFAULT stays 2.
    n = max(1, args.n)

    personas = load_personas()
    colors = team_colors()

    jobs = []   # (out_path, prompt, ref_paths, aspect)
    if args.phase == "halfcards":
        aspect = args.aspect or "3:4"
        plate_styles = styles if args.styles else HALFCARD_STYLES
        bad = [s for s in plate_styles if s not in STYLE_SET]
        if bad:
            print("ERROR: unknown style(s) " + str(bad), file=sys.stderr)
            return 1
        leads = [l.strip() for l in args.leads.split(",") if l.strip()]
        for style in plate_styles:
            for mid in leads:
                for facing in ("right", "left"):
                    prompt, refs = build_halfcard_prompt(
                        mid, facing, style, personas, colors)
                    check_prompt(prompt, "halfcards/" + mid + "/" + facing
                                 + "/" + style)
                    for i in range(1, n + 1):
                        jobs.append((
                            ROOT / "output" / "halfcards" / "panel" /
                            ("panel_half_" + mid + "_" + facing + "_" + style
                             + "_" + format(i, "02d") + ".png"),
                            prompt, refs, aspect))
    elif args.phase in ("posters", "matchups"):
        aspect = args.aspect or "3:4"
        style = styles[0]
        if not args.trophy_plate:
            print("ERROR: posters need --trophy-plate PATH.", file=sys.stderr)
            return 1
        plate = Path(args.trophy_plate)
        if not plate.is_absolute():
            plate = ROOT / args.trophy_plate
        if not plate.exists():
            print("ERROR: trophy plate not found: " + str(plate), file=sys.stderr)
            return 1
        entries = (list(MATCHUPS) + [MATCHUP_FULLCARD]
                   if args.phase == "matchups" else list(POSTERS) + [FULLCARD])
        for e in entries:
            prompt, refs = build_poster_prompt(e, style, personas, colors, plate)
            check_prompt(prompt, args.phase + "/" + e["slug"])
            for i in range(1, n + 1):
                jobs.append((ROOT / "output" / "posters" / "panel" /
                             ("panel_" + e["slug"] + "_" + format(i, "02d") + ".png"),
                             prompt, refs, aspect))
    elif args.phase == "batch":
        aspect = args.aspect or "16:9"
        style = styles[0]
        plate = None
        if args.trophy_plate:
            plate = Path(args.trophy_plate)
            if not plate.is_absolute():
                plate = ROOT / args.trophy_plate
            if not plate.exists():
                print("ERROR: trophy plate not found: " + str(plate), file=sys.stderr)
                return 1
        # A rotating entry expands to one composition per lead, the lead first
        # in the reference order; everything else is a single fixed composition.
        expanded = []
        for e in BATCH:
            if e.get("leads"):
                for lead in e["leads"]:
                    expanded.append({**e, "slug": e["slug"] + "_" + lead,
                                     "who": [lead]})
            else:
                expanded.append(e)
        for e in expanded:
            if e.get("trophy") and plate is None:
                print("ERROR: " + e["slug"] + " needs --trophy-plate PATH.",
                      file=sys.stderr)
                return 1
            prompt, refs = build_batch_prompt(e, style, personas, colors, plate)
            check_prompt(prompt, "batch/" + e["slug"])
            for i in range(1, n + 1):
                jobs.append((ROOT / "output" / "scenes" / "panel" /
                             ("panel_" + e["slug"] + "_" + format(i, "02d") + ".png"),
                             prompt, refs, aspect))
    elif args.phase == "trophy_plate":
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
                        print(reference_failure(m), file=sys.stderr)
                        return 1
                    refs.append(rp)
                if plate:
                    refs = refs + [plate]
                for i in range(1, n + 1):
                    jobs.append((ROOT / "output" / "scenes" / "panel" /
                                 f"panel_{args.phase}_{lead}_{style}_{i:02d}.png",
                                 prompt, refs, aspect))

    # gpt-image takes an unbounded image[] list, so the slot ceiling is a
    # Gemini-model property and is only enforced on that path.
    limit = gemini_image.ref_limit(args.model) if args.provider == "gemini" else 99
    over = [j for j in jobs if len(j[2]) > limit]
    if over:
        print(f"ERROR: {len(over)} job(s) exceed the {limit} reference slots on "
              f"{args.model}.", file=sys.stderr)
        return 1

    print(f"phase={args.phase} provider={args.provider} model={args.model} "
          f"aspect={aspect} styles={','.join(styles)} variants={n}")
    print(f"planned paid calls: {len(jobs)}")
    if args.dry_run:
        first = jobs[0]
        print(f"\nrefs/call: {len(first[2])}   banned-term scan: PASSED")
        print(f"\nsample output path: {first[0].relative_to(ROOT)}")
        print(f"\n--- sample prompt ---\n{first[1]}")
        print("\ndry run; nothing written, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    env_name = "GEMINI_API_KEY" if args.provider == "gemini" else "OPENAI_API_KEY"
    key = os.environ.get(env_name, "").strip()
    if not key:
        print(f"ERROR: {env_name} missing from .env", file=sys.stderr)
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
        total = gemini_image.bump_budget(budget, args.provider)
        if total > args.daily_warn:
            print(f"  ::warning:: image-API daily tally {total} -- past the "
                  f"{args.daily_warn} threshold.")
        try:
            if args.provider == "openai":
                img = gen_openai(key, args.model, refs, prompt, asp,
                                 args.quality)
            else:
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
