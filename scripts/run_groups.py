#!/usr/bin/env python3
"""
run_groups.py — Multi-tenant pipeline loop (ARCHITECTURE §5, build order §10.4).

Reshapes the ONE shared data/cfbd_cache.json into every group's boards:
    (fetch) -> validate -> score -> project -> timeline
looping over all groups off the single cache (four groups cost the same CFBD
calls as one). Everything keyed by group_id; the only write target is
docs/data/<group_id>/ (docs/output-contract.md).

Resilience (ARCHITECTURE §4, CLAUDE.md playbook rules 3/5):
  - Board 1 (standings) is the credibility spine: a scoring failure is FATAL.
  - Board 2 (projection) is a labeled best-guess: a projector failure LOGS a
    ::warning:: and continues — standings.json is still written, the run stays
    green-but-degraded rather than dark.
  - timeline.json is append-only + idempotent on the effective week.

Fetch is a separately-gated step (needs-new-data vs reshapes-existing, playbook
rule 3): by default run_groups reshapes the existing cache; pass --fetch to run
fetch_results.py first (the workflow keeps fetch as its own continue-on-error
step). --no-fetch is the default and needs no API key.

Usage:
    python scripts/run_groups.py                       # all groups, off cache, live week
    python scripts/run_groups.py --as-of-week 6        # replay week 6
    python scripts/run_groups.py --group all --fetch   # fetch first, then reshape
    python scripts/run_groups.py --test --as-of-week 6 # the test fixture
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import scoring
import projector
from validate_team_names import validate_group

SCRIPTS = Path(__file__).resolve().parent


# --- Timeline (append-only, idempotent on the effective week) ----------------

def effective_week(as_of_week):
    """The concrete scored week: the --as-of-week value, or the cache's real
    current week on a live run. Never null — it is the timeline idempotency key."""
    return as_of_week if as_of_week is not None else utils.cache_meta(utils.get_season())["week"]


def build_snapshot(standings, projection, eff_week):
    """One timeline row per manager per pick (banked + projected metrics),
    joined by (manager_id, team). Projection may be None (projector degraded)."""
    proj_mgr, proj_pick = {}, {}
    if projection:
        for m in projection["managers"]:
            proj_mgr[m["manager_id"]] = m
            for p in m["picks"]:
                proj_pick[(m["manager_id"], p["team"])] = p

    managers = []
    for sm in standings["managers"]:
        mid = sm["manager_id"]
        picks = []
        for sp in sm["picks"]:
            pp = proj_pick.get((mid, sp["team"]), {})
            picks.append({
                "team": sp["team"],
                "banked_delta": sp["banked_delta"],
                "floor": sp["floor"],
                "ceiling": sp["ceiling"],
                "expected_delta": pp.get("expected_delta"),
                "p_beat_line": pp.get("p_beat_line"),
            })
        managers.append({
            "manager_id": mid,
            "p_win_pool": proj_mgr.get(mid, {}).get("p_win_pool"),
            "picks": picks,
        })
    return {
        "as_of_week": eff_week,
        "generated_at": standings["meta"]["generated_at"],
        "managers": managers,
    }


def append_timeline(config, snapshot):
    """Append `snapshot`, replacing any existing snapshot for the same effective
    week (idempotent), keeping snapshots sorted. Never rewrites earlier weeks."""
    path = utils.WEB_DATA_DIR / config["group_id"] / "timeline.json"
    if path.exists():
        tl = utils.load_json(path)
    else:
        tl = {"group_id": config["group_id"], "season": utils.get_season(), "snapshots": []}
    snaps = [s for s in tl.get("snapshots", []) if s.get("as_of_week") != snapshot["as_of_week"]]
    snaps.append(snapshot)
    snaps.sort(key=lambda s: (s["as_of_week"] is None, s["as_of_week"]))
    tl["snapshots"] = snaps
    utils.save_json_atomic(path, tl)
    return tl


# --- Pipeline ----------------------------------------------------------------

def run_fetch():
    season = utils.get_season()
    print("\n[fetch] fetch_results.py")
    proc = subprocess.run([sys.executable, str(SCRIPTS / "fetch_results.py"),
                           "--season", str(season)])
    if proc.returncode != 0:
        # commentary-bypass already logged its own ::warning::/::error:: (§4).
        print(f"::warning:: fetch exited {proc.returncode}; reshaping off the "
              f"existing cache (degraded).")


def run_group(slug, as_of_week):
    """score (fatal) -> project (degraded on failure) -> timeline. Returns
    (standings, projection|None)."""
    config, picks = utils.load_group(slug)

    if slug != utils.TEST_GROUP_ID:
        checked, errors = validate_group(slug)          # name + conference gate (§9)
        if errors:
            print(f"::error:: [{slug}] name gate FAILED ({len(errors)} bad pick(s)) "
                  f"— refusing to score. Run validate_team_names.py for detail.")
            sys.exit(1)

    # Board 1 — fatal on failure (credibility spine).
    standings = scoring.write_standings(config, picks, as_of_week)
    print(f"  [{slug}] standings.json ({len(standings['managers'])} managers)")

    # Board 2 — degrade, don't die.
    projection = None
    try:
        projection = projector.write_projection(config, picks, as_of_week)
        print(f"  [{slug}] projection.json")
    except Exception as e:  # noqa: BLE001 — projector must never take down Board 1
        print(f"::warning:: [{slug}] projector FAILED ({type(e).__name__}: {e}); "
              f"standings.json still written, running degraded (§4).")

    eff = effective_week(as_of_week)
    append_timeline(config, build_snapshot(standings, projection, eff))
    print(f"  [{slug}] timeline.json (week {eff})")
    return standings, projection


def main():
    ap = argparse.ArgumentParser(description="Multi-tenant pipeline loop")
    ap.add_argument("--group", default="all", help="group slug or 'all'")
    ap.add_argument("--test", action="store_true", help="run the data/test_picks.json fixture")
    ap.add_argument("--as-of-week", type=int, default=None,
                    help="replay: treat games after week N as unplayed (§7)")
    ap.add_argument("--fetch", action="store_true", help="run fetch_results.py first (default: off)")
    args = ap.parse_args()

    season = utils.get_season()                    # single source (season.json)
    print("=" * 60)
    print(f"RUN GROUPS — season {season}"
          + (f", as-of week {args.as_of_week}" if args.as_of_week is not None else " (live week)"))
    print("=" * 60)

    if args.fetch:
        run_fetch()

    slugs = [utils.TEST_GROUP_ID] if args.test else (
        utils.get_all_group_ids() if args.group == "all" else [args.group])

    utils.assert_season_matches_cache()            # §6 season single-source guard

    for slug in slugs:
        print(f"\n[{slug}]")
        run_group(slug, args.as_of_week)

    print(f"\nDone: {len(slugs)} group(s) scored off the shared "
          f"season-{season} cache -> docs/data/<group_id>/.")


if __name__ == "__main__":
    main()
