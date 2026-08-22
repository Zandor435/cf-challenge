#!/usr/bin/env python3
"""
generate_commentary.py — The one pundit (ARCHITECTURE §8 ADAPT, build order §10.8).

Job: narrative garnish only — LLMs NEVER touch scoring (§2). This script does no
arithmetic. It assembles a prompt out of three already-written artifacts and, in
live mode, posts it to OpenAI:

  templates/svp_persona.md                        the voice (system prompt)
  groups/<group>/output/week_packet.json          the week's numbers + storylines
  groups/<group>/output/column_memory.json        season-long continuity
  groups/<group>/output/column_week_<N>.md        last week's column, for callbacks

Persona: DECIDED — a Scott Van Pelt parody, a SINGLE pinned voice across all
three groups (ARCHITECTURE §12, landed 2026-07-27). Rome / Herbstreit / Berman
were dropped; do not reopen. The voice lives in the template and is passed
verbatim — this script composes, it does not characterize.

FAIL-SOFT IS THE POINT (playbook rule 3, ARCHITECTURE §4). Commentary is the
last and least important step in the pipeline. In live mode ANY failure — no key,
no packet, a dead API, a malformed response — logs a ::warning:: and exits 0, so
a missing column can never take down standings, the site deploy, or the rest of
the run. Note that utils.load_json() calls sys.exit(1) on a missing file, which
raises SystemExit, NOT Exception — so the live guard catches both, and the
loaders below raise instead of exiting.

--dry-run is developer-facing and does the opposite: it builds the complete
prompt, writes it to groups/<group>/output/commentary_prompt_preview.txt, makes
NO network call, and is allowed to exit non-zero so a broken prompt is loud.

Usage:
    python scripts/generate_commentary.py --group panel --dry-run   # no network
    python scripts/generate_commentary.py --group panel             # live (needs key)
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils

# --- Knobs -------------------------------------------------------------------

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
TEMPERATURE = 0.8          # a column, not a report
MAX_TOKENS = 1200          # ~400 words with headroom
REQUEST_TIMEOUT = 60

# Two distinct retry loops, because they are different failure modes
# (playbook rule 2). Transient network errors back off fast; a rate limit has to
# wait out the provider's window.
NETWORK_BACKOFF = (5, 10, 20)
RATE_LIMIT_WAIT = 60
RATE_LIMIT_RETRIES = 2

# How many filed columns to remember. The pundit only reads the newest one; the
# list is continuity bookkeeping, not a corpus.
MEMORY_COLUMNS_KEPT = 8

PERSONA_PATH = utils.ROOT / "templates" / "svp_persona.md"


# --- Paths -------------------------------------------------------------------

def out_dir(group_id):
    return utils.GROUPS_DIR / group_id / "output"


def packet_path(group_id):
    return out_dir(group_id) / "week_packet.json"


def memory_path(group_id):
    return out_dir(group_id) / "column_memory.json"


def preview_path(group_id):
    return out_dir(group_id) / "commentary_prompt_preview.txt"


def column_path(group_id, week):
    return out_dir(group_id) / f"column_week_{week}.md"


# --- Loaders (raise; never sys.exit — the live guard needs a catchable error) --

def read_text(path, what):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{what} not found: {p}")
    return p.read_text(encoding="utf-8")


def read_json(path, what):
    return json.loads(read_text(path, what))


def empty_memory(group_id):
    """First-run shape. `columns` is the ONLY key this script ever writes.

    nicknames / feuds / character_bits are CURATED BY HAND — deliberately no
    LLM-based extraction. A model inventing its own continuity is how a pundit
    starts citing a feud that never happened."""
    return {
        "group_id": group_id,
        "_note": ("Hand-curated season continuity. generate_commentary.py only "
                  "ever appends to `columns`; nicknames/feuds/character_bits are "
                  "edited by a human. Nicknames must be earned by behavior in a "
                  "filed column before they are added here."),
        "nicknames": {},
        "feuds": [],
        "character_bits": {},
        "columns": [],
    }


def load_memory(group_id):
    p = memory_path(group_id)
    if not p.exists():
        return empty_memory(group_id)
    mem = json.loads(p.read_text(encoding="utf-8"))
    for key, default in (("nicknames", {}), ("feuds", []),
                         ("character_bits", {}), ("columns", [])):
        mem.setdefault(key, default)
    return mem


def last_column_text(group_id, memory):
    """The most recently filed column, for callbacks. Missing file is not an
    error — a deleted or not-yet-written column just means no callback."""
    for name in reversed(memory.get("columns", [])):
        p = out_dir(group_id) / name
        if p.exists():
            return name, p.read_text(encoding="utf-8")
    return None, None


# --- Prompt assembly ---------------------------------------------------------

def build_prompt(group_id):
    """Returns (system_text, user_text, packet). Shared by both modes so the
    dry-run preview is the real prompt, not an approximation of it."""
    system_text = read_text(PERSONA_PATH, "persona template")
    packet = read_json(packet_path(group_id),
                       "week packet (run build_week_packet.py first)")
    memory = load_memory(group_id)
    last_name, last_text = last_column_text(group_id, memory)

    cmp_ = packet.get("comparison", {})
    basis = cmp_.get("basis", "unknown basis")
    elapsed = cmp_.get("weeks_elapsed")

    # The packet's movement fields are named *_this_week but measure the gap to
    # the previous snapshot. If that gap is not one week, say so in the prompt —
    # otherwise the column compresses a multi-week move into one Saturday, which
    # is a false claim built out of true numbers.
    if elapsed is None:
        basis_warning = (
            "There is NO prior snapshot, so every *_this_week field is null. Do "
            "not describe week-over-week movement at all; write from the "
            "standings and the picks as they stand.")
    elif elapsed == 1:
        basis_warning = "Movement fields cover exactly one week. 'This week' is literal."
    else:
        basis_warning = (
            f"IMPORTANT: the fields named *_this_week actually measure movement "
            f"{basis} — {elapsed} weeks, not one. Do not call that 'this week'. "
            f"Say '{basis}' or 'over the last {elapsed} weeks'.")

    stakes = packet.get("stakes")
    stakes_line = (f"Stakes for this group: {stakes}. You may reference them by "
                   f"their actual terms." if stakes else
                   "This group has no declared stakes. Do not invent any.")

    # The packet publishes season_complete, but a bool buried in 12KB of JSON is
    # not an instruction — the same reason basis_warning and stakes_line exist as
    # prose rather than trusting the model to read the field. A finished season
    # read as a live one produces forward-looking sign-offs ("only time and more
    # Saturdays will tell") against a board where every pick has already
    # resolved, which is a false claim assembled out of true numbers.
    season_over = packet.get("season_complete")
    if season_over is True:
        season_line = (
            "THE SEASON IS OVER — every game on the schedule has been played and "
            "nothing is left to decide. Do not write forward-looking prose: no "
            "'more Saturdays to come', no 'time will tell', no looking ahead to "
            "next week or to a rematch. This is the last word on a finished "
            "season, so write it in the past tense and settle it.")
    elif season_over is False:
        season_line = ("The season is still running — games remain to be played, "
                       "and picks that are still LIVE can still move.")
    else:
        season_line = ("Whether the season is finished is UNKNOWN (this packet "
                       "predates the season_complete field). Do not claim either "
                       "way, in either direction.")

    # Same reasoning as season_line: a dict buried in 12KB of JSON is not an
    # instruction. Week 16 the column read picks_alive: 0 off ONE profile and
    # filed "the only manager with no live picks" -- true number, fabricated
    # exclusivity, on a board where all four were zero (persona sacred rule 7).
    uniform = packet.get("uniform_profile_fields") or {}
    if uniform:
        listed = ", ".join(f"{k} ({v:g})" if isinstance(v, (int, float))
                           and not isinstance(v, bool) else f"{k} ({v})"
                           for k, v in sorted(uniform.items()))
        uniform_line = (
            f"THESE manager_profiles FIELDS DISTINGUISH NOBODY this week — every "
            f"manager shares the same value: {listed}. Do not write any of them "
            f"as a trait that sets someone apart: no 'the only one who', 'nobody "
            f"else', 'more than anyone', 'the first to'. You may still state such "
            f"a value as a fact about the whole group.")
    else:
        uniform_line = ("Every manager_profiles field varies across the group "
                        "this week, so any of them may support a comparison — "
                        "provided the numbers actually back it.")

    parts = [
        f"GROUP: {group_id}    WEEK: {packet.get('week')}    "
        f"SEASON: {packet.get('season')}",
        f"BASIS FOR MOVEMENT: {basis}",
        basis_warning,
        season_line,
        uniform_line,
        stakes_line,
        "",
        "=== COLUMN MEMORY (season continuity — established nicknames, feuds, "
        "and character bits. Reuse what is here; coin nothing new unless this "
        "week's packet earns it.) ===",
        json.dumps({k: memory.get(k) for k in
                    ("nicknames", "feuds", "character_bits")},
                   indent=2, ensure_ascii=False),
        "",
        "=== LAST PUBLISHED COLUMN ===",
        (f"({last_name})\n\n{last_text}" if last_text else
         "(none — this is the first column of the season. No callbacks yet.)"),
        "",
        "=== WEEK PACKET (every number you print must appear here, verbatim) ===",
        json.dumps(packet, indent=2, ensure_ascii=False),
        "",
        "=== YOUR ASSIGNMENT ===",
        "File this week's column now, following the template exactly:",
        "  Beat 1 — One Big Thing (~250-300 words), built from the HIGHEST-RANKED "
        "storyline in the packet that you can tell as one story. The packet's "
        "storylines are pre-ranked; prefer a feud over a collapse over an irony "
        "over a heater when scores are close.",
        "  Beat 2 — Bad Beat of the Week (~75-100 words), from "
        "bad_beat_candidates. The `how_it_died` text is limited to final scores — "
        "the pipeline has no play-by-play, so do NOT invent drives, onside kicks, "
        "or clock situations that are not in the packet.",
        "  End with the sign-off verbatim.",
        "",
        "Prose only — no headings, no lists, no bullets. Roughly 400 words total. "
        "Every number verbatim from the packet; if a number you want is not "
        "there, write around it. Character roasts must cite behavior visible in "
        "manager_profiles, not invented history.",
        "Return ONLY the column text. No preamble, no title, no explanation.",
    ]
    return system_text, "\n".join(parts), packet


# --- Live call (raw urllib, no SDK) ------------------------------------------

def call_openai(system_text, user_text, api_key, model=None, temperature=None,
                max_tokens=None, response_format=None):
    """POST to the chat-completions endpoint with the two retry loops rule 2
    prescribes. Re-raises after the last attempt — the caller's fail-soft guard
    decides what a persistent outage means (here: warn and exit 0).

    The optional arguments exist so a second caller can reuse this retry shell
    rather than growing a fourth copy of it (scripts/derive_style.py, which
    needs a vision message, a lower temperature and a JSON response format).
    Every one defaults to the column's own settings, so the commentary call
    site is unchanged. `user_text` may be a plain string or an already-built
    list of content parts — the vision API's shape.
    """
    body = json.dumps({
        "model": model or OPENAI_MODEL,
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens or MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        **({"response_format": response_format} if response_format else {}),
    }).encode("utf-8")

    rate_limit_attempts = 0
    for attempt in range(len(NETWORK_BACKOFF) + 1):
        req = urllib.request.Request(
            OPENAI_URL, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            # Rate limit: wait the provider's window, on its own budget.
            if e.code == 429 and rate_limit_attempts < RATE_LIMIT_RETRIES:
                rate_limit_attempts += 1
                print(f"::warning:: OpenAI 429 (rate limited); waiting "
                      f"{RATE_LIMIT_WAIT}s (attempt {rate_limit_attempts}/"
                      f"{RATE_LIMIT_RETRIES}).", file=sys.stderr)
                time.sleep(RATE_LIMIT_WAIT)
                continue
            if e.code < 500 or attempt == len(NETWORK_BACKOFF):
                raise                      # 4xx is our bug; last 5xx re-raises
            _backoff(attempt, f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == len(NETWORK_BACKOFF):
                raise                      # persistent outage: fail loud upward
            _backoff(attempt, type(e).__name__)
    raise RuntimeError("unreachable")      # loop always returns or raises


def _backoff(attempt, why):
    wait = NETWORK_BACKOFF[attempt]
    print(f"::warning:: OpenAI call failed ({why}); retrying in {wait}s "
          f"({attempt + 1}/{len(NETWORK_BACKOFF)}).", file=sys.stderr)
    time.sleep(wait)


# --- Modes -------------------------------------------------------------------

def run_dry(group_id):
    system_text, user_text, packet = build_prompt(group_id)
    preview = "\n".join([
        "=" * 78,
        f"COMMENTARY PROMPT PREVIEW — group {group_id}, week {packet.get('week')}",
        f"model={OPENAI_MODEL}  temperature={TEMPERATURE}  max_tokens={MAX_TOKENS}",
        "DRY RUN — no network call was made. This is the exact prompt live mode "
        "would post.",
        "=" * 78,
        "",
        "----- SYSTEM (templates/svp_persona.md) " + "-" * 39,
        system_text,
        "",
        "----- USER " + "-" * 67,
        user_text,
        "",
        "=" * 78,
        f"END PREVIEW — system {len(system_text)} chars, user {len(user_text)} chars",
        "=" * 78,
        "",
    ])
    path = preview_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(preview, encoding="utf-8")
    print(f"  -> wrote {path}")
    print(f"  [{group_id}] dry run: week {packet.get('week')}, "
          f"{len(packet.get('storylines', []))} storyline(s), "
          f"{len(packet.get('bad_beat_candidates', []))} bad beat(s), "
          f"prompt {len(system_text) + len(user_text)} chars. No network call.")
    return 0


def run_live(group_id):
    utils.load_env_file()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set (env or .env)")

    system_text, user_text, packet = build_prompt(group_id)
    week = packet.get("week")
    column = call_openai(system_text, user_text, api_key)
    if not column:
        raise RuntimeError("OpenAI returned an empty column")

    path = column_path(group_id, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(column + "\n", encoding="utf-8")
    print(f"  -> wrote {path}")

    # Append to memory LAST, and only the filename — the curated keys are
    # round-tripped untouched.
    memory = load_memory(group_id)
    cols = [c for c in memory.get("columns", []) if c != path.name]
    cols.append(path.name)
    memory["columns"] = cols[-MEMORY_COLUMNS_KEPT:]
    utils.save_json_atomic(memory_path(group_id), memory)
    print(f"  [{group_id}] filed column_week_{week}.md ({len(column.split())} words)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Write the weekly SVP column")
    ap.add_argument("--group", required=True, help="group slug (panel/family/church)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the prompt and write the preview; NO network call")
    args = ap.parse_args()

    if args.dry_run:
        # Developer-facing: let it fail loudly.
        return run_dry(args.group)

    # Live: commentary is garnish and must never block the pipeline. SystemExit
    # is caught too — utils.load_json() exits rather than raising.
    try:
        return run_live(args.group)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — deliberate catch-all
        print(f"::warning:: [{args.group}] commentary FAILED "
              f"({type(e).__name__}: {e}); continuing. Standings and the rest of "
              f"the pipeline are unaffected (playbook rule 3).", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
