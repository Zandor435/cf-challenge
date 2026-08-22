#!/usr/bin/env python3
"""
test_selftest_census.py — selftest_10_1's schedule census skips, passes, and fails
for the right cache states, proven on synthetic caches (no live data, no network).

selftest_10_1.py is the live-cache health check CI runs as its own blocking
step. Its census asserts a COMPLETE regular-season slate (every FBS team at 12
with conf-title games excluded, exactly the title-game participants at 13 with
them included), which CFBD cannot satisfy until it posts a season's title games
— so the census now SKIPS with its reason until they land. That gate is the
thing that let continue-on-error come off the CI step, so it gets a test of
its own, over caches this file builds rather than whatever is committed:

  1. no title games       -> census skipped (reason printed), nothing failed
  2. complete slate        -> census runs, every check passes, counts derived
                              from the cache (2 x title games at 13)
  3. one team short a game -> census runs and FAILS loudly

The real teams_canonical.json supplies the FBS roster; every game is synthetic.
utils.peek_cache is patched for the duration of each case and restored.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_selftest_census.py
    python scripts/test_selftest_census.py
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import selftest_10_1 as st

SEASON = 2099   # any int: the census compares the cache's own tag to what it is told

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _fbs_schools():
    recs = utils.load_json(utils.CANONICAL_PATH)["teams"]
    return sorted((t["school"], t["conference"]) for t in recs)


def synthetic_cache(title_games=9, short_one=False):
    """A neutral cache in fetch_results' shape: every FBS team plays 12 games
    against its own private FCS opponents (so no two FBS slates couple), and
    the first 2*title_games teams pair off in conference_championship games."""
    schools = _fbs_schools()
    games, teams = [], {}
    for i, (school, conf) in enumerate(schools):
        n = 12 - (1 if short_one and i == len(schools) - 1 else 0)
        for k in range(n):
            games.append({"week": k + 1, "home_team": school,
                          "away_team": f"FCS Foe {i}-{k}",
                          "conference_championship": False, "completed": False})
        teams[school] = {"conference": conf, "scheduled_games": n}
    participants = [s for s, _ in schools[:2 * title_games]]
    for a, b in zip(participants[0::2], participants[1::2]):
        games.append({"week": 14, "home_team": a, "away_team": b,
                      "conference_championship": True, "completed": False})
        teams[a]["scheduled_games"] += 1
        teams[b]["scheduled_games"] += 1
    return {"season": SEASON, "fetched_at": "2099-01-01T00:00:00+00:00",
            "counts": {"games": len(games), "sp_ratings": 1},
            "games": games, "teams": teams, "sp_ratings": {"x": 1}}


def run_census(cache):
    """Drive selftest_10_1.test_cache over `cache`; return (results, skipped, printed)."""
    saved_peek = utils.peek_cache
    utils.peek_cache = lambda path=None: cache
    st._results.clear()
    st._skipped.clear()
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            st.test_cache(SEASON)
    finally:
        utils.peek_cache = saved_peek
    return list(st._results), list(st._skipped), out.getvalue()


def test_no_title_games_skips_the_census_without_failing():
    """A pre-season cache is a SKIP with a reason, never a red."""
    print("\n[1] cache with 0 conference title games")
    results, skipped, printed = run_census(synthetic_cache(title_games=0))
    check("the four basic cache checks still ran and passed",
          len(results) == 4 and all(results), f"results={results}")
    check("exactly one census skip was recorded", len(skipped) == 1, f"skipped={skipped}")
    check("the skip names its reason on the transcript",
          "[SKIP]" in printed and "0 conference title games" in printed, repr(printed[-300:]))
    check("nothing was recorded as FAIL", "[FAIL]" not in printed)


def test_complete_slate_passes_with_counts_derived_from_the_cache():
    """With title games posted the census runs, and its expectations come from the data."""
    print("\n[2] complete slate, 9 title games")
    results, skipped, printed = run_census(synthetic_cache(title_games=9))
    check("nothing was skipped", skipped == [], f"skipped={skipped}")
    check("every check passed (4 basic + 4 census)",
          len(results) == 8 and all(results), f"results={results}")
    check("the 13-game expectation was derived as 2 x 9 = 18",
          "exactly 18 FBS teams sit at 13" in printed, repr(printed[-400:]))

    # A different number of title games is NOT a failure — the old magic 18/9 would have been.
    results, skipped, printed = run_census(synthetic_cache(title_games=7))
    check("7 title games passes too (no hardcoded 18/9)",
          skipped == [] and len(results) == 8 and all(results), f"results={results}")
    check("...and the expectation reads 14", "exactly 14 FBS teams sit at 13" in printed)


def test_a_short_slate_fails_loudly():
    """Title games posted but one team a game short: that IS a census failure."""
    print("\n[3] complete slate except one FBS team at 11")
    results, skipped, printed = run_census(synthetic_cache(title_games=9, short_one=True))
    check("nothing was skipped (title games are present)", skipped == [], f"skipped={skipped}")
    check("the census recorded at least one FAIL", not all(results), f"results={results}")
    check("the 12/13 outlier check names the short team",
          "outliers={" in printed and "11" in printed, repr(printed[-500:]))
    check("the flag-OFF all-at-12 check failed too",
          "[FAIL] flag OFF" in printed, repr(printed[-500:]))


def main():
    test_no_title_games_skips_the_census_without_failing()
    test_complete_slate_passes_with_counts_derived_from_the_cache()
    test_a_short_slate_fails_loudly()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
