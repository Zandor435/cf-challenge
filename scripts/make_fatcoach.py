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
    "generically handsome, DO NOT change his beard, DO NOT give him hair he "
    "does not have."
)

BUILD_CHANGE = (
    " DELIBERATELY CHANGED, and this is the point of the picture: his BODY. "
    "He is now a large, heavy-set, big-bellied man -- a genuinely overweight "
    "career football coach carrying real weight in the belly, chest, neck and "
    "jowls, with the coaching shirt pulled tight over the stomach. Make the "
    "body substantially heavier than the source photograph. Keep the face "
    "recognizably his, but let the neck and jaw carry the weight the rest of "
    "him does, so the head belongs to the body and does not look pasted on."
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
EXCLUSION = (
    " ABSOLUTELY FORBIDDEN anywhere in this picture: {names}. None of these "
    "may appear on his apparel, on a patch, on the credential, on the "
    "play-call sheet, on the field, in the crowd, or on any signage or "
    "backdrop. Ignore any suggestion of them coming from the reference image "
    "or from earlier attempts, and build the scene from the named team's own "
    "colors and marks instead."
)


def build_prompt(color_word, hexc, patch, exclude=None):
    text = SCENE.format(color=color_word) + f" The team color is {hexc}."
    text += IDENTITY_LOCK + BUILD_CHANGE
    if patch:
        # The one mark that IS wanted. Named explicitly and placed, because a
        # vague "add a patch" lands as noise on the chest or not at all.
        text += (f" ON HIS LEFT CHEST, over the heart, he wears {patch} -- a "
                 f"single embroidered patch, clearly legible, sitting flat on "
                 f"the fabric like real team-issued embroidery. It is the ONLY "
                 f"lettering or insignia anywhere in the picture.")
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
    ap.add_argument("--exclude", default=None,
                    help="comma-joined things that must NOT appear, e.g. "
                         '"JMU, James Madison, purple". Opt-in; see EXCLUSION')
    ap.add_argument("--n", type=int, default=2, help="variants")
    ap.add_argument("--aspect", default=DEFAULT_ASPECT)
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

    if a.hex:
        hexc, word = a.hex.lower(), color_name(a.hex)
    elif a.team:
        colors = team_colors()
        if a.team not in colors:
            raise SystemExit(f"ERROR: team {a.team!r} not in teams_canonical.json")
        hexc = colors[a.team]["color"]
        word = color_name(hexc)
    else:
        ap.error("one of --team or --hex is required")

    prompt = build_prompt(word, hexc, a.patch, a.exclude)
    out_dir = Path(a.out) if a.out else ROOT / "output" / "personas" / a.group
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    print(f"group={a.group} manager={a.manager} color={word} ({hexc}) "
          f"aspect={a.aspect}")
    with Image.open(src) as im:
        print(f"source {src.name} {im.size[0]}x{im.size[1]}")
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
        dest = out_dir / f"{a.group}_{a.manager}_fat_{i:02d}.png"
        if dest.exists() and not a.force:
            print(f"  skip (exists): {dest.name}")
            skipped += 1
            continue
        print(f"  variant {i} ...")
        img = gemini_image.generate(key, a.model, ref, prompt, a.aspect,
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
