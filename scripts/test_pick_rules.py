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

Fixtures use real canonical teams with their real 2026 conference AND line (per
data/team_win_totals_2026.json), so the ONLY possible error is the draft-rule
violation — not a name, conference, or line mismatch.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_pick_rules.py
    python scripts/test_pick_rules.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_team_names import validate_group_data

RULES = {"picks_per_manager": 4, "min_distinct_conferences": 4}

# (canonical team, conference, line) — verified against data/team_win_totals_2026.json,
# which is the authority on BOTH conference and line (validate_team_names now checks
# each pick against it). Note Boise State is Pac-12 in 2026: teams_canonical.json
# still says Mountain West, and using that stale value here would fail the gate.
OHIO_STATE = ("Ohio State", "Big Ten", 9.5)
MICHIGAN = ("Michigan", "Big Ten", 8.5)
ALABAMA = ("Alabama", "SEC", 8.5)
GEORGIA = ("Georgia", "SEC", 9.5)
CLEMSON = ("Clemson", "ACC", 7.5)
MIAMI = ("Miami", "ACC", 10.5)
UTAH = ("Utah", "Big 12", 8.5)
BAYLOR = ("Baylor", "Big 12", 6.5)
BOISE = ("Boise State", "Pac-12", 7.5)

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def picks(mgr, teamconfs):
    return [{"manager": mgr, "team": t, "conference": c, "line": ln, "direction": "O"}
            for t, c, ln in teamconfs]


def sided(mgr, items):
    """Build picks with an explicit O/U side per pick: items = [((team, conf, line), dir)]."""
    return [{"manager": mgr, "team": t, "conference": c, "line": ln, "direction": d}
            for (t, c, ln), d in items]


def problems_for(pk, manager=None):
    _, errors = validate_group_data("rulestest", RULES, pk)
    return [e["problem"] for e in errors if manager is None or e["manager"] == manager]


def test_picks_per_manager_and_conference_count():
    """The LOCKED format: exactly 4 picks spanning 4 distinct conferences."""
    print("\n[1] picks-per-manager / min-distinct-conferences")

    # FAIL - 3 picks (too few)
    p = problems_for(picks("m", [OHIO_STATE, ALABAMA, CLEMSON]), "m")
    check("3 picks fails picks-per-manager", "picks-per-manager" in p, f"problems={p}")

    # FAIL - 5 picks (too many); 5 distinct conferences so conf rule is satisfied
    p = problems_for(picks("m", [OHIO_STATE, ALABAMA, CLEMSON, UTAH, BOISE]), "m")
    check("5 picks fails picks-per-manager", "picks-per-manager" in p, f"problems={p}")
    check("5-in-5-conferences does NOT trip the conference rule",
          "min-distinct-conferences" not in p, f"problems={p}")

    # FAIL - 4 picks in only 3 conferences (two Big Ten); count is fine
    p = problems_for(picks("m", [OHIO_STATE, MICHIGAN, ALABAMA, CLEMSON]), "m")
    check("4 picks / 3 conferences fails min-distinct-conferences",
          "min-distinct-conferences" in p, f"problems={p}")
    check("4 picks / 3 conferences does NOT trip picks-per-manager",
          "picks-per-manager" not in p, f"problems={p}")

    # PASS - 4 picks in 4 conferences
    p = problems_for(picks("m", [OHIO_STATE, ALABAMA, CLEMSON, UTAH]))
    check("valid 4-picks / 4-conferences roster passes clean", p == [], f"problems={p}")


def test_todo_placeholder_manager_is_skipped():
    """A manager who has not drafted yet is SKIPPED, never failed."""
    print("\n[2] undrafted placeholder")
    stub = [{"manager": "undrafted", "team": "TODO", "conference": "TODO",
             "line": 0.0, "direction": "O", "todo": True}]
    valid = picks("drafted", [OHIO_STATE, ALABAMA, CLEMSON, UTAH])
    _, errors = validate_group_data("rulestest", RULES, stub + valid)
    check("stub-only manager is skipped (not failed)",
          all(e["manager"] != "undrafted" for e in errors) and errors == [],
          f"errors={[(e['manager'], e['problem']) for e in errors]}")


def test_team_sharing_gate():
    """The silent double-win bug: two managers cannot 'win' the same bet.

    Each roster below is a valid 4-picks/4-conferences roster, so the ONLY
    possible violation is the sharing rule - isolating it.
    """
    print("\n[3] team-sharing gate (opposite-side rule)")

    # FAIL - same team + same side across two managers (the silent double-win bug).
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

    # FAIL - one manager holding the SAME team twice (any side).
    dup = sided("a", [(OHIO_STATE, "O"), (OHIO_STATE, "U"), (ALABAMA, "O"), (CLEMSON, "O")])
    p = problems_for(dup, "a")
    check("one manager holding a team twice fails duplicate-team",
          "duplicate-team" in p, f"problems={p}")

    # PASS - same team on OPPOSITE sides (the only legal sharing); clean roster.
    opposite = (
        sided("a", [(OHIO_STATE, "O"), (ALABAMA, "O"), (CLEMSON, "O"), (UTAH, "O")]) +
        sided("b", [(OHIO_STATE, "U"), (GEORGIA, "O"), (MIAMI, "O"), (BAYLOR, "O")])
    )
    p = problems_for(opposite)
    check("same team on OPPOSITE sides passes clean", p == [], f"problems={p}")


def test_direction_domain_and_case_hardening():
    """A lowercase 'o' costs twice, so the domain is checked and the buckets normalised.

    The engine is uniformly `direction == "O"` else-under (scoring.signed_delta,
    projector.signed_delta, build_week_packet), so a lowercase "o" scores the
    pick as an UNDER, and (before this hardening) it split the same-side buckets
    so two managers on the same real side passed the gate.
    """
    print("\n[4] direction domain + case hardening")

    for bad in ("o", "u", "over", "", None):
        roster = sided("a", [(OHIO_STATE, bad), (ALABAMA, "O"),
                             (CLEMSON, "O"), (UTAH, "O")])
        p = problems_for(roster, "a")
        check(f"direction {bad!r} fails direction-invalid",
              "direction-invalid" in p, f"problems={p}")

    # the same-side check must survive a case difference on the two picks
    mixed_case = (
        sided("a", [(OHIO_STATE, "O"), (ALABAMA, "O"), (CLEMSON, "O"), (UTAH, "O")]) +
        sided("b", [(OHIO_STATE, "o"), (GEORGIA, "O"), (MIAMI, "O"), (BAYLOR, "O")])
    )
    p = problems_for(mixed_case)
    check("'O' vs 'o' on the same team still fails same-team-same-side",
          "same-team-same-side" in p, f"problems={p}")
    check("...and the invalid direction is reported in the same run",
          "direction-invalid" in p, f"problems={p}")
    _, errs = validate_group_data("rulestest", RULES, mixed_case)
    ssd = [e for e in errs if e["problem"] == "same-team-same-side"]
    check("the normalized bucket still labels the side correctly",
          bool(ssd) and "over" in ssd[0]["detail"],
          f"error={ssd[0] if ssd else None}")

    # a legal opposite-side share must NOT be broken by normalization
    opposite_mixed = (
        sided("a", [(OHIO_STATE, "O"), (ALABAMA, "O"), (CLEMSON, "O"), (UTAH, "O")]) +
        sided("b", [(OHIO_STATE, "U"), (GEORGIA, "O"), (MIAMI, "O"), (BAYLOR, "O")])
    )
    p = problems_for(opposite_mixed)
    check("normalization does not break a legal opposite-side share",
          "same-team-same-side" not in p, f"problems={p}")


def main():
    test_picks_per_manager_and_conference_count()
    test_todo_placeholder_manager_is_skipped()
    test_team_sharing_gate()
    test_direction_domain_and_case_hardening()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
