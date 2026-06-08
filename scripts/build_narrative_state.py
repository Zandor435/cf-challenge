#!/usr/bin/env python3
"""
build_narrative_state.py — Build the narrative context the commentary engine needs.

Adapted for the delta game model (no tiers). For each owner it computes:
  - rank + rank change since last run
  - current score vs projected final score + win probability
  - which picks are CARRYING vs DRAGGING (by delta)
  - the SWEAT METER: picks whose team is sitting right on its line
  - remaining schedule difficulty (expected wins still to come)
  - auto-tagged narrative themes

It emits the rich structure under `owner_snapshots`, and also fills a few
legacy-compatible keys so the (untouched) generate_commentary.py prompt builder
keeps rendering good context.

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
    load_json, save_json, load_league_config,
    get_owners, get_all_league_ids, league_site_data_dir,
)


def side_label(pick):
    """Compact OVER/UNDER + line label, e.g. 'U10.5' / 'O7'."""
    side = (pick.get("side") or "").upper()[:1] or "?"
    line = pick.get("line", 0)
    line_str = str(int(line)) if float(line).is_integer() else str(line)
    return f"{side}{line_str}"


def classify_picks(current_picks, proj_by_team):
    """
    Return per-pick narrative detail plus carrying/dragging/sweat groupings.
    A pick is 'sweat' when its projected delta is near zero and games remain
    (the over/under is still a live coin flip).
    """
    detail = []
    for cp in current_picks:
        proj = proj_by_team.get(cp["team"], {})
        proj_delta = (proj.get("projected_delta") or {}).get("median", cp["current_delta"])
        proj_wins = (proj.get("projected_final_wins") or {}).get("median", cp["current_wins"])
        remaining = cp.get("games_remaining", proj.get("games_remaining", 0))

        sweat = remaining > 0 and abs(proj_delta) <= 1.0
        if cp["current_delta"] > 0:
            status = "carrying"
        elif cp["current_delta"] < 0:
            status = "dragging"
        else:
            status = "neutral"

        detail.append({
            "team": cp["team"],
            "conference": cp.get("conference", ""),
            "side": cp["side"],
            "line": cp["line"],
            "label": side_label(cp),
            "current_wins": cp["current_wins"],
            "current_losses": cp.get("current_losses", 0),
            "record": f"{cp['current_wins']}-{cp.get('current_losses', 0)}",
            "games_remaining": remaining,
            "current_delta": cp["current_delta"],
            "projected_delta": proj_delta,
            "projected_final_wins": proj_wins,
            "expected_added_wins": round(proj_wins - cp["current_wins"], 1),
            "sweat": sweat,
            "status": status,
        })

    carrying = sorted([d for d in detail if d["status"] == "carrying"],
                      key=lambda d: d["current_delta"], reverse=True)
    dragging = sorted([d for d in detail if d["status"] == "dragging"],
                      key=lambda d: d["current_delta"])
    sweat = [d for d in detail if d["sweat"]]
    return detail, carrying, dragging, sweat


def carry_share(detail):
    """Share of total positive delta contributed by the single best pick."""
    positives = [d["current_delta"] for d in detail if d["current_delta"] > 0]
    total = sum(positives)
    if total <= 0:
        return 0.0
    return round(max(positives) / total, 3)


def detect_themes(win_prob, proj_score, sweat_picks):
    themes = []
    if win_prob >= 0.40:
        themes.append("frontrunner")
    if win_prob <= 0.10:
        themes.append("longshot")
    if (proj_score.get("p90", 0) - proj_score.get("p10", 0)) >= 8:
        themes.append("boom_or_bust")
    if len(sweat_picks) >= 2:
        themes.append("sweating_it")
    return themes


def build_state(league_id):
    """Build narrative state for one league."""
    print(f"\n--- Narrative State: {league_id} ---")

    config = load_league_config(league_id)
    owners = get_owners(league_id)
    out_dir = league_site_data_dir(league_id)

    standings_path = out_dir / "owner_standings.json"
    standings = load_json(standings_path) if standings_path.exists() else {"owners": []}

    proj_path = out_dir / "projections.json"
    projections = load_json(proj_path) if proj_path.exists() else {"owners": []}
    proj_by_owner = {o["id"]: o for o in projections.get("owners", [])}

    prev_state_path = out_dir / "narrative_state.json"
    prev_state = load_json(prev_state_path) if prev_state_path.exists() else {}
    prev_rank = {o.get("owner_id"): o.get("rank") for o in prev_state.get("owner_snapshots", [])}

    as_of_week = standings.get("as_of_week", projections.get("as_of_week", 0))

    owner_snapshots = []
    for o in standings.get("owners", []):
        oid = o["id"]
        proj = proj_by_owner.get(oid, {})
        proj_score = proj.get("projected_final_score", {})
        win_prob = proj.get("win_probability", 0)
        proj_by_team = {p["team"]: p for p in proj.get("picks", [])}

        detail, carrying, dragging, sweat = classify_picks(o.get("picks", []), proj_by_team)
        themes = detect_themes(win_prob, proj_score, sweat)

        curr_rank = o.get("rank")
        pr = prev_rank.get(oid)
        rank_change = (pr - curr_rank) if (pr and curr_rank) else 0
        expected_added = round(sum(d["expected_added_wins"] for d in detail), 1)

        owner_snapshots.append({
            "owner_id": oid,
            "owner_name": o.get("name", oid),
            "rank": curr_rank,
            "rank_change": rank_change,
            "current_score": o.get("current_score", 0),
            "projected_score": {
                "p10": proj_score.get("p10"),
                "median": proj_score.get("median"),
                "p90": proj_score.get("p90"),
            },
            "win_probability": win_prob,
            "expected_added_wins": expected_added,
            "carrying": [{"team": d["team"], "delta": d["current_delta"]} for d in carrying],
            "dragging": [{"team": d["team"], "delta": d["current_delta"]} for d in dragging],
            "sweat_picks": [{"team": d["team"], "label": d["label"], "record": d["record"]} for d in sweat],
            "themes": themes,
            "picks": detail,

            # --- legacy-compatible keys for generate_commentary.py's prompt builder ---
            "total_points": o.get("current_score", 0),
            "streak": {},
            "dependency_index": carry_share(detail),
            "teams": [{
                "tier": d["label"],          # reuses the [TIER] slot to show O/U + line
                "team": d["team"],
                "points": d["current_delta"],
                "record": d["record"],
            } for d in detail],
        })

    # Notable events: projected leader, biggest sweat, biggest mover.
    notable_events = []
    if proj_by_owner:
        leader = max(proj_by_owner.values(), key=lambda p: p.get("win_probability", 0))
        notable_events.append({
            "type": "projected_leader",
            "owner": leader.get("name", leader.get("id")),
            "points": round(leader.get("win_probability", 0) * 100),
            "week": as_of_week,
        })
    mover = max(owner_snapshots, key=lambda s: abs(s["rank_change"]), default=None)
    if mover and mover["rank_change"]:
        notable_events.append({
            "type": "biggest_mover",
            "owner": mover["owner_name"],
            "points": mover["rank_change"],
            "week": as_of_week,
        })

    state = {
        "league_id": league_id,
        "league_name": config.get("display_name", league_id),
        "current_week": as_of_week,
        "owner_snapshots": owner_snapshots,
        "notable_events": notable_events,
        "projections_summary": [
            {
                "owner_id": p["id"],
                "win_probability": p.get("win_probability", 0),
                "projected_median": p.get("projected_final_score", {}).get("median", 0),
            }
            for p in projections.get("owners", [])
        ],
    }

    save_json(out_dir / "narrative_state.json", state)
    print(f"  ✓ Built narrative state: {len(owner_snapshots)} owners, {len(notable_events)} events")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    args = parser.parse_args()

    leagues = get_all_league_ids() if args.league == "all" else [args.league]
    for lid in leagues:
        build_state(lid)


if __name__ == "__main__":
    main()
