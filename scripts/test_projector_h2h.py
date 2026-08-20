#!/usr/bin/env python3
"""
test_projector_h2h.py — Head-to-head games are ONE event (ARCHITECTURE §3).

When two PICKED teams play each other, the pool sim must draw that game once and
give the two sides opposite outcomes. Before coupling, each team's remaining
games were drawn independently, so in ~p*q of trials BOTH teams won the same
game and in ~(1-p)*(1-q) both lost it. Neither outcome exists. It is not a rare
corner either — at week 6 the four real groups carry 5 / 10 / 17 / 28 such games.

This is the same class of defect ARCHITECTURE §3 already treats as serious. That
one was two MANAGERS on opposite sides of one team; this is two TEAMS playing
each other.

What is asserted here:
  - COHERENCE: across every trial, exactly one side of a head-to-head game wins
    it. Zero tolerance — this is a logical impossibility, not a tolerance band.
  - COMPLEMENTARITY: p_A + p_B == 1 for every pair. HFA is antisymmetric and
    logistic(-x) == 1 - logistic(x), so the two sides are already exact
    complements; the coupling relies on it and must not paper over a violation.
  - MARGINALS PRESERVED: coupling changes the JOINT distribution only. Every
    team's simulated mean wins must still match banked_wins + sum(p_i), and every
    PER-PICK field must be untouched (they come from the Poisson-binomial over
    that pick's own probs and never touch the sim at all).
  - UNPAIRED PATH: a group with zero head-to-head games is drawn exactly as
    before, one column per game.
  - ASYMMETRY FAILS LOUD: a slate where A lists B but B does not list A is a
    disagreement about reality, and is refused rather than silently uncoupled.

Scored off the frozen contract fixture where a fixture is needed, and off the
real committed groups for the pair census (that is the number that makes the
defect real).

Usage:
    python scripts/test_projector_h2h.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import projector as P

WK = 6                     # mid-season: every team still has real games left
_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _cache(slug, as_of_week=WK):
    config, picks = utils.load_group(slug)
    return config, picks, P._team_cache(config, picks, as_of_week,
                                        utils.season_sp_ratings(utils.get_season()))


# --- 1. coherence ------------------------------------------------------------

def test_coherence():
    print("\n[1] coherence: exactly one side of a head-to-head game wins it")
    total_pairs = 0
    bad_both_win = bad_both_lose = 0
    census = []

    for slug in utils.get_all_group_ids():
        _, _, tc = _cache(slug)
        rng = np.random.default_rng(1234)
        wins, pairs = P.simulate_game_wins(tc, rng, trials=4000)
        census.append(f"{slug}={len(pairs)}")
        for info in pairs.values():
            total_pairs += 1
            (ta, gia), (tb, gib) = info["slots"].items()
            a = wins[ta][:, gia]
            b = wins[tb][:, gib]
            bad_both_win += int(np.count_nonzero(a & b))
            bad_both_lose += int(np.count_nonzero(~a & ~b))

    check("real groups actually contain head-to-head games (the defect is real)",
          total_pairs > 0, ", ".join(census))
    check("no trial has BOTH sides winning the same game", bad_both_win == 0,
          f"{bad_both_win} violations across {total_pairs} pairs")
    check("no trial has BOTH sides losing the same game", bad_both_lose == 0,
          f"{bad_both_lose} violations across {total_pairs} pairs")


# --- 2. complementarity ------------------------------------------------------

def test_complementarity():
    print("\n[2] complementarity: p_A + p_B == 1 for every head-to-head pair")
    worst = 0.0
    n = 0
    for slug in utils.get_all_group_ids():
        _, _, tc = _cache(slug)
        for info in P.head_to_head_pairs(tc).values():
            (ta, gia), (tb, gib) = info["slots"].items()
            s = tc[ta]["probs"][gia] + tc[tb]["probs"][gib]
            worst = max(worst, abs(s - 1.0))
            n += 1
    check("every pair's win probs are exact complements", worst < 1e-9,
          f"max |p_A+p_B-1| = {worst:.2e} over {n} pairs")


# --- 3. marginals preserved --------------------------------------------------

def test_marginals_preserved():
    print("\n[3] marginals: coupling changes the joint, never a single team's season")
    worst_team, worst_err = None, 0.0
    for slug in utils.get_all_group_ids():
        _, _, tc = _cache(slug)
        rng = np.random.default_rng(99)
        wins, _ = P.simulate_game_wins(tc, rng, trials=40000)
        for team, info in tc.items():
            probs = np.asarray(info["probs"])
            if not probs.size:
                continue
            expected = float(probs.sum())
            observed = float(wins[team].sum(axis=1).mean())
            err = abs(observed - expected)
            if err > worst_err:
                worst_team, worst_err = f"{slug}/{team}", err
    # 40k trials, <=13 games: MC standard error on the mean is well under 0.02
    check("every team's simulated mean wins matches sum(p_i)", worst_err < 0.05,
          f"worst {worst_team} off by {worst_err:.4f}")


def test_per_pick_fields_untouched():
    print("\n[4] per-pick fields are Poisson-binomial only — the sim cannot move them")
    season = utils.pin_contract_fixture()
    config, picks = utils.load_group(utils.TEST_GROUP_ID)
    pr = P.build_projection(config, picks, as_of_week=WK)
    tc = P._team_cache(config, picks, WK, utils.season_sp_ratings(season))

    ok_delta = ok_beat = ok_dist = True
    for m in pr["managers"]:
        for p in m["picks"]:
            info = tc[p["team"]]
            probs = info["probs"]
            bw = info["banked_wins"]
            dist = P.poisson_binomial(probs)
            finals = bw + np.arange(len(dist))
            exp_final = bw + float(sum(probs))
            exp_delta = P.signed_delta(p["direction"], exp_final, p["line"])
            beat = float(dist[finals > p["line"]].sum()) if p["direction"] == "O" \
                else float(dist[finals < p["line"]].sum())
            ok_delta &= (p["expected_delta"] == round(exp_delta, 2))
            ok_beat &= (p["p_beat_line"] == round(beat, 6))
            ok_dist &= (len(p["win_distribution"]) == len(dist))
    check("expected_delta reproduces exactly from the pick's own probs", ok_delta)
    check("p_beat_line reproduces exactly from the pick's own probs", ok_beat)
    check("win_distribution length unchanged", ok_dist)


# --- 5. the unpaired path ----------------------------------------------------

def test_unpaired_group_uses_one_column_per_game():
    print("\n[5] a group with zero head-to-head games keeps one draw column per game")
    config = {"group_id": "nopairs", "count_conference_championship": False,
              "managers": [{"manager_id": "solo", "display_name": "Solo"}]}
    # four teams that do not play each other in the remaining slate
    picks = [{"manager": "solo", "team": t, "line": 8.5, "direction": "O", "conference": "x"}
             for t in ("Ohio State", "Clemson", "Utah", "Memphis")]
    tc = P._team_cache(config, picks, WK, utils.season_sp_ratings(utils.get_season()))
    pairs = P.head_to_head_pairs(tc)
    slot_index, invert, p_slot, n = P._draw_plan(tc)
    n_games = sum(len(v["probs"]) for v in tc.values())
    check("no pairs found for a game-disjoint roster", len(pairs) == 0, f"{len(pairs)} pairs")
    check("draw columns == total games (nothing shared)", p_slot.size == n_games,
          f"{p_slot.size} columns / {n_games} games")
    check("nothing inverted on the unpaired path",
          not any(v.any() for v in invert.values()))


# --- 6. asymmetry fails loud -------------------------------------------------

def test_asymmetry_fails_loud():
    print("\n[6] a slate the two teams disagree about is refused, not silently uncoupled")
    _, _, tc = _cache("panel")
    pairs = P.head_to_head_pairs(tc)
    if not pairs:
        check("panel has a pair to corrupt", False, "no head-to-head games found")
        return
    info = next(iter(pairs.values()))
    (ta, gia), (tb, _) = info["slots"].items()
    # Delete ONE side's view of a game both teams currently list.
    doctored = {t: dict(v) for t, v in tc.items()}
    doctored[ta] = dict(tc[ta])
    doctored[ta]["remaining_games"] = [g for i, g in enumerate(tc[ta]["remaining_games"])
                                       if i != gia]
    doctored[ta]["probs"] = [p for i, p in enumerate(tc[ta]["probs"]) if i != gia]
    try:
        P.head_to_head_pairs(doctored)
        check("asymmetric slate raises", False, "no exception")
    except ValueError as e:
        named = ta in str(e) and tb in str(e)
        check("asymmetric slate raises ValueError", True)
        check("the error NAMES both offending teams", named, str(e)[:96])


def main():
    test_coherence()
    test_complementarity()
    test_marginals_preserved()
    test_unpaired_group_uses_one_column_per_game()
    test_asymmetry_fails_loud()
    test_per_pick_fields_untouched()      # LAST: pins the process to the fixture season

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
