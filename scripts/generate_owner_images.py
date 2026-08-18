#!/usr/bin/env python3
"""Standalone owner-portrait generator — coach-persona trading-card art.

NOT part of the live weekly pipeline. Takes a real photo of a league owner and
re-renders it as a college-football-coach persona, fanning out N iterations per
archetype so the owner can pick a winner. Two providers:

  - gemini : gemini-2.5-flash-image  (needs GEMINI_API_KEY in .env)
  - openai : gpt-image-1 /v1/images/edits  (needs OPENAI_API_KEY in .env)

Usage:
    python scripts/generate_owner_images.py --photo assets/source_photos/blaine.jpg \
        --owner blaine --styles emperor,mad-scientist --n 2 --provider both

    python scripts/generate_owner_images.py --list-styles

Likeness rules baked into every prompt: the FRIEND's face stays recognizable;
archetypes are described by wardrobe/energy/setting only — no real coach is
named and the face must not be morphed toward any real person. No text or
logos in the art (the site's type layer supplies names/weeks).

Playbook compliance (CLAUDE.md):
  - rule 2: two retry loops — transient network 3x (5/10/20s), 429 waits the
    full 60s window; re-raises after the last attempt.
  - rule 6: per-run request count + cumulative UTC-daily tally per provider in
    data/gemini_budget.json; loud warning past --daily-warn (default 80).
  - rule 7: paid generation is --skip-if-exists by DEFAULT; --force re-bills.

Privacy: source photos in assets/source_photos/ and outputs in output/personas/
are BOTH gitignored. Nothing reaches the public site until a chosen image is
manually copied into docs/assets/portraits/.
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
GEMINI_MODEL = "gemini-2.5-flash-image"
GEMINI_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
              f"{GEMINI_MODEL}:generateContent")
OPENAI_URL = "https://api.openai.com/v1/images/edits"
BUDGET_FILE = ROOT / "data" / "gemini_budget.json"   # tracks both providers

# Shared contract appended to every archetype prompt. FOOTBALL_KIT is
# non-negotiable: every archetype must read as a college football coach on a
# football field first, and as its personality second.
FOOTBALL_KIT = (
    " He must unmistakably read as a COLLEGE FOOTBALL HEAD COACH: team-colored "
    "coaching polo or quarter-zip pullover with a coaching headset over the "
    "ears (or a whistle on a lanyard and a team ball cap or visor), a "
    "laminated play-call sheet in hand or tucked under the arm, credential "
    "lanyard, standing on a marked football field or sideline with yard lines, "
    "goalposts, and stadium stands visible behind him."
)
BASE_RULES = (
    FOOTBALL_KIT +
    " Keep this person's actual facial likeness clearly recognizable — same "
    "face, same features, same build. Do NOT morph the face toward any real "
    "coach or celebrity. Head-and-shoulders portrait with headroom, subject "
    "centered, painted/illustrated trading-card finish. No text, no numbers, "
    "no lettering, no team logos, no brand marks, no watermarks anywhere."
)

# Coach-persona archetypes. Wardrobe/energy/setting only — no real names.
STYLES = {
    # ---- the recommended four (rich prompts) ----
    "emperor": (
        "Restyle this person as THE EMPEROR: a clinical, joyless, terrifyingly "
        "competent championship head coach. Immaculate slate-gray fleece "
        "pullover over a collared shirt, coaching headset, arms crossed, thin "
        "unimpressed stare that has never once been satisfied, cold overcast "
        "stadium light, muted steel palette, everything precise and starched."
        + BASE_RULES
    ),
    "bayou-maniac": (
        "Restyle this person as THE BAYOU MANIAC: a gravel-voiced, "
        "maximum-energy southern head coach mid-fire-up. Sweat-sheened, veins "
        "up, mouth open in a joyful roar, whistle bouncing on a strained polo, "
        "cap slightly crooked, humid golden swamp-country sideline behind, hot "
        "saturated color, pure chaotic passion." + BASE_RULES
    ),
    "mad-scientist": (
        "Restyle this person as THE MAD SCIENTIST of offense: an eccentric, "
        "contrarian professor-of-football on the practice field. Rumpled team "
        "windbreaker, bemused far-away expression mid-bizarre-tangent, one "
        "eyebrow up, gripping a play-call sheet covered in impossible "
        "diagrams, a small pirate flag flying on the sideline behind him, "
        "overcast practice-day light." + BASE_RULES
    ),
    "troll": (
        "Restyle this person as THE TROLL: a smug, extremely-online head coach "
        "who enjoys everyone else's pain. Team visor and designer sunglasses, "
        "one-sided smirk, phone in one hand mid-post and a play sheet in the "
        "other, sun-washed practice field with palm trees beyond the goalpost, "
        "bright light, insufferably pleased with himself." + BASE_RULES
    ),
    # ---- the rest of the panel (leaner prompts) ----
    "gunslinger": (
        "Restyle this person as THE GUNSLINGER: a cocky, quick-witted head "
        "coach in a visor and polo, smirking sideways mid-insult, golf-tanned, "
        "sunny practice-field backdrop, loose and swaggering." + BASE_RULES
    ),
    "showman": (
        "Restyle this person as THE SHOWMAN: flash, confidence, spectacle — "
        "designer sunglasses, gleaming smile, gold chain over a crisp coaching "
        "jacket, stadium lights blazing behind like a stage." + BASE_RULES
    ),
    "football-lunatic": (
        "Restyle this person as THE FOOTBALL LUNATIC: wild-eyed old-school "
        "intensity, navy sweater over a collared shirt, pleated khakis, "
        "clutching a glass of milk like a trophy, mid-shout about toughness, "
        "gray practice-day sky." + BASE_RULES
    ),
    "corporate-mercenary": (
        "Restyle this person as THE CORPORATE MERCENARY: polished executive "
        "head coach, tailored quarter-zip, boardroom smile that never reaches "
        "the eyes, glass-and-steel facility atrium behind, LinkedIn-headshot "
        "lighting." + BASE_RULES
    ),
    "evangelist": (
        "Restyle this person as THE EVANGELIST: relentless folksy optimism, "
        "beaming ear-to-ear, orange-tinted sunrise sideline, arms spread "
        "mid-sermon about believing, khakis and team polo, warm glowing "
        "light." + BASE_RULES
    ),
    "psycho-competitor": (
        "Restyle this person as THE PSYCHO COMPETITOR: modern, aggressive, "
        "fourth-down-every-time energy — jaw set, eyes locked, play sheet "
        "gripped in one fist, athletic-fit coaching gear, dramatic night-game "
        "LED lighting." + BASE_RULES
    ),
    "mad-hatter": (
        "Restyle this person as THE MAD HATTER: serenely unpredictable — white "
        "cap slightly askew, blissful knowing grin, a single blade of grass "
        "held near his mouth, chaotic scoreboard glow behind, the calm of a "
        "man whose decisions cannot be explained." + BASE_RULES
    ),
    "outlaw": (
        "Restyle this person as THE OUTLAW: old-school swagger, rules-are-"
        "suggestions grin, aviators, unbuttoned 1980s coaching jacket, "
        "sun-bleached film-photo grain, dusty golden practice field." + BASE_RULES
    ),
}

REQUEST_TIMEOUT = 180
TRANSIENT_WAITS = [5, 10, 20]     # rule 2 loop A
RATE_LIMIT_WAITS = [60, 60, 60]   # rule 2 loop B


# ---------- budget (rule 6) -------------------------------------------------
def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_budget() -> dict:
    if BUDGET_FILE.exists():
        try:
            b = json.loads(BUDGET_FILE.read_text())
            if b.get("date") == _utc_date() and isinstance(b.get("requests"), dict):
                return b
        except (json.JSONDecodeError, OSError):
            pass
    return {"date": _utc_date(), "requests": {"gemini": 0, "openai": 0}}


def bump_budget(budget: dict, provider: str) -> int:
    budget["requests"][provider] = budget["requests"].get(provider, 0) + 1
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_FILE.write_text(json.dumps(budget, indent=1))
    return sum(budget["requests"].values())


# ---------- shared retry shell (rule 2) --------------------------------------
def _post_with_retries(do_post):
    """do_post() -> requests.Response. Retries transient + 429; returns 200 resp.

    ChunkedEncodingError is in the transient tuple deliberately: it is a SIBLING
    of ConnectionError under RequestException, not a subclass, so a truncated
    response body ("Connection broken: IncompleteRead") slipped straight past the
    retry loop and failed the call outright. Observed on a real image-gen run.
    """
    transient_left = list(TRANSIENT_WAITS)
    ratelimit_left = list(RATE_LIMIT_WAITS)
    while True:
        try:
            resp = do_post()
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            if not transient_left:
                raise
            wait = transient_left.pop(0)
            print(f"    transient network error ({type(e).__name__}); retry in {wait}s")
            time.sleep(wait)
            continue
        if resp.status_code == 429:
            # Quota/billing exhaustion is not a rate limit — waiting won't fix
            # it. Fail fast with the provider's own message.
            if "insufficient_quota" in resp.text or "credit_balance" in resp.text:
                raise RuntimeError(f"quota exhausted (not retryable): {resp.text[:200]}")
            if not ratelimit_left:
                resp.raise_for_status()
            wait = ratelimit_left.pop(0)
            print(f"    429 rate-limited; waiting {wait}s")
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            if not transient_left:
                resp.raise_for_status()
            wait = transient_left.pop(0)
            print(f"    HTTP {resp.status_code}; retry in {wait}s")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
        return resp


# ---------- providers ---------------------------------------------------------
def gen_gemini(api_key: str, photo_bytes: bytes, mime: str, prompt: str,
               aspect: str) -> bytes:
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
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    resp = _post_with_retries(
        lambda: requests.post(GEMINI_URL, json=payload, headers=headers,
                              timeout=REQUEST_TIMEOUT))
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


def gen_openai(api_key: str, photo_bytes: bytes, mime: str, prompt: str,
               size: str) -> bytes:
    files = {"image[]": ("photo" + mimetypes.guess_extension(mime, strict=False)
                         or ".jpg", photo_bytes, mime)}
    form = {"model": "gpt-image-1", "prompt": prompt, "size": size,
            "quality": "medium", "n": "1"}
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = _post_with_retries(
        lambda: requests.post(OPENAI_URL, data=form, files=files, headers=headers,
                              timeout=REQUEST_TIMEOUT))
    data = resp.json()
    try:
        return base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError):
        raise RuntimeError(f"No image in response: {json.dumps(data)[:400]}")


# ---------- main --------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--photo", help="path to the source photo (jpg/png/webp)")
    ap.add_argument("--owner", help="owner slug, e.g. blaine — names output files")
    ap.add_argument("--styles", default="emperor",
                    help="comma-separated archetypes, or 'all' "
                         f"(available: {', '.join(STYLES)})")
    ap.add_argument("--n", type=int, default=2, help="iterations per style per provider")
    ap.add_argument("--provider", default="gemini",
                    choices=["gemini", "openai", "both"])
    ap.add_argument("--aspect", default="3:4",
                    help="gemini aspect (default 3:4 ≈ the site's 5:7 crop)")
    ap.add_argument("--size", default="1024x1536",
                    help="openai size (default 1024x1536 portrait)")
    ap.add_argument("--out", default=str(ROOT / "output" / "personas"))
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the output file exists (re-bills!)")
    ap.add_argument("--daily-warn", type=int, default=80)
    ap.add_argument("--list-styles", action="store_true")
    args = ap.parse_args()

    if args.list_styles:
        for name, prompt in STYLES.items():
            print(f"{name}:\n  {prompt}\n")
        return 0
    if not args.photo or not args.owner:
        ap.error("--photo and --owner are required (or use --list-styles)")

    load_dotenv(ROOT / ".env")
    keys = {
        "gemini": os.environ.get("GEMINI_API_KEY", "").strip(),
        "openai": os.environ.get("OPENAI_API_KEY", "").strip(),
    }
    providers = ["gemini", "openai"] if args.provider == "both" else [args.provider]
    missing = [p for p in providers if not keys[p]]
    if missing:
        names = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
        print("ERROR: missing key(s) in .env: "
              + ", ".join(names[p] for p in missing), file=sys.stderr)
        return 1

    photo = Path(args.photo)
    if not photo.exists():
        print(f"ERROR: photo not found: {photo}", file=sys.stderr)
        return 1
    mime = mimetypes.guess_type(photo.name)[0] or "image/jpeg"
    photo_bytes = photo.read_bytes()

    styles = list(STYLES) if args.styles == "all" else \
        [s.strip() for s in args.styles.split(",") if s.strip()]
    unknown = [s for s in styles if s not in STYLES]
    if unknown:
        print(f"ERROR: unknown style(s): {unknown}. Available: {', '.join(STYLES)}",
              file=sys.stderr)
        return 1

    out_dir = Path(args.out) / args.owner
    out_dir.mkdir(parents=True, exist_ok=True)

    budget = load_budget()
    made, skipped, failed = 0, 0, 0
    for style in styles:
        for provider in providers:
            for i in range(1, args.n + 1):
                out_file = out_dir / f"{args.owner}_{style}_{provider}_{i:02d}.png"
                if out_file.exists() and not args.force:
                    print(f"  skip (exists): {out_file.relative_to(ROOT)}")
                    skipped += 1
                    continue
                print(f"  {provider}: {style} #{i} …")
                total = bump_budget(budget, provider)
                if total > args.daily_warn:
                    print(f"  ::warning:: image-API daily tally {total} — past "
                          f"the {args.daily_warn} threshold.")
                try:
                    if provider == "gemini":
                        img = gen_gemini(keys["gemini"], photo_bytes, mime,
                                         STYLES[style], args.aspect)
                    else:
                        img = gen_openai(keys["openai"], photo_bytes, mime,
                                         STYLES[style], args.size)
                except Exception as e:
                    print(f"  FAILED {provider} {style} #{i}: {e}", file=sys.stderr)
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
