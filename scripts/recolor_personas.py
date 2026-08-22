#!/usr/bin/env python3
"""Recolor an approved persona poster into the team's PRIMARY color.

NOT part of the live weekly pipeline. Run by hand.

The approved coach-persona posters all landed in black, because black is the
`alternateColor` for every team involved. CFBD's `color` is the vivid primary
(Oklahoma State #fe5c00 orange, Texas Tech #c30020 red, Colorado #cfb87c gold,
Wake Forest #ceb888 gold) and that is what these posters should be wearing.

This is an EDIT, not a generation. The existing poster goes in as the image and
the prompt changes the wardrobe color ONLY -- face, body shape, pose, framing,
background, and every piece of baked-in lettering are pinned. The likeness is
the whole asset; a variant that alters a face is a failed variant, not a style
choice. Review the contact sheet and throw those away.

Why not scripts/generate_owner_images.py: its BASE_RULES forbid text, lettering
and team logos and demand a head-and-shoulders crop. These posters are full-body
with the lettering as the punchline -- the opposite brief. Its retry/backoff and
budget helpers are reused here; its prompts deliberately are not.

Usage:
    python scripts/recolor_personas.py --group panel \
        --map "blaine=output/personas/panel/panel_blaine_fat_01.png:Oklahoma State" \
        --n 4 --preview

    # review the sheet, then drop --preview to write the variants
    python scripts/recolor_personas.py --group panel --map ... --n 4

Playbook compliance (CLAUDE.md):
  - rule 2: reuses _post_with_retries -- transient 3x (5/10/20s), 429 waits the
    full 60s window, quota exhaustion fails fast instead of looping.
  - rule 6: per-run + cumulative UTC-daily tally via the shared budget file.
  - rule 7: --skip-if-exists by DEFAULT; --force is required to re-bill.

Privacy: reads from and writes to gitignored paths only. Nothing here reaches
the public site until a winner is passed through scripts/prepare_portraits.py.
"""

import argparse
import base64
import colorsys
import json
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_owner_images import (  # noqa: E402  -- shared shells, not prompts
    _post_with_retries, load_budget,
)

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "docs" / "data" / "teams_canonical.json"
DEFAULT_MODEL = "gemini-3-pro-image"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
OPENAI_URL = "https://api.openai.com/v1/images/edits"

# Gemini imageConfig.aspectRatio accepts this set. An edit must be told the
# source's own ratio or it re-frames to a square and the composition is lost.
SUPPORTED_ASPECTS = {
    "1:1": 1.0, "3:2": 1.5, "2:3": 2 / 3, "3:4": 0.75, "4:3": 4 / 3,
    "4:5": 0.8, "5:4": 1.25, "9:16": 9 / 16, "16:9": 16 / 9, "21:9": 21 / 9,
}

# The pin. Everything that must NOT move, stated positively and then again as a
# prohibition -- image models honour the negative list far more reliably when the
# positive one precedes it.
LIKENESS_LOCK = (
    " ABSOLUTELY UNCHANGED: this exact man's face, facial features, jawline, "
    "hair, facial hair, skin tone, glasses or eyewear, his full body shape and "
    "size, his weight and build, his pose, his hands, and his expression. He "
    "must remain instantly recognizable as the same individual with the same "
    "physique. Also unchanged: the camera angle, the crop and framing, the "
    "background and setting, the stadium or room behind him, the lighting "
    "direction and mood, and EVERY piece of text, lettering, numbering, "
    "signage and graphic design already present in the image -- reproduce all "
    "existing words exactly as they appear, same fonts, same positions. "
    "DO NOT slim him, DO NOT thin his face, DO NOT alter his body, DO NOT "
    "change his identity, DO NOT restyle or redraw the picture, DO NOT add or "
    "remove any text, DO NOT change the background. This is a targeted garment "
    "recolor of an existing photograph, not a reinterpretation of it."
)

# Four variants per manager. Each changes only what the garment IS, so the
# fan-out gives real choice without ever loosening the likeness lock.
VARIANTS = [
    ("polo", "a solid {color_name} team coaching polo shirt"),
    ("quarterzip", "a {color_name} quarter-zip coaching pullover"),
    ("jacket", "a {color_name} team coaching jacket or windbreaker, worn open "
               "over a matching shirt"),
    ("accent", "a predominantly {color_name} coaching polo with contrasting "
               "team-color trim on the collar and sleeves"),
]


def nearest_aspect(w: int, h: int) -> str:
    """Closest supported aspect string to the source, so an edit keeps framing."""
    r = w / h
    return min(SUPPORTED_ASPECTS, key=lambda k: abs(SUPPORTED_ASPECTS[k] - r))


def team_colors() -> dict:
    data = json.loads(CANONICAL.read_text(encoding="utf-8"))
    return {t["school"]: t for t in data.get("teams", []) if t.get("school")}


def color_name(hex_code: str) -> str:
    """Plain-language name for a hex, so the prompt reads naturally.

    Hue-based, not RGB-branch-based: the school golds (Colorado #cfb87c, Wake
    Forest #ceb888) are desaturated yellows whose red channel dominates, so
    naive "is red biggest?" logic calls them red. Hue separates them cleanly.
    The word does the steering work in the prompt; the hex only pins the shade.
    """
    h = (hex_code or "").lstrip("#").lower()
    if len(h) != 6:
        return "team-colored"
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    deg = hue * 360
    if sat < 0.12:
        return "white" if val > 0.85 else "grey" if val > 0.35 else "black"
    if deg < 15 or deg >= 345:
        return "bright red" if val > 0.8 else "crimson red" if val > 0.5 else "dark red"
    if deg < 40:
        # Brown is perceptually a DARK orange and had no word here at all, so
        # every brown came out "burnt orange" -- wrong for Cleveland Browns
        # brown, and wrong for the two schools below. Of 137 canonical team
        # colors only Western Michigan (#532e1f) and Wyoming (#492f24) fall in
        # this tier, and both are brown, so this corrects rather than regresses.
        if val < 0.45:
            return "brown"
        return "bright orange" if val > 0.85 else "burnt orange"
    if deg < 70:
        # Muted = the school "old gold"; saturated = a true yellow.
        return "gold" if sat < 0.6 else "yellow"
    if deg < 160:
        return "bright green" if val > 0.7 else "dark green"
    if deg < 200:
        return "teal"
    if deg < 255:
        return "royal blue" if val > 0.55 else "navy blue"
    if deg < 300:
        return "purple"
    return "magenta"


def build_prompt(team: str, hexc: str, garment_tmpl: str) -> str:
    cname = color_name(hexc)
    garment = garment_tmpl.format(color_name=cname)
    return (
        f"Recolor ONLY the clothing this football coach is wearing. Replace his "
        f"current dark/black outfit with {garment}, in {team}'s primary team "
        f"color {cname} ({hexc}). It must look like authentic team-issued "
        f"coaching apparel in that color: keep the fabric texture, the folds and "
        f"creases, the way it drapes over his body, and any team logos or marks "
        f"on the garment -- recolor those only as needed so they stay clearly "
        f"legible against the new {cname} fabric. The garment must fit his body "
        f"exactly as the current one does." + LIKENESS_LOCK
    )


def gen_gemini_edit(api_key, model, photo_bytes, mime, prompt, aspect,
                    budget=None, daily_warn=None) -> bytes:
    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime,
                                 "data": base64.b64encode(photo_bytes).decode()}},
                {"text": prompt},
            ],
        }],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": aspect},
        },
    }
    resp = _post_with_retries(lambda: requests.post(
        f"{GEMINI_BASE}/{model}:generateContent", json=payload,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        timeout=300), budget=budget, provider="gemini", daily_warn=daily_warn)
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise RuntimeError(f"No candidate. promptFeedback={data.get('promptFeedback')}")
    for part in parts:
        blob = part.get("inlineData") or part.get("inline_data")
        if blob and blob.get("data"):
            return base64.b64decode(blob["data"])
    texts = " ".join(p.get("text", "") for p in parts).strip()
    raise RuntimeError(f"No image in response. Model said: {texts[:400]}")


def gen_openai_edit(api_key, model, photo_bytes, mime, prompt, size,
                    budget=None, daily_warn=None) -> bytes:
    files = {"image[]": ("photo.png", photo_bytes, mime)}
    form = {"model": model, "prompt": prompt, "size": size, "n": "1"}
    resp = _post_with_retries(lambda: requests.post(
        OPENAI_URL, data=form, files=files,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=300),
        budget=budget, provider="openai", daily_warn=daily_warn)
    data = resp.json()
    try:
        return base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError):
        raise RuntimeError(f"No image in response: {json.dumps(data)[:400]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", required=True)
    ap.add_argument("--map", action="append", default=[], metavar="ID=PATH:TEAM",
                    help="manager_id=source_poster_path:Team Name (repeatable)")
    ap.add_argument("--n", type=int, default=4,
                    help="variants per manager (max %d)" % len(VARIANTS))
    ap.add_argument("--provider", default="gemini", choices=["gemini", "openai"])
    ap.add_argument("--model", default=None,
                    help=f"override (default {DEFAULT_MODEL} / gpt-image-2)")
    ap.add_argument("--size", default="1024x1536", help="openai only")
    ap.add_argument("--out", default=str(ROOT / "output" / "personas" / "recolor"))
    ap.add_argument("--force", action="store_true", help="re-bill existing files")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--preview", action="store_true",
                    help="print resolved colors/prompts and write NOTHING")
    args = ap.parse_args()

    colors = team_colors()
    jobs = []
    for entry in args.map:
        if "=" not in entry or ":" not in entry.split("=", 1)[1]:
            ap.error(f"--map needs ID=PATH:TEAM, got {entry!r}")
        mid, rest = entry.split("=", 1)
        raw_path, team = rest.rsplit(":", 1)
        src = Path(raw_path) if Path(raw_path).is_absolute() else ROOT / raw_path
        if not src.exists():
            print(f"ERROR: poster not found for {mid!r}: {src}", file=sys.stderr)
            return 1
        team = team.strip()
        if team not in colors:
            # rule 4: an unresolvable team name stops the run and names it.
            print(f"ERROR: team {team!r} (for {mid}) is not in teams_canonical.json",
                  file=sys.stderr)
            return 1
        hexc = colors[team].get("color")
        if not hexc:
            print(f"ERROR: no primary color recorded for {team!r}", file=sys.stderr)
            return 1
        jobs.append((mid.strip(), src, team, hexc))
    if not jobs:
        ap.error("at least one --map ID=PATH:TEAM is required")

    n = max(1, min(args.n, len(VARIANTS)))
    model = args.model or (DEFAULT_MODEL if args.provider == "gemini" else "gpt-image-2")

    print(f"provider={args.provider} model={model} variants/manager={n}")
    print(f"planned paid calls: {len(jobs) * n}\n")
    for mid, src, team, hexc in jobs:
        print(f"  {mid:9s} {team:16s} {hexc}  -> {color_name(hexc)}   ({src.name})")
    if args.preview:
        print("\n--- prompt sample (first job, first variant) ---")
        mid, src, team, hexc = jobs[0]
        print(build_prompt(team, hexc, VARIANTS[0][1])[:1400])
        print("\npreview only; nothing written, nothing billed.")
        return 0

    load_dotenv(ROOT / ".env")
    import os
    key = os.environ.get(
        "GEMINI_API_KEY" if args.provider == "gemini" else "OPENAI_API_KEY", "").strip()
    if not key:
        print(f"ERROR: missing key for provider {args.provider}", file=sys.stderr)
        return 1

    budget = load_budget()
    made, skipped, failed = 0, 0, 0
    for mid, src, team, hexc in jobs:
        out_dir = Path(args.out) / mid
        out_dir.mkdir(parents=True, exist_ok=True)
        photo = src.read_bytes()
        with Image.open(src) as im:
            aspect = nearest_aspect(*im.size)
        for slug, tmpl in VARIANTS[:n]:
            out_file = out_dir / f"{mid}_{slug}_{args.provider}.png"
            if out_file.exists() and not args.force:
                print(f"  skip (exists): {out_file.relative_to(ROOT)}")
                skipped += 1
                continue
            print(f"  {mid}: {slug} ({color_name(hexc)}, {aspect}) ...")
            prompt = build_prompt(team, hexc, tmpl)
            try:
                if args.provider == "gemini":
                    img = gen_gemini_edit(key, model, photo, "image/png", prompt, aspect,
                                          budget=budget, daily_warn=args.daily_warn)
                else:
                    img = gen_openai_edit(key, model, photo, "image/png", prompt, args.size,
                                          budget=budget, daily_warn=args.daily_warn)
            except Exception as e:
                print(f"  FAILED {mid} {slug}: {e}", file=sys.stderr)
                failed += 1
                continue
            out_file.write_bytes(img)
            made += 1
            print(f"  wrote {out_file.relative_to(ROOT)} ({len(img)//1024} KB)")

    req = budget["requests"]
    print(f"\ndone: {made} generated, {skipped} skipped, {failed} failed. "
          f"Today (UTC): gemini {req.get('gemini',0)}, openai {req.get('openai',0)}.")
    print("Review the output for LIKENESS before publishing any of it.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
