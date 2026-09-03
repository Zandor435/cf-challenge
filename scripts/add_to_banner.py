#!/usr/bin/env python3
"""Add ONE more subject to a banner that already exists — the EDIT path.

NOT part of the live weekly pipeline. Run by hand. Paid.

WHY THIS IS A SEPARATE SCRIPT FROM generate_banners.py
------------------------------------------------------
generate_banners.py renders a group from scratch: one character reference per
manager, all of them in one call. That path has a ceiling it states itself —
five character slots on gemini-3-pro-image — and church crossed it the day
josh_b joined, at six managers. "Split the group or composite instead" is the
error it raises, and both of those answers throw away an approved picture to
solve a roster change.

This script does the other thing. The FINISHED banner goes in as reference #1
and the new manager's poster as reference #2, and the prompt asks for the same
picture with one more man in it. Two references, so the slot cap is not in play
at any roster size, and the existing art is the model's own guide rather than
something it has to reconstruct from a prompt.

THE RISK, STATED PLAINLY. The model re-renders the WHOLE frame. Nothing here
edits pixels in place, and there is no way to ask it to. The five faces that
were already approved can come back subtly different — a changed jaw, a lost
pair of glasses, a re-posed hand. That is what --n is for: generate several,
open them side by side against the original, and keep the one where nobody but
the new man changed. If none of them hold, the fallback is the from-scratch
split-and-composite build, not a worse banner.

WHERE THE VARIANTS LAND, and why it is not the obvious directory.
build_banners.py publishes EVERY image at the flat output/banners/<group>/
level, so writing four candidates there publishes four banners into the
rotation on the next build. They go to output/banners/<group>/_review/
instead — the same parking convention church already uses for _alts/ — and the
keeper is moved up one level by hand. Nothing here writes to docs/.

Usage:
    # look at the prompt first; this bills nothing
    python scripts/add_to_banner.py --group church \
        --base output/banners/church/church_trophychase_01.png \
        --add josh_b=output/personas/church/church_josh_b_fat_01.png \
        --existing 5 --n 4 --preview

    # then drop --preview to spend
    python scripts/add_to_banner.py --group church \
        --base output/banners/church/church_trophychase_01.png \
        --add josh_b=output/personas/church/church_josh_b_fat_01.png \
        --existing 5 --n 4 \
        --wardrobe "Duke blue team gear -- a Duke blue quarter-zip -- with a
                    white clerical collar visible at the throat"

Playbook compliance (CLAUDE.md):
  - rule 2: every request goes through gemini_image.generate(), which carries
    the shared two-loop retry shell.
  - rule 4: an unresolvable path or an unknown image type stops the run and
    names the file, before anything is billed.
  - rule 6: the shared per-provider UTC-daily budget tally, counted per HTTP
    attempt inside the retry shell.
  - rule 7: --skip-if-exists is the DEFAULT; --force is required to re-bill.

Privacy: sources under output/ are gitignored. A chosen variant reaches the
site only by being moved into output/banners/<group>/ and passed through
scripts/build_banners.py, same as every other banner.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_owner_images import load_budget  # noqa: E402
import gemini_image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = gemini_image.DEFAULT_MODEL

# Reference bytes are sent with a declared mime type, and the declaration has to
# match the bytes. generate_banners.py can hardcode image/png because it only
# ever reads generator output; this script's base image may legitimately be the
# PUBLISHED webp when the master PNG is not on the machine, so the type is read
# off the suffix. An unknown suffix is fatal rather than guessed — a wrong mime
# is a 400 that costs a request to discover.
MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# ---------------------------------------------------------------- prompt ----
# Three blocks, concatenated in a fixed order, and the ORDER is load-bearing in
# the same way generate_banners.assemble() is: preserve first, because it is
# the instruction most at risk of being diluted; the single change second; the
# frame and typography guards last.
#
# References are addressed by INDEX, never by name — the model sees an ordered
# list of images and has no way to map "josh_b" onto one of them. #1 is always
# the existing banner, #2 always the new subject. main() builds the list in
# that order and nothing else may reorder it.

PRESERVE = (
    "Reference image #1 is a FINISHED illustration, not a sketch to reinterpret. "
    "Reproduce it as exactly as you can: the same scene, the same composition, "
    "the same {existing}people in the same left-to-right order, each with the "
    "same face, the same facial features, the same hair, the same glasses, "
    "headset and garment, and the same pose and hand position he already has. "
    "Keep the same background, the same props, the same lighting, the same "
    "colour palette, and the same painted texture, grain and edge treatment. "
    "Treat every figure already in reference image #1 as fixed artwork that "
    "must survive unchanged: do NOT restyle, re-age, slim, re-pose, re-dress, "
    "re-light or swap any of them, do NOT merge or blend their faces, and do "
    "NOT remove anyone or add anyone beyond the one man named below."
)

ADD = (
    " ONE CHANGE ONLY: add the man from reference image #2 to the scene, "
    "{placement}. He must be clearly and unmistakably recognizable as that "
    "specific individual — the same face, the same facial features, the same "
    "hair and the same heavy-set build as reference image #2 — and must not be "
    "given an invented face or blended with any of the men already present. "
    "Render him in the same painted photoreal treatment, at the same scale, "
    "the same depth and the same three-quarter framing as the figures already "
    "in the frame, lit by the same light, and doing what they are doing so he "
    "reads as part of the same picture rather than pasted into it. He wears "
    "{wardrobe}. Take ONLY THE MAN from reference image #2: it is a separate "
    "poster with its own background, panels, layout and lettering, and none of "
    "that comes with him. Do not carry over its setting, its typography or its "
    "colour treatment; he belongs in the scene from reference image #1."
)

FRAME = (
    " Keep the wide banner framing and aspect ratio of reference image #1. All "
    "{total}figures must be fully visible end to end, none cropped by the edge "
    "and none pushed out: make room for the added man by widening or "
    "re-spacing the row, never by cropping the existing figures or shrinking "
    "them out of frame. Any lettering, wordmark or embroidery already present "
    "in reference image #1 — and only there — stays exactly as it is; do NOT "
    "add new words, letters, numbers, logos or watermarks anywhere in the "
    "image."
)


def build_prompt(placement, wardrobe, existing=None):
    """Assemble the three blocks. Authors nothing, decides nothing.

    existing: how many figures are in the base image, when it is known. It is
    optional because it is a fact about the ARTWORK, not about the roster — the
    banner may hold a mascot, a bystander, someone who has left the group — so
    it is passed in rather than counted off personas.json, and omitted entirely
    when nobody has looked. A wrong count is worse than no count: it tells the
    model to reproduce a number of people the picture does not contain.
    """
    existing_bit = f"{existing} " if existing else ""
    total_bit = f"{existing + 1} " if existing else ""
    return (PRESERVE.format(existing=existing_bit)
            + ADD.format(placement=placement.strip().rstrip("."),
                         wardrobe=wardrobe.strip().rstrip("."))
            + FRAME.format(total=total_bit))


def mime_for(path):
    """Declared mime for a reference image, or None when the suffix is unknown."""
    return MIME.get(path.suffix.lower())


def show(path):
    """Repo-relative inside the repo, absolute outside it — see the same helper
    in generate_banners.py. Printing a filename must never fail a run that has
    already spent money."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def resolve(raw):
    """A repo-relative or absolute path, as a Path. No existence check here."""
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--base", required=True, metavar="PATH",
                    help="the finished banner to add someone to. The generator "
                         "master under output/banners/<group>/ when you have "
                         "it; the published docs/assets/banners/<group>/*.webp "
                         "is an accepted fallback and is noted when used.")
    ap.add_argument("--add", required=True, metavar="ID=PATH",
                    help="manager_id=poster_path for the person being added")
    ap.add_argument("--existing", type=int, default=None, metavar="N",
                    help="how many people are ALREADY in --base. Optional; "
                         "counted off the art by eye, not off the roster.")
    ap.add_argument("--placement", default="at one end of the row, beside the "
                                           "figure currently on that end",
                    help="where in the frame the new man goes")
    ap.add_argument("--wardrobe", default="the same gear he wears in reference "
                                          "image #2",
                    help="what the new man wears, in the group's idiom")
    ap.add_argument("--n", type=int, default=3,
                    help="variants to generate. The existing faces can drift on "
                         "any single render, so more than one is the point.")
    ap.add_argument("--aspect", default=None,
                    help="default: the nearest supported ratio to --base's own")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="default output/banners/<group>/_review/ — NOT the "
                         "flat group directory, which build_banners.py "
                         "publishes wholesale")
    ap.add_argument("--force", action="store_true",
                    help="re-generate and re-bill variants already on disk")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--preview", action="store_true",
                    help="print the resolved prompt, write NOTHING, bill NOTHING")
    args = ap.parse_args()

    if "=" not in args.add:
        ap.error(f"--add needs ID=PATH, got {args.add!r}")
    add_id, add_raw = args.add.split("=", 1)
    add_id = add_id.strip()
    if not add_id:
        ap.error(f"--add needs a manager id before the '=', got {args.add!r}")

    base = resolve(args.base)
    add_src = resolve(add_raw)
    for label, p in (("--base", base), ("--add", add_src)):
        if not p.is_file():
            print(f"ERROR: {label} not found: {show(p)}", file=sys.stderr)
            return 1
        if mime_for(p) is None:
            print(f"ERROR: {label} is not an image this can send: {show(p)} "
                  f"(known: {', '.join(sorted(MIME))})", file=sys.stderr)
            return 1
    if args.existing is not None and args.existing < 1:
        ap.error("--existing counts the people already in the base image; "
                 "it cannot be less than 1")
    if args.n < 1:
        ap.error("--n must be at least 1")

    # Aspect comes from the BASE image's own ratio, because the whole premise is
    # "the same picture, plus one". Pillow is already a dependency of every art
    # script in here; a base whose size cannot be read is fatal rather than
    # silently rendered square, which is what omitting imageConfig would do.
    if args.aspect is None:
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(base) as im:
                bw, bh = im.size
        except (UnidentifiedImageError, OSError) as e:
            print(f"ERROR: cannot read the size of --base {show(base)}: {e}",
                  file=sys.stderr)
            return 1
        args.aspect = gemini_image.nearest_aspect(bw, bh)
        note = (f"  note: --base is {bw}x{bh} ({bw / bh:.3f}); nearest supported "
                f"aspect is {args.aspect} ({gemini_image.SUPPORTED_ASPECTS[args.aspect]:.3f}). "
                f"A variant may need the same crop the original took to reach "
                f"its published frame.")
    else:
        note = None
        if args.aspect not in gemini_image.SUPPORTED_ASPECTS:
            print(f"ERROR: unsupported aspect {args.aspect!r}; pick one of "
                  f"{', '.join(gemini_image.SUPPORTED_ASPECTS)}", file=sys.stderr)
            return 1

    prompt = build_prompt(args.placement, args.wardrobe, args.existing)

    out_dir = resolve(args.out) if args.out else \
        ROOT / "output" / "banners" / args.group / "_review"
    stem = f"{base.stem}_plus_{add_id}"

    print(f"model={args.model} aspect={args.aspect} group={args.group}")
    print(f"base={show(base)}")
    print(f"add={add_id} <- {show(add_src)}")
    if note:
        print(note)
    print(f"out={show(out_dir)}")
    print(f"{args.n} planned paid calls ({stem}_01..{args.n:02d}.png)\n")
    if args.preview:
        print(f"--- prompt ---\n{prompt}\n")
        print("preview only; nothing written, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("ERROR: GEMINI_API_KEY missing from .env", file=sys.stderr)
        return 1

    # ORDER IS THE BINDING. The prompt says "reference image #1" and "reference
    # image #2" and this list is the only thing that gives those words meaning.
    refs = [(base.read_bytes(), mime_for(base)),
            (add_src.read_bytes(), mime_for(add_src))]

    out_dir.mkdir(parents=True, exist_ok=True)
    budget = load_budget()
    made, skipped, failed = 0, 0, 0
    for i in range(1, args.n + 1):
        out_file = out_dir / f"{stem}_{i:02d}.png"
        if out_file.exists() and not args.force:
            print(f"  skip (exists): {show(out_file)}")
            skipped += 1
            continue
        print(f"  variant #{i} ...")
        try:
            img = gemini_image.generate(key, args.model, refs, prompt, args.aspect,
                                        timeout=420, budget=budget,
                                        daily_warn=args.daily_warn)
        except Exception as e:
            print(f"  FAILED #{i}: {e}", file=sys.stderr)
            failed += 1
            continue
        out_file.write_bytes(img)
        made += 1
        print(f"  wrote {show(out_file)} ({len(img) // 1024} KB)")

    req = budget["requests"]
    print(f"\ndone: {made} generated, {skipped} skipped, {failed} failed. "
          f"Today (UTC): gemini {req.get('gemini', 0)}, openai {req.get('openai', 0)}.")

    if made:
        print("\nNEXT — none of this happens automatically:")
        print(f"  1. Compare every variant against {show(base)} face by face. "
              f"Keep one only if the people who were already there are still "
              f"themselves; a drifted face is a reason to re-run, not to ship.")
        print(f"  2. Move the keeper up one level, into "
              f"{show(out_dir.parent)}/ . Replacing the base file rather than "
              f"adding beside it is usually right: two published banners means "
              f"the rotation sometimes serves the one {add_id} is missing from.")
        print(f"  3. RE-MEASURE its sidecars. An added figure moves the face "
              f"band, and focal.json/faces.json keys are SOURCE FILENAMES — a "
              f"stale entry silently frames the new art by the old picture's "
              f"numbers. Update alt.json too: the alt text names everyone.")
        print(f"  4. python scripts/build_banners.py --group {args.group} --check"
              f"   (then without --check), then "
              f"python -m pytest scripts/test_banners.py")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
