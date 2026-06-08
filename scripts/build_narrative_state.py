#!/usr/bin/env python3
"""
build_narrative_state.py — Build the narrative context GPT needs for good commentary.

Reads standings, weekly results, projections, and previous narrative state,
then computes: streaks, rank changes, upsets, dependency index, themes.

Output (per league):
    site/data/<league>/narrative_state.json

Usage:
    python scripts/build_narrative_state.py --league league-1
    python scripts/build_narrative_state.py --league all
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import (
    DATA_DIR, load_json, save_json, load_league_config,
    get_draft_board, get_owners, get_all_league_ids,
    league_site_data_dir
)


def compute_streaks(weekly_data, owner_ids):
    """Compute current scoring streaks per owner."""
    streaks = {oid: {"type": None, "length": 0} for oid in owner_ids}

    weeks = sorted(weekly_data, key=lambda w: w["week"])
    for week in weeks:
        pts = week.get("owner_points", {})
        if not pts:
            continue

        avg = sum(pts.values()) / len(pts) if pts else 0

        for oid in owner_ids:
            op = pts.get(oid, 0)
            if op > avg:
                if streaks[oid]["type"] == "hot":
                    streaks[oid]["length"] += 1
                else:
                    streaks[oid] = {"type": "hot", "length": 1}
            elif op < avg:
                if streaks[oid]["type"] == "cold":
                    streaks[oid]["length"] += 1
                else:
                    streaks[oid] = {"type": "cold", "length": 1}
            else:
                streaks[oid] = {"type": None, "length": 0}

    return streaks


def compute_dependency_index(standings):
    """How dependent is each owner on their T1 pick?"""
    dep = {}
    for owner in standings.get("owners", []):
        teams = owner.get("teams", [])
        total = owner.get("total_points", 0)
        if total == 0:
            dep[owner["id"]] = 0
            continue

        t1_pts = sum(t["points"] for t in teams if t.get("tier") == "T1")
        dep[owner["id"]] = round(t1_pts / total, 3) if total > 0 else 0

    return dep


def detect_themes(owner_data, streaks, dep_index, projections):
    """Auto-tag narrative themes for each owner."""
    themes = {}
    for o in owner_data:
        oid = o["id"]
        t = []

        # Streak themes
        s = streaks.get(oid, {})
        if s.get("type") == "hot" and s.get("length", 0) >= 3:
            t.append("surge")
        if s.get("type") == "cold" and s.get("length", 0) >= 3:
            t.append("drought")

        # Dependency theme
        if dep_index.get(oid, 0) >= 0.5:
            t.append("one_trick_pony")
        if dep_index.get(oid, 0) <= 0.2 and dep_index.get(oid, 0) > 0:
            t.append("balanced_portfolio")

        # Underdog / frontrunner
        proj = next((p for p in projections if p["owner_id"] == oid), None)
        if proj:
            if proj.get("win_probability", 0) >= 0.4:
                t.append("frontrunner")
            elif proj.get("win_probability", 0) <= 0.1:
                t.append("longshot")

        themes[oid] = t

    return themes


def build_state(league_id):
    """Build narrative state for one league."""
    print(f"\n--- Narrative State: {league_id} ---")

    config = load_league_config(league_id)
    owners = get_owners(league_id)
    owner_ids = [o["id"] for o in owners]
    out_dir = league_site_data_dir(league_id)

    # Load standings
    standings_path = out_dir / "owner_standings.json"
    standings = load_json(standings_path) if standings_path.exists() else {"owners": []}

    # Load weekly results
    weekly_path = out_dir / "weekly_results.json"
    weekly = load_json(weekly_path) if weekly_path.exists() else {"weeks": []}

    # Load projections
    proj_path = out_dir / "projections.json"
    projections = []
    if proj_path.exists():
        proj_data = load_json(proj_path)
        projections = proj_data.get("projections", [])

    # Load previous narrative state (for rank change tracking)
    prev_state_path = out_dir / "narrative_state.json"
    prev_state = load_json(prev_state_path) if prev_state_path.exists() else {}
    prev_rankings = {
        o.get("owner_id"): o.get("rank")
        for o in prev_state.get("owner_snapshots", [])
    }

    # Compute narrative elements
    streaks = compute_streaks(weekly.get("weeks", []), owner_ids)
    dep_index = compute_dependency_index(standings)
    themes = detect_themes(owners, streaks, dep_index, projections)

    # Build owner snapshots
    owner_snapshots = []
    for o in standings.get("owners", []):
        oid = o["id"]
        prev_rank = prev_rankings.get(oid)
        curr_rank = o.get("rank")
        rank_change = (prev_rank - curr_rank) if (prev_rank and curr_rank) else 0

        owner_snapshots.append({
            "owner_id": oid,
            "owner_name": o.get("name", oid),
            "rank": curr_rank,
            "rank_change": rank_change,
            "total_points": o.get("total_points", 0),
            "streak": streaks.get(oid, {}),
            "dependency_index": dep_index.get(oid, 0),
            "themes": themes.get(oid, []),
            "teams": o.get("teams", []),
        })

    # Detect notable events this week
    notable_events = []
    weeks = weekly.get("weeks", [])
    if weeks:
        latest = weeks[-1]
        # Biggest single-week score
        pts = latest.get("owner_points", {})
        if pts:
            best_oid = max(pts, key=pts.get)
            best_name = next((o["name"] for o in owners if o["id"] == best_oid), best_oid)
            notable_events.append({
                "type": "week_winner",
                "owner": best_name,
                "points": pts[best_oid],
                "week": latest["week"],
            })

    # Build state
    state = {
        "league_id": league_id,
        "league_name": config.get("display_name", league_id),
        "current_week": weeks[-1]["week"] if weeks else 0,
        "owner_snapshots": owner_snapshots,
        "notable_events": notable_events,
        "projections_summary": [
            {
                "owner_id": p["owner_id"],
                "win_probability": p["win_probability"],
                "projected_median": p["projected_median"],
            }
            for p in projections
        ],
        "h2h_matchups": [],  # TODO: compute head-to-head point differentials
    }

    save_json(out_dir / "narrative_state.json", state)
    print(f"  ✓ Built narrative state: {len(owner_snapshots)} owners, {len(notable_events)} events")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    args = parser.parse_args()

    if args.league == "all":
        for lid in get_all_league_ids():
            build_state(lid)
    else:
        build_state(args.league)


if __name__ == "__main__":
    main()
