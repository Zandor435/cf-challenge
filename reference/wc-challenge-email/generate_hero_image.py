#!/usr/bin/env python3
"""Generate the daily newsletter HERO image with Nano Banana (Gemini 2.5 Flash Image).

Sibling to generate_pundit_avatars.py — same SDK and call pattern — but this makes
ONE wide editorial illustration per email: a magazine-cover take on the day's main
storyline (who won big, who collapsed, which rivalry popped off). It is NOT a
photoreal headshot; it is stylized SI-cover-meets-political-cartoon art.

Input: email/payload.json (built first by build_email_payload.py). The payload has
everything resolved — Rome's headline + hook, the standings (so we know the leader
and the basement), the matchday/stage, and the URL the image must deploy to. The
fuller tournament_recap.md is read as optional extra narrative colour.

Output: site/assets/email/day_{N}.png  (N = meta.day_number). OVERWRITE by default
(idempotent; the latest send for a given matchday wins). The site serves this over
GitHub Pages, and the email references it by absolute URL — so the image must be
committed + deployed BEFORE the email sends (see .github/workflows/update-data.yml).

Graceful fallback — this is the whole point of the design: if GEMINI_API_KEY is
missing or generation fails, we DO NOT raise. Instead we patch
payload.json -> hero_image_url = null so the template simply hides the hero section
and the email still sends. generate_hero_image.py is the authority on that field:
build_email_payload.py sets the prospective URL; we confirm it (success) or null it
(failure).

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python email/generate_hero_image.py
    python email/generate_hero_image.py --payload email/payload.json --dry-run
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE_DATA = os.path.join(ROOT, "site", "data")
EMAIL_ASSETS = os.path.join(ROOT, "site", "assets", "email")
MODEL = "gemini-2.5-flash-image"
HERO_W, HERO_H = 1200, 675  # 16:9 hero, retina-friendly for a 600px-wide email column

# Shared art direction for EVERY hero. The opposite of the pundit avatars: those are
# pushed-photoreal headshots; this is a bold STYLIZED illustration — a magazine cover.
_STYLE = (
    "Editorial sports-illustration cover art, WIDE 16:9 landscape composition. Bold "
    "saturated colours, dramatic high-contrast lighting, strong diagonal composition, "
    "thick confident linework and flat painterly shapes — the energy of a Sports "
    "Illustrated cover crossed with a sharp political cartoon. Stylized and graphic, "
    "NOT photorealistic, NOT a photo, NOT 3D render: an illustration. Soccer/World Cup "
    "iconography (the ball, stadium lights, a trophy, jerseys, flags) used as bold "
    "graphic motifs. Cinematic, poster-like, instantly readable as a single strong "
    "image even at small size. Leave the composition uncluttered — no paragraphs of "
    "text; at most a few large stylized words if any. Dark moody backdrop so it sits "
    "on a near-black (#0c0e14) email background. "
)


# --------------------------------------------------------------------------- IO
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def load_env_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    home_env = os.path.join(os.path.expanduser("~"), ".env")
    if os.path.exists(home_env):
        with open(home_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def patch_hero_url(payload_path, url):
    """Set payload.json -> hero_image_url to `url` (a string, or None to hide the hero)."""
    data = load_json(payload_path)
    if data is None:
        return
    data["hero_image_url"] = url
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------- prompt
def build_prompt(payload, recap_md):
    """Turn the day's resolved storyline into a single hero-image prompt."""
    meta = payload.get("meta", {})
    rome = payload.get("rome", {})
    standings = payload.get("standings", []) or []
    is_preseason = bool(meta.get("is_preseason"))
    day_label = meta.get("day_label", "")
    tournament = meta.get("tournament", "FIFA World Cup 2026")

    headline = (rome.get("headline") or "").strip()
    hook = (rome.get("short") or "").strip()

    # Who's the story? Leader, the basement, and anyone who moved this cycle.
    def describe(row):
        ring = row.get("ring_name")
        tag = f' ("{ring}")' if ring else ""
        return f"{row.get('owner')}{tag}, team colour {row.get('color')}"

    cast = []
    if standings:
        cast.append("LEADER — " + describe(standings[0]))
        if len(standings) > 1:
            cast.append("LAST PLACE — " + describe(standings[-1]))
        movers = [s for s in standings[1:-1] if s.get("move_arrow") in ("▲", "▼")]
        for m in movers[:2]:
            dir_word = "rising" if m.get("move_arrow") == "▲" else "falling"
            cast.append(f"{dir_word.upper()} — " + describe(m))
    cast_block = "\n".join(f"  - {c}" for c in cast) if cast else "  - the five managers of the pool"

    # Tournament stage, for composition cues (group brawl vs knockout drama vs final).
    if is_preseason or day_label.lower().startswith("preseason"):
        stage = ("PRE-TOURNAMENT: the pool is set, nothing decided — render anticipation, "
                 "bravado, managers sizing each other up before kickoff.")
    else:
        stage = (f"This is {day_label} of {tournament} (group/knockout stage in progress) — "
                 "render the drama of the day's results, momentum and collapse.")

    parts = [
        _STYLE,
        f"THE DAY'S STORYLINE (illustrate THIS, do not just decorate): \"{headline}\".",
    ]
    if hook:
        parts.append(f"More context from the columnist: {hook}")
    parts.append(stage)
    parts.append(
        "FEATURE THESE managers as bold stylized characters / their team colours as the "
        "dominant palette (do NOT render real recognizable faces — invent expressive "
        "caricature figures in team-coloured kits):\n" + cast_block
    )
    if recap_md:
        parts.append("Extra narrative for flavour (do not transcribe, just inform the mood): "
                     + recap_md[:600])
    parts.append(
        "Compose a single dramatic cover image that captures who is winning, who is "
        "collapsing, and the rivalry energy of the day. Make it want-to-click striking."
    )
    return "\n\n".join(parts)


# ----------------------------------------------------------------- image
def to_hero_png(raw_bytes):
    """Center-crop the model output to 16:9 and resize to HERO_W x HERO_H; PNG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    target = HERO_W / HERO_H
    if w / h > target:                      # too wide -> trim sides
        new_w = int(h * target)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:                                   # too tall -> trim top/bottom
        new_h = int(w / target)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((HERO_W, HERO_H), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def generate_one(client, prompt):
    """Call the model with one retry on an empty response. Return raw image bytes or None."""
    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model=MODEL, contents=[prompt])
        except Exception as e:  # noqa: BLE001
            print(f"   API error (attempt {attempt}): {e}", file=sys.stderr)
            continue
        cand = (resp.candidates or [None])[0]
        content = getattr(cand, "content", None) if cand else None
        if content and getattr(content, "parts", None):
            for part in content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    return part.inline_data.data
            txt = "".join(getattr(p, "text", "") or "" for p in content.parts)
            print(f"   no image in response: {txt[:160]}", file=sys.stderr)
        else:
            reason = getattr(cand, "finish_reason", "unknown") if cand else "no candidates"
            print(f"   empty response (finish_reason={reason})", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Generate the WC Challenge email hero image")
    ap.add_argument("--payload", default=os.path.join(HERE, "payload.json"))
    ap.add_argument("--recap", default=os.path.join(SITE_DATA, "tournament_recap.md"))
    ap.add_argument("--out-dir", default=EMAIL_ASSETS)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and exit; do not call Gemini or touch files")
    args = ap.parse_args()

    payload = load_json(args.payload)
    if payload is None:
        print(f"ERROR: {args.payload} not found — run build_email_payload.py first.",
              file=sys.stderr)
        sys.exit(1)

    n = int(payload.get("meta", {}).get("day_number", 0) or 0)
    recap_md = read_text(args.recap)
    prompt = build_prompt(payload, recap_md)

    if args.dry_run:
        print(prompt)
        return

    out_path = os.path.join(args.out_dir, f"day_{n}.png")

    # --- graceful-fallback wrapper: any failure path nulls hero_image_url and exits 0,
    #     so the email still sends (template hides the missing hero). ----------------
    def bail(msg):
        print(f"{msg} — hiding hero image (payload.hero_image_url -> null).", file=sys.stderr)
        patch_hero_url(args.payload, None)
        sys.exit(0)

    key = load_env_key()
    if not key:
        bail("GEMINI_API_KEY not set (env or ~/.env)")

    try:
        from google import genai
    except ImportError:
        bail("google-genai not installed")

    client = genai.Client(api_key=key)
    print(f"[gen ] hero for {payload.get('meta', {}).get('day_label', '?')}: "
          f"{payload.get('rome', {}).get('headline', '')[:80]}")
    data = generate_one(client, prompt)
    if not data:
        bail("Gemini returned no image")

    try:
        png = to_hero_png(data)
    except Exception as e:  # noqa: BLE001
        bail(f"image post-processing failed: {e}")

    os.makedirs(args.out_dir, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(png)
    print(f"   wrote {out_path} ({len(png)//1024} KB)")

    # Success: confirm the URL build_email_payload.py predicted (it already points here).
    expected = payload.get("hero_image_url")
    if not expected:
        base = (payload.get("meta", {}).get("site_base_url") or "").rstrip("/")
        expected = f"{base}/assets/email/day_{n}.png" if base else None
        patch_hero_url(args.payload, expected)
    print(f"   hero_image_url: {expected or '(none — no site_base_url)'}")


if __name__ == "__main__":
    main()
