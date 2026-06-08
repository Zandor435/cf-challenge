#!/usr/bin/env python3
"""
win_probability.py — Monte Carlo projection of final standings.

Locks completed games, simulates each remaining game involving a picked team
using CFBD pregame win probabilities (falling back to betting-line spreads, then
50/50), and reports where every owner is projected to finish.

Output (per league):
    site/data/<league>/projections.json   (current snapshot)
    site/data/<league>/timeline.json       (one entry per week — win-prob chart)

Usage:
    python scripts/win_probability.py --league league-1
    python scripts/win_probability.py --league all --skip-if-empty
    python scripts/win_probability.py --league all --test --sims 5000
"""

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    DATA_DIR, load_json, save_json, load_league_config,
    get_owners, get_all_league_ids, get_picks,
    league_site_data_dir, normalize_team,
)
from scoring import current_week

NUM_SIMS = 5000  # Daily run. Preseason can afford 20,000.
MARGIN_STD = 13.5  # Std. dev. of CFB game margins, for spread → win-prob conversion.


def spread_to_home_wp(spread):
    """
    Convert a CFBD betting spread (negative = home favored) to home win prob.
    Expected home margin = -spread; P(home win) = Phi(margin / sigma).
    """
    if spread is None:
        return 0.5
    expected_margin = -float(spread)
    z = expected_margin / (MARGIN_STD * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))


def build_wp_lookups():
    """Return (pregame_by_id, spread_by_id) keyed by game_id."""
    pregame_by_id = {}
    wp_path = DATA_DIR / "pregame_wp.json"
    if wp_path.exists():
        for g in load_json(wp_path).get("games", []):
            if g.get("game_id") is not None and g.get("home_win_prob") is not None:
                pregame_by_id[g["game_id"]] = g["home_win_prob"]

    spread_by_id = {}
    lines_path = DATA_DIR / "betting_lines.json"
    if lines_path.exists():
        for g in load_json(lines_path).get("games", []):
            if g.get("game_id") is not None and g.get("spread") is not None:
                spread_by_id[g["game_id"]] = g["spread"]

    return pregame_by_id, spread_by_id


def team_win_prob_for_game(team, game, pregame_by_id, spread_by_id):
    """Win probability for `team` in a single remaining game."""
    gid = game.get("id")
    home = normalize_team(game.get("home_team", ""))
    is_home = (team == home)

    if gid in pregame_by_id:
        home_wp = pregame_by_id[gid]
    elif gid in spread_by_id:
        home_wp = spread_to_home_wp(spread_by_id[gid])
    else:
        home_wp = 0.5

    wp = home_wp if is_home else 1.0 - home_wp
    return min(0.99, max(0.01, wp))


def team_sim_inputs(team, games, pregame_by_id, spread_by_id):
    """
    Return (locked_wins, [win_prob, ...]) for a team:
    locked_wins from completed regular-season games, plus a win prob for each
    remaining regular-season game.
    """
    locked_wins = 0
    remaining_wps = []
    for g in games:
        if g.get("season_type", "regular") != "regular":
            continue
        home = normalize_team(g.get("home_team", ""))
        away = normalize_team(g.get("away_team", ""))
        if team not in (home, away):
            continue

        if g.get("completed"):
            hp, ap = g.get("home_points"), g.get("away_points")
            if hp is None or ap is None:
                continue
            is_home = (team == home)
            ts = hp if is_home else ap
            os_ = ap if is_home else hp
            if ts > os_:
                locked_wins += 1
        else:
            remaining_wps.append(team_win_prob_for_game(team, g, pregame_by_id, spread_by_id))

    return locked_wins, remaining_wps


def simulate_final_wins(locked_wins, remaining_wps, num_sims, rng):
    """Vectorized: return an array (num_sims,) of simulated final win totals."""
    if not remaining_wps:
        return np.full(num_sims, locked_wins, dtype=float)
    probs = np.array(remaining_wps)
    draws = rng.random((num_sims, len(probs))) < probs  # (sims, games)
    return draws.sum(axis=1) + locked_wins


def pct(arr, p):
    return float(np.percentile(arr, p))


def run_simulation(league_id, num_sims=NUM_SIMS, test=False, seed=None):
    """Run Monte Carlo projection for one league."""
    print(f"\n--- Win Probability: {league_id} ({num_sims} sims) ---")

    config = load_league_config(league_id)
    owners = get_owners(league_id)
    picks = get_picks(league_id, test=test)
    out_dir = league_site_data_dir(league_id)

    if not picks:
        print(f"  ⚠ No picks for {league_id} — skipping simulation")
        return

    results = load_json(DATA_DIR / "live_results.json")
    games = results.get("games", [])
    as_of = current_week(games)
    pregame_by_id, spread_by_id = build_wp_lookups()

    rng = np.random.default_rng(seed)

    # Cache per-team simulated final wins (teams can repeat across owners/sides).
    team_cache = {}

    def team_samples(team):
        if team not in team_cache:
            locked, wps = team_sim_inputs(team, games, pregame_by_id, spread_by_id)
            team_cache[team] = (locked, wps, simulate_final_wins(locked, wps, num_sims, rng))
        return team_cache[team]

    owner_meta = {o["id"]: o for o in owners}
    owner_picks = {}
    for p in picks:
        owner_picks.setdefault(p["owner"], []).append(p)

    # Per-owner projected score samples + per-pick detail.
    owner_score_samples = {}
    owner_pick_detail = {}
    for oid, plist in owner_picks.items():
        score = np.zeros(num_sims)
        details = []
        for p in plist:
            locked, wps, final_wins = team_samples(p["team"])
            if p["side"] == "over":
                delta = final_wins - p["line"]
            else:
                delta = p["line"] - final_wins
            score = score + delta
            details.append({
                "team": p["team"],
                "conference": p["conference"],
                "side": p["side"],
                "line": p["line"],
                "current_wins": locked,
                "games_remaining": len(wps),
                "projected_final_wins": {
                    "p10": round(pct(final_wins, 10)),
                    "median": round(pct(final_wins, 50)),
                    "p90": round(pct(final_wins, 90)),
                },
                "projected_delta": {
                    "p10": round(pct(delta, 10), 1),
                    "median": round(pct(delta, 50), 1),
                    "p90": round(pct(delta, 90), 1),
                },
            })
        owner_score_samples[oid] = score
        owner_pick_detail[oid] = details

    # Who wins each sim? Stack owner score arrays and argmax per sim.
    oid_order = list(owner_score_samples.keys())
    score_matrix = np.vstack([owner_score_samples[oid] for oid in oid_order])  # (owners, sims)
    winners = np.argmax(score_matrix, axis=0)
    win_counts = np.bincount(winners, minlength=len(oid_order))

    # Load current scores from owner_standings (if scoring ran first).
    current_scores = {}
    standings_path = out_dir / "owner_standings.json"
    if standings_path.exists():
        for o in load_json(standings_path).get("owners", []):
            current_scores[o["id"]] = o.get("current_score", 0)

    projections = []
    for i, oid in enumerate(oid_order):
        samples = owner_score_samples[oid]
        meta = owner_meta.get(oid, {})
        projections.append({
            "id": oid,
            "name": meta.get("name", oid),
            "current_score": current_scores.get(oid, 0),
            "win_probability": round(float(win_counts[i]) / num_sims, 4),
            "projected_final_score": {
                "p10": round(pct(samples, 10), 1),
                "median": round(pct(samples, 50), 1),
                "p90": round(pct(samples, 90), 1),
            },
            "picks": owner_pick_detail[oid],
        })

    projections.sort(key=lambda p: p["win_probability"], reverse=True)
    timestamp = datetime.now(timezone.utc).isoformat()

    save_json(out_dir / "projections.json", {
        "league_id": league_id,
        "league_name": config.get("display_name", league_id),
        "as_of_week": as_of,
        "num_sims": num_sims,
        "timestamp": timestamp,
        "owners": projections,
    })

    _append_timeline(out_dir / "timeline.json", league_id, as_of, timestamp, projections)

    print(f"  ✓ {league_id}: projected (as of week {as_of})")
    for p in projections:
        s = p["projected_final_score"]
        print(f"     {p['name']:>16s}: win {p['win_probability']*100:5.1f}% | "
              f"final {s['p10']:+.1f}/{s['median']:+.1f}/{s['p90']:+.1f} (p10/med/p90)")


def _append_timeline(path, league_id, week, timestamp, projections):
    """Append (or replace) this week's snapshot in the timeline."""
    timeline = {"league_id": league_id, "entries": []}
    if path.exists():
        existing = load_json(path)
        if isinstance(existing, dict) and "entries" in existing:
            timeline = existing
        elif isinstance(existing, list):  # legacy shape
            timeline["entries"] = existing

    entry = {
        "week": week,
        "timestamp": timestamp,
        "owners": [{
            "id": p["id"],
            "name": p["name"],
            "win_probability": p["win_probability"],
            "projected_median": p["projected_final_score"]["median"],
        } for p in projections],
    }

    # Replace same-week entry on reruns; otherwise append.
    timeline["entries"] = [e for e in timeline["entries"] if e.get("week") != week]
    timeline["entries"].append(entry)
    timeline["entries"].sort(key=lambda e: e.get("week", 0))

    save_json(path, timeline)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    parser.add_argument("--test", action="store_true", help="Use data/test_picks.json")
    parser.add_argument("--skip-if-empty", action="store_true", help="Skip if no live results exist")
    parser.add_argument("--sims", type=int, default=NUM_SIMS)
    parser.add_argument("--seed", type=int, default=None, help="RNG seed (reproducible)")
    args = parser.parse_args()

    if args.skip_if_empty and not (DATA_DIR / "live_results.json").exists():
        print("No live_results.json — skipping (--skip-if-empty)")
        return

    leagues = get_all_league_ids() if args.league == "all" else [args.league]
    for lid in leagues:
        run_simulation(lid, args.sims, test=args.test, seed=args.seed)


if __name__ == "__main__":
    main()
