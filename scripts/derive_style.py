#!/usr/bin/env python3
"""Look at finished art, write down its STYLE. One job, one output.

    python scripts/derive_style.py --style-id gameday_fat_photoreal \
        --exemplar "output/_vault/church/Fat Banner.png" \
        --render-mode photoreal --text-policy typography --aspect 2:3 \
        --out templates/art/gameday_fat_photoreal.json [--dry-run] [--force]

WHAT THIS IS FOR. Part 1 of five (see scripts/style_spec.py for the map). It
captures a style that exists ONLY as pixels and is codified nowhere. That is a
real gap: `grep -rn "fat" --include=*.py scripts/` returns no style definition,
yet output/_vault/ is full of finished "fat coach" art -- Fat Banner.png is a
photoreal GameDay poster with first-class typography that no script here can
reproduce. It was made by hand in a web UI and dropped in a folder.

WHAT THIS IS NOT FOR. Re-deriving a style that is already source code. The four
STYLES entries in generate_banners.py and the seven STYLE_SET entries in
generate_scenes.py produced the approved art; asking a model to reverse-engineer
a prompt from the output of a prompt we already own is strictly lossy. Worse,
outputs contain defects -- panel_comic_01.png renders team wordmarks that
NO_TEXT forbade -- and a model cannot tell a defect from an intention. It will
encode the mistake as style. templates/style_derivation.md tells it not to
describe logos or text at all; style_spec.py rejects the spec if it does anyway.

WHO DECIDES WHAT. The model describes; the operator classifies. --render-mode
and --text-policy are flags, not model outputs, because render_mode='photoreal'
waives the BANNED_TERMS guard and a model does not get to waive our safety
checks about its own work.

COSTS ONE OPENAI CALL. --dry-run builds the identical request, writes it to a
preview file and makes no network call -- the same contract
generate_commentary.py --dry-run offers. --skip-if-exists is the default
(rule 7): an existing --out is refused without --force.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_commentary import call_openai  # noqa: E402  -- shared retry shell
from generate_owner_images import bump_budget, load_budget  # noqa: E402
import style_spec  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_FILE = ROOT / "templates" / "style_derivation.md"
PREVIEW_DIR = ROOT / "output" / "art_specs"

# Vision settings. Deliberately NOT the column's: this is a description task,
# not a piece of writing, so temperature is low and the response is JSON.
MODEL = os.environ.get("OPENAI_VISION_MODEL",
                       os.environ.get("OPENAI_MODEL", "gpt-4o"))
TEMPERATURE = 0.2
MAX_TOKENS = 800

# "low" downsamples to 512px, which destroys exactly what we are trying to
# capture -- grain, brushwork, halftone dot pitch.
DETAIL = "high"

# More than three averages distinct variants into mush, and the payload grows
# ~33% over the file size once base64'd.
MAX_EXEMPLARS = 3
MAX_ONE_MB = 15
MAX_TOTAL_MB = 25

TASK = ("Describe the STYLE of the attached image(s) as one reusable fragment, "
        "following every rule in your instructions. The images share one "
        "style. Return JSON with keys `fragment` and `notes`.")


def read_exemplars(paths):
    """-> [(path, bytes, mime)], with the size ceilings enforced up front."""
    out, total = [], 0
    for raw in paths:
        p = Path(raw) if Path(raw).is_absolute() else ROOT / raw
        if not p.is_file():
            raise SystemExit(f"ERROR: exemplar not found: {p}")
        blob = p.read_bytes()
        mb = len(blob) / 1e6
        if mb > MAX_ONE_MB:
            raise SystemExit(f"ERROR: {p.name} is {mb:.1f} MB, over the "
                             f"{MAX_ONE_MB} MB per-file ceiling.")
        total += mb * 1.33
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        out.append((p, blob, mime))
    if total > MAX_TOTAL_MB:
        raise SystemExit(f"ERROR: exemplars total ~{total:.1f} MB base64, over "
                         f"the {MAX_TOTAL_MB} MB ceiling. Use fewer.")
    return out


def build_message(exemplars):
    """The user content parts: the task, then every image."""
    parts = [{"type": "text", "text": TASK}]
    for _p, blob, mime in exemplars:
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{base64.b64encode(blob).decode()}",
            "detail": DETAIL}})
    return parts


def preview_text(system_text, exemplars):
    """The exact request, with the blobs elided so it is readable."""
    lines = [f"model={MODEL} temperature={TEMPERATURE} "
             f"max_tokens={MAX_TOKENS} detail={DETAIL}",
             "response_format=json_object",
             "", "=== SYSTEM ===", system_text,
             "", "=== USER ===", TASK, ""]
    for p, blob, mime in exemplars:
        lines.append(f"<image_url: {len(blob) * 1.33 / 1e6:.1f} MB base64 "
                     f"elided -- {p.relative_to(ROOT).as_posix()} "
                     f"({mime}, detail={DETAIL})>")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--style-id", required=True,
                    help="lowercase slug; must equal the --out filename stem")
    ap.add_argument("--exemplar", action="append", default=[], required=True,
                    metavar="PATH", help="finished art to describe (repeatable)")
    ap.add_argument("--render-mode", required=True,
                    choices=list(style_spec.RENDER_MODES),
                    help="OPERATOR call: 'photoreal' waives BANNED_TERMS")
    ap.add_argument("--text-policy", default="none",
                    choices=list(style_spec.TEXT_POLICIES),
                    help="'typography' suppresses NO_TEXT downstream")
    ap.add_argument("--photoreal-why", default="",
                    help="required when --render-mode photoreal: argue the waiver")
    ap.add_argument("--aspect", default=None, help="e.g. 21:9, 2:3")
    ap.add_argument("--out", default=None,
                    help="default templates/art/<style-id>.json")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--force", action="store_true", help="overwrite --out")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--dry-run", action="store_true",
                    help="build the request, write a preview, call nothing")
    a = ap.parse_args()

    if len(a.exemplar) > MAX_EXEMPLARS:
        ap.error(f"at most {MAX_EXEMPLARS} exemplars (got {len(a.exemplar)}); "
                 f"more than that averages distinct variants into mush")
    if a.render_mode == "photoreal" and not a.photoreal_why.strip():
        ap.error("--render-mode photoreal waives the BANNED_TERMS guard, so "
                 "--photoreal-why is required. A silent waiver is how a "
                 "load-bearing guard rots.")

    out = Path(a.out) if a.out else style_spec.ART_DIR / f"{a.style_id}.json"
    if not out.is_absolute():
        out = ROOT / out
    if out.exists() and not a.force:
        print(f"skip (exists): {out.relative_to(ROOT).as_posix()} -- --force "
              f"to re-derive (git diff is your review)")
        return 0

    if not SYSTEM_FILE.is_file():
        raise SystemExit(f"ERROR: missing system prompt {SYSTEM_FILE}")
    system_text = SYSTEM_FILE.read_text(encoding="utf-8")
    exemplars = read_exemplars(a.exemplar)

    print(f"style_id={a.style_id} model={a.model} "
          f"render_mode={a.render_mode} text_policy={a.text_policy}")
    for p, blob, _m in exemplars:
        print(f"  exemplar {p.relative_to(ROOT).as_posix()} "
              f"({len(blob) / 1e6:.1f} MB)")

    if a.dry_run:
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        dest = PREVIEW_DIR / f"{a.style_id}_request_preview.txt"
        dest.write_text(preview_text(system_text, exemplars), encoding="utf-8")
        print(f"\ndry run: wrote {dest.relative_to(ROOT).as_posix()}")
        print("nothing called, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("ERROR: OPENAI_API_KEY missing from .env")

    budget = load_budget()
    total = bump_budget(budget, "openai")
    if total > a.daily_warn:
        print(f"::warning:: API daily tally {total} -- past the "
              f"{a.daily_warn} threshold.", file=sys.stderr)

    print("\n  calling ...")
    raw = call_openai(system_text, build_message(exemplars), key,
                      model=a.model, temperature=TEMPERATURE,
                      max_tokens=MAX_TOKENS,
                      response_format={"type": "json_object"})
    try:
        got = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: model did not return JSON ({e}):\n{raw[:600]}")
    if "fragment" not in got:
        raise SystemExit(f"ERROR: no `fragment` in response:\n{raw[:600]}")

    spec = {
        "_note": ("STYLE fragment only -- part 1 of five. It says HOW a picture "
                  "is made, never who is in it, how they are posed, what they "
                  "wear, or what must not change. See scripts/style_spec.py."),
        "style_id": a.style_id,
        "version": 1,
        "derived_from": [Path(p).as_posix() if not Path(p).is_absolute()
                         else Path(p).relative_to(ROOT).as_posix()
                         for p in a.exemplar],
        "derived_by": {"script": "derive_style.py", "model": a.model,
                       "detail": DETAIL, "temperature": TEMPERATURE},
        "render_mode": a.render_mode,
        "text_policy": a.text_policy,
        "fragment": got["fragment"].strip(),
        "model_notes": got.get("notes", ""),
        "human_edits": [],
    }
    if a.render_mode == "photoreal":
        spec["_photoreal_why"] = a.photoreal_why.strip()
    if a.aspect:
        spec["aspect"] = a.aspect

    # Validate BEFORE writing: an invalid spec on disk is a trap for the next
    # person, and the failure names which of the five parts the model reached
    # into. The paid call is already spent either way -- but a rejected
    # fragment is diagnostic, not wasted.
    print("\n  --- fragment ---")
    print(f"  {spec['fragment']}")
    if spec.get("model_notes"):
        print(f"\n  --- model notes (never sent to an image model) ---")
        print(f"  {spec['model_notes']}")
    print()
    style_spec.validate(spec, out)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"  wrote {out.relative_to(ROOT).as_posix()}")
    print(f"\nREAD IT before committing -- it is source code that spends money "
          f"on every future run. Today (UTC): openai {total}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
