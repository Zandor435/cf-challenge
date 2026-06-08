#!/usr/bin/env python3
"""
fetch_results.py — Pull data from CollegeFootballData.com (CFBD) API.

Shared step: runs once, all leagues read from the same raw data.

Output:
    data/live_results.json   — game results + full schedule
    data/ap_rankings.json    — AP Top 25 by week
    data/team_records.json   — team W-L records
    data/pregame_wp.json     — pregame win probabilities (SP+ based)
    data/betting_lines.json  — betting lines per game (spread fallback)

Usage:
    python scripts/fetch_results.py
    python scripts/fetch_results.py --week 5            # games/rankings for one week
    python scripts/fetch_results.py --include-postseason

Requires:
    CFB_API_KEY env var (get a free key at collegefootballdata.com/key)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import DATA_DIR, save_json

API_BASE = "https://api.collegefootballdata.com"

# CFBD only has data for completed/ongoing seasons. Use 2025 for testing now;
# flip to 2026 once the 2026 season's data goes live in CFBD.
SEASON = 2025


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
        url = f"{url}?{urlencode(params)}"

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


def _normalize_game(g, season_type):
    """Map a CFBD /games record to our compact shape."""
    return {
        "id": g.get("id"),
        "week": g.get("week"),
        "season_type": g.get("seasonType", g.get("season_type", season_type)),
        "start_date": g.get("startDate", g.get("start_date")),
        "completed": g.get("completed", False),
        "home_team": g.get("homeTeam", g.get("home_team")),
        "home_points": g.get("homePoints", g.get("home_points")),
        "home_conference": g.get("homeConference", g.get("home_conference")),
        "away_team": g.get("awayTeam", g.get("away_team")),
        "away_points": g.get("awayPoints", g.get("away_points")),
        "away_conference": g.get("awayConference", g.get("away_conference")),
        "neutral_site": g.get("neutralSite", g.get("neutral_site", False)),
        "conference_game": g.get("conferenceGame", g.get("conference_game", False)),
        "notes": g.get("notes", ""),
    }


def fetch_games(season, week=None, season_type="regular"):
    """Fetch games for a full season (or a single week)."""
    params = {"year": season, "seasonType": season_type}
    if week:
        params["week"] = week

    label = f"season={season} type={season_type}" + (f" week={week}" if week else "")
    print(f"  Fetching games: {label}")
    games = api_get("/games", params)
    if games is None:
        return []

    results = [_normalize_game(g, season_type) for g in games]
    print(f"  → {len(results)} games fetched")
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
                ranks = [{
                    "rank": r.get("rank"),
                    "school": r.get("school"),
                    "conference": r.get("conference"),
                    "first_place_votes": r.get("firstPlaceVotes"),
                    "points": r.get("points"),
                } for r in poll.get("ranks", [])]
                rankings_by_week.append({
                    "week": week_num,
                    "season": season,
                    "poll": "AP Top 25",
                    "ranks": ranks,
                })

    print(f"  → {len(rankings_by_week)} weeks of rankings fetched")
    return rankings_by_week


def fetch_records(season):
    """Fetch team W-L records (regular-season totals as a cross-check)."""
    print(f"  Fetching team records: season={season}")
    data = api_get("/records", {"year": season})
    if data is None:
        return []

    records = []
    for r in data:
        total = r.get("total", {}) or {}
        records.append({
            "team": r.get("team"),
            "conference": r.get("conference"),
            "games": total.get("games", 0),
            "wins": total.get("wins", 0),
            "losses": total.get("losses", 0),
            "ties": total.get("ties", 0),
        })

    print(f"  → {len(records)} team records fetched")
    return records


def fetch_pregame_wp(season):
    """Fetch game-level pregame win probabilities (SP+ based)."""
    print(f"  Fetching pregame win probabilities: season={season}")
    data = api_get("/metrics/wp/pregame", {"year": season, "seasonType": "regular"})
    if data is None:
        return []

    games = []
    for w in data:
        games.append({
            "game_id": w.get("gameId"),
            "week": w.get("week"),
            "season_type": w.get("seasonType", "regular"),
            "home_team": w.get("homeTeam"),
            "away_team": w.get("awayTeam"),
            "spread": w.get("spread"),
            "home_win_prob": w.get("homeWinProb"),
        })

    print(f"  → {len(games)} pregame win probabilities fetched")
    return games


def _consensus_spread(lines):
    """Average the home-team spread across providers (None if unavailable)."""
    spreads = []
    for ln in lines or []:
        s = ln.get("spread")
        if s is None:
            continue
        try:
            spreads.append(float(s))
        except (TypeError, ValueError):
            continue
    if not spreads:
        return None
    return round(sum(spreads) / len(spreads), 2)


def fetch_betting_lines(season):
    """Fetch betting lines per game (spread → win-prob fallback source)."""
    print(f"  Fetching betting lines: season={season}")
    data = api_get("/lines", {"year": season})
    if data is None:
        return []

    games = []
    for g in data:
        games.append({
            "game_id": g.get("id"),
            "week": g.get("week"),
            "season_type": g.get("seasonType", "regular"),
            "home_team": g.get("homeTeam"),
            "away_team": g.get("awayTeam"),
            # CFBD convention: negative spread = home team favored.
            "spread": _consensus_spread(g.get("lines")),
        })

    print(f"  → {len(games)} games with betting lines fetched")
    return games


def main():
    parser = argparse.ArgumentParser(description="Fetch CFBD results and supporting data")
    parser.add_argument("--week", type=int, default=None, help="Specific week for games/rankings")
    parser.add_argument("--include-postseason", action="store_true", help="Also fetch postseason games")
    args = parser.parse_args()

    print("=" * 50)
    print("FETCH RESULTS")
    print("=" * 50)

    # Game results / schedule
    games = fetch_games(SEASON, week=args.week, season_type="regular")
    if args.include_postseason:
        games.extend(fetch_games(SEASON, season_type="postseason"))

    rankings = fetch_rankings(SEASON, week=args.week)
    records = fetch_records(SEASON)
    pregame_wp = fetch_pregame_wp(SEASON)
    betting_lines = fetch_betting_lines(SEASON)

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
    save_json(DATA_DIR / "team_records.json", {
        "season": SEASON,
        "records": records,
    })
    save_json(DATA_DIR / "pregame_wp.json", {
        "season": SEASON,
        "games": pregame_wp,
    })
    save_json(DATA_DIR / "betting_lines.json", {
        "season": SEASON,
        "games": betting_lines,
    })

    print(f"\nDone. {len(games)} games, {len(rankings)} ranking weeks, "
          f"{len(records)} records, {len(pregame_wp)} WP entries, {len(betting_lines)} line entries.")


if __name__ == "__main__":
    main()
