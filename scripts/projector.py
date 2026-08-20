#!/usr/bin/env python3
"""
projector.py — Board 2: Projected Finish, ratings-driven (ARCHITECTURE §3, §10.3).

Emits docs/data/<group_id>/projection.json per docs/output-contract.md. Each
remaining game gets a win probability from the SP+ rating differential + home
field; a pick's additional wins are the exact Poisson-binomial over those games
(np.convolve, n<=13 — analytic, no Monte Carlo). Deterministic but clearly a
PROJECTION: it moves only because SP+ refreshes weekly (§6, the auto-reseed).

POOL ODDS use SHARED per-team draws (ARCHITECTURE §3): each trial draws every
team's remaining season once, then scores every manager off that same draw, so
managers on opposite sides of a team are correctly anti-correlated. Independent
draws mis-state P(win pool) by 5-7 points; test_projector_correlation.py guards it.

Never reads the cache or the raw banked index directly — utils owns both (guarded).

Usage:
    python scripts/projector.py --group all
    python scripts/projector.py --group church --as-of-week 6
    python scripts/projector.py --test --as-of-week 6
"""

import argparse
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils

# --- Tuning knobs (exposed on purpose — these get A/B'd, §4/§12) -------------
# scale/HFA JOINTLY fitted on the leak-free market bridge (calibrate_spread.py,
# 2026-07, 2021-2025): (scale, HFA) chosen so the SP+ projector reproduces the
# closing-market win probability. Point est 13.55/3.95 (95% CI scale [12.8,14.4],
# HFA [3.7,4.3]); adopted at 1-decimal. This is a LOWER bound on flatness — it
# uses final SP+, and live in-season SP+ is noisier, so the true live scale is
# higher. The old 11.0 (inherited, untested) sat BELOW the CI -> overconfident;
# the leaky 7.1 is refuted (§12). Re-fit both together each offseason — never mix
# a scale from one method with an HFA from another.
HOME_FIELD_ADVANTAGE_PTS = 4.0     # points added to the home team's SP+ margin
WIN_PROB_POINTS_SCALE = 13.5       # logistic scale (pts): p = 1/(1+exp(-margin/scale))
FCS_FALLBACK_RATING = -35.0        # SP+ for an unrated (typically FCS) opponent
POOL_SIM_TRIALS = 20000            # Monte-Carlo trials for shared-draw pool odds
POOL_SIM_SEED_BASE = 20250101      # base seed; combined w/ group + week for reproducibility


def game_win_prob(team_rating, opp_rating, is_home, neutral):
    """P(team beats opponent) from the SP+ margin + home field, via a logistic.
    Deterministic and defined for every scheduled game (§4 rejected the native
    spread-gated endpoint)."""
    margin = team_rating - opp_rating
    if not neutral:
        margin += HOME_FIELD_ADVANTAGE_PTS if is_home else -HOME_FIELD_ADVANTAGE_PTS
    return 1.0 / (1.0 + np.exp(-margin / WIN_PROB_POINTS_SCALE))


def _rating(sp_ratings, team):
    rec = sp_ratings.get(team)
    r = rec.get("rating") if rec else None
    return FCS_FALLBACK_RATING if r is None else float(r)


def remaining_win_probs(state, sp_ratings):
    """Per-game win prob for each of a team's remaining games (order = schedule)."""
    tr = _rating(sp_ratings, state["team"])
    probs = []
    for g in state["remaining_games"]:
        opp = _rating(sp_ratings, g["opponent"])
        probs.append(game_win_prob(tr, opp, g["home_away"] == "home", g["neutral"]))
    return probs


def poisson_binomial(probs):
    """Exact distribution over the number of successes across independent trials
    with distinct probabilities (Poisson-binomial), by convolving [1-p, p]."""
    dist = np.array([1.0])
    for p in probs:
        dist = np.convolve(dist, [1.0 - p, p])
    return dist


def signed_delta(direction, final_wins, line):
    """Delta in the pick's O/U direction; accepts scalars or numpy arrays."""
    return (final_wins - line) if direction == "O" else (line - final_wins)


def _safe_canonical(name):
    """resolve_canonical, or None for a name the resolver does not know.

    Deliberately swallowing here and ONLY here: a team's remaining_games list
    its opponents raw off the cache, and most slates include an FCS opponent
    that is legitimately absent from teams_canonical.json ('Eastern Illinois').
    An unresolvable opponent simply cannot be a picked team, so it can never be
    a head-to-head pair — the right answer is "not paired", not a crash. Every
    other resolve in this file stays strict (§9)."""
    try:
        return utils.resolve_canonical(name)
    except Exception:                                # noqa: BLE001 — see docstring
        return None


def _reference_side(team_a, entry_a, team_b):
    """Which side of a head-to-head game is the REFERENCE — the one whose win
    probability drives the single shared draw. The home team; at a neutral site
    the alphabetically-first canonical name (home_away is meaningless there).
    Deterministic, and written down because leverage will condition on it."""
    if entry_a["neutral"]:
        return min(team_a, team_b)
    return team_a if entry_a["home_away"] == "home" else team_b


def head_to_head_pairs(team_cache):
    """Every remaining game where BOTH participants are picked teams in this
    group, as {pair_key: {...}} — the games the sim must draw ONCE.

    pair_key is (week, lo_team, hi_team): identical viewed from either side, and
    week-qualified because a conference-championship rematch is a real second
    game between the same two teams.

    Fails loud on two conditions rather than papering over them:
      - ASYMMETRY: A lists B in week N but B does not list A in week N. The two
        teams disagree about the slate, which utils.team_state is supposed to
        make impossible (banked and remaining come off the same scan).
      - NON-COMPLEMENTARY probabilities: p_A + p_B != 1. HFA is applied
        antisymmetrically (+/-HFA, 0 at a neutral site) and the logistic
        satisfies logistic(-x) == 1 - logistic(x), so the two sides' win probs
        are already exact complements. If that ever stops holding, the coupling
        would be silently arbitrating a contradiction — better to stop.
    """
    by_key = {}
    for team in sorted(team_cache):
        for gi, g in enumerate(team_cache[team]["remaining_games"]):
            opp = _safe_canonical(g["opponent"])
            if opp is None or opp not in team_cache:
                continue                    # unpicked (or FCS) opponent — never paired
            lo, hi = sorted((team, opp))
            by_key.setdefault((g["week"], lo, hi), []).append((team, gi, g))

    pairs = {}
    for key, sides in by_key.items():
        week, lo, hi = key
        if len(sides) != 2:
            seen = ", ".join(f"{t} (game {gi})" for t, gi, _ in sides)
            raise ValueError(
                f"head-to-head slate asymmetry, week {week}: {lo} vs {hi} appears "
                f"{len(sides)} time(s) [{seen}] — expected exactly 2 (once from each "
                f"side). The two teams disagree about the slate; refusing to couple a "
                f"game that is not the same game on both sides.")
        (ta, gia, ga), (tb, gib, gb) = sides
        pa = team_cache[ta]["probs"][gia]
        pb = team_cache[tb]["probs"][gib]
        if abs(pa + pb - 1.0) > 1e-9:
            raise ValueError(
                f"head-to-head probabilities are not complementary, week {week}: "
                f"P({ta})={pa!r} + P({tb})={pb!r} = {pa + pb!r}, expected 1.0. The "
                f"logistic + antisymmetric HFA guarantee this; a violation means the "
                f"win-prob model changed and the coupling would be arbitrating a "
                f"contradiction.")
        ref = _reference_side(ta, ga, tb)
        pairs[key] = {
            "week": week,
            "reference": ref,
            "other": tb if ref == ta else ta,
            "neutral": bool(ga["neutral"]),
            # (team, game_index) for each side, so the draw plan can address them
            "slots": {ta: gia, tb: gib},
            "p_reference": pa if ref == ta else pb,
        }
    return pairs


def _draw_plan(team_cache):
    """Assign every (team, game) a column in ONE shared uniform matrix.

    Returns (slot_index, invert, p_slot, n_pairs):
      slot_index[team] -> int array, the draw column for each of its games
      invert[team]     -> bool array, True where this team wins iff the draw
                          says the REFERENCE side lost
      p_slot           -> float array, the reference win prob per column

    A head-to-head game gets ONE column shared by both teams, with the
    non-reference side inverted — so a trial can never have both sides winning,
    and never both losing. Every other game gets its own column and is its own
    reference, which is exactly the old behavior."""
    pairs = head_to_head_pairs(team_cache)

    slot_index = {t: [None] * len(team_cache[t]["probs"]) for t in team_cache}
    invert = {t: [False] * len(team_cache[t]["probs"]) for t in team_cache}
    p_slot = []

    for info in pairs.values():                     # paired games first
        col = len(p_slot)
        p_slot.append(info["p_reference"])
        for team, gi in info["slots"].items():
            slot_index[team][gi] = col
            invert[team][gi] = (team != info["reference"])

    for team in sorted(team_cache):                 # everything else: its own column
        for gi, p in enumerate(team_cache[team]["probs"]):
            if slot_index[team][gi] is None:
                slot_index[team][gi] = len(p_slot)
                p_slot.append(p)

    return ({t: np.asarray(v, dtype=int) for t, v in slot_index.items()},
            {t: np.asarray(v, dtype=bool) for t, v in invert.items()},
            np.asarray(p_slot, dtype=float),
            len(pairs))


def simulate_game_wins(team_cache, rng, trials=None):
    """Per-team, per-GAME win indicators: {team -> (trials, n_games) bool array},
    plus the pair map that produced them.

    THE coupling point of the whole sim. A head-to-head game between two picked
    teams is ONE event: both sides read the same draw column and the
    non-reference side is inverted, so across every trial exactly one of them
    wins it. Drawing the two sides independently (the pre-coupling behavior) put
    both teams in the win column of the same game in ~p*q of trials — an outcome
    that does not exist, and one that leaks straight into p_win_pool.

    Marginals are untouched by construction: the non-reference side wins with
    probability 1 - p_reference, which head_to_head_pairs has already asserted
    equals its own win probability. Coupling changes the JOINT distribution only.

    Returned per-game (not summed) because the callers that need the joint
    structure — the pool sim here, and anything conditioning on a single game's
    outcome — cannot recover it from a win total."""
    # Read the module constant at CALL time, not as a default argument bound at
    # import time — POOL_SIM_TRIALS is documented as an A/B knob, and a def-time
    # default silently ignores anyone who tunes it.
    trials = POOL_SIM_TRIALS if trials is None else trials
    slot_index, invert, p_slot, _ = _draw_plan(team_cache)
    draws = rng.random((trials, p_slot.size)) < p_slot        # reference-side wins
    wins = {}
    for team in team_cache:
        idx = slot_index[team]
        if idx.size:
            won_ref = draws[:, idx]
            wins[team] = np.where(invert[team], ~won_ref, won_ref)
        else:
            wins[team] = np.zeros((trials, 0), dtype=bool)
    return wins, head_to_head_pairs(team_cache)


def _team_cache(config, picks, as_of_week, sp_ratings):
    """{canonical team -> {banked_wins, probs, conference, remaining_games}} over
    the group's unique picked teams. One team_state per team, shared by every
    pick/manager referencing it — the foundation of the shared-draw pool sim.
    remaining_games rides along (same order as probs) so the head-to-head pairing
    can tell which games are the SAME game seen from two sides."""
    cache = {}
    for pick in utils.real_picks(picks):
        canonical = utils.resolve_canonical(pick["team"])
        if canonical not in cache:
            st = utils.team_state(pick["team"], config, as_of_week)
            # conference comes off the pick, not team_state — it is gate-checked
            # against the frozen reference (§9), the single source of truth. This
            # cache holds ONE entry per team shared across managers, so it takes
            # the conference from whichever pick lands here first; that is
            # unambiguous because validate_team_names.py already fails any pick
            # whose conference disagrees with the reference, so two picks on the
            # same team cannot carry different values.
            cache[canonical] = {"banked_wins": st["banked_wins"],
                                "probs": remaining_win_probs(st, sp_ratings),
                                "conference": pick["conference"],
                                "remaining_games": st["remaining_games"]}
    return cache


def _group_by_manager(config, picks):
    """(order, {manager_id -> [pick,...]}) with config roster first."""
    display = utils.manager_display_map(config)
    order = list(display.keys())
    by_mgr = {mid: [] for mid in order}
    for pick in utils.real_picks(picks):
        mid = pick.get("manager", "?")
        if mid not in by_mgr:
            by_mgr[mid] = []
            order.append(mid)
        by_mgr[mid].append(pick)
    return order, by_mgr


def simulate_totals(config, picks, as_of_week=None, team_cache=None):
    """Shared-per-team-draw Monte Carlo (ARCHITECTURE §3). Draws each unique
    team's remaining season ONCE, then scores every manager off that same draw.
    Returns (order, totals, p_win_pool):
      totals[mid]      -> (POOL_SIM_TRIALS,) array of that manager's total delta
      p_win_pool[mid]  -> tie-aware P(this manager has the group's highest total)
    Managers on opposite sides of one team share its draw, so their totals are
    anti-correlated — the property test_projector_correlation.py asserts."""
    if team_cache is None:
        team_cache = _team_cache(config, picks, as_of_week,
                                 utils.season_sp_ratings(utils.get_season()))
    order, by_mgr = _group_by_manager(config, picks)

    seed = (POOL_SIM_SEED_BASE + zlib.crc32(str(config["group_id"]).encode())
            + (as_of_week or 0)) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)

    game_wins, _ = simulate_game_wins(team_cache, rng)

    team_final = {}                                    # canonical -> (trials,) final wins
    for canonical, info in team_cache.items():
        w = game_wins[canonical]
        adds = w.sum(axis=1) if w.size else np.zeros(POOL_SIM_TRIALS, dtype=int)
        team_final[canonical] = info["banked_wins"] + adds

    totals = {}
    for mid in order:
        arr = np.zeros(POOL_SIM_TRIALS)
        for p in by_mgr[mid]:
            fw = team_final[utils.resolve_canonical(p["team"])]
            arr = arr + signed_delta(p["direction"], fw, float(p["line"]))
        totals[mid] = arr

    p_win_pool = {}
    if order:
        stack = np.vstack([totals[mid] for mid in order])
        is_max = stack == stack.max(axis=0)
        share = is_max / is_max.sum(axis=0, keepdims=True)
        for i, mid in enumerate(order):
            p_win_pool[mid] = float(share[i].mean())
    return order, totals, p_win_pool


def build_projection(config, picks, as_of_week=None):
    """Full projection.json object for a group (no I/O)."""
    season = utils.get_season()
    sp_ratings = utils.season_sp_ratings(season)
    display = utils.manager_display_map(config)
    team_cache = _team_cache(config, picks, as_of_week, sp_ratings)
    order, by_mgr = _group_by_manager(config, picks)

    # Pool sim (shared draws) — reuses the same team_cache the dists come from.
    _, totals, p_win_pool = simulate_totals(config, picks, as_of_week, team_cache)

    def pick_projection(pick):
        canonical = utils.resolve_canonical(pick["team"])
        info = team_cache[canonical]
        line, direction = float(pick["line"]), pick["direction"]
        bw, probs = info["banked_wins"], info["probs"]
        dist = poisson_binomial(probs)                 # index j = additional wins
        finals = bw + np.arange(len(dist))             # final win totals
        exp_final = bw + float(sum(probs))
        exp_delta = signed_delta(direction, exp_final, line)
        p_beat = float(dist[finals > line].sum()) if direction == "O" \
            else float(dist[finals < line].sum())
        return {
            "team": canonical, "conference": info["conference"],
            "line": line, "direction": direction,
            "p_beat_line": round(p_beat, 6),
            "expected_delta": round(exp_delta, 2),
            "expected_final_wins": round(exp_final, 3),
            "win_distribution": [{"wins": int(w), "prob": round(float(pr), 6)}
                                 for w, pr in zip(finals, dist)],
        }

    managers = []
    for mid in order:
        mpicks = [pick_projection(p) for p in by_mgr[mid]]
        arr = totals[mid]
        p05, p50, p95 = (float(np.percentile(arr, q)) for q in (5, 50, 95))
        managers.append({
            "manager_id": mid,
            "display_name": display.get(mid, mid),
            "expected_total": round(sum(p["expected_delta"] for p in mpicks), 2),
            "p05": round(p05, 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p_win_pool": round(p_win_pool.get(mid, 0.0), 6),
            "picks": mpicks,
        })
    managers.sort(key=lambda m: (-m["p_win_pool"], -m["expected_total"], m["manager_id"]))

    cm = utils.cache_meta(season)
    return {
        "meta": {
            "group_id": config["group_id"],
            "season": season,
            "as_of_week": as_of_week,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_fetched_at": cm["fetched_at"],
            "ratings_source": "SP+",
            "ratings_asof": cm["fetched_at"],
        },
        "managers": managers,
    }


def write_projection(config, picks, as_of_week=None):
    out = build_projection(config, picks, as_of_week)
    path = utils.WEB_DATA_DIR / config["group_id"] / "projection.json"
    utils.save_json_atomic(path, out)
    return out


def main():
    ap = argparse.ArgumentParser(description="Board 2 — labeled projection")
    ap.add_argument("--group", default="all", help="group slug or 'all'")
    ap.add_argument("--test", action="store_true", help="project the data/test_picks.json fixture")
    ap.add_argument("--as-of-week", type=int, default=None,
                    help="replay: treat games after week N as unplayed (§7)")
    args = ap.parse_args()

    slugs = [utils.TEST_GROUP_ID] if args.test else (
        utils.get_all_group_ids() if args.group == "all" else [args.group])

    utils.assert_season_matches_cache()          # §6 season single-source guard

    for slug in slugs:
        config, picks = utils.load_group(slug)
        out = write_projection(config, picks, args.as_of_week)
        top = out["managers"][0] if out["managers"] else None
        lead = f"{top['display_name']} P(win)={top['p_win_pool']:.1%}" if top else "(no managers)"
        print(f"  [{slug}] projection.json — {len(out['managers'])} managers, "
              f"favorite: {lead}")


if __name__ == "__main__":
    main()
