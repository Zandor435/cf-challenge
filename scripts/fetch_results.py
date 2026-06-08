#!/usr/bin/env python3
"""
fetch_results.py — Pull game results from CollegeFootballData.com API.

Shared step: runs once, all leagues read from the same raw data.

Output: data/live_results.json
        data/ap_rankings.json

Usage:
    python scripts/fetch_results.py
    python scripts/fetch_results.py --week 5    # fetch specific week only

Requires:
    CFB_API_KEY env var (get free key at collegefootballdata.com)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import ROOT, DATA_DIR, save_json

API_BASE = "https://api.collegefootballdata.com"
SEASON = 2026  # TODO: update each year or pull from config


def get_api_key():
    key = os.environ.get("CFB_API_KEY", "")
    if not key:
        print("ERROR: CFB_API_KEY environment variable not set")
        print("  Get a free key at https://collegefootballdata.com/key")
        sys.exit(1)
    return key


def api_get(endpoint, params=None):
    """Make authenticated GET request to CFBD API."""
    key = get_api_key()
    url = f"{API_BASE}{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    req = Request(url)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"  API error {e.code}: {url}")
        if e.code == 429:
            print("  Rate limited — waiting 60s")
            time.sleep(60)
            return api_get(endpoint, params)
        return None


def fetch_games(season, week=None):
    """Fetch game results. If week is None, fetch all completed weeks."""
    params = {"year": season, "seasonType": "regular"}
    if week:
        params["week"] = week

    print(f"  Fetching games: season={season}" + (f" week={week}" if week else ""))
    games = api_get("/games", params)

    if games is None:
        return []

    results = []
    for g in games:
        results.append({
            "id": g.get("id"),
            "week": g.get("week"),
            "season_type": g.get("season_type", "regular"),
            "start_date": g.get("start_date"),
            "completed": g.get("completed", False),
            "home_team": g.get("home_team"),
            "home_points": g.get("home_points"),
            "home_conference": g.get("home_conference"),
            "away_team": g.get("away_team"),
            "away_points": g.get("away_points"),
            "away_conference": g.get("away_conference"),
            "neutral_site": g.get("neutral_site", False),
            "conference_game": g.get("conference_game", False),
        })

    print(f"  → {len(results)} games fetched")
    return results


def fetch_postseason_games(season):
    """Fetch postseason / bowl / CFP games."""
    params = {"year": season, "seasonType": "postseason"}
    print(f"  Fetching postseason games: season={season}")
    games = api_get("/games", params)

    if games is None:
        return []

    results = []
    for g in games:
        results.append({
            "id": g.get("id"),
            "week": g.get("week"),
            "season_type": "postseason",
            "start_date": g.get("start_date"),
            "completed": g.get("completed", False),
            "home_team": g.get("home_team"),
            "home_points": g.get("home_points"),
            "away_team": g.get("away_team"),
            "away_points": g.get("away_points"),
            "neutral_site": g.get("neutral_site", True),
            "notes": g.get("notes", ""),  # Bowl name, CFP round, etc.
        })

    print(f"  → {len(results)} postseason games fetched")
    return results


def fetch_rankings(season, week=None):
    """Fetch AP Top 25 rankings."""
    params = {"year": season, "seasonType": "regular"}
    if week:
        params["week"] = week

    print(f"  Fetching AP rankings: season={season}" + (f" week={week}" if week else ""))
    data = api_get("/rankings", params)

    if data is None:
        return []

    rankings_by_week = []
    for entry in data:
        week_num = entry.get("week")
        for poll in entry.get("polls", []):
            if poll.get("poll") == "AP Top 25":
                ranks = []
                for r in poll.get("ranks", []):
                    ranks.append({
                        "rank": r.get("rank"),
                        "school": r.get("school"),
                        "conference": r.get("conference"),
                        "first_place_votes": r.get("firstPlaceVotes"),
                        "points": r.get("points"),
                    })
                rankings_by_week.append({
                    "week": week_num,
                    "season": season,
                    "poll": "AP Top 25",
                    "ranks": ranks,
                })

    print(f"  → {len(rankings_by_week)} weeks of rankings fetched")
    return rankings_by_week


def main():
    parser = argparse.ArgumentParser(description="Fetch CFB results and rankings")
    parser.add_argument("--week", type=int, default=None, help="Specific week to fetch")
    parser.add_argument("--include-postseason", action="store_true", help="Also fetch postseason")
    args = parser.parse_args()

    print("=" * 50)
    print("FETCH RESULTS")
    print("=" * 50)

    # Fetch regular season
    games = fetch_games(SEASON, week=args.week)

    # Fetch postseason if flagged
    if args.include_postseason:
        postseason = fetch_postseason_games(SEASON)
        games.extend(postseason)

    # Fetch rankings
    rankings = fetch_rankings(SEASON, week=args.week)

    # Save
    save_json(DATA_DIR / "live_results.json", {
        "season": SEASON,
        "fetched_week": args.week,
        "game_count": len(games),
        "games": games,
    })

    save_json(DATA_DIR / "ap_rankings.json", {
        "season": SEASON,
        "rankings": rankings,
    })

    print(f"\nDone. {len(games)} games, {len(rankings)} ranking weeks.")


if __name__ == "__main__":
    main()
