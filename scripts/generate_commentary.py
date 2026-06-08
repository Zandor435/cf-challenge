#!/usr/bin/env python3
"""
generate_commentary.py — Generate ESPN-parody commentary via GPT.

Reads narrative_state.json, produces hot takes in two formats:
1. Stateful anchor (Stephen A. Smith parody) — builds on itself weekly
2. Rotating analyst — different voice each week, stateless

Output (per league):
    site/data/<league>/commentary.json

Usage:
    python scripts/generate_commentary.py --league league-1
    python scripts/generate_commentary.py --league all
    python scripts/generate_commentary.py --league league-1 --dry-run
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    DATA_DIR, load_json, save_json, load_league_config,
    get_all_league_ids, league_site_data_dir
)

# --- ESPN Parody Voice Definitions ---

ANCHOR_SYSTEM_PROMPT = """You are a parody of Stephen A. Smith providing weekly hot takes for a college football fantasy league.

VOICE RULES:
- Dramatic pauses rendered as "..." and ALL CAPS for emphasis
- Start takes with "Now let me tell you something..." or "I've been SAYING this..."
- Reference previous takes when available — callbacks are gold
- 80% roast of specific owners' draft picks, 20% actual analysis
- Name-drop owners constantly — this is personal
- Use signature phrases: "BLASPHEMOUS", "STAY OFF THE WEED", "How DARE you"
- Maximum 250 words per take
- End with a bold prediction or ultimatum
- Never break character. You ARE this person.
"""

ROTATING_ANALYSTS = {
    "corso": {
        "name": "Not Lee Corso",
        "system": """You are a parody of Lee Corso on College GameDay providing fantasy league commentary.

VOICE RULES:
- Enthusiastic, grandfatherly, slightly unhinged energy
- End every take by "putting on the headgear" of the owner you're picking to win
- Use "Not so fast, my friend!" at least once
- Mix genuine football insight with absurd tangents
- Reference mascots and traditions heavily
- Maximum 200 words
""",
    },
    "herbstreit": {
        "name": "Not Kirk Herbstreit",
        "system": """You are a parody of Kirk Herbstreit providing fantasy league analysis.

VOICE RULES:
- Start measured and analytical, then get increasingly heated
- Use phrases like "And here's the thing that people don't understand..."
- Reference "watching the film" and "I talked to the coaches"
- Get emotional about good football — "That's just beautiful football right there"
- Transition from calm analysis to passionate rants mid-paragraph
- Maximum 200 words
""",
    },
    "mcafee": {
        "name": "Not Pat McAfee",
        "system": """You are a parody of Pat McAfee providing fantasy league commentary.

VOICE RULES:
- MAXIMUM ENERGY at all times
- Reference punting at least once — everything relates back to punting
- Use "FOR THE BRAND" and "STOMP THE SHMOTZ" (or similar made-up hype phrases)
- Interrupt yourself with tangents
- Call everyone by nicknames you just made up
- Use "BOYS" to address the audience
- Maximum 200 words
""",
    },
    "gameday_sign": {
        "name": "GameDay Sign Guy",
        "system": """You are College GameDay Sign Guy. You communicate ONLY through signs.

FORMAT RULES:
- Output 5-8 signs, one per line
- Each sign is short (3-8 words max), punchy, and roast-focused
- Signs reference specific owners and their draft picks
- Mix in pop culture references
- At least one sign should be self-deprecating or meta
- Format each sign in ALL CAPS between [SIGN] and [/SIGN] tags
- No paragraphs, no prose, SIGNS ONLY
""",
    },
    "desmond": {
        "name": "Not Desmond Howard",
        "system": """You are a parody of Desmond Howard providing fantasy league analysis.

VOICE RULES:
- Strike a Heisman pose (describe it) after every bold take
- Reference your own Heisman win at Michigan in every segment
- Compare every good performance to "what I did against Ohio State"
- Casually dismiss anyone who didn't pick a Big Ten team
- Maximum 200 words
""",
    },
}


def call_gpt(system_prompt, user_prompt, api_key):
    """Call OpenAI GPT-4o API."""
    url = "https://api.openai.com/v1/chat/completions"

    body = json.dumps({
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 600,
        "temperature": 0.9,
    }).encode()

    req = Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
    except HTTPError as e:
        print(f"  GPT API error {e.code}")
        return None


def build_user_prompt(state, previous_anchor=None):
    """Build the context prompt from narrative state."""
    lines = []
    lines.append(f"League: {state.get('league_name', 'Unknown')}")
    lines.append(f"Current Week: {state.get('current_week', '?')}")
    lines.append("")

    lines.append("=== STANDINGS ===")
    for o in state.get("owner_snapshots", []):
        rank_delta = o.get("rank_change", 0)
        delta_str = f"(↑{rank_delta})" if rank_delta > 0 else f"(↓{abs(rank_delta)})" if rank_delta < 0 else ""
        themes_str = ", ".join(o.get("themes", [])) or "none"
        streak = o.get("streak", {})
        streak_str = f"{streak.get('type', 'none')} x{streak.get('length', 0)}" if streak.get("type") else "none"

        lines.append(f"#{o.get('rank', '?')} {o.get('owner_name', '?')} {delta_str} — "
                      f"{o.get('total_points', 0)} pts | "
                      f"Streak: {streak_str} | "
                      f"T1 dependency: {o.get('dependency_index', 0):.0%} | "
                      f"Themes: {themes_str}")
        for t in o.get("teams", []):
            lines.append(f"    [{t.get('tier', '?')}] {t.get('team', '?')} — {t.get('points', 0)} pts ({t.get('record', '?')})")

    lines.append("")
    lines.append("=== WIN PROBABILITIES ===")
    for p in state.get("projections_summary", []):
        lines.append(f"  {p.get('owner_id', '?')}: {p.get('win_probability', 0):.1%} | "
                      f"projected median: {p.get('projected_median', 0)}")

    if state.get("notable_events"):
        lines.append("")
        lines.append("=== NOTABLE EVENTS ===")
        for e in state["notable_events"]:
            lines.append(f"  {e.get('type', '?')}: {e.get('owner', '?')} — {e.get('points', 0)} pts (week {e.get('week', '?')})")

    if previous_anchor:
        lines.append("")
        lines.append("=== YOUR PREVIOUS TAKE (build on this) ===")
        lines.append(previous_anchor)

    return "\n".join(lines)


def generate_for_league(league_id, dry_run=False):
    """Generate commentary for one league."""
    print(f"\n--- Commentary: {league_id} ---")

    config = load_league_config(league_id)
    out_dir = league_site_data_dir(league_id)

    # Load narrative state
    state_path = out_dir / "narrative_state.json"
    if not state_path.exists():
        print(f"  ⚠ No narrative_state.json — run build_narrative_state.py first")
        return
    state = load_json(state_path)

    # Load previous commentary (for anchor continuity)
    commentary_path = out_dir / "commentary.json"
    prev_commentary = load_json(commentary_path) if commentary_path.exists() else {}
    previous_anchor = prev_commentary.get("anchor", {}).get("content", "")

    # Build the user prompt
    user_prompt = build_user_prompt(state, previous_anchor)

    if dry_run:
        print("  [DRY RUN] Would send to GPT:")
        print(user_prompt[:500] + "...")
        return

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  ⚠ OPENAI_API_KEY not set — writing placeholder commentary")
        save_json(commentary_path, {
            "league_id": league_id,
            "week": state.get("current_week", 0),
            "anchor": {"voice": "Stephen A. (placeholder)", "content": "Commentary will generate when OPENAI_API_KEY is set."},
            "analyst": {"voice": "Analyst (placeholder)", "content": "Rotating analyst take will appear here."},
        })
        return

    # Generate anchor take (Stephen A.)
    print("  Generating anchor take...")
    anchor_content = call_gpt(ANCHOR_SYSTEM_PROMPT, user_prompt, api_key)

    # Pick rotating analyst for this week
    week = state.get("current_week", 0)
    analyst_keys = list(ROTATING_ANALYSTS.keys())
    analyst_key = analyst_keys[week % len(analyst_keys)]
    analyst = ROTATING_ANALYSTS[analyst_key]

    print(f"  Generating analyst take ({analyst['name']})...")
    analyst_content = call_gpt(analyst["system"], user_prompt, api_key)

    # Save
    commentary = {
        "league_id": league_id,
        "week": week,
        "anchor": {
            "voice": "Not Stephen A. Smith",
            "content": anchor_content or "Take generation failed.",
        },
        "analyst": {
            "voice": analyst["name"],
            "key": analyst_key,
            "content": analyst_content or "Take generation failed.",
        },
    }

    save_json(commentary_path, commentary)
    print(f"  ✓ Commentary generated for week {week}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without calling GPT")
    args = parser.parse_args()

    if args.league == "all":
        for lid in get_all_league_ids():
            generate_for_league(lid, args.dry_run)
    else:
        generate_for_league(args.league, args.dry_run)


if __name__ == "__main__":
    main()
