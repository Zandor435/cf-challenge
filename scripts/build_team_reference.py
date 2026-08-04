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

Output is shared across all groups (same pattern as data/cfbd_cache.json) and is
OVERWRITE-on-regenerate: it is derived wholly from the raw TSV, accumulates
nothing, so re-running is safe. Write is atomic (playbook rule 5).

What it enforces (fail loud, exit 1 — never a warning, never a silent skip):
  - duplicate team keys (two raw rows collapsing to one slug),
  - a conference string that does not map cleanly onto the canonical 11,
  - a missing / non-numeric win total, or a malformed row.
All offenders are collected and named in one report; nothing is written when any
error is present.

What it FLAGS but does not resolve (§9): every raw team name is pushed through
utils.resolve_team — the ambiguity-guarded human-entry resolver backed by
team_aliases.json + ambiguous.json (the ASU/BSU/FSU/KSU/MSU/OSU collision set).
A name that comes back ambiguous or unknown is reported for a human to add a
deliberate alias entry. It is NOT auto-resolved here: guessing at the alias edge
is exactly how a pick silently mis-scores.

Usage:
    python scripts/build_team_reference.py
    python scripts/build_team_reference.py --dry-run     # report only, no write
"""

import argparse
import re
import sys
import unicodedata
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import AmbiguityError, UnknownTeamError

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
# so casing, spacing, hyphens and punctuation don't need enumerating).
_CONFERENCE_VARIANTS = {
    "SEC": ["SEC", "Southeastern Conference", "Southeastern"],
    "Big Ten": ["Big Ten", "Big Ten Conference", "B1G", "Big 10", "Big 10 Conference"],
    "Big 12": ["Big 12", "Big 12 Conference", "Big XII"],
    "ACC": ["ACC", "Atlantic Coast Conference", "Atlantic Coast"],
    "American": ["American", "American Conference", "American Athletic Conference",
                 "American Athletic", "AAC"],
    "Mountain West": ["Mountain West", "Mountain West Conference", "MWC", "MW"],
    "Pac-12": ["Pac-12", "Pac-12 Conference", "Pac 12", "PAC-12", "Pac-12 Conference"],
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


def _conf_key(s):
    """Normalize a conference string for lookup: lowercase, alphanumerics only."""
    return "".join(c for c in str(s).lower() if c.isalnum())


_CONFERENCE_LOOKUP = {}
for _canonical, _variants in _CONFERENCE_VARIANTS.items():
    for _v in [_canonical] + _variants:
        _CONFERENCE_LOOKUP[_conf_key(_v)] = _canonical


def slugify(name):
    """Team key: lowercase, underscore-joined, ASCII alphanumerics only.
    'Texas A&M' -> texas_am, 'Miami (FL)' -> miami_fl, 'San José State' ->
    san_jose_state."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))  # strip diacritics
    s = s.lower().replace("&", "")                              # A&M -> AM
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


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
    """Normalize rows -> (teams dict, errors, conference_map, alias_flags).

    conference_map: canonical -> ordered raw variants actually seen.
    alias_flags:    [(raw_name, kind, detail)] — names that do NOT resolve
                    unambiguously through team_aliases.json / ambiguous.json.
    """
    teams = OrderedDict()
    errors = []
    seen_key = {}                       # slug -> (line_no, raw name)
    conference_map = OrderedDict()      # canonical -> [raw variants seen]
    alias_flags = []

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

        # --- key + display name ----------------------------------------------
        display = display_name(raw_team)
        key = slugify(display)
        if not key:
            errors.append(f"line {line_no}: team name {raw_team!r} slugifies to "
                          f"an empty key")
            continue
        if key in seen_key:
            prev_line, prev_name = seen_key[key]
            errors.append(f"line {line_no}: duplicate team key '{key}' — "
                          f"{raw_team!r} collides with {prev_name!r} (line "
                          f"{prev_line})")
            continue
        seen_key[key] = (line_no, raw_team)

        # --- §9 alias cross-reference: flag, never silently resolve ----------
        flag = _alias_check(raw_team, display)
        if flag:
            alias_flags.append(flag)

        teams[key] = {
            "display_name": display,
            "conference": conference,
            "win_total": win_total,
        }

    return teams, errors, conference_map, alias_flags


def _alias_check(raw_team, display):
    """Push a raw name through the ambiguity-guarded human-entry resolver. The
    display form is tried first (it carries the §9 disambiguation), then the raw
    form. Returns None when the name resolves cleanly, else a flag tuple."""
    last = None
    for candidate in ([display, raw_team] if display != raw_team else [raw_team]):
        try:
            utils.resolve_team(candidate)
            return None
        except AmbiguityError as e:
            last = (raw_team, "ambiguous",
                    f"'{candidate}' is in the ambiguity guard — could be "
                    f"{', '.join(e.candidates)}; needs a disambiguated alias entry")
        except UnknownTeamError as e:
            hint = (f"closest canonical: {', '.join(e.suggestions)}"
                    if e.suggestions else "no close canonical match")
            last = (raw_team, "unresolved",
                    f"'{candidate}' is in neither team_aliases.json nor "
                    f"teams_canonical.json — {hint}")
    return last


def print_summary(teams, conference_map, alias_flags):
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
               if "(" in e["display_name"]]
    if renamed:
        print("\n§9 disambiguation convention applied:")
        for key, disp in renamed:
            print(f"  {key:<16} -> {disp}")

    print("\n§9 alias cross-reference (team_aliases.json + ambiguous.json):")
    if not alias_flags:
        print("  OK — all team names resolve unambiguously.")
    else:
        print(f"  {len(alias_flags)} name(s) FLAGGED — not auto-resolved, add a "
              f"deliberate alias entry:")
        for raw_team, kind, detail in alias_flags:
            print(f"    [{kind}] {raw_team}: {detail}")


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
    teams, build_errors, conference_map, alias_flags = build(rows)
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

    print_summary(teams, conference_map, alias_flags)


if __name__ == "__main__":
    main()
