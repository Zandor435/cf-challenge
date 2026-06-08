#!/usr/bin/env python3
"""
scoring.py — Delta scoring for a league.

The game: each pick is a team + a side (OVER/UNDER) against a preseason win-total
line. Score per pick:
    OVER:  current_wins − line
    UNDER: line − current_wins
Owner's current score = sum of their 4 deltas.

Only regular-season wins count toward the line (bowl/CFP excluded).

Output (per league):
    site/data/<league>/owner_standings.json   ("current standings" snapshot)

Usage:
    python scripts/scoring.py --league league-1
    python scripts/scoring.py --league all
    python scripts/scoring.py --league all --test   # use data/test_picks.json
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    DATA_DIR, load_json, save_json, load_league_config,
    get_owners, get_all_league_ids, get_picks, validate_picks,
    league_site_data_dir, normalize_team,
)


def team_record(team, games):
    """
    Count a team's regular-season record from the games list.
    Returns (wins, losses, games_remaining).
    """
    wins = losses = remaining = 0
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
                wins += 1
            else:
                losses += 1
        else:
            remaining += 1
    return wins, losses, remaining


def pick_delta(side, wins, line):
    """Delta for a single pick given the team's current/final wins."""
    if side == "over":
        return round(wins - line, 1)
    return round(line - wins, 1)


def current_week(games):
    """Highest week number among completed regular-season games."""
    weeks = [
        g.get("week", 0) for g in games
        if g.get("completed") and g.get("season_type", "regular") == "regular"
    ]
    return max(weeks) if weeks else 0


def score_league(league_id, test=False):
    """Run delta scoring for one league. Writes owner_standings.json."""
    print(f"\n--- Scoring: {league_id} ---")

    config = load_league_config(league_id)
    owners = get_owners(league_id)
    picks = get_picks(league_id, test=test)
    out_dir = league_site_data_dir(league_id)

    if not picks:
        print(f"  ⚠ No picks for {league_id} — writing empty standings")
        save_json(out_dir / "owner_standings.json", {
            "league_id": league_id,
            "league_name": config.get("display_name", league_id),
            "as_of_week": 0,
            "owners": [],
        })
        return

    for w in validate_picks(picks):
        print(f"  ⚠ validation: {w}")

    results = load_json(DATA_DIR / "live_results.json")
    games = results.get("games", [])
    as_of = current_week(games)

    # Group picks by owner
    owner_picks = {}
    for p in picks:
        owner_picks.setdefault(p["owner"], []).append(p)

    owner_meta = {o["id"]: o for o in owners}

    standings = []
    for oid, plist in owner_picks.items():
        meta = owner_meta.get(oid, {})
        scored_picks = []
        current_score = 0.0
        for p in plist:
            wins, losses, remaining = team_record(p["team"], games)
            delta = pick_delta(p["side"], wins, p["line"])
            current_score += delta
            scored_picks.append({
                "team": p["team"],
                "conference": p["conference"],
                "side": p["side"],
                "line": p["line"],
                "current_wins": wins,
                "current_losses": losses,
                "games_remaining": remaining,
                "current_delta": delta,
            })

        standings.append({
            "id": oid,
            "name": meta.get("name", oid),
            "short_name": meta.get("short_name", meta.get("name", oid)),
            "current_score": round(current_score, 1),
            "picks": scored_picks,
        })

    standings.sort(key=lambda o: o["current_score"], reverse=True)
    for i, s in enumerate(standings):
        s["rank"] = i + 1

    save_json(out_dir / "owner_standings.json", {
        "league_id": league_id,
        "league_name": config.get("display_name", league_id),
        "as_of_week": as_of,
        "owners": standings,
    })

    print(f"  ✓ {league_id}: {len(standings)} owners scored (as of week {as_of})")
    for s in standings:
        print(f"     #{s['rank']} {s['name']:>16s}: current {s['current_score']:+.1f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, help="League ID or 'all'")
    parser.add_argument("--test", action="store_true", help="Use data/test_picks.json")
    args = parser.parse_args()

    print("=" * 50)
    print("SCORING ENGINE (delta)")
    print("=" * 50)

    leagues = get_all_league_ids() if args.league == "all" else [args.league]
    for lid in leagues:
        score_league(lid, test=args.test)


if __name__ == "__main__":
    main()
