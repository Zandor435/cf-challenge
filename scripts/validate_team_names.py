#!/usr/bin/env python3
"""
validate_team_names.py — The fetch->score name gate (ARCHITECTURE §8 CLONE, §9).

Resolves every real pick's team through utils.resolve_team (aliases -> canonical,
with the ambiguity guard). HARD gate: any unresolved or ambiguous name makes the
run exit 1 and NAMES the offender, so a mismatch can never silently mis-score a
pick (§9). Placeholder picks (todo=true / team "TODO") are skipped.

Usage:
    python scripts/validate_team_names.py                # all groups
    python scripts/validate_team_names.py --group group_a
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (resolve_team, AmbiguityError, UnknownTeamError, TeamNameError,
                   get_all_group_ids, load_group_picks)


def _is_placeholder(pick):
    return pick.get("todo") is True or str(pick.get("team", "")).strip().upper() == "TODO"


def validate_group(group_id):
    picks = load_group_picks(group_id).get("picks", [])
    errors, checked = [], 0
    for pick in picks:
        if isinstance(pick, dict) and pick.get("_note"):
            continue
        if _is_placeholder(pick):
            continue
        team = pick.get("team")
        try:
            resolve_team(team)
            checked += 1
        except AmbiguityError as e:
            errors.append({"group": group_id, "manager": pick.get("manager", "?"),
                           "team": team, "problem": "ambiguous",
                           "candidates": e.candidates})
        except UnknownTeamError as e:
            errors.append({"group": group_id, "manager": pick.get("manager", "?"),
                           "team": team, "problem": "unknown",
                           "candidates": e.suggestions})
        except TeamNameError as e:
            errors.append({"group": group_id, "manager": pick.get("manager", "?"),
                           "team": team, "problem": str(e), "candidates": []})
    return checked, errors


def main():
    ap = argparse.ArgumentParser(description="Validate pick team names against canonical")
    ap.add_argument("--group", default="all")
    args = ap.parse_args()

    group_ids = get_all_group_ids() if args.group == "all" else [args.group]
    total_checked, all_errors = 0, []
    for gid in group_ids:
        checked, errors = validate_group(gid)
        total_checked += checked
        all_errors.extend(errors)

    if all_errors:
        print(f"NAME GATE FAILED — {len(all_errors)} unresolved pick(s):")
        for e in all_errors:
            hint = (f"did you mean: {', '.join(e['candidates'])}?" if e["candidates"]
                    else "no close match")
            label = "AMBIGUOUS" if e["problem"] == "ambiguous" else "UNKNOWN"
            print(f"  [{e['group']}] manager {e['manager']}: {label} team "
                  f"'{e['team']}' — {hint}")
        sys.exit(1)

    print(f"Name gate OK: {total_checked} real pick(s) resolved across "
          f"{len(group_ids)} group(s).")


if __name__ == "__main__":
    main()
