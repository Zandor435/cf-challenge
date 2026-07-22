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


def _team_cache(config, picks, as_of_week, sp_ratings):
    """{canonical team -> {banked_wins, probs, conference}} over the group's
    unique picked teams. One team_state per team, shared by every pick/manager
    referencing it — the foundation of the shared-draw pool sim."""
    cache = {}
    for pick in utils.real_picks(picks):
        canonical = utils.resolve_canonical(pick["team"])
        if canonical not in cache:
            st = utils.team_state(pick["team"], config, as_of_week)
            cache[canonical] = {"banked_wins": st["banked_wins"],
                                "probs": remaining_win_probs(st, sp_ratings),
                                "conference": st["conference"]}
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

    team_final = {}                                    # canonical -> (trials,) final wins
    for canonical, info in team_cache.items():
        probs = np.asarray(info["probs"])
        if probs.size:
            adds = (rng.random((POOL_SIM_TRIALS, probs.size)) < probs).sum(axis=1)
        else:
            adds = np.zeros(POOL_SIM_TRIALS, dtype=int)
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
