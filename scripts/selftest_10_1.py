#!/usr/bin/env python3
"""
selftest_10_1.py — Verifies the §10.1 fetch+cache deliverables against the cache.

Assumes build_canonical.py and a real fetch_results.py pass have already run
(teams_canonical.json + cfbd_cache.json exist). Checks:
  1. cache holds results + schedules (per-team 12/13 counts) + SP+,
  2. at least one team has a 13th regular-season game,
  3. the resolver's ambiguity/alias behavior (§9),
  4. the failure path: --simulate-failure leaves the good cache untouched and
     exits non-fatally,
  5. the season guard: a wrong-season run refuses the cache and exits non-zero
     rather than scoring a clean-but-wrong board.

Usage:
    python scripts/selftest_10_1.py                    # season from season.json
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import load_json, resolve_team, AmbiguityError, UnknownTeamError, CANONICAL_PATH

SCRIPTS = Path(__file__).resolve().parent

_results = []


def check(name, ok, detail=""):
    _results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def test_cache(season):
    cache = utils.peek_cache()
    check("cache season matches", cache.get("season") == season, f"season={cache.get('season')}")
    check("cache holds results", cache["counts"]["games"] > 0, f"{cache['counts']['games']} games")
    check("cache holds SP+ ratings", cache["counts"]["sp_ratings"] > 0,
          f"{cache['counts']['sp_ratings']} teams")
    check("cache has fetched_at", bool(cache.get("fetched_at")), cache.get("fetched_at"))

    # Per-team scheduled counts for FBS teams (from canonical) must be 12 or 13
    # in the NEUTRAL cache (which counts every game, conf-title games included).
    canonical = {t["school"] for t in load_json(CANONICAL_PATH)["teams"]}
    teams = cache["teams"]
    fbs_present = [t for t in teams if t in canonical]
    outliers = {t: teams[t]["scheduled_games"] for t in fbs_present
                if teams[t]["scheduled_games"] not in (12, 13)}
    check("FBS teams have 12/13 scheduled games", len(outliers) == 0,
          f"{len(fbs_present)} FBS teams" + (f"; outliers={outliers}" if outliers else ""))

    # §1 conference-championship rule (the 13th game). CFBD returns conf-title
    # games as seasonType=regular, tagged per-game (conference_championship);
    # whether they count is per-group config. Assert the flag actually gates the
    # 13th game — NOT that "a 13th exists" (which asserted the old bug was present).
    games = cache["games"]
    off = utils.count_scheduled_games(games, count_conference_championship=False)
    off_fbs = {t: off.get(t, 0) for t in canonical}
    not12 = {t: n for t, n in off_fbs.items() if n != 12}
    check("flag OFF: all 136 FBS teams sit at 12",
          len(canonical) == 136 and not not12,
          f"{len(canonical)} FBS teams" + (f"; !=12: {not12}" if not12 else ""))

    on = utils.count_scheduled_games(games, count_conference_championship=True)
    at13_on = sorted(t for t in canonical if on.get(t, 0) == 13)
    champ = [g for g in games if g.get("conference_championship")]
    participants = {g["home_team"] for g in champ} | {g["away_team"] for g in champ}
    check("flag ON: exactly 18 FBS teams sit at 13", len(at13_on) == 18,
          f"{len(at13_on)}: {at13_on}")
    check("the 18 are exactly the 9 conference title-game pairings",
          len(champ) == 9 and set(at13_on) == (participants & canonical),
          f"{len(champ)} title games, {len(participants & canonical)} FBS participants")


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
    before = utils.peek_cache()
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "fetch_results.py"),
         "--season", str(season), "--simulate-failure"],
        capture_output=True, text=True,
    )
    after = utils.peek_cache()
    check("simulate-failure exits non-fatally (0)", proc.returncode == 0,
          f"exit={proc.returncode}")
    check("good cache survived the failed pull untouched", before == after)
    check("bypass warning emitted", "::warning::" in (proc.stdout + proc.stderr))


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
    print(f"RESULT: {passed}/{total} checks passed")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
