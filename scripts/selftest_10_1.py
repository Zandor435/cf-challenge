#!/usr/bin/env python3
"""
selftest_10_1.py — Verifies the §10.1 fetch+cache deliverables against the cache.

Assumes build_canonical.py and a real fetch_results.py pass have already run
(teams_canonical.json + cfbd_cache.json exist). Checks:
  1. cache holds results + schedules (per-team 12/13 counts) + SP+,
  2. the conf-title flag gates the 13th game: every FBS team at 12 with it off,
     exactly the title-game participants at 13 with it on,
  3. the resolver's ambiguity/alias behavior (§9),
  4. the failure path: --simulate-failure leaves the good cache untouched and
     takes the documented branch for the cache's age (fresh -> exit 0 with a
     ::warning::, past STALE_DAYS -> exit 4 naming the ceiling),
  5. the season guard: a wrong-season run refuses the cache and exits non-zero
     rather than scoring a clean-but-wrong board.

This reads the LIVE committed cache, not the pinned contract fixture, so two
things about a live cache are season state rather than code: CFBD posts a new
season's conference title games months after the rest of the slate, and a
cache can age past the staleness ceiling between fetches. Neither is allowed
to be a failure CI is told to ignore. The census (1-2) SKIPS with its reason
printed until the title games land; the bypass test (4) asserts whichever
branch the cache is actually in. Everything that is reported is asserted.

Usage:
    python scripts/selftest_10_1.py                    # season from season.json
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import fetch_results as fr
from utils import load_json, resolve_team, AmbiguityError, UnknownTeamError, CANONICAL_PATH

SCRIPTS = Path(__file__).resolve().parent

_results = []
_skipped = []


def check(name, ok, detail=""):
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def skip(name, reason):
    """A check the cache's season state cannot support yet. Printed with its
    reason and counted separately -- never a silent pass, never a failure."""
    _skipped.append(name)
    print(f"  [SKIP] {name} — {reason}")


def test_cache(season):
    cache = utils.peek_cache()
    check("cache season matches", cache.get("season") == season, f"season={cache.get('season')}")
    check("cache holds results", cache["counts"]["games"] > 0, f"{cache['counts']['games']} games")
    check("cache holds SP+ ratings", cache["counts"]["sp_ratings"] > 0,
          f"{cache['counts']['sp_ratings']} teams")
    check("cache has fetched_at", bool(cache.get("fetched_at")), cache.get("fetched_at"))

    # The schedule census below asserts properties of a COMPLETE regular-season
    # slate: every FBS team at 12 with conf-title games excluded, and exactly
    # the title-game participants at 13 with them included. CFBD posts a new
    # season's conference title games months after the rest of the slate (the
    # 2026 cache on 2026-08-21 held 0 of them, with the Pac-12 still at 11
    # games), so before they land the census cannot hold and that is not a
    # code defect. Skipped with the reason printed -- never failed, never
    # ignored: the old shape failed here on every pre-season cache and hid
    # behind continue-on-error in CI, which would have hidden a real
    # regression in [2]-[4] just as well.
    games = cache["games"]
    champ = [g for g in games if g.get("conference_championship")]
    if not champ:
        skip("schedule census (12/13 per FBS team; title-game participants at 13)",
             f"the season-{season} cache holds 0 conference title games -- CFBD "
             f"has not posted them yet, so the slate is not complete enough to "
             f"census. Runs again on its own once a fetch brings them in.")
        return

    canonical_records = load_json(CANONICAL_PATH)["teams"]
    canonical = {t["school"] for t in canonical_records}
    fbs_conferences = {t["conference"] for t in canonical_records}
    teams = cache["teams"]
    # teams_canonical.json carries a 2026 FBS addendum (Sacramento State, which
    # was still FCS in 2025), so the canonical roster can be larger than the
    # cache season's FBS field. Scope the census to canonical teams the cache
    # actually has in an FBS conference that season — a magic 136 against the
    # whole file would drift every time a newcomer is added, and a magic 18/9
    # would drift the first time a conference stopped playing a title game.
    # Every expected count below is derived from the cache itself.
    fbs_this_season = {t for t in canonical
                       if teams.get(t, {}).get("conference") in fbs_conferences}

    # Per-team scheduled counts for FBS teams must be 12 or 13 in the NEUTRAL
    # cache (which counts every game, conf-title games included).
    outliers = {t: teams[t]["scheduled_games"] for t in sorted(fbs_this_season)
                if teams[t]["scheduled_games"] not in (12, 13)}
    check("FBS teams have 12/13 scheduled games", len(outliers) == 0,
          f"{len(fbs_this_season)} FBS teams" + (f"; outliers={outliers}" if outliers else ""))

    # §1 conference-championship rule (the 13th game). CFBD returns conf-title
    # games as seasonType=regular, tagged per-game (conference_championship);
    # whether they count is per-group config. Assert the flag actually gates the
    # 13th game — NOT that "a 13th exists" (which asserted the old bug was present).
    off = utils.count_scheduled_games(games, count_conference_championship=False)
    not12 = {t: off.get(t, 0) for t in sorted(fbs_this_season) if off.get(t, 0) != 12}
    check("flag OFF: every FBS team this season sits at 12", not not12,
          f"{len(fbs_this_season)} FBS teams this season "
          f"({len(canonical)} canonical incl. later-season additions)"
          + (f"; !=12: {not12}" if not12 else ""))

    on = utils.count_scheduled_games(games, count_conference_championship=True)
    at13_on = sorted(t for t in fbs_this_season if on.get(t, 0) == 13)
    participants = ({g["home_team"] for g in champ}
                    | {g["away_team"] for g in champ}) & fbs_this_season
    check(f"flag ON: exactly {2 * len(champ)} FBS teams sit at 13 "
          f"(two per title game, {len(champ)} games)",
          len(at13_on) == 2 * len(champ), f"{len(at13_on)}: {at13_on}")
    check("the teams at 13 are exactly the title-game participants",
          set(at13_on) == participants,
          f"{len(champ)} title games, {len(participants)} FBS participants")


def test_resolver():
    def raises(name, exc):
        try:
            resolve_team(name)
            return False
        except exc:
            return True
        except Exception:
            return False

    check("bare 'USC' raises ambiguity", raises("USC", AmbiguityError))
    check("bare 'Miami' raises ambiguity", raises("Miami", AmbiguityError))
    check("unknown name raises", raises("Notre Dame Fighting Leprechauns XYZ", UnknownTeamError))
    cases = {
        "Southern California": "USC",
        "Mississippi": "Ole Miss",
        "Ole Miss": "Ole Miss",
        "Ohio St.": "Ohio State",
        "Miami (OH)": "Miami (OH)",
        "Miami (FL)": "Miami",
        "Pitt": "Pittsburgh",
    }
    for name, expected in cases.items():
        try:
            got = resolve_team(name)
            check(f"resolve '{name}' -> '{expected}'", got == expected, f"got '{got}'")
        except Exception as e:  # canonical target may not exist in a given season
            check(f"resolve '{name}' -> '{expected}'", False, str(e))


def test_failure_path(season):
    """--simulate-failure exercises the commentary-bypass (§4) with zero
    network. fetch_results.degraded_exit() has TWO documented outcomes for a
    same-season cache, and this asserts whichever one the committed cache is in:

      within STALE_DAYS  -> exit 0, a ::warning::, cache untouched
      older than that    -> exit 4, the ceiling named, cache untouched

    Both are the contract. The old shape asserted only the first, so a cache
    that aged past the ceiling between fetches turned this into a red that CI
    had to be told to ignore."""
    before = utils.peek_cache()
    age = fr.cache_age_days(before.get("fetched_at"))
    stale = age is None or age > fr.STALE_DAYS
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "fetch_results.py"),
         "--season", str(season), "--simulate-failure"],
        capture_output=True, text=True,
    )
    after = utils.peek_cache()
    out = proc.stdout + proc.stderr
    if stale:
        shown = "unreadable" if age is None else f"{age:.1f}d old"
        print(f"  (cache fetched_at is {shown}, past the {fr.STALE_DAYS}-day "
              f"ceiling -- asserting the fail-loud branch)")
        check("stale fallback exits 4 (refuses to score stale data)",
              proc.returncode == 4, f"exit={proc.returncode}")
        check("error names the staleness ceiling",
              f"{fr.STALE_DAYS}-day ceiling" in out or "fetched_at" in out)
    else:
        print(f"  (cache is {age:.1f}d old, within the {fr.STALE_DAYS}-day "
              f"ceiling -- asserting the degraded-but-running branch)")
        check("simulate-failure exits non-fatally (0)", proc.returncode == 0,
              f"exit={proc.returncode}")
        check("bypass warning emitted", "::warning::" in out)
    check("good cache survived the failed pull untouched", before == after)


def test_season_guard(season):
    """A run for a DIFFERENT season, forced to fall back, must refuse the
    wrong-season cache and exit non-zero rather than score it (§4)."""
    wrong = season + 1  # cache on disk is `season`; request a mismatched season
    before = utils.peek_cache()
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "fetch_results.py"),
         "--season", str(wrong), "--simulate-failure"],
        capture_output=True, text=True,
    )
    after = utils.peek_cache()
    out = proc.stdout + proc.stderr
    check("wrong-season fallback exits NON-zero", proc.returncode != 0,
          f"exit={proc.returncode}")
    check("error names both seasons", str(season) in out and str(wrong) in out,
          f"requested {wrong}, cache {season}")
    check("wrong-season cache NOT overwritten/scored", before == after)


def main():
    ap = argparse.ArgumentParser()
    # No season literal: default is the single source (season.json).
    ap.add_argument("--season", type=int, default=None)
    args = ap.parse_args()
    args.season = args.season if args.season is not None else utils.get_season()

    print("=" * 60)
    print(f"SELFTEST §10.1 — season {args.season}")
    print("=" * 60)
    print("\n[1] cache contents")
    test_cache(args.season)
    print("\n[2] resolver (§9)")
    test_resolver()
    print("\n[3] failure path (commentary-bypass)")
    test_failure_path(args.season)
    print("\n[4] season guard (wrong-season cache refused)")
    test_season_guard(args.season)

    passed, total = sum(_results), len(_results)
    print("\n" + "=" * 60)
    print(f"RESULT: {passed}/{total} checks passed"
          + (f", {len(_skipped)} skipped (reasons above)" if _skipped else ""))
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
