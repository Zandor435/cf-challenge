#!/usr/bin/env python3
"""
scoring.py — Score games and produce owner standings for a league.

Runs per-league: reads shared live_results.json + league-specific draft board,
applies scoring rules, outputs standings.

Output (per league):
    site/data/<league>/owner_standings.json
    site/data/<league>/team_table.json
    site/data/<league>/weekly_results.json

Usage:
    python scripts/scoring.py --league league-1
    python scripts/scoring.py --league all    # run all leagues
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    ROOT, DATA_DIR, load_json, save_json, load_league_config,
    load_scoring_config, get_draft_board, get_owners, get_all_league_ids,
    league_site_data_dir, normalize_team
)


def get_rankings_for_week(rankings_data, week):
    """Return dict { team_name: rank } for a given week."""
    for entry in rankings_data.get("rankings", []):
        if entry.get("week") == week:
            return {
                r["school"]: r["rank"]
                for r in entry.get("ranks", [])
            }
    return {}


def score_game_for_team(team, game, rankings, scoring):
    """
    Score a single completed game for a specific team.
    Returns { points, breakdown[] } where breakdown describes each scoring event.
    """
    if not game.get("completed"):
        return {"points": 0, "breakdown": []}

    points = 0
    breakdown = []

    home = normalize_team(game["home_team"])
    away = normalize_team(game["away_team"])
    is_home = (team == home)
    team_score = game["home_points"] if is_home else game["away_points"]
    opp_score = game["away_points"] if is_home else game["home_points"]
    opponent = away if is_home else home

    if team_score is None or opp_score is None:
        return {"points": 0, "breakdown": []}

    won = team_score > opp_score

    if won:
        # Base win points
        rs = scoring.get("regular_season", {})
        pts = rs.get("win", 3)
        points += pts
        breakdown.append({"event": "win", "points": pts, "vs": opponent})

        # Ranked win bonus
        opp_rank = rankings.get(opponent)
        if opp_rank:
            if opp_rank <= 5:
                bonus = rs.get("win_vs_ranked_5_1", 6)
                points += bonus
                breakdown.append({"event": f"win_vs_#{opp_rank}", "points": bonus, "vs": opponent})
            elif opp_rank <= 15:
                bonus = rs.get("win_vs_ranked_15_6", 4)
                points += bonus
                breakdown.append({"event": f"win_vs_#{opp_rank}", "points": bonus, "vs": opponent})
            elif opp_rank <= 25:
                bonus = rs.get("win_vs_ranked_25_16", 2)
                points += bonus
                breakdown.append({"event": f"win_vs_#{opp_rank}", "points": bonus, "vs": opponent})

        # Upset bonus
        team_rank = rankings.get(team)
        ub = scoring.get("upset_bonus", {})

        if opp_rank and not team_rank:
            # Unranked beats ranked
            bonus = ub.get("unranked_beats_ranked", 4)
            points += bonus
            breakdown.append({"event": "upset_unranked_beats_ranked", "points": bonus, "vs": opponent})
        elif opp_rank and team_rank:
            diff = team_rank - opp_rank
            if diff >= 20:
                bonus = ub.get("rank_diff_20_plus", 8)
                points += bonus
                breakdown.append({"event": f"upset_rank_diff_{diff}", "points": bonus, "vs": opponent})
            elif diff >= 15:
                bonus = ub.get("rank_diff_15_plus", 5)
                points += bonus
                breakdown.append({"event": f"upset_rank_diff_{diff}", "points": bonus, "vs": opponent})
            elif diff >= 10:
                bonus = ub.get("rank_diff_10_plus", 3)
                points += bonus
                breakdown.append({"event": f"upset_rank_diff_{diff}", "points": bonus, "vs": opponent})

    return {"points": points, "breakdown": breakdown}


def score_league(league_id):
    """Run scoring for one league. Returns owner standings + team table."""
    print(f"\n--- Scoring: {league_id} ---")

    config = load_league_config(league_id)
    scoring = load_scoring_config(league_id)
    draft_board = get_draft_board(league_id)
    owners = get_owners(league_id)

    if not draft_board or all(k == "example_team" for k in draft_board):
        print(f"  ⚠ No draft board for {league_id} — writing empty standings")
        out_dir = league_site_data_dir(league_id)
        save_json(out_dir / "owner_standings.json", {"league_id": league_id, "owners": []})
        save_json(out_dir / "team_table.json", {"league_id": league_id, "teams": []})
        save_json(out_dir / "weekly_results.json", {"league_id": league_id, "weeks": []})
        return

    # Load shared results
    results_path = DATA_DIR / "live_results.json"
    if not results_path.exists():
        print("  ⚠ No live_results.json — run fetch_results.py first")
        return
    results = load_json(results_path)
    games = results.get("games", [])

    rankings_data = {}
    rankings_path = DATA_DIR / "ap_rankings.json"
    if rankings_path.exists():
        rankings_data = load_json(rankings_path)

    # Group games by week
    games_by_week = {}
    for g in games:
        w = g.get("week", 0)
        games_by_week.setdefault(w, []).append(g)

    # Score each team
    team_results = {}  # team -> { total_points, wins, losses, weekly: [{week, points, breakdown}] }
    for team, draft_info in draft_board.items():
        team_data = {
            "team": team,
            "owner": draft_info["owner"],
            "tier": draft_info["tier"],
            "total_points": 0,
            "wins": 0,
            "losses": 0,
            "weekly": [],
        }

        for week_num in sorted(games_by_week.keys()):
            rankings = get_rankings_for_week(rankings_data, week_num)
            week_points = 0
            week_breakdown = []

            for game in games_by_week[week_num]:
                home = normalize_team(game.get("home_team", ""))
                away = normalize_team(game.get("away_team", ""))

                if team in (home, away) and game.get("completed"):
                    result = score_game_for_team(team, game, rankings, scoring)
                    week_points += result["points"]
                    week_breakdown.extend(result["breakdown"])

                    # Win/loss tracking
                    is_home = (team == home)
                    ts = game["home_points"] if is_home else game["away_points"]
                    os_ = game["away_points"] if is_home else game["home_points"]
                    if ts is not None and os_ is not None:
                        if ts > os_:
                            team_data["wins"] += 1
                        else:
                            team_data["losses"] += 1

            if week_breakdown:
                team_data["weekly"].append({
                    "week": week_num,
                    "points": week_points,
                    "breakdown": week_breakdown,
                })
                team_data["total_points"] += week_points

        team_results[team] = team_data

    # Build owner standings
    owner_map = {o["id"]: {**o, "total_points": 0, "teams": []} for o in owners}
    for team, data in team_results.items():
        oid = data["owner"]
        if oid in owner_map:
            owner_map[oid]["total_points"] += data["total_points"]
            owner_map[oid]["teams"].append({
                "team": team,
                "tier": data["tier"],
                "points": data["total_points"],
                "record": f"{data['wins']}-{data['losses']}",
            })

    standings = sorted(owner_map.values(), key=lambda o: o["total_points"], reverse=True)
    for i, s in enumerate(standings):
        s["rank"] = i + 1

    # Build weekly results for the timeline
    weeks_output = []
    for week_num in sorted(games_by_week.keys()):
        week_owner_points = {}
        for team, data in team_results.items():
            for w in data["weekly"]:
                if w["week"] == week_num:
                    oid = data["owner"]
                    week_owner_points.setdefault(oid, 0)
                    week_owner_points[oid] += w["points"]
        weeks_output.append({
            "week": week_num,
            "owner_points": week_owner_points,
        })

    # Write outputs
    out_dir = league_site_data_dir(league_id)

    save_json(out_dir / "owner_standings.json", {
        "league_id": league_id,
        "league_name": config.get("display_name", league_id),
        "owners": standings,
    })

    team_table = sorted(team_results.values(), key=lambda t: t["total_points"], reverse=True)
    save_json(out_dir / "team_table.json", {
        "league_id": league_id,
        "teams": [{
            "team": t["team"],
            "owner": t["owner"],
            "tier": t["tier"],
            "points": t["total_points"],
            "record": f"{t['wins']}-{t['losses']}",
        } for t in team_table],
    })

    save_json(out_dir / "weekly_results.json", {
        "league_id": league_id,
        "weeks": weeks_output,
    })

    print(f"  ✓ {league_id}: {len(team_results)} teams scored, {len(standings)} owners ranked")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, help="League ID or 'all'")
    args = parser.parse_args()

    print("=" * 50)
    print("SCORING ENGINE")
    print("=" * 50)

    if args.league == "all":
        for lid in get_all_league_ids():
            score_league(lid)
    else:
        score_league(args.league)


if __name__ == "__main__":
    main()
