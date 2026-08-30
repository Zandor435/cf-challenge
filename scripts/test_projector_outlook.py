#!/usr/bin/env python3
"""
test_projector_outlook.py — the per-game outlook + pace read on projection.json.

These two blocks are what the home board's drill-down renders: `remaining_games`
(one row per unplayed game, with a likely-win / toss-up / likely-loss bucket) and
`pace` (how many wins the model would have expected by now, against how many were
actually banked).

Both are DERIVED, not modelled. The whole point of the design is that they are
views of numbers projector.py already computed for the Poisson-binomial — so the
risk here is never "is the model right", it is "did the view drift from the model
it claims to be showing". That is what this file asserts:

  - BUCKET BOUNDARIES are closed the way the contract says: >= 0.65 is a likely
    win, <= 0.35 a likely loss, and the open interval between them is a toss-up.
    The boundaries themselves are tested explicitly because "<=" vs "<" at 0.35
    is exactly the kind of edge that silently reclassifies games.
  - ROWS MIRROR THE MODEL: remaining_games[i].p_win is the SAME probability the
    distribution was convolved from, paired with the SAME game — a zip over two
    parallel arrays is only correct while they stay parallel.
  - OUTLOOK IS TOTAL: the three counts account for every remaining game, so the
    page can never render a slate with games missing from the tally.
  - PACE RECONCILES, and is null (never 0) before anything is played — the
    honest-absence rule output-contract.md applies to share_of_delta.
  - PACE USES THE PLAYED SLATE, not the remaining one: expected_wins must be
    summed over the games team_state banked, which is the half that would break
    silently if played_games and banked_wins ever came off different derivations.

Scored off the frozen 2025 contract fixture wherever games must already be
PLAYED — the live cache is preseason, so it cannot exercise the pace path at all.

Runs both ways: pytest collects one test per section and conftest.py raises on
any check() recorded as FAIL; the standalone runner sums the same ledger.

Usage:
    python -m pytest scripts/test_projector_outlook.py
    python scripts/test_projector_outlook.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import projector as P

# The check ledger — same contract as the other projector tests. conftest.py
# clears it before every pytest test and raises on any recorded FAIL.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# A synthetic one-manager group over teams the frozen fixture has full 2025
# seasons for. Deliberately NOT a real group: the real ones are 2026 picks and
# the fixture is 2025, and the point here is the played-slate arithmetic, not
# anybody's actual roster.
FIXTURE_TEAMS = ["Ohio State", "Texas", "Indiana"]


def _fixture_projection():
    """build_projection over the pinned 2025 fixture. PINS THE PROCESS — every
    later cache read in this process is the fixture (utils._SEASON_CACHE is a
    module-level memo mutated in place; see the warning in utils.py). Sections
    that need the LIVE cache must therefore run before this one."""
    utils.pin_contract_fixture()
    config = {"group_id": "outlook_fixture", "display_name": "Outlook Fixture",
              "count_conference_championship": False,
              "managers": [{"manager_id": "a", "display_name": "A"}]}
    picks = [{"manager": "a", "team": t, "conference": "X",
              "direction": "O", "line": 8.5} for t in FIXTURE_TEAMS]
    return config, picks, P.build_projection(config, picks)


# --- 1. bucket boundaries ----------------------------------------------------

def test_bucket_boundaries():
    print("\n[1] buckets are cut exactly where the contract says")
    win_t, loss_t = P.LIKELY_WIN_THRESHOLD, P.LIKELY_LOSS_THRESHOLD

    check("thresholds are the documented 0.65 / 0.35",
          (win_t, loss_t) == (0.65, 0.35), f"{win_t} / {loss_t}")
    # The boundaries are CLOSED into the confident buckets on both sides.
    check("p == 0.65 is a likely_win (closed above)",
          P.game_bucket(win_t) == "likely_win")
    check("p == 0.35 is a likely_loss (closed below)",
          P.game_bucket(loss_t) == "likely_loss")
    # ...and the open interval between them is the toss-up.
    check("just inside 0.65 is a toss_up", P.game_bucket(win_t - 1e-9) == "toss_up")
    check("just inside 0.35 is a toss_up", P.game_bucket(loss_t + 1e-9) == "toss_up")
    check("0.50 is a toss_up", P.game_bucket(0.5) == "toss_up")
    check("the extremes land where expected",
          P.game_bucket(1.0) == "likely_win" and P.game_bucket(0.0) == "likely_loss")

    # Totality: every probability in [0,1] gets exactly one valid bucket. This is
    # what lets game_outlook's tally be asserted equal to the game count.
    grid = [i / 1000.0 for i in range(1001)]
    valid = {"likely_win", "toss_up", "likely_loss"}
    check("every p in [0,1] maps to exactly one valid bucket",
          all(P.game_bucket(p) in valid for p in grid), f"{len(grid)} points")


# --- 2. the percent string ---------------------------------------------------

def test_percent_string_matches_house_rule():
    print("\n[2] p_win_pct is rendered here, not in the browser")
    # +0.5 rounding, matching build_rail.py's _percent — half goes UP, not to
    # even, so two surfaces can never print a different number for one game.
    cases = [(0.0, "0%"), (0.5, "50%"), (0.505, "51%"), (0.504, "50%"),
             (0.995, "100%"), (1.0, "100%"), (0.125, "13%")]
    bad = [(p, P._percent(p), want) for p, want in cases if P._percent(p) != want]
    check("_percent rounds +0.5 and formats as a whole-percent string",
          not bad, f"mismatches: {bad}" if bad else f"{len(cases)} cases")


# --- 3. rows mirror the model (LIVE cache — must run before the fixture pin) --

def test_rows_mirror_the_model():
    print("\n[3] remaining_games is a view of the model, not a second model")
    slug = utils.get_all_group_ids()[0]
    config, picks = utils.load_group(slug)
    sp = utils.season_sp_ratings(utils.get_season())
    pr = P.build_projection(config, picks)

    ok_pair = ok_bucket = ok_count = ok_order = True
    n_games = 0
    for m in pr["managers"]:
        for p in m["picks"]:
            st = utils.team_state(p["team"], config)
            probs = P.remaining_win_probs(st, sp)
            rows = p["remaining_games"]
            ok_count &= (len(rows) == len(probs) == st["games_remaining"])
            for g, src, q in zip(rows, st["remaining_games"], probs):
                n_games += 1
                # SAME game, SAME probability — the zip stayed parallel.
                ok_pair &= (g["opponent"] == src["opponent"]
                            and g["week"] == src["week"]
                            and g["home_away"] == src["home_away"]
                            and abs(g["p_win"] - q) < 5e-5)
                ok_bucket &= (g["bucket"] == P.game_bucket(q))
            weeks = [g["week"] for g in rows if g["week"] is not None]
            ok_order &= (weeks == sorted(weeks))

    check("the real group actually has remaining games to check", n_games > 0,
          f"{slug}: {n_games} game rows")
    check("one row per remaining game", ok_count)
    check("each row carries ITS game's opponent/week/side and probability", ok_pair)
    check("each row's bucket is game_bucket of its own probability", ok_bucket)
    check("rows are in ascending week order", ok_order)


# --- 4. the tally is total ---------------------------------------------------

def test_outlook_tally_is_total():
    print("\n[4] outlook accounts for every remaining game")
    ok_sum = ok_bucket_counts = True
    n = 0
    for slug in utils.get_all_group_ids():
        config, picks = utils.load_group(slug)
        for m in P.build_projection(config, picks)["managers"]:
            for p in m["picks"]:
                rows, ol = p["remaining_games"], p["outlook"]
                n += 1
                ok_sum &= (sum(ol.values()) == len(rows))
                # ...and each count is the real tally of that bucket, not just a
                # set of three numbers that happen to add up.
                for bucket, key in (("likely_win", "likely_wins"),
                                    ("toss_up", "toss_ups"),
                                    ("likely_loss", "likely_losses")):
                    ok_bucket_counts &= (
                        ol[key] == sum(1 for g in rows if g["bucket"] == bucket))
    check("checked every pick in every real group", n > 0, f"{n} picks")
    check("outlook counts sum to the number of remaining games", ok_sum)
    check("each outlook count is that bucket's true tally", ok_bucket_counts)


# --- 5. preseason is an absence ----------------------------------------------

def test_pace_is_null_before_kickoff():
    print("\n[5] preseason pace is an absence, not a zero")
    ok = True
    n = 0
    for slug in utils.get_all_group_ids():
        config, picks = utils.load_group(slug)
        for m in P.build_projection(config, picks)["managers"]:
            for p in m["picks"]:
                pc = p["pace"]
                if pc["banked_games"] != 0:
                    continue
                n += 1
                ok &= (pc["state"] == "preseason"
                       and pc["expected_wins"] is None
                       and pc["delta"] is None)
    # The live cache is preseason today, so this is the shipping path. Once the
    # season starts this loop simply has fewer picks to score, which is why the
    # rule is also asserted directly against a constructed zero.
    check("pace_state(0, ...) is null-valued and labelled 'preseason'",
          P.pace_state(0, 0, 0.0) == {"banked_games": 0, "actual_wins": 0,
                                      "expected_wins": None, "delta": None,
                                      "state": "preseason"})
    check("every unplayed pick in the real groups reports it that way", ok,
          f"{n} unplayed picks")


# --- 6. pace arithmetic (FIXTURE — pins the process, keep it last) -----------

def test_pace_reconciles_on_played_games():
    print("\n[6] pace: expected wins come off the PLAYED slate and reconcile")
    config, picks, pr = _fixture_projection()
    sp = utils.season_sp_ratings(utils.get_season())

    ok_delta = ok_expected = ok_played = ok_state = True
    seen = []
    for m in pr["managers"]:
        for p in m["picks"]:
            pc = p["pace"]
            st = utils.team_state(p["team"], config)
            seen.append((p["team"], pc["banked_games"], pc["state"]))

            # played_games is the same derivation banked_wins came from.
            # scheduled-minus-remaining, not st["games_played"]: GUARD 2 in
            # test_cache_access.py reserves the raw banked key names and its AST
            # scan cannot tell a team_state read from a raw cache subscript.
            banked = st["games_scheduled"] - st["games_remaining"]
            ok_played &= (len(st["played_games"]) == banked == pc["banked_games"])
            ok_played &= (pc["actual_wins"] == st["banked_wins"])

            # expected_wins is the model replayed over THAT slate.
            want = sum(P.played_win_probs(st, sp))
            ok_expected &= (abs(pc["expected_wins"] - want) < 5e-3)
            ok_delta &= (abs((pc["actual_wins"] - pc["expected_wins"])
                             - pc["delta"]) < 5e-3)

            band = P.PACE_ON_PACE_BAND
            want_state = ("on_pace" if abs(pc["delta"]) < band
                          else ("ahead" if pc["delta"] > 0 else "behind"))
            ok_state &= (pc["state"] == want_state)

    check("the fixture picks really have played games",
          bool(seen) and all(g > 0 for _, g, _ in seen),
          "; ".join(f"{t}:{g}g:{s}" for t, g, s in seen))
    check("pace.banked_games/actual_wins agree with team_state", ok_played)
    check("expected_wins == sum of played_win_probs over the played slate", ok_expected)
    check("delta == actual_wins - expected_wins", ok_delta)
    check("state matches the on_pace band", ok_state)

    # A finished season has nothing left: no rows, and an empty tally.
    ok_empty = all(p["remaining_games"] == [] and sum(p["outlook"].values()) == 0
                   for m in pr["managers"] for p in m["picks"]
                   if utils.team_state(p["team"], config)["games_remaining"] == 0)
    check("a team with nothing left emits no rows and a zero tally", ok_empty)


def main():
    test_bucket_boundaries()
    test_percent_string_matches_house_rule()
    test_rows_mirror_the_model()
    test_outlook_tally_is_total()
    test_pace_is_null_before_kickoff()
    test_pace_reconciles_on_played_games()   # LAST: pins the process to the fixture

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
