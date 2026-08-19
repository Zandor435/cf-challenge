#!/usr/bin/env python3
"""
report_build1.py — BUILD 1 verification report. Run AFTER build_canonical.py.

Prints, from the freshly built teams_canonical.json + team_aliases.json:
  - FBS count,
  - for a requested team list: CFBD's verbatim canonical string (exact/normalized
    match) and what utils.resolve_team() returns (canonical / RAISES ...),
  - an explicit ambiguity-guard demo for bare "USC" and bare "Miami",
  - FBS teams that no alias resolves to AND whose canonical differs from a naive
    typing (candidates that likely need an alias).

Usage:
    python scripts/report_build1.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (load_json, CANONICAL_PATH, resolve_team, normalize_team_name,
                   TeamNameError, load_aliases)

REQUESTED = [
    "USC", "Southern California", "South Carolina", "Miami (FL)", "Miami (OH)",
    "Ole Miss", "Mississippi", "Mississippi State", "Appalachian State", "NC State",
    "Louisiana", "Louisiana Monroe", "Louisiana Tech", "San Jose State", "Hawaii",
    "UMass", "UTSA", "UAB",
]


def main():
    teams = load_json(CANONICAL_PATH)["teams"]
    canon_by_key = {normalize_team_name(t["school"]): t["school"] for t in teams}
    print(f"FBS canonical count: {len(teams)}\n")

    print("Requested teams — verbatim canonical + resolver result:")
    print(f"  {'you type':24s} {'exact canonical':24s} resolve_team()")
    for name in REQUESTED:
        exact = canon_by_key.get(normalize_team_name(name), "— (no literal match)")
        try:
            res = resolve_team(name)
        except TeamNameError as e:
            res = f"RAISES {type(e).__name__}"
        print(f"  {name:24s} {exact:24s} {res}")

    print("\nAmbiguity guard demo (explicit error text):")
    for name in ("USC", "Miami"):
        try:
            resolve_team(name)
            print(f"  resolve_team({name!r}) -> NO ERROR (unexpected!)")
        except TeamNameError as e:
            print(f"  resolve_team({name!r}) -> {type(e).__name__}: {e}")

    # FBS teams no alias resolves to, whose canonical != a naive typing.
    alias_targets = set(load_aliases().values())
    print("\nFBS teams with NO alias whose canonical may not be what a person types:")
    flagged = 0
    for t in teams:
        school = t["school"]
        if school in alias_targets:
            continue
        # Heuristic: multi-word canonicals or ones with non-ascii are the risky
        # "you'd never type this exactly" cases worth a human look.
        if not school.isascii() or ("(" in school):
            print(f"  {school}  ({t.get('conference')})")
            flagged += 1
    if not flagged:
        print("  (none flagged by heuristic — read the full list below for judgment)")

    print(f"\nAll {len(teams)} canonical FBS strings (alphabetical, one per line):")
    for school in sorted((t["school"] for t in teams), key=str.lower):
        print(school)


if __name__ == "__main__":
    main()

