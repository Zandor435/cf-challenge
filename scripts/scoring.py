#!/usr/bin/env python3
"""
Pace standings with exact records, pick statuses, and final-score envelopes.
The established projector supplies canonical full-precision pace; result-only
arithmetic remains available for explanations and season-end verification.
A failed refresh preserves the last coherent pace generation.

Usage:
    python scripts/scoring.py --group all
    python scripts/scoring.py --group church --as-of-week 6
    python scripts/scoring.py --test --as-of-week 6
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import pace


def signed_delta(direction, wins, line):
    """Delta in the pick's chosen direction (ARCHITECTURE §1)."""
    return pace.team_pace(direction, wins, line)


def pick_standing(pick, config, as_of_week):
    """Exact Board-1 arithmetic for one pick off team_state."""
    st = utils.team_state(pick["team"], config, as_of_week)
    line = float(pick["line"])
    direction = pick["direction"]
    bw, gr = st["banked_wins"], st["games_remaining"]

    banked_delta = signed_delta(direction, bw, line)
    if direction == "O":
        floor = signed_delta("O", bw, line)            # lose out (worst for over)
        ceiling = signed_delta("O", bw + gr, line)      # win out (best for over)
    else:
        floor = signed_delta("U", bw + gr, line)        # win out (worst for under)
        ceiling = signed_delta("U", bw, line)           # lose out (best for under)
    status = "CLINCHED" if floor > 0 else ("DEAD" if ceiling < 0 else "LIVE")

    return {
        "team": st["team"],
        # straight off the pick — it is gate-checked against the frozen
        # reference, so it is the single source of conference truth (§9)
        "conference": pick["conference"],
        "line": line,
        "direction": direction,
        "banked_wins": bw,
        "banked_losses": st["banked_losses"],
        "games_remaining": gr,
        "banked_delta": round(banked_delta, 2),
        "floor": round(floor, 2),
        "ceiling": round(ceiling, 2),
        "status": status,
    }


def build_standings(config, picks, as_of_week=None, draft_status=None, projection=None):
    """Join canonical projections to exact team state, with no output writes.
    `draft_status` (from picks.json) is surfaced into meta so the site can flag
    engineered sample data ("dummy") vs a real draft ("final") — STEP 4."""
    display = utils.manager_display_map(config)
    # manager order: config roster first, then any pick-only managers (defensive)
    order = list(display.keys())
    by_mgr = {mid: [] for mid in order}
    for pick in utils.real_picks(picks):
        mid = pick.get("manager", "?")
        if mid not in by_mgr:
            by_mgr[mid] = []
            order.append(mid)
        by_mgr[mid].append(pick_standing(pick, config, as_of_week))

    managers = []
    for mid in order:
        mpicks = by_mgr[mid]
        managers.append({
            "manager_id": mid,
            "display_name": display.get(mid, mid),
            "banked_total": round(sum(p["banked_delta"] for p in mpicks), 2),
            "floor": round(sum(p["floor"] for p in mpicks), 2),
            "ceiling": round(sum(p["ceiling"] for p in mpicks), 2),
            "picks": mpicks,
        })

    if projection is None:
        import projector
        projection = projector.build_projection(config, picks, as_of_week, include_simulation=False)
    projected = {m["manager_id"]: m for m in projection["managers"]}
    for manager in managers:
        model = projected.get(manager["manager_id"], {})
        for key in ("expected_total", "expected_total_display", "expected_total_move", "expected_total_move_display"):
            manager[key] = model.get(key)
        by_team = {p["team"]: p for p in model.get("picks", [])}
        for pick in manager["picks"]:
            pr = by_team.get(pick["team"], {})
            for key in ("expected_delta", "expected_delta_display", "expected_final_wins", "remaining_games", "played_games", "outlook"):
                pick[key] = pr.get(key)
    managers.sort(key=pace.rank_key)
    for rank, manager in enumerate(managers, 1):
        manager["rank"] = rank if manager["expected_total"] is not None else None

    season = utils.get_season()
    cm = utils.cache_meta(season)
    return {
        "meta": {
            "group_id": config["group_id"],
            "season": season,
            "as_of_week": as_of_week,
            "draft_status": draft_status,
            "generated_at": projection.get("meta", {}).get("generated_at", datetime.now(timezone.utc).isoformat()),
            "scoring_metric": "pace",
            "count_conference_championship": utils.counts_conference_championship(config),
            "pace_stale": False,
            "cache_fetched_at": cm["fetched_at"],
        },
        "managers": managers,
    }


def write_standings(config, picks, as_of_week=None, draft_status=None):
    # The standalone command uses the same coherent publication path.
    from run_groups import publish_pace
    return publish_pace(config, picks, as_of_week, draft_status)[0]


def main():
    ap = argparse.ArgumentParser(description="Board 1 — exact standings")
    ap.add_argument("--group", default="all", help="group slug or 'all'")
    ap.add_argument("--test", action="store_true", help="score the data/test_picks.json fixture")
    ap.add_argument("--as-of-week", type=int, default=None,
                    help="replay: treat games after week N as unplayed (§7)")
    args = ap.parse_args()

    slugs = [utils.TEST_GROUP_ID] if args.test else (
        utils.get_all_group_ids() if args.group == "all" else [args.group])

    utils.assert_season_matches_cache()          # §6 season single-source guard

    for slug in slugs:
        config, picks = utils.load_group(slug)
        out = write_standings(config, picks, args.as_of_week, utils.group_draft_status(slug))
        top = out["managers"][0] if out["managers"] else None
        lead = f"{top['display_name']} {top.get('expected_total_display') or 'pace unavailable'}" if top else "(no managers)"
        print(f"  [{slug}] standings.json — {len(out['managers'])} managers, "
              f"leader: {lead}")


if __name__ == "__main__":
    main()
