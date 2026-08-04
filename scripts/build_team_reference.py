#!/usr/bin/env python3
"""
build_team_reference.py — ingest the frozen preseason win-total lines
(ARCHITECTURE §1 "the line never moves", §7, §9).

One-off ingestion: reads the hand-entered TSV in data/source/, normalizes it,
and writes the canonical team/conference/win-total reference that every group's
picks.json is validated against. The line is STATIC CONFIG — entered once at
draft, never refreshed by the live pipeline — so this script exists to turn one
paste into one machine-checked file, not to be run on a schedule.

  data/source/2026_win_totals_raw.tsv  ->  data/team_win_totals_2026.json

**Keyed by CANONICAL team name** (the exact `school` string in
teams_canonical.json), so picks — which store canonical names (§9) — look up
directly, with no slug/bridge layer in between. `display_name` carries the
human-readable form, including the §9 "(FL)"/"(OH)" disambiguation.

AUTHORITY (locked): for the 2026 season this file is the sole authority on a
team's `conference` and `win_total`. teams_canonical.json's `conference` field is
a 2025-vintage snapshot that realignment has moved past (Boise State, Texas
State, ... are Pac-12 in 2026) — it is authoritative for team IDENTITY only. The
one place this script reads that field is the ambiguity fallback below, and there
it is used strictly to decide WHICH TEAM a row means, never to set the output.

Output is shared across all groups (same pattern as data/cfbd_cache.json) and is
OVERWRITE-on-regenerate: it is derived wholly from the raw TSV, accumulates
nothing, so re-running is safe. Write is atomic (playbook rule 5).

Name resolution per row, in order (§9):
  1. resolve_canonical  — the row is already a canonical CFBD name.
  2. resolve_team       — the row is a known alias ("FIU" -> Florida International).
  3. (name, conference) — resolve_team raised on an ambiguous bare token, so the
     row's own conference column picks between the candidates ("USC" + Big Ten ->
     USC/Southern California; South Carolina is SEC). This is the only read of
     teams_canonical.json's conference field, and it is identity-only.
  4. hard error — nothing is guessed at the alias edge; the run fails and names it.

Fail loud, exit 1, nothing written — never a warning, never a silent skip — on:
  - a team name that does not resolve (step 4 above),
  - duplicate team keys (two raw rows resolving to one canonical team),
  - a conference string that does not map onto the canonical 11,
  - a missing / non-numeric win total, or a malformed row.
All offenders are collected and named in one report.

Usage:
    python scripts/build_team_reference.py
    python scripts/build_team_reference.py --dry-run     # report only, no write
"""

import argparse
import sys
import unicodedata
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (AmbiguityError, TeamNameError, UnknownTeamError,
                   canonical_conference, resolve_canonical, resolve_team)

ROOT = Path(__file__).resolve().parent.parent

# The line is frozen at draft for the 2026 season. Deliberately NOT read from
# season.json: that file tracks which season the live pipeline fetches/scores,
# and this reference has to be buildable before it is flipped over.
SEASON = 2026
RAW_PATH = ROOT / "data" / "source" / "2026_win_totals_raw.tsv"
OUT_PATH = ROOT / "data" / "team_win_totals_2026.json"
SOURCE_LABEL = "manual entry — preseason win totals, frozen at draft"

EXPECTED_HEADER = ["Team", "Conference", "Win Total"]

# The canonical 11 FBS conference labels. Closed set — every raw variant maps
# onto one of these; a string that doesn't is an error, not a new label.
CANONICAL_CONFERENCES = [
    "SEC", "Big Ten", "Big 12", "ACC", "American", "Mountain West",
    "Pac-12", "Sun Belt", "Conference USA", "MAC", "Independent",
]

# canonical -> accepted raw variants (compared after _conf_key normalization,
# so casing, spacing, hyphens and punctuation don't need enumerating). The
# teams_canonical.json spellings ("American Athletic", "Mid-American", "FBS
# Independents") are included so the ambiguity fallback can compare like with like.
_CONFERENCE_VARIANTS = {
    "SEC": ["SEC", "Southeastern Conference", "Southeastern"],
    "Big Ten": ["Big Ten", "Big Ten Conference", "B1G", "Big 10", "Big 10 Conference"],
    "Big 12": ["Big 12", "Big 12 Conference", "Big XII"],
    "ACC": ["ACC", "Atlantic Coast Conference", "Atlantic Coast"],
    "American": ["American", "American Conference", "American Athletic Conference",
                 "American Athletic", "AAC"],
    "Mountain West": ["Mountain West", "Mountain West Conference", "MWC", "MW"],
    "Pac-12": ["Pac-12", "Pac-12 Conference", "Pac 12", "PAC-12"],
    "Sun Belt": ["Sun Belt", "Sun Belt Conference", "SBC"],
    "Conference USA": ["Conference USA", "C-USA", "CUSA", "Conference USA (CUSA)"],
    "MAC": ["MAC", "Mid-American Conference", "Mid-American", "Mid American Conference"],
    "Independent": ["Independent", "Independents", "FBS Independent",
                    "FBS Independents", "Ind."],
}

# §9 disambiguation convention: a bare trailing state token becomes a
# parenthesized suffix — "Miami FL" -> "Miami (FL)", "Miami OH" -> "Miami (OH)".
_STATE_SUFFIXES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

# Resolution paths, for the summary report.
PATH_CANONICAL = "canonical"
PATH_ALIAS = "alias"
PATH_CONFERENCE = "conference-disambiguated"


def _conf_key(s):
    """Normalize a conference string for lookup: lowercase, alphanumerics only."""
    return "".join(c for c in str(s).lower() if c.isalnum())


_CONFERENCE_LOOKUP = {}
for _canonical, _variants in _CONFERENCE_VARIANTS.items():
    for _v in [_canonical] + _variants:
        _CONFERENCE_LOOKUP[_conf_key(_v)] = _canonical


def display_name(raw_name):
    """Clean display form. Applies the §9 bare-state-suffix convention; every
    other name passes through as entered."""
    name = " ".join(str(raw_name).split())
    parts = name.split(" ")
    if len(parts) > 1 and parts[-1].upper() in _STATE_SUFFIXES and parts[-1].isupper():
        return " ".join(parts[:-1]) + f" ({parts[-1].upper()})"
    return name


def normalize_conference(raw_conf):
    """Raw conference string -> one of the canonical 11. Returns None if it does
    not map cleanly (caller fails the run)."""
    return _CONFERENCE_LOOKUP.get(_conf_key(raw_conf))


def disambiguate_by_conference(candidates, conference):
    """Ambiguity fallback: pick the candidate whose team sits in `conference`
    (already normalized to the canonical 11).

    `candidates` are the disambiguated strings the ambiguity guard offers
    (e.g. "Southern California" / "South Carolina"), so each resolves on its own.
    The comparison reads teams_canonical.json's conference — IDENTITY use only,
    to decide which team the row means; the output's conference always comes from
    the raw row. Returns the canonical name on a unique match, else None."""
    matches = []
    for cand in candidates:
        try:
            canonical = resolve_team(cand)
            cand_conf = normalize_conference(canonical_conference(canonical))
        except TeamNameError:
            continue
        if cand_conf is not None and cand_conf == conference and canonical not in matches:
            matches.append(canonical)
    return matches[0] if len(matches) == 1 else None


def resolve_row(raw_team, conference):
    """Raw team name -> (canonical, path, error). Exactly one of canonical/error
    is non-None. `conference` is the row's normalized conference, used only by
    the ambiguity fallback.

    The ambiguity guard is consulted FIRST, mirroring resolve_team's order (§9).
    That matters for a token that is BOTH ambiguous to a human AND a real
    canonical string: "USC" is canonical for Southern California, so a plain
    resolve_canonical would accept a row that a South-Carolina-meaning typist
    entered and key it as the wrong team. Going through the guard forces the
    row's own conference column to confirm which team is meant."""
    ambiguous = utils.load_ambiguous()
    key = utils.normalize_team_name(raw_team)
    if key in ambiguous:
        candidates = ambiguous[key]
        picked = disambiguate_by_conference(candidates, conference)
        if picked is not None:
            return picked, PATH_CONFERENCE, None
        return None, None, (
            f"'{raw_team}' is ambiguous (could be {', '.join(candidates)}) and "
            f"conference {conference!r} does not single one out — add a "
            f"disambiguated alias entry")
    try:
        return resolve_canonical(raw_team), PATH_CANONICAL, None
    except UnknownTeamError:
        pass
    try:
        return resolve_team(raw_team), PATH_ALIAS, None
    except UnknownTeamError as e:
        hint = (f"closest canonical: {', '.join(e.suggestions)}"
                if e.suggestions else "no close canonical match")
        return None, None, (
            f"'{raw_team}' is in neither team_aliases.json nor "
            f"teams_canonical.json — {hint}")


def parse_raw(path):
    """Read the TSV into [(line_no, team, conference, win_total_str)]. Blank
    lines are skipped; a row without exactly 3 tab-separated fields is an error."""
    if not path.exists():
        print(f"ERROR: raw win-total file not found: {path}")
        sys.exit(1)

    rows, errors = [], []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    if not lines:
        print(f"ERROR: {path} is empty")
        sys.exit(1)

    header = [c.strip() for c in lines[0].split("\t")]
    if header != EXPECTED_HEADER:
        print(f"ERROR: unexpected header in {path}: {header!r} "
              f"(expected {EXPECTED_HEADER!r})")
        sys.exit(1)

    for i, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        fields = [c.strip() for c in line.split("\t")]
        if len(fields) != 3:
            errors.append(f"line {i}: expected 3 tab-separated fields, got "
                          f"{len(fields)} — {line!r}")
            continue
        rows.append((i, fields[0], fields[1], fields[2]))
    return rows, errors


def build(rows):
    """Normalize rows -> (teams dict, errors, conference_map, resolutions).

    teams:          canonical team name -> {display_name, conference, win_total}
    conference_map: canonical conference -> ordered raw variants actually seen
    resolutions:    [(raw_name, canonical, path)] for every resolved row
    """
    teams = OrderedDict()
    errors = []
    seen_key = {}                       # canonical -> (line_no, raw name)
    conference_map = OrderedDict()      # canonical conf -> [raw variants seen]
    resolutions = []

    for line_no, raw_team, raw_conf, raw_total in rows:
        if not raw_team:
            errors.append(f"line {line_no}: empty team name")
            continue

        # --- win total: required and numeric ---------------------------------
        if raw_total == "":
            errors.append(f"line {line_no}: '{raw_team}' has no win total")
            continue
        try:
            win_total = float(raw_total)
        except ValueError:
            errors.append(f"line {line_no}: '{raw_team}' has a non-numeric win "
                          f"total {raw_total!r}")
            continue

        # --- conference: must map onto the canonical 11 ----------------------
        conference = normalize_conference(raw_conf)
        if conference is None:
            errors.append(f"line {line_no}: '{raw_team}' has conference "
                          f"{raw_conf!r}, which does not map to any of the "
                          f"canonical 11 {CANONICAL_CONFERENCES}")
            continue
        variants = conference_map.setdefault(conference, [])
        clean_conf = " ".join(str(raw_conf).split())
        if clean_conf not in variants:
            variants.append(clean_conf)

        # --- key: the canonical team name (§9) -------------------------------
        canonical, path, resolve_error = resolve_row(raw_team, conference)
        if canonical is None:
            errors.append(f"line {line_no}: {resolve_error}")
            continue
        if canonical in seen_key:
            prev_line, prev_name = seen_key[canonical]
            errors.append(f"line {line_no}: duplicate team key '{canonical}' — "
                          f"{raw_team!r} collides with {prev_name!r} (line "
                          f"{prev_line})")
            continue
        seen_key[canonical] = (line_no, raw_team)
        resolutions.append((raw_team, canonical, path))

        teams[canonical] = {
            "display_name": display_name(raw_team),
            "conference": conference,
            "win_total": win_total,
        }

    return teams, errors, conference_map, resolutions


def print_summary(teams, conference_map, resolutions):
    print(f"\nTeams ingested: {len(teams)}")

    print("\nPer-conference counts:")
    counts = {}
    for entry in teams.values():
        counts[entry["conference"]] = counts.get(entry["conference"], 0) + 1
    for conf in CANONICAL_CONFERENCES:
        if conf in counts:
            print(f"  {conf:<16} {counts[conf]:>3}")
    print(f"  {'TOTAL':<16} {sum(counts.values()):>3}")

    print("\nConference-string normalizations applied "
          "(canonical <- raw variants seen):")
    for conf in CANONICAL_CONFERENCES:
        if conf in conference_map:
            print(f"  {conf} <- {', '.join(conference_map[conf])}")

    renamed = [(k, e["display_name"]) for k, e in teams.items()
               if e["display_name"] != k]
    if renamed:
        print("\nDisplay names differing from the canonical key "
              "(includes the §9 (FL)/(OH) convention):")
        for key, disp in renamed:
            print(f"  {key:<24} -> {disp}")

    print("\nName resolution (§9) — canonical keys, nothing guessed:")
    by_path = {}
    for raw_team, canonical, path in resolutions:
        by_path.setdefault(path, []).append((raw_team, canonical))
    print(f"  {len(by_path.get(PATH_CANONICAL, [])):>3} resolved directly as a "
          f"canonical CFBD name")
    for path, label in ((PATH_ALIAS, "via team_aliases.json"),
                        (PATH_CONFERENCE, "via the (team, conference) pair")):
        hits = by_path.get(path, [])
        print(f"  {len(hits):>3} resolved {label}"
              + (":" if hits else ""))
        for raw_team, canonical in hits:
            print(f"        {raw_team!r} -> {canonical!r}")
    print("    0 flagged (unresolved names are a hard error — see above)")


def main():
    ap = argparse.ArgumentParser(
        description="Ingest the frozen preseason win-total TSV into "
                    "data/team_win_totals_<season>.json")
    ap.add_argument("--raw", default=str(RAW_PATH), help="raw TSV path")
    ap.add_argument("--out", default=str(OUT_PATH), help="output JSON path")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse, validate and report — write nothing")
    args = ap.parse_args()

    rows, parse_errors = parse_raw(Path(args.raw))
    teams, build_errors, conference_map, resolutions = build(rows)
    errors = parse_errors + build_errors

    if errors:
        print(f"BUILD FAILED — {len(errors)} error(s); nothing written:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    payload = {
        "season": SEASON,
        "source": SOURCE_LABEL,
        "teams": {k: teams[k] for k in sorted(teams)},
    }

    if args.dry_run:
        print(f"[dry-run] would write {len(teams)} teams to {args.out}")
    else:
        utils.save_json_atomic(Path(args.out), payload)

    print_summary(teams, conference_map, resolutions)


if __name__ == "__main__":
    main()
