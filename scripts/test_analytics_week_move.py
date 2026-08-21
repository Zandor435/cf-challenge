#!/usr/bin/env python3
"""
test_analytics_week_move.py — Prior-snapshot selection for Board 3's week_move.

WHAT THIS EXISTS TO STOP. week_move (both kinds — race's signed places, and
championship_odds' probability delta) is measured against the most recent
timeline snapshot strictly before the week being scored. Selecting the WRONG
snapshot does not crash and does not look wrong: it produces correct arithmetic
against the wrong board, and ships a confident number that describes nothing.

The 2026 preseason produced exactly that. `analytics.prior_snapshot` used to
short-circuit its strictly-before test whenever the effective week was
unresolvable:

    and (eff_week is None or s["as_of_week"] < eff_week)

With no --as-of-week and a cache reporting `week: null` (the ordinary preseason
state), eff_week was None, so EVERY snapshot passed the filter and max() handed
back the highest week on file — a week-16 snapshot left behind by the 2025
replay. Blaine read `week_move: -3` and a probability move of -0.918871 against
a board from a season that had already ended.

The module's own rule already answered this: "Honest null beats a confident
zero." A confident wrong number is rejected for the same reason.

Four properties are pinned here:
  1. An unresolvable week refuses — prior is None and every week_move is null,
     rather than falling through to the highest available snapshot.
  2. A REAL prior week still selects exactly what it selected before, and
     computes exactly the values it computed before. The arithmetic was never
     wrong; only the selection was. Values are pinned as literals so a future
     change to the math cannot pass this file.
  3. Selection never reaches across a season boundary. timeline.json is
     append-only across seasons and its snapshots are keyed by week ALONE, so
     last season's week 6 is indistinguishable from this season's by week.
  4. The unresolvable-week refusal is NOT one catch. It covers two different
     situations wearing one symptom — benign preseason, and a live season whose
     cache lost its week tag — and the second must not go dark behind the first.
     Both emit null; only the fault emits a ::warning::.

Offline: scores off data/fixtures/contract_cache.json via
utils.pin_contract_fixture(), the same frozen artifact test_output_shape.py
uses, so nothing here depends on what the live cache happens to hold.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_analytics_week_move.py
    python scripts/test_analytics_week_move.py
"""

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import scoring
import projector
import run_groups
import analytics

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def zero_manager(mid, rank):
    """A drafted manager whose season has not started: a real pick, nothing
    banked. The literal shape of every group in the 2026 preseason."""
    return {"manager_id": mid, "display_name": mid.title(), "banked_total": 0.0,
            "floor": -12.0, "ceiling": 12.0, "rank": rank,
            "picks": [{"team": "Ohio State", "conference": "Big Ten", "line": 9.5,
                       "direction": "O", "banked_wins": 0, "banked_losses": 0,
                       "games_remaining": 12, "banked_delta": -9.5, "floor": -9.5,
                       "ceiling": 2.5, "status": "LIVE"}]}


@contextlib.contextmanager
def no_cache_week():
    """Make utils.cache_meta report `week: null` for this block.

    That is the ordinary preseason cache state and the only way to drive
    build_analytics down the unresolvable-week path end to end — the frozen
    contract fixture is a COMPLETED season and reports week 16. Patches the
    utils accessor, never CACHE_PATH or a cache path literal (test_cache_access
    GUARD 1): cache I/O ownership stays where it belongs."""
    orig = utils.cache_meta

    def patched(season):
        meta = dict(orig(season))
        meta["week"] = None
        return meta

    utils.cache_meta = patched
    try:
        yield
    finally:
        utils.cache_meta = orig


def capture(fn):
    """(return value, everything it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn()
    return out, buf.getvalue()


def moves(analytics_obj, module):
    return [m["week_move"] for m in analytics_obj[module]["managers"]]


_BOARDS = None


def _boards():
    """Everything main() built once up front, memoised.

    pin_contract_fixture() is called on EVERY entry, not just the first: it
    re-points the whole process at the frozen fixture and conftest.py restores
    utils' globals after each test, so a memoised board set alone would leave
    the second test computing against the live cache. Pinning is cheap (three
    globals and one json load); the board derivation is what gets cached.
    """
    global _BOARDS
    season = utils.pin_contract_fixture()
    if _BOARDS is None:
        print(f"  (scoring off the frozen contract fixture, season {season})")
        config, picks = utils.load_group(utils.TEST_GROUP_ID)

        st6 = scoring.build_standings(config, picks, 6)
        pr6 = projector.build_projection(config, picks, 6)
        st14 = scoring.build_standings(config, picks, 14)
        pr14 = projector.build_projection(config, picks, 14)

        # One week-6 snapshot, tagged with the season being scored - the shape
        # run_groups.append_timeline creates on a group's first scored week.
        tl = {"group_id": config["group_id"], "season": season,
              "snapshots": [run_groups.build_snapshot(st6, pr6, 6)]}
        # Two snapshots, week 6 and week 16: the shape that produced the bug.
        tl_many = {"group_id": config["group_id"], "season": season,
                   "snapshots": [run_groups.build_snapshot(st6, pr6, 6),
                                 run_groups.build_snapshot(st14, pr14, 16)]}
        _BOARDS = {"season": season, "config": config, "picks": picks,
                   "st6": st6, "pr6": pr6, "st14": st14, "pr14": pr14,
                   "tl": tl, "tl_many": tl_many}
    return _BOARDS


def test_select_prior_week():
    """[1] the week test: strictly-before, and NEVER the highest on file."""
    print("\nselect_prior - week")
    b = _boards()
    season, tl, tl_many = b["season"], b["tl"], b["tl_many"]

    snap, reason = analytics.select_prior(tl, 14, season)
    check("a real effective week selects the latest snapshot strictly before it",
          snap is not None and snap["as_of_week"] == 6 and reason is None,
          f"week={snap and snap['as_of_week']} reason={reason}")

    check("the current week's own snapshot is never the baseline",
          analytics.select_prior(tl, 6, season)
          == (None, analytics.PRIOR_NO_EARLIER_WEEK))

    check("no timeline at all => refuse, named",
          analytics.select_prior(None, 14, season)
          == (None, analytics.PRIOR_NO_TIMELINE))

    # THE REGRESSION. Before the fix this returned the week-16 snapshot: with
    # eff_week None the strictly-before test short-circuited to True, so every
    # snapshot qualified and max() took the highest.
    check("unresolvable week => refuse, NOT the highest snapshot on file",
          analytics.select_prior(tl_many, None, season)
          == (None, analytics.PRIOR_WEEK_UNRESOLVED),
          f"got {analytics.select_prior(tl_many, None, season)[0]}")

    # Same assertion against the real committed timeline, which is the artifact
    # that actually produced the wrong numbers. Non-vacuous only while that file
    # still carries integer-week snapshots, so say which case ran.
    live = Path("docs/data/panel/timeline.json")
    if live.exists():
        tl_live = json.loads(live.read_text(encoding="utf-8"))
        weeks = [s.get("as_of_week") for s in tl_live.get("snapshots", [])
                 if isinstance(s.get("as_of_week"), int)]
        check("committed panel timeline + unresolvable week => refuse",
              analytics.select_prior(tl_live, None, tl_live.get("season"))[0] is None,
              f"integer-week snapshots present: {weeks or 'none (check is vacuous)'}")


def test_select_prior_season_boundary():
    """[2] a week alone does not identify a snapshot; the season has to agree."""
    print("\nselect_prior - season boundary")
    b = _boards()
    season, config, tl = b["season"], b["config"], b["tl"]

    check("a timeline declaring another season is refused whatever the week",
          analytics.select_prior(tl, 14, season + 1)
          == (None, analytics.PRIOR_SEASON_MISMATCH))

    check("...including when its week would otherwise qualify (wk6 < wk7)",
          analytics.select_prior(tl, 7, season + 1)[0] is None)

    check("the same timeline IS eligible for its own season",
          analytics.select_prior(tl, 14, season)[0]["as_of_week"] == 6)

    # Snapshots carry no season of their own. When the FILE declares none
    # either, there is nothing to compare and this check must not fire - the
    # alternative is inventing a guarantee the data does not make.
    untagged = {"group_id": config["group_id"], "snapshots": tl["snapshots"]}
    check("an untagged timeline is not blocked (no season is invented)",
          analytics.select_prior(untagged, 14, season + 1)[0]["as_of_week"] == 6)

    check("season is only compared when the caller supplies one",
          analytics.select_prior(tl, 14, None)[0]["as_of_week"] == 6)


def test_any_games_banked():
    """[3] the discriminator that tells preseason apart from a live-season fault."""
    print("\nany_games_banked")
    st14 = _boards()["st14"]
    check("a scored board reports played", analytics.any_games_banked(st14) is True)
    check("an all-zero board reports not-played",
          analytics.any_games_banked(
              {"managers": [zero_manager("a", 1), zero_manager("b", 2)]}) is False)
    check("a single banked LOSS still counts as played",
          analytics.any_games_banked({"managers": [{"manager_id": "a", "picks": [
              {"banked_wins": 0, "banked_losses": 1}]}]}) is True)
    check("no managers at all reports not-played",
          analytics.any_games_banked({"managers": []}) is False)


def test_build_analytics_real_prior_week_unchanged():
    """[4] the arithmetic was never wrong - only the selection was.

    Pinned as literals so this file fails if the math MOVES, not merely if it
    changes shape.
    """
    print("\nbuild_analytics - a real prior week still computes what it did")
    b = _boards()
    an14 = analytics.build_analytics(b["config"], b["st14"], b["pr14"],
                                     timeline=b["tl"], as_of_week=14)
    check("race.prior_week == 6", an14["race"]["prior_week"] == 6)
    check("championship_odds.prior_week == 6",
          an14["championship_odds"]["prior_week"] == 6)
    check("race week_move values unchanged",
          [(m["manager_id"], m["week_move"]) for m in an14["race"]["managers"]]
          == [("owner-3", 1), ("owner-2", -1), ("owner-1", 0)],
          str([(m["manager_id"], m["week_move"]) for m in an14["race"]["managers"]]))
    check("race week_move sums to 0 (ranks are a permutation)",
          sum(m["week_move"] for m in an14["race"]["managers"]) == 0)
    check("championship_odds week_move values unchanged",
          [(m["manager_id"], m["week_move"])
           for m in an14["championship_odds"]["managers"]]
          == [("owner-3", 0.628733), ("owner-1", -0.012167), ("owner-2", -0.616567)],
          str([(m["manager_id"], m["week_move"])
               for m in an14["championship_odds"]["managers"]]))


def test_build_analytics_unresolvable_week_emits_null():
    """[5] unresolvable week end to end: null, never a number."""
    print("\nbuild_analytics - unresolvable week emits null, not a number")
    b = _boards()
    an14 = analytics.build_analytics(b["config"], b["st14"], b["pr14"],
                                     timeline=b["tl"], as_of_week=14)
    with no_cache_week():
        an_pre, _ = capture(lambda: analytics.build_analytics(
            b["config"], b["st14"], b["pr14"], timeline=b["tl_many"], as_of_week=None))
    check("race.prior_week is null", an_pre["race"]["prior_week"] is None)
    check("championship_odds.prior_week is null",
          an_pre["championship_odds"]["prior_week"] is None)
    check("every race week_move is null",
          all(v is None for v in moves(an_pre, "race")),
          str(moves(an_pre, "race")))
    check("every championship_odds week_move is null",
          all(v is None for v in moves(an_pre, "championship_odds")),
          str(moves(an_pre, "championship_odds")))
    # The MOVE is what is unknowable, not the odds. Nulling the whole module
    # would be its own overreach.
    check("p_win_pool is untouched — only the move is null",
          all(m["p_win_pool"] is not None
              for m in an_pre["championship_odds"]["managers"]))
    check("the exact modules are unaffected",
          [m["banked_total"] for m in an_pre["race"]["managers"]]
          == [m["banked_total"] for m in an14["race"]["managers"]]
          and len(an_pre["portfolio"]["managers"]) == len(b["st14"]["managers"]))


def test_preseason_and_fault_stay_distinguishable():
    """[6] one symptom, two situations - kept apart.

    Both refuse and both emit null. Only one is a fault, and it has to be
    possible to tell which happened from the run's own output.
    """
    print("\nbuild_analytics - preseason and a live-season fault stay distinguishable")
    b = _boards()
    st_zero = {"managers": [zero_manager("a", 1), zero_manager("b", 2)]}
    with no_cache_week():
        an_benign, log_benign = capture(lambda: analytics.build_analytics(
            b["config"], st_zero, None, timeline=b["tl_many"], as_of_week=None))
        an_fault, log_fault = capture(lambda: analytics.build_analytics(
            b["config"], b["st14"], b["pr14"], timeline=b["tl_many"], as_of_week=None))

    check("preseason (nothing banked) does not raise a warning",
          "::warning::" not in log_benign, log_benign.strip() or "(silent)")
    check("preseason still says out loud which null it emitted",
          "preseason" in log_benign.lower())
    check("games banked + no week DOES raise a warning",
          "::warning::" in log_fault, log_fault.strip() or "(silent)")
    check("the warning refuses the preseason label explicitly",
          "not the preseason" in log_fault.lower())
    check("the two paths do not produce the same log line",
          log_benign.strip() != log_fault.strip())
    # Reported, not repaired: a fault does not license a guessed number.
    check("both paths still emit null week_move",
          all(v is None for v in moves(an_benign, "race"))
          and all(v is None for v in moves(an_fault, "race")))


def test_ordinary_refusals_stay_quiet():
    """[7] a group's first scored week has no earlier snapshot and never will.

    If that logged, every run would carry noise that means nothing.
    """
    print("\nbuild_analytics - ordinary refusals stay quiet")
    b = _boards()
    _, log_first = capture(lambda: analytics.build_analytics(
        b["config"], b["st6"], b["pr6"], timeline=b["tl"], as_of_week=6))
    check("first scored week logs nothing", log_first.strip() == "",
          log_first.strip() or "(silent)")
    _, log_none = capture(lambda: analytics.build_analytics(
        b["config"], b["st14"], b["pr14"], timeline=None, as_of_week=14))
    check("no timeline logs nothing", log_none.strip() == "",
          log_none.strip() or "(silent)")


def main():
    test_select_prior_week()
    test_select_prior_season_boundary()
    test_any_games_banked()
    test_build_analytics_real_prior_week_unchanged()
    test_build_analytics_unresolvable_week_emits_null()
    test_preseason_and_fault_stay_distinguishable()
    test_ordinary_refusals_stay_quiet()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
