#!/usr/bin/env python3
"""
win_probability.py — Monte Carlo simulation for win probability.

Locks actual results, simulates remaining games forward, tracks
which owner wins in each sim to produce win probabilities.

Output (per league):
    site/data/<league>/timeline.json    (cumulative — appends each run)
    site/data/<league>/projections.json (current snapshot)

Usage:
    python scripts/win_probability.py --league league-1
    python scripts/win_probability.py --league all
    python scripts/win_probability.py --league all --skip-if-empty
"""

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    ROOT, DATA_DIR, load_json, save_json, load_league_config,
    load_scoring_config, get_draft_board, get_owners, get_all_league_ids,
    league_site_data_dir, normalize_team
)

NUM_SIMS = 5000  # Daily run — preseason can do 20,000


def load_team_strength():
    """
    Load team strength ratings for simulation.
    Returns dict { team: rating } where higher = stronger.

    TODO: Replace with real SP+ or FPI ratings before season.
    Currently returns placeholder ratings.
    """
    path = DATA_DIR / "team_strength.json"
    if path.exists():
        return load_json(path)

    # Placeholder — will be replaced with real preseason ratings
    print("  ⚠ No team_strength.json — using flat ratings (sim will be uninformative)")
    return {}


def sim_game(team_a, team_b, strength, completed_results=None):
    """
    Simulate a single game outcome.
    If the game is already completed, return the actual result.
    Otherwise, use strength ratings to simulate.

    Returns: (winner, loser)
    """
    # Check if already completed
    if completed_results:
        key = tuple(sorted([team_a, team_b]))
        if key in completed_results:
            return completed_results[key]

    # Simulate using strength difference
    str_a = strength.get(team_a, 50)
    str_b = strength.get(team_b, 50)

    # Convert to win probability using logistic function
    diff = str_a - str_b
    prob_a = 1.0 / (1.0 + 10 ** (-diff / 15.0))

    if random.random() < prob_a:
        return (team_a, team_b)
    else:
        return (team_b, team_a)


def run_simulation(league_id, num_sims=NUM_SIMS):
    """
    Run Monte Carlo forward simulation for a league.
    """
    print(f"\n--- Win Probability: {league_id} ({num_sims} sims) ---")

    config = load_league_config(league_id)
    draft_board = get_draft_board(league_id)
    owners = get_owners(league_id)
    strength = load_team_strength()

    if not draft_board or all(k == "example_team" for k in draft_board):
        print(f"  ⚠ No draft board for {league_id} — skipping simulation")
        return

    # Load current results
    results_path = DATA_DIR / "live_results.json"
    if not results_path.exists():
        print("  ⚠ No live_results.json — skipping")
        return

    results = load_json(results_path)
    games = results.get("games", [])

    # Build completed results lookup
    completed = {}
    for g in games:
        if g.get("completed") and g.get("home_points") is not None:
            home = normalize_team(g["home_team"])
            away = normalize_team(g["away_team"])
            if g["home_points"] > g["away_points"]:
                winner, loser = home, away
            else:
                winner, loser = away, home
            key = tuple(sorted([home, away]))
            completed[key] = (winner, loser)

    # Get remaining (incomplete) games that involve drafted teams
    drafted_teams = set(draft_board.keys())
    remaining_games = []
    for g in games:
        if not g.get("completed"):
            home = normalize_team(g.get("home_team", ""))
            away = normalize_team(g.get("away_team", ""))
            if home in drafted_teams or away in drafted_teams:
                remaining_games.append((home, away))

    print(f"  Completed games: {len(completed)}")
    print(f"  Remaining games involving drafted teams: {len(remaining_games)}")

    if not remaining_games:
        print("  No remaining games — season may be complete or not started")

    # Current actual points (from owner_standings if available)
    out_dir = league_site_data_dir(league_id)
    standings_path = out_dir / "owner_standings.json"
    current_points = {}
    if standings_path.exists():
        standings = load_json(standings_path)
        for o in standings.get("owners", []):
            current_points[o["id"]] = o.get("total_points", 0)

    # Run simulations
    win_counts = {o["id"]: 0 for o in owners}
    point_distributions = {o["id"]: [] for o in owners}

    for _ in range(num_sims):
        # Start with actual points
        sim_points = dict(current_points)
        for oid in win_counts:
            sim_points.setdefault(oid, 0)

        # Simulate remaining games (simplified — just win points)
        # TODO: Full scoring with rankings simulation
        scoring = load_scoring_config(league_id)
        win_pts = scoring.get("regular_season", {}).get("win", 3)

        for home, away in remaining_games:
            winner, loser = sim_game(home, away, strength)
            if winner in draft_board:
                oid = draft_board[winner]["owner"]
                sim_points[oid] = sim_points.get(oid, 0) + win_pts

        # Who wins this sim?
        winner_id = max(sim_points, key=sim_points.get)
        win_counts[winner_id] += 1

        for oid, pts in sim_points.items():
            point_distributions[oid].append(pts)

    # Calculate probabilities and percentiles
    projections = []
    for o in owners:
        oid = o["id"]
        dist = sorted(point_distributions.get(oid, [0]))
        n = len(dist) if dist else 1
        projections.append({
            "owner_id": oid,
            "owner_name": o.get("name", oid),
            "current_points": current_points.get(oid, 0),
            "win_probability": round(win_counts.get(oid, 0) / num_sims, 4),
            "projected_p10": dist[int(n * 0.1)] if dist else 0,
            "projected_median": dist[int(n * 0.5)] if dist else 0,
            "projected_p90": dist[int(n * 0.9)] if dist else 0,
        })

    projections.sort(key=lambda p: p["win_probability"], reverse=True)

    # Save snapshot
    save_json(out_dir / "projections.json", {
        "league_id": league_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "num_sims": num_sims,
        "projections": projections,
    })

    # Append to timeline (cumulative)
    timeline_path = out_dir / "timeline.json"
    timeline = []
    if timeline_path.exists():
        timeline = load_json(timeline_path)
        if not isinstance(timeline, list):
            timeline = timeline.get("entries", [])

    timeline.append({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "projections": projections,
    })

    save_json(timeline_path, {"league_id": league_id, "entries": timeline})

    # Print summary
    for p in projections:
        print(f"  {p['owner_name']:>15s}: {p['win_probability']*100:5.1f}% win | "
              f"pts: {p['projected_p10']}-{p['projected_median']}-{p['projected_p90']} (p10/med/p90)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    parser.add_argument("--skip-if-empty", action="store_true",
                        help="Skip if no live results exist")
    parser.add_argument("--sims", type=int, default=NUM_SIMS)
    args = parser.parse_args()

    if args.skip_if_empty:
        if not (DATA_DIR / "live_results.json").exists():
            print("No live_results.json — skipping (--skip-if-empty)")
            return

    if args.league == "all":
        for lid in get_all_league_ids():
            run_simulation(lid, args.sims)
    else:
        run_simulation(args.league, args.sims)


if __name__ == "__main__":
    main()
