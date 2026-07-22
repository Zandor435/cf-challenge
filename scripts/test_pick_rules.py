#!/usr/bin/env python3
"""
test_pick_rules.py — Draft-rule gate: exactly 4 picks / 4 distinct conferences (§5).

The format is settled (LOCKED): every manager drafts EXACTLY 4 picks spanning 4
distinct conferences. validate_team_names enforces this per group, always — no
unenforced path. This test drives validate_group_data with synthetic rosters:

  FAIL — 3 picks (too few)
  FAIL — 5 picks (too many)
  FAIL — 4 picks in only 3 conferences
  PASS — 4 picks in 4 conferences
  SKIP — a manager with only TODO placeholders is skipped, not failed

Fixtures use real canonical teams with correct conferences, so the ONLY possible
error is the draft-rule violation (not a name/conference mismatch).

Usage:
    python scripts/test_pick_rules.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_team_names import validate_group_data

RULES = {"picks_per_manager": 4, "min_distinct_conferences": 4}

# (team, canonical conference) — verified against teams_canonical.json
OHIO_STATE = ("Ohio State", "Big Ten")
MICHIGAN = ("Michigan", "Big Ten")
ALABAMA = ("Alabama", "SEC")
CLEMSON = ("Clemson", "ACC")
UTAH = ("Utah", "Big 12")
BOISE = ("Boise State", "Mountain West")

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def picks(mgr, teamconfs):
    return [{"manager": mgr, "team": t, "conference": c, "line": 9.5, "direction": "O"}
            for t, c in teamconfs]


def problems_for(pk, manager=None):
    _, errors = validate_group_data("rulestest", RULES, pk)
    return [e["problem"] for e in errors if manager is None or e["manager"] == manager]


def main():
    # FAIL — 3 picks (too few)
    p = problems_for(picks("m", [OHIO_STATE, ALABAMA, CLEMSON]), "m")
    check("3 picks fails picks-per-manager", "picks-per-manager" in p, f"problems={p}")

    # FAIL — 5 picks (too many); 5 distinct conferences so conf rule is satisfied
    p = problems_for(picks("m", [OHIO_STATE, ALABAMA, CLEMSON, UTAH, BOISE]), "m")
    check("5 picks fails picks-per-manager", "picks-per-manager" in p, f"problems={p}")
    check("5-in-5-conferences does NOT trip the conference rule",
          "min-distinct-conferences" not in p, f"problems={p}")

    # FAIL — 4 picks in only 3 conferences (two Big Ten); count is fine
    p = problems_for(picks("m", [OHIO_STATE, MICHIGAN, ALABAMA, CLEMSON]), "m")
    check("4 picks / 3 conferences fails min-distinct-conferences",
          "min-distinct-conferences" in p, f"problems={p}")
    check("4 picks / 3 conferences does NOT trip picks-per-manager",
          "picks-per-manager" not in p, f"problems={p}")

    # PASS — 4 picks in 4 conferences
    p = problems_for(picks("m", [OHIO_STATE, ALABAMA, CLEMSON, UTAH]))
    check("valid 4-picks / 4-conferences roster passes clean", p == [], f"problems={p}")

    # SKIP — a manager with only a TODO placeholder is skipped, not failed.
    stub = [{"manager": "undrafted", "team": "TODO", "conference": "TODO",
             "line": 0.0, "direction": "O", "todo": True}]
    valid = picks("drafted", [OHIO_STATE, ALABAMA, CLEMSON, UTAH])
    _, errors = validate_group_data("rulestest", RULES, stub + valid)
    check("stub-only manager is skipped (not failed)",
          all(e["manager"] != "undrafted" for e in errors) and errors == [],
          f"errors={[(e['manager'], e['problem']) for e in errors]}")

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
