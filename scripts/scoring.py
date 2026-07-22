#!/usr/bin/env python3
"""
scoring.py — Board 1: Standings, pure arithmetic (ARCHITECTURE §3, §10.2).

Emits site/data/<group_id>/standings.json per docs/output-contract.md. Every
number is exact, reproducible-by-hand arithmetic off utils.team_state (the sole
flag-aware / as-of-aware source): banked delta in the pick's O/U direction, a
floor/ceiling envelope from games_remaining, and a CLINCHED/DEAD/LIVE status.
Zero model, zero randomness — this board is the credibility spine, and it must
succeed even if the projector fails.

team_state honors count_conference_championship (per group) and --as-of-week
(global replay: games after week N are treated as unplayed, §7). Never reads the
cache or the raw banked index directly — utils owns both (guarded).

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


def signed_delta(direction, wins, line):
    """Delta in the pick's chosen direction (ARCHITECTURE §1)."""
    return (wins - line) if direction == "O" else (line - wins)


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
        "conference": st["conference"],
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


def build_standings(config, picks, as_of_week=None):
    """Full standings.json object for a group (no I/O). Pure arithmetic."""
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

    managers.sort(key=lambda m: (-m["banked_total"], -m["floor"], m["manager_id"]))
    for i, m in enumerate(managers, 1):
        m["rank"] = i
    # emit rank alongside the identity/total fields (dict order is cosmetic)
    managers = [{"manager_id": m["manager_id"], "display_name": m["display_name"],
                 "banked_total": m["banked_total"], "floor": m["floor"],
                 "ceiling": m["ceiling"], "rank": m["rank"], "picks": m["picks"]}
                for m in managers]

    season = utils.get_season()
    cm = utils.cache_meta(season)
    return {
        "meta": {
            "group_id": config["group_id"],
            "season": season,
            "as_of_week": as_of_week,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_fetched_at": cm["fetched_at"],
        },
        "managers": managers,
    }


def write_standings(config, picks, as_of_week=None):
    out = build_standings(config, picks, as_of_week)
    path = utils.SITE_DATA_DIR / config["group_id"] / "standings.json"
    utils.save_json_atomic(path, out)
    return out


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
        out = write_standings(config, picks, args.as_of_week)
        top = out["managers"][0] if out["managers"] else None
        lead = f"{top['display_name']} {top['banked_total']:+g}" if top else "(no managers)"
        print(f"  [{slug}] standings.json — {len(out['managers'])} managers, "
              f"leader: {lead}")


if __name__ == "__main__":
    main()
