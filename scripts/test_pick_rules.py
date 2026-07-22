#!/usr/bin/env python3
"""
test_pick_rules.py — Draft-rule gate: exactly 4 picks / 4 distinct conferences (§5),
plus the team-sharing gate (same team only on OPPOSITE sides).

The format is settled (LOCKED): every manager drafts EXACTLY 4 picks spanning 4
distinct conferences. validate_team_names enforces this per group, always — no
unenforced path. This test drives validate_group_data with synthetic rosters:

  FAIL — 3 picks (too few)
  FAIL — 5 picks (too many)
  FAIL — 4 picks in only 3 conferences
  PASS — 4 picks in 4 conferences
  SKIP — a manager with only TODO placeholders is skipped, not failed

Team-sharing gate (the silent double-win bug — two managers "winning" the same
bet — must be impossible):

  FAIL — same team + same side across two managers
  FAIL — one manager holding the same team twice
  PASS — same team, OPPOSITE sides (the only legal sharing)

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
GEORGIA = ("Georgia", "SEC")
CLEMSON = ("Clemson", "ACC")
MIAMI = ("Miami", "ACC")
UTAH = ("Utah", "Big 12")
BAYLOR = ("Baylor", "Big 12")
BOISE = ("Boise State", "Mountain West")

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def picks(mgr, teamconfs):
    return [{"manager": mgr, "team": t, "conference": c, "line": 9.5, "direction": "O"}
            for t, c in teamconfs]


def sided(mgr, items):
    """Build picks with an explicit O/U side per pick: items = [((team, conf), dir)]."""
    return [{"manager": mgr, "team": t, "conference": c, "line": 9.5, "direction": d}
            for (t, c), d in items]


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

    # ---- team-sharing gate (opposite-side rule) --------------------------------
    # Each roster below is a valid 4-picks/4-conferences roster, so the ONLY
    # possible violation is the sharing rule — isolating it.

    # FAIL — same team + same side across two managers (the silent double-win bug).
    same_side = (
        sided("a", [(OHIO_STATE, "O"), (ALABAMA, "O"), (CLEMSON, "O"), (UTAH, "O")]) +
        sided("b", [(OHIO_STATE, "O"), (GEORGIA, "O"), (MIAMI, "O"), (BAYLOR, "O")])
    )
    p = problems_for(same_side)
    check("same team + same side (two managers) fails same-team-same-side",
          "same-team-same-side" in p, f"problems={p}")
    _, errs = validate_group_data("rulestest", RULES, same_side)
    ssd = [e for e in errs if e["problem"] == "same-team-same-side"]
    check("same-team-same-side error names both managers, team, and side",
          bool(ssd) and "a" in ssd[0]["manager"] and "b" in ssd[0]["manager"]
          and ssd[0]["team"] == "Ohio State" and "over" in ssd[0]["detail"],
          f"error={ssd[0] if ssd else None}")

    # FAIL — one manager holding the SAME team twice (any side).
    dup = sided("a", [(OHIO_STATE, "O"), (OHIO_STATE, "U"), (ALABAMA, "O"), (CLEMSON, "O")])
    p = problems_for(dup, "a")
    check("one manager holding a team twice fails duplicate-team",
          "duplicate-team" in p, f"problems={p}")

    # PASS — same team on OPPOSITE sides (the only legal sharing); clean roster.
    opposite = (
        sided("a", [(OHIO_STATE, "O"), (ALABAMA, "O"), (CLEMSON, "O"), (UTAH, "O")]) +
        sided("b", [(OHIO_STATE, "U"), (GEORGIA, "O"), (MIAMI, "O"), (BAYLOR, "O")])
    )
    p = problems_for(opposite)
    check("same team on OPPOSITE sides passes clean", p == [], f"problems={p}")

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
