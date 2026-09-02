#!/usr/bin/env python3
"""Turn one real photograph into that person's heavy-set college-coach portrait.

    python scripts/make_fatcoach.py --group browns --manager mark \
        --source "assets/source_photos/mark.jpg" --team Indiana \
        --patch "a returning national champions patch" [--preview] [--n 2]

WHY THIS EXISTS. Twenty of the portraits this site publishes are the "fat
coach" treatment -- Fat Bryan, Fat Matt H, Fat Brian, and the rest -- and until
now NOTHING in the repo produced them. `grep -rn "fat" --include=*.py scripts/`
returns a filename convention and no generator. They were made by hand in a web
UI and dropped in a folder, which means every new manager was a manual errand
and the look drifted with whoever was doing it.

WHY NOT generate_owner_images.py. That script restyles a person into coach
ARCHETYPES and its BASE_RULES conflict with this job three ways: it demands a
"painted/illustrated trading-card finish" (this treatment is photoreal), it
forbids all lettering and logos (this one may need a patch), and it locks
"same build" (this one deliberately changes the build). Bending it would have
broken the twelve archetypes it already serves.

THE LOCK IS INVERTED, and that is the whole design. recolor_personas.py freezes
the body and changes the garment; this freezes the FACE and changes the body.
Those are opposite instructions, so they cannot share a lock -- stating both
halves explicitly, positively then as a prohibition, is what keeps the result a
picture of the right person.

OUTPUT  output/personas/<group>/<group>_<manager>_fat_<nn>.png
        which is the path art_gaps.py reads and prepare_portraits.py,
        build_profile_heroes.py and build_avatars.py all derive from. One
        source image feeds three site slots; nothing downstream needs an edit.

Playbook: rule 2 retries via gemini_image, rule 6 budget tally, rule 7
--skip-if-exists by default. --preview prints the prompt and bills nothing.
The rule-6 tally counts HTTP ATTEMPTS, never images -- the bump is inside
_post_with_retries, so a retried image bills more than once.
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_owner_images import load_budget  # noqa: E402
from recolor_personas import color_name, team_colors  # noqa: E402
import gemini_image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Portrait orientation, matching the twenty already published (1122x1402 and
# 1024x1536 are both ~0.75-0.80). The source photo's own aspect is deliberately
# NOT used: a square headshot would crop the body this treatment is about.
DEFAULT_ASPECT = "4:5"

# What the picture must BE. Photoreal, because every published example is a
# photoreal composite and an illustrated one would not sit beside them.
SCENE = (
    "Turn this into a PHOTOREALISTIC photograph of this same man as a "
    "big-time college football HEAD COACH on the sideline: a genuine press "
    "photograph, not an illustration, not a painting, not a render. He wears "
    "team-issued {color} coaching apparel -- a {color} coaching polo or "
    "quarter-zip with a coaching headset over his ears, credential lanyard, "
    "a laminated play-call sheet in one hand. Behind him is a packed stadium "
    "on game day, shot with a long lens so the crowd falls into soft bokeh. "
    "Natural daylight, real skin texture, real fabric texture."
)

# The inverted lock. FACE frozen, BODY changed -- stated positively first and
# then as prohibitions, which image models honour far more reliably.
IDENTITY_LOCK = (
    " ABSOLUTELY UNCHANGED: his face and head. Same facial features, same "
    "bone structure, same eyes, same nose, same mouth, same beard and exactly "
    "the same beard shape and length, same hairline and the same amount of "
    "hair, same skin tone, same complexion, same age. He must be instantly "
    "recognizable to anyone who knows him. DO NOT swap his face, DO NOT "
    "morph him toward any real coach or celebrity, DO NOT make him "
    "generically handsome, DO NOT change his beard, DO NOT add facial hair "
    "he does not have, DO NOT grow stubble into a full beard, DO NOT grey his "
    "hair or beard, DO NOT give him hair he does not have."
)

BUILD_CHANGE = (
    " DELIBERATELY CHANGED, and this is the point of the picture: his BODY. "
    "He is now a large, heavy-set, big-bellied man -- a genuinely overweight "
    "career football coach carrying real weight in the belly, chest, neck and "
    "jowls, with the coaching shirt pulled tight over the stomach. Make the "
    "body substantially heavier than the source photograph. Keep the face "
    "recognizably his, but let the neck and jaw carry the weight the rest of "
    "him does, so the head belongs to the body and does not look pasted on. "
    "The shirt stays fully on and fully covering him: it never rides up or "
    "comes untucked, and NO bare skin of his stomach, midriff, chest or back "
    "is visible anywhere. Making him heavier is the ONLY change -- do not age "
    "him, do not grey him, and do not add, lengthen or thicken his facial "
    "hair, which stays exactly as it is in the reference image."
)

NO_FURNITURE = (
    " No captions, no scoreboard text, no watermarks, no signage and no "
    "brand marks anywhere in the frame."
)

# A NAMED negative. --exclude exists because a generation that has already
# produced the wrong school once will produce it again: the browns hauck batch
# came back in James Madison purple and gold, and one variant put a JMU
# wordmark on a Kentucky-blue polo inside a Kentucky stadium. Naming the
# offender is far more reliable than trusting the positive prompt to crowd it
# out, and it is opt-in, so no existing call site changes behaviour.
# Lifts the "only insignia" sentence so the named team may dress the whole
# scene. The spelling clause is not optional decoration: every garbled render
# in the hauck batch failed on LETTERING, not on color or composition, and
# more permitted text means more surface for that failure.
TEAM_MARKS = (
    " The team's OWN wordmark and logo may also appear where they really "
    "would: on the credential, on the play-call sheet, on sideline signage, "
    "and on the stadium backdrop behind him. Every mark in the frame must "
    "belong to that one team. EVERY PIECE OF LETTERING MUST BE SPELLED "
    "CORRECTLY and be cleanly legible -- no invented words, no garbled or "
    "approximated team name, no dummy text."
)

EXCLUSION = (
    " ABSOLUTELY FORBIDDEN anywhere in this picture: {names}. None of these "
    "may appear on his apparel, on a patch, on the credential, on the "
    "play-call sheet, on the field, in the crowd, or on any signage or "
    "backdrop. Ignore any suggestion of them coming from the reference image "
    "or from earlier attempts, and build the scene from the named team's own "
    "colors and marks instead."
)


# --- scene-preserving edit mode (--keep-scene) --------------------------------
# The generation path above BUILDS a sideline scene from a real photo. Once a
# manager's portrait is approved and published, the scene is the asset: CEC's
# zach is a Wake Forest presser, not a sideline, and regenerating it from
# SCENE would throw away the backdrop, the podium nameplate and the shirt
# script that make it his. So --keep-scene swaps SCENE for a pin -- everything
# in the reference frame is frozen and only the build moves. Same inverted
# lock, one degree tighter: recolor_personas.py freezes the body and changes
# the garment, this freezes the garment AND the room and changes the body.
SCENE_KEEP = (
    "This is a TARGETED EDIT of the photograph provided, not a new picture. "
    "Return the same photograph with one thing changed. ABSOLUTELY UNCHANGED: "
    "the setting and the room, the backdrop and every logo, wordmark and "
    "graphic printed on it, the podium and its nameplate, the microphones, "
    "the camera angle, the crop and framing, the lighting direction and mood, "
    "his garment and its exact color, and EVERY piece of text and lettering "
    "already in the frame -- reproduce all existing words exactly as they "
    "appear, same wording, same spelling, same fonts, same positions. It stays "
    "a photorealistic press photograph with real skin and fabric texture. "
    "DO NOT restyle or redraw the picture, DO NOT change the background, "
    "DO NOT re-letter or reword the nameplate, DO NOT add or remove signage."
)

# Distinct from BUILD_CHANGE because the input is different. BUILD_CHANGE
# reads a real photo of a normal-sized man; this reads a portrait that is
# ALREADY the heavy-set treatment, so "make him heavy-set" is a no-op the
# model happily satisfies by returning the source. The comparative -- heavier
# THAN HE ALREADY IS IN THIS IMAGE -- is what actually moves it.
BUILD_HEAVIER = (
    " DELIBERATELY CHANGED, and this is the only reason for the edit: his "
    "BODY. Make him CLEARLY AND SUBSTANTIALLY HEAVIER THAN HE ALREADY IS IN "
    "THIS PHOTOGRAPH -- considerably more weight in the belly, chest, "
    "shoulders, neck and jowls, a fuller double chin, rounder and heavier "
    "cheeks, thicker arms and forearms, and a wider overall frame that fills "
    "more of the podium than it does now. The coaching shirt is pulled "
    "visibly tighter across the stomach and strains at the zip. Keep the face "
    "recognizably his and let the neck and jaw carry the weight the rest of "
    "him does, so the head belongs to the body and does not look pasted on."
)

# Props on the podium. Unbranded is not fussiness: every garbled render in the
# hauck batch failed on LETTERING, and snack packaging is nothing but
# lettering. Denying it the surface is cheaper than policing the spelling.
PODIUM_PROPS = (
    " ADD to the flat top of the podium, spread along its front edge to the "
    "LEFT and RIGHT of the nameplate and BELOW the microphones: {props}. They "
    "rest on the podium surface as real objects, shot in the same light and "
    "the same photographic style as the rest of the frame, slightly "
    "overlapping each other. They are props at the edges of the frame, not "
    "the subject of it. EVERY microphone already in the picture stays exactly "
    "where it is and stays fully visible, and the podium nameplate stays "
    "COMPLETELY unobstructed and fully readable end to end, both of its "
    "lines of text -- nothing may overlap, cover or crop a single letter of "
    "it. The snacks sit on the podium's flat top surface BEHIND the "
    "nameplate, so the nameplate stands in front of them and may itself hide "
    "the lower part of any of them; that is correct and expected. It is "
    "always the nameplate in front and the snacks behind, never the reverse. "
    "Nothing may cover his "
    "face, his chest or his hands. Every wrapper, bag, cup, box and napkin is "
    "BLANK: plain solid-colored packaging with NO printing whatsoever -- no "
    "lettering, no words, no numbers, no logos, no brand names, no invented "
    "or garbled text, no decorative graphics anywhere on any of it."
)


def build_prompt(color_word, hexc, patch, exclude=None, team_marks=False,
                 keep_scene=False, props=None):
    if keep_scene:
        text = SCENE_KEEP + IDENTITY_LOCK + BUILD_HEAVIER
        if props:
            text += PODIUM_PROPS.format(props=props)
        if exclude:
            text += EXCLUSION.format(names=exclude)
        return text

    text = SCENE.format(color=color_word) + f" The team color is {hexc}."
    text += IDENTITY_LOCK + BUILD_CHANGE
    if patch:
        # The one mark that IS wanted. Named explicitly and placed, because a
        # vague "add a patch" lands as noise on the chest or not at all.
        text += (f" ON HIS LEFT CHEST, over the heart, he wears {patch} -- a "
                 f"single embroidered patch, clearly legible, sitting flat on "
                 f"the fabric like real team-issued embroidery.")
        # The default keeps the chest patch as the sole mark, which is what
        # stops a team name in the prompt from spraying invented signage
        # across the frame. team_marks lifts exactly that sentence -- and
        # nothing else -- for the case where the dressed set IS the point.
        text += (TEAM_MARKS if team_marks else
                 " It is the ONLY lettering or insignia anywhere in the "
                 "picture.")
    else:
        text += NO_FURNITURE
    if exclude:
        text += EXCLUSION.format(names=exclude)
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--manager", required=True, help="manager_id")
    ap.add_argument("--source", required=True, help="path to the real photo")
    ap.add_argument("--team", default=None,
                    help="canonical team name; supplies the color")
    ap.add_argument("--hex", default=None, help="literal #RRGGBB instead of --team")
    ap.add_argument("--patch", default=None,
                    help='e.g. "a returning national champions patch"')
    ap.add_argument("--team-marks", action="store_true",
                    help="let the named team's marks dress the scene "
                         "(backdrop, signage, credential), not just the chest "
                         "patch; adds a spelling guard. Default off.")
    ap.add_argument("--exclude", default=None,
                    help="comma-joined things that must NOT appear, e.g. "
                         '"JMU, James Madison, purple". Opt-in; see EXCLUSION')
    ap.add_argument("--keep-scene", action="store_true",
                    help="treat --source as an ALREADY-APPROVED portrait and "
                         "edit it in place: freeze the scene, backdrop, "
                         "garment and lettering, change only the build. "
                         "Makes --team/--hex optional and defaults --aspect "
                         "to the source's own ratio so the framing survives.")
    ap.add_argument("--props", default=None,
                    help='things to add to the podium top, e.g. "an open bag '
                         'of chips and a stack of candy bars". Unbranded by '
                         "construction; see PODIUM_PROPS.")
    ap.add_argument("--label", default="fat",
                    help="filename stem: <group>_<manager>_<label>_<nn>.png. "
                         "Change it so a new batch cannot overwrite the "
                         "published one. Default 'fat'.")
    ap.add_argument("--n", type=int, default=2, help="variants")
    ap.add_argument("--aspect", default=None,
                    help=f"default {DEFAULT_ASPECT}, or the source's own "
                         "ratio under --keep-scene")
    ap.add_argument("--model", default=gemini_image.DEFAULT_MODEL)
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--preview", action="store_true",
                    help="print the prompt, write NOTHING, bill NOTHING")
    a = ap.parse_args()

    src = Path(a.source)
    if not src.is_absolute():
        src = ROOT / a.source
    if not src.is_file():
        raise SystemExit(f"ERROR: source photo not found: {src}")

    if a.keep_scene and not (a.hex or a.team):
        # The reference image already carries the palette; SCENE_KEEP pins it.
        hexc = word = None
    elif a.hex:
        hexc, word = a.hex.lower(), color_name(a.hex)
    elif a.team:
        colors = team_colors()
        if a.team not in colors:
            raise SystemExit(f"ERROR: team {a.team!r} not in teams_canonical.json")
        hexc = colors[a.team]["color"]
        word = color_name(hexc)
    else:
        ap.error("one of --team or --hex is required")

    prompt = build_prompt(word, hexc, a.patch, a.exclude, a.team_marks,
                          keep_scene=a.keep_scene, props=a.props)
    out_dir = Path(a.out) if a.out else ROOT / "output" / "personas" / a.group
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    with Image.open(src) as im:
        size = im.size
    # An edit told the wrong ratio re-frames the composition it was supposed
    # to preserve, so --keep-scene reads the ratio off the source itself.
    aspect = a.aspect or (gemini_image.nearest_aspect(*size) if a.keep_scene
                          else DEFAULT_ASPECT)
    swatch = f"{word} ({hexc})" if word else "from reference"
    print(f"group={a.group} manager={a.manager} color={swatch} "
          f"aspect={aspect} mode={'edit' if a.keep_scene else 'generate'}")
    print(f"source {src.name} {size[0]}x{size[1]}")
    print(f"\n--- prompt ---\n{prompt}\n")
    if a.preview:
        print("preview only; nothing written, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("ERROR: GEMINI_API_KEY missing from .env")

    out_dir.mkdir(parents=True, exist_ok=True)
    ref = [(src.read_bytes(), "image/png" if src.suffix.lower() == ".png"
            else "image/jpeg")]
    budget = load_budget()
    made = skipped = 0
    for i in range(1, a.n + 1):
        dest = out_dir / f"{a.group}_{a.manager}_{a.label}_{i:02d}.png"
        if dest.exists() and not a.force:
            print(f"  skip (exists): {dest.name}")
            skipped += 1
            continue
        print(f"  variant {i} ...")
        img = gemini_image.generate(key, a.model, ref, prompt, aspect,
                                    budget=budget, daily_warn=a.daily_warn)
        dest.write_bytes(img)
        print(f"  wrote {dest.name} ({len(img) // 1024} KB)")
        made += 1
    print(f"\ndone: {made} generated, {skipped} skipped.")
    print("REVIEW THE LIKENESS before publishing. Then:")
    print(f"  python scripts/prepare_portraits.py --group {a.group} "
          f'--map "{a.manager}=<path>"')
    print(f"  python scripts/build_avatars.py --group {a.group} "
          f"--only {a.manager}")
    print("  python scripts/build_profile_heroes.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
