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
SEASON_PATH = ROOT / "season.json"


# --- Season: single source of truth (ARCHITECTURE §4/§6) --------------------
# season lives ONLY in season.json — never in a group config or a script
# literal. `season` is what every group scores; `cfbd_default_season` is the
# fetch/build default. Both ints. The §6 guard asserts season == cache season.

_SEASON_CONF = None


def load_season_config():
    global _SEASON_CONF
    if _SEASON_CONF is None:
        _SEASON_CONF = load_json(SEASON_PATH)
    return _SEASON_CONF


def get_season():
    """The season every group scores (int). Single source: season.json."""
    return int(load_season_config()["season"])


def get_cfbd_default_season():
    """Default season for fetch/build scripts (int). Single source: season.json."""
    return int(load_season_config()["cfbd_default_season"])

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


# --- Schedule counts off the neutral cache (§1 conf-championship rule) -------

def counts_conference_championship(group_config):
    """Per-group rule (§1/§5): do conference-championship wins settle into the
    season win total? Sportsbooks differ, so it's league config, not a code
    constant. Default False (ARCHITECTURE §1) when the key is absent."""
    return bool((group_config or {}).get("count_conference_championship", False))


def count_scheduled_games(games, count_conference_championship=False):
    """Per-team scheduled regular-season game count off the shared (neutral)
    cache. The cache tags conference-championship games (per-game
    `conference_championship`) but excludes nothing — filtering is THIS
    scoring-layer decision, gated per group by count_conference_championship
    (§1/§5, default False). With the flag off, every FBS team lands at 12; with
    it on, the 18 title-game participants land at 13.

    games_remaining is always derived from this real slate (12 or 13), never a
    hardcoded constant (§1). scoring.py / projector.py call this per group."""
    counts = {}
    for g in games:
        if not count_conference_championship and g.get("conference_championship"):
            continue
        for side in (g.get("home_team"), g.get("away_team")):
            if side:
                counts[side] = counts.get(side, 0) + 1
    return counts


# --- Team state: the flag-aware single source (ARCHITECTURE §1/§5, §7) -------
# team_state() is the ONLY sanctioned way for a scorer to read a team's banked
# W/L and remaining schedule. It honors count_conference_championship AND the
# --as-of-week replay, computing the banked side and the schedule side off the
# SAME slate so the two boards can never disagree on what is still to play.
#
# The raw per-team index fetch_results.build_team_index writes into the cache
# (cache["teams"][t] = {"wins","losses","games_played",...}) is FLAG-BLIND and
# as-of-blind: it counts every game (conf-title games included) as of the fetch.
# Reading it in a scorer would silently mis-count. test_cache_access.py enforces
# that nothing outside utils.py reads those raw banked fields (same AST pattern
# as the cache guard).

_SEASON_CACHE = {}      # season -> parsed, season-guarded cache
_CANONICAL_FULL = None  # normalized key -> full canonical team dict


def load_canonical_full():
    """{normalized -> full canonical team dict} from teams_canonical.json."""
    global _CANONICAL_FULL
    if _CANONICAL_FULL is None:
        raw = load_json(CANONICAL_PATH)
        teams = raw["teams"] if isinstance(raw, dict) else raw
        _CANONICAL_FULL = {normalize_team_name(t["school"]): t for t in teams}
    return _CANONICAL_FULL


def canonical_conference(team):
    """Conference of a canonical team per teams_canonical.json. Resolves `team`
    to its canonical school first (exact, no ambiguity guard). Raises
    UnknownTeamError if `team` is not an FBS canonical team."""
    school = resolve_canonical(team)
    return load_canonical_full()[normalize_team_name(school)].get("conference")


def _season_cache(season):
    """Memoized, season-guarded full cache for `season`. Loads via load_cache(),
    so a cache tagged for a different season raises SeasonMismatchError (§4)
    rather than silently returning the wrong season's data. Centralizing cache
    reads here keeps the scorers off the raw cache (test_cache_access.py guard)."""
    if season not in _SEASON_CACHE:
        _SEASON_CACHE[season] = load_cache(season)
    return _SEASON_CACHE[season]


def _season_games(season):
    return _season_cache(season)["games"]


def season_games(season):
    """Season-guarded games list (public accessor for analysis tools like
    calibrate.py). Reads through utils so the cache guard stays satisfied."""
    return _season_games(season)


def season_sp_ratings(season):
    """SP+ ratings map {team: {rating, ranking, offense, defense}} for the
    season-guarded cache. The sanctioned way for projector.py to read SP+."""
    return _season_cache(season)["sp_ratings"]


def season_fpi_ratings(season):
    """FPI ratings map {team: {fpi, ...}} for the season-guarded cache (may be
    empty if a fetch predates the FPI column). Secondary rating for calibration
    comparison — the live projection spine is SP+ (ARCHITECTURE §4)."""
    return _season_cache(season).get("fpi_ratings", {})


def cache_meta(season):
    """Freshness/identity block off the season-guarded cache (fetched_at, week,
    season) — for the output `meta` blocks. No raw banked-index fields."""
    c = _season_cache(season)
    return {"fetched_at": c.get("fetched_at"), "week": c.get("week"),
            "season": c.get("season")}


def _game_played(g, as_of_week):
    """A slate game counts as PLAYED iff it is completed AND (no as-of-week
    replay, or its week is within the replay horizon). --as-of-week N treats
    games after week N as not-yet-played (§7)."""
    if not g.get("completed"):
        return False
    if as_of_week is None:
        return True
    wk = g.get("week")
    return wk is not None and wk <= as_of_week


def team_state(team, group_config, as_of_week=None):
    """THE flag-aware single source for a team's banked side + schedule side
    (ARCHITECTURE §1/§5, §7). Honors count_conference_championship (conf-title
    games leave the slate entirely when the flag is off) and the --as-of-week
    replay (games after week N are treated as unplayed).

    Returns a dict:
      team, conference,
      banked_wins, banked_losses,
      games_scheduled, games_played, games_remaining,
      remaining_games: [{opponent, home_away, week}, ...]  (ascending by week)

    Banked totals and remaining_games come off the SAME slate, so
    games_played + games_remaining == games_scheduled always — Board 1 and
    Board 2 can never disagree on what is still to play."""
    flag = counts_conference_championship(group_config)
    season = get_season()                  # single source (season.json), not the config
    key = resolve_canonical(team)          # picks store canonical names (§9)

    banked_wins = banked_losses = games_played = 0
    remaining = []
    for g in _season_games(season):
        home, away = g.get("home_team"), g.get("away_team")
        if key != home and key != away:
            continue
        if g.get("conference_championship") and not flag:
            continue                        # off the slate when flag off (§1)
        is_home = key == home
        rem = {"opponent": away if is_home else home,
               "home_away": "home" if is_home else "away",
               "week": g.get("week"),
               "neutral": bool(g.get("neutral_site"))}
        hp, ap = g.get("home_points"), g.get("away_points")
        if _game_played(g, as_of_week) and hp is not None and ap is not None:
            games_played += 1
            mine, theirs = (hp, ap) if is_home else (ap, hp)
            if mine > theirs:
                banked_wins += 1
            elif theirs > mine:
                banked_losses += 1
            # exact tie (no OT in CFB): played, but neither a win nor a loss
        else:
            remaining.append(rem)

    remaining.sort(key=lambda r: (r["week"] is None, r["week"]))
    return {
        "team": key,
        "conference": canonical_conference(key),
        "banked_wins": banked_wins,
        "banked_losses": banked_losses,
        "games_scheduled": games_played + len(remaining),
        "games_played": games_played,
        "games_remaining": len(remaining),
        "remaining_games": remaining,
    }


# --- Season single-source guard (ARCHITECTURE §4/§6) ------------------------

def assert_season_matches_cache():
    """§6 guard, single-source edition: season.json's `season` must match the
    cache's season tag — ONE comparison, not one per group. A stale cache or a
    forgotten season.json flip would otherwise score a clean-but-wrong-season
    board. Non-zero exit naming both values; returns the season on success."""
    season = get_season()
    cache_season = peek_cache().get("season")
    if season != cache_season:
        print(f"::error:: season mismatch — season.json says season {season!r} but "
              f"the cache is tagged season {cache_season!r}. Refusing to score a "
              f"wrong-season board (ARCHITECTURE §4/§6).")
        sys.exit(2)
    return season


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


TEST_PICKS_PATH = DATA_DIR / "test_picks.json"
TEST_GROUP_ID = "test"


def load_test_group():
    """Synthesize a group from data/test_picks.json for `--test` (ARCHITECTURE
    §10.2). Managers are derived from the picks' manager ids (display_name = id);
    rules are unenforced; conf-title games excluded (flag off). Lets the engine
    produce a real mid-season board before any group's roster is drafted."""
    raw = load_json(TEST_PICKS_PATH)
    picks = raw.get("picks", [])
    mgr_ids = []
    for p in picks:
        m = p.get("manager")
        if m and m not in mgr_ids:
            mgr_ids.append(m)
    config = {
        "group_id": TEST_GROUP_ID,
        "display_name": "Test Fixture",
        "count_conference_championship": False,
        "picks_per_manager": None,
        "min_distinct_conferences": None,
        "managers": [{"manager_id": m, "display_name": m, "email": "TODO"}
                     for m in mgr_ids],
        "email_enabled": False,
    }
    return config, picks


def load_group(slug):
    """Resolve a group slug to (config, picks_list). `test` loads the synthetic
    fixture; anything else loads groups/<slug>/."""
    if slug == TEST_GROUP_ID:
        return load_test_group()
    return load_group_config(slug), load_group_picks(slug).get("picks", [])


def manager_display_map(config):
    """{manager_id -> display_name} from a group config's managers list."""
    return {m["manager_id"]: m.get("display_name", m["manager_id"])
            for m in config.get("managers", [])}


def real_picks(picks):
    """Picks that are neither the _note row nor TODO placeholders (§9)."""
    out = []
    for p in picks:
        if isinstance(p, dict) and p.get("_note"):
            continue
        if p.get("todo") is True or str(p.get("team", "")).strip().upper() == "TODO":
            continue
        out.append(p)
    return out
