"""
Shared utilities for the CFB Fantasy League pipeline.
Handles config loading, team name normalization, and common helpers.
"""

import json
import os
import sys
from pathlib import Path

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
    with open(p) as f:
        return json.load(f)


def save_json(path, data, indent=2):
    """Write JSON file, creating parent dirs if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=indent)
    print(f"  → Wrote {p}")


def load_league_config(league_id):
    """Load a league's config.json."""
    return load_json(LEAGUES_DIR / league_id / "config.json")


def load_scoring_config(league_id=None):
    """Load scoring config, applying league-specific overrides if any."""
    base = load_json(DATA_DIR / "scoring_config.json")
    if league_id:
        league = load_league_config(league_id)
        overrides = league.get("scoring_overrides", {})
        # Shallow merge — section-level override
        for section, values in overrides.items():
            if section.startswith("_"):
                continue
            if section in base and isinstance(base[section], dict):
                base[section].update(values)
            else:
                base[section] = values
    return base


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
    aliases = _load_aliases()
    return aliases.get(name, name)


def get_draft_board(league_id):
    """
    Return the draft board as a dict: { canonical_team_name: { owner, tier } }
    Skips placeholder entries.
    """
    config = load_league_config(league_id)
    board = config.get("draft_board", {})
    return {
        normalize_team(team): info
        for team, info in board.items()
        if not team.startswith("_") and team != "example_team"
    }


def get_owners(league_id):
    """Return list of owner dicts from league config."""
    config = load_league_config(league_id)
    return config.get("owners", [])
