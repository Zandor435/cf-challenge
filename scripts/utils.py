#!/usr/bin/env python3
"""
utils.py — Shared plumbing for the CF Challenge pipeline (ARCHITECTURE §5, §8, §9).

Config-driven I/O (nothing hardcoded), a tiny .env loader (no hard dependency on
python-dotenv), atomic JSON writes, and the team-name resolver that is the
load-bearing fetch->score gate (§9). The resolver normalizes on lookup and:
  - raises AmbiguityError for dangerously ambiguous bare tokens (USC, Miami),
    never resolving them silently,
  - resolves known variants via data/team_aliases.json (targets are canonical),
  - resolves exact/normalized canonical names from data/teams_canonical.json,
  - raises UnknownTeamError otherwise (a silent miss mis-scores a pick).
"""

import difflib
import json
import os
import sys
import unicodedata
from pathlib import Path

# Keep console arrows/checkmarks from crashing on Windows cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
GROUPS_DIR = ROOT / "groups"
SITE_DATA_DIR = ROOT / "site" / "data"

CANONICAL_PATH = DATA_DIR / "teams_canonical.json"
ALIASES_PATH = DATA_DIR / "team_aliases.json"
AMBIGUOUS_PATH = DATA_DIR / "ambiguous.json"
CACHE_PATH = DATA_DIR / "cfbd_cache.json"

# Curated human-disambiguation cases. Keyed by normalized form (§9). Values are
# the strings that ACTUALLY resolve via resolve_team() — never the ambiguous
# token itself. This BASE is merged at runtime with the mechanically-generated
# collisions written to data/ambiguous.json (acronyms like "UM" that map to 2+
# teams) — see load_ambiguous(). (test_resolver.py asserts each candidate resolves.)
AMBIGUOUS_BASE = {
    "usc": ["Southern California", "South Carolina"],
    "miami": ["Miami (FL)", "Miami (OH)"],
}


# --- JSON I/O ---------------------------------------------------------------

def load_json(path):
    p = Path(path)
    if not p.exists():
        print(f"ERROR: {p} not found")
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data, indent=2):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    print(f"  -> wrote {p}")


def save_json_atomic(path, data, indent=2):
    """Write to <path>.tmp then os.replace — never leave a half-written file,
    and never clobber a good file with a partial write (§4 / playbook rule 5)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, p)  # atomic on same filesystem
    print(f"  -> wrote {p} (atomic)")


# --- Cache access (centralized — §4 season guard) ---------------------------
# ALL access to the cache goes through these helpers. Nothing else in the repo
# may read or write it directly (enforced mechanically by test_cache_access.py),
# so a consumer cannot bypass the season assertion via a docstring it ignored.

class SeasonMismatchError(Exception):
    """The cache is tagged for a different season than the caller requested.
    A stale-season fallback would produce a clean, plausible, wrong board —
    so this is fatal: fail loud, never score against it."""
    def __init__(self, expected, found):
        self.expected = expected
        self.found = found
        super().__init__(
            f"cache season mismatch: requested {expected} but the cache is tagged "
            f"season {found}. Refusing to use a wrong-season cache."
        )


def cache_exists(path=None):
    return (Path(path) if path else CACHE_PATH).exists()


def peek_cache(path=None):
    """Raw cache load, NO season assertion. Only for the fetch fallback (which
    must read the season tag before it knows whether it matches) and for tests.
    Code that computes standings/projections MUST use load_cache(season)."""
    return load_json(path or CACHE_PATH)


def load_cache(expected_season, path=None):
    """The ONLY sanctioned cache loader for scoring/projection. Asserts the
    cache's season tag matches before returning (§4), so a stale-season cache
    can never be silently scored. scoring.py / projector.py MUST use this."""
    cache = peek_cache(path)
    found = cache.get("season")
    if found != expected_season:
        raise SeasonMismatchError(expected_season, found)
    return cache


def save_cache(cache, path=None):
    """Atomic cache write — never clobbers a good cache with a partial write."""
    save_json_atomic(path or CACHE_PATH, cache)


# --- .env / secrets ---------------------------------------------------------

def load_env_file(path=None):
    """Minimal KEY=VALUE .env parser. No dependency on python-dotenv so a fresh
    checkout works. Existing os.environ values win (CI passes the secret in)."""
    p = Path(path) if path else (ROOT / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def get_api_key():
    """CFB_API_KEY — must match the GitHub Actions secret name so §10.6 works."""
    load_env_file()
    key = os.environ.get("CFB_API_KEY", "").strip()
    if not key:
        print("ERROR: CFB_API_KEY not set (env or .env).")
        print("  Get a free key at https://collegefootballdata.com/key")
        sys.exit(1)
    return key


# --- Team-name resolution (the §9 gate) -------------------------------------

class TeamNameError(Exception):
    pass


class AmbiguityError(TeamNameError):
    def __init__(self, token, candidates):
        self.token = token
        self.candidates = candidates
        super().__init__(
            f"ambiguous team name '{token}' — could be: {', '.join(candidates)}. "
            f"Enter a disambiguated name."
        )


class UnknownTeamError(TeamNameError):
    def __init__(self, name, normalized, suggestions=None):
        self.name = name
        self.normalized = normalized
        self.suggestions = suggestions or []
        msg = f"unknown team '{name}' (normalized '{normalized}')"
        if self.suggestions:
            msg += f" — did you mean: {', '.join(self.suggestions)}?"
        else:
            msg += " — not in team_aliases.json or teams_canonical.json."
        super().__init__(msg)


def normalize_team_name(name):
    """Lowercase, strip diacritics, drop everything that isn't a letter/digit."""
    if name is None:
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))  # strip diacritics
    s = s.lower()
    return "".join(c for c in s if c.isalnum())  # drop punctuation/whitespace


_CANONICAL = None       # {normalized_key: canonical_school}
_ALIASES = None         # {normalized_variant_key: canonical_school}
_AMBIGUOUS = None       # {normalized_key: [candidate canonical strings]}


def load_canonical():
    """{normalized -> canonical school string} from teams_canonical.json."""
    global _CANONICAL
    if _CANONICAL is None:
        raw = load_json(CANONICAL_PATH)
        teams = raw["teams"] if isinstance(raw, dict) else raw
        _CANONICAL = {normalize_team_name(t["school"]): t["school"] for t in teams}
    return _CANONICAL


def load_aliases():
    """{normalized variant -> canonical school} from team_aliases.json."""
    global _ALIASES
    if _ALIASES is None:
        _ALIASES = {}
        if ALIASES_PATH.exists():
            raw = load_json(ALIASES_PATH)
            for k, v in raw.items():
                if k.startswith("_"):
                    continue
                _ALIASES[normalize_team_name(k)] = v
    return _ALIASES


def load_ambiguous():
    """Merged ambiguity map: curated AMBIGUOUS_BASE + the mechanically-generated
    collisions in data/ambiguous.json (acronyms that map to 2+ teams). Keyed by
    normalized token -> [candidate canonical strings]."""
    global _AMBIGUOUS
    if _AMBIGUOUS is None:
        _AMBIGUOUS = dict(AMBIGUOUS_BASE)
        if AMBIGUOUS_PATH.exists():
            raw = load_json(AMBIGUOUS_PATH)
            for k, cands in raw.items():
                if k.startswith("_"):
                    continue
                _AMBIGUOUS.setdefault(normalize_team_name(k), cands)
    return _AMBIGUOUS


def _is_subsequence(short, long):
    it = iter(long)
    return all(c in it for c in short)


def suggest_canonical(name, n=3):
    """Top-N nearest canonical strings to `name` (fuzzy) — the self-correcting
    hint when a hand-entered pick doesn't resolve. Combines difflib similarity
    with acronym signals so an acronym hidden in a full name is surfaced (e.g.
    'Brigham Young' -> 'BYU'), which plain edit distance would miss."""
    key = normalize_team_name(name)
    inits = normalize_team_name("".join(w[:1] for w in str(name).split()))
    scored = []
    for ck, cname in load_canonical().items():
        score = difflib.SequenceMatcher(None, key, ck).ratio()
        is_acronym = "".join(c for c in cname if c.isalpha()).isupper()
        if is_acronym and _is_subsequence(ck, key):        # BYU ⊂ brighamyoung
            score += 0.6
        if is_acronym and inits and (ck.startswith(inits) or inits.startswith(ck)):
            score += 0.4                                    # typed initials ~ the acronym
        scored.append((score, cname))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [cname for _, cname in scored[:n]]


def resolve_team(name):
    """HUMAN-INPUT resolver — strict, ambiguity-guarded. For hand-entered picks
    only (validate_team_names.py). Bare 'USC'/'Miami' RAISE here on purpose.
    Order (§9): ambiguity guard -> aliases -> canonical -> unknown-with-suggestions.

    Internal code that already holds a canonical string (cache, scoring,
    projector) must use resolve_canonical() — it has no ambiguity logic, so
    'USC' and 'Miami' (both legitimate canonical names) resolve there."""
    key = normalize_team_name(name)
    ambiguous = load_ambiguous()
    if key in ambiguous:
        raise AmbiguityError(name, ambiguous[key])
    aliases = load_aliases()
    if key in aliases:
        return aliases[key]
    canonical = load_canonical()
    if key in canonical:
        return canonical[key]
    raise UnknownTeamError(name, key, suggest_canonical(name))


def resolve_canonical(name):
    """INTERNAL exact-match resolver — NO ambiguity guard. Accepts any canonical
    CFBD string (all 136 pass, including 'USC' and 'Miami') and returns it. For
    code reading already-canonical names off the cache. Raises UnknownTeamError
    if the name isn't a known canonical team. Human picks use resolve_team()."""
    key = normalize_team_name(name)
    canonical = load_canonical()
    if key in canonical:
        return canonical[key]
    raise UnknownTeamError(name, key)


# --- Groups (multi-tenant, §5) ---------------------------------------------

def get_all_group_ids():
    if not GROUPS_DIR.exists():
        return []
    return sorted(
        d.name for d in GROUPS_DIR.iterdir()
        if d.is_dir() and (d / "config.json").exists()
    )


def load_group_config(group_id):
    return load_json(GROUPS_DIR / group_id / "config.json")


def load_group_picks(group_id):
    return load_json(GROUPS_DIR / group_id / "picks.json")
