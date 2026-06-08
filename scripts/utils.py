"""
Shared utilities for the CF Challenge pipeline.

The game: each owner picks 4 CFB teams (each from a different conference) against a
preseason win-total line, declaring OVER or UNDER. Scoring is delta-based. See
docs/scoring-spec.md for the full spec.
"""

import json
import sys
from pathlib import Path

# Keep console logging (arrows, check marks) from crashing on Windows cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Project root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LEAGUES_DIR = ROOT / "leagues"
SITE_DATA_DIR = ROOT / "site" / "data"


def load_json(path):
    """Load a JSON file, exit with message if missing."""
    p = Path(path)
    if not p.exists():
        print(f"ERROR: {p} not found")
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data, indent=2):
    """Write JSON file, creating parent dirs if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)
    print(f"  → Wrote {p}")


def load_league_config(league_id):
    """Load a league's config.json."""
    return load_json(LEAGUES_DIR / league_id / "config.json")


def get_all_league_ids():
    """Return list of league IDs from the leagues/ directory."""
    if not LEAGUES_DIR.exists():
        return []
    return sorted([
        d.name for d in LEAGUES_DIR.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    ])


def league_site_data_dir(league_id):
    """Return the site/data/<league_id>/ output path, creating it if needed."""
    d = SITE_DATA_DIR / league_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_owners(league_id):
    """Return list of owner dicts from league config."""
    config = load_league_config(league_id)
    return config.get("owners", [])


# --- Team name normalization ---

_ALIASES = None


def _load_aliases():
    global _ALIASES
    if _ALIASES is None:
        alias_path = DATA_DIR / "team_aliases.json"
        if alias_path.exists():
            raw = load_json(alias_path)
            _ALIASES = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            _ALIASES = {}
    return _ALIASES


def normalize_team(name):
    """Normalize a team name using the aliases map."""
    if not name:
        return name
    aliases = _load_aliases()
    return aliases.get(name, name)


# --- Picks ---

def _clean_picks(raw_picks):
    """
    Filter and normalize a raw picks array.
    Drops note/placeholder entries (no owner/team, or marked todo) and
    normalizes team names to canonical form.
    """
    cleaned = []
    for p in raw_picks or []:
        if not isinstance(p, dict):
            continue
        if "owner" not in p or "team" not in p:
            continue
        if p.get("todo"):
            continue
        cleaned.append({
            "owner": p["owner"],
            "team": normalize_team(p["team"]),
            "conference": p.get("conference", ""),
            "side": (p.get("side") or "").lower(),
            "line": float(p.get("line", 0)),
        })
    return cleaned


def get_picks(league_id, test=False):
    """
    Return the league's picks as a list of normalized dicts:
        { owner, team, conference, side, line }

    If test=True, load data/test_picks.json instead (realistic picks for
    end-to-end testing against a completed season). Placeholder/TODO entries
    in the league config are skipped.
    """
    if test:
        test_path = DATA_DIR / "test_picks.json"
        if not test_path.exists():
            print(f"  ⚠ --test set but {test_path} not found")
            return []
        return _clean_picks(load_json(test_path).get("picks", []))

    config = load_league_config(league_id)
    return _clean_picks(config.get("picks", []))


def picks_by_owner(picks):
    """Group a picks list into { owner_id: [pick, ...] }."""
    grouped = {}
    for p in picks:
        grouped.setdefault(p["owner"], []).append(p)
    return grouped


def validate_picks(picks):
    """
    Validate picks against the game rules. Returns a list of human-readable
    warning strings (empty = valid). Non-fatal — callers decide what to do.
    """
    warnings = []
    by_owner = picks_by_owner(picks)

    for owner, owner_picks in by_owner.items():
        if len(owner_picks) != 4:
            warnings.append(f"{owner} has {len(owner_picks)} picks (expected 4)")
        confs = [p["conference"] for p in owner_picks]
        if len(set(confs)) != len(confs):
            warnings.append(f"{owner} has duplicate conferences: {confs}")
        for p in owner_picks:
            if p["side"] not in ("over", "under"):
                warnings.append(f"{owner}/{p['team']} has invalid side '{p['side']}'")

    # Same team taken by multiple owners must be opposite sides
    team_sides = {}
    for p in picks:
        team_sides.setdefault(p["team"], []).append((p["owner"], p["side"]))
    for team, entries in team_sides.items():
        sides = {s for _, s in entries}
        if len(entries) > 1 and len(sides) < 2:
            warnings.append(f"{team} taken by multiple owners on the same side: {entries}")

    return warnings
