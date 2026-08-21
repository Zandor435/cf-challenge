#!/usr/bin/env python3
"""
run_groups.py — Multi-tenant pipeline loop (ARCHITECTURE §5, build order §10.4).

Reshapes the ONE shared data/cfbd_cache.json into every group's boards:
    (fetch) -> validate -> score -> project -> timeline -> analytics
looping over all groups off the single cache (four groups cost the same CFBD
calls as one). Everything keyed by group_id; the only write target is
docs/data/<group_id>/ (docs/output-contract.md).

Resilience (ARCHITECTURE §4, CLAUDE.md playbook rules 3/5):
  - Board 1 (standings) is the credibility spine: a scoring failure is FATAL.
  - Board 2 (projection) is a labeled best-guess: a projector failure LOGS a
    ::warning:: and continues — standings.json is still written, the run stays
    green-but-degraded rather than dark.
  - Board 3 (analytics) is a pure reshape of boards 1/2 + the timeline; like the
    projector it LOGS a ::warning:: and continues rather than taking Board 1 down.
  - timeline.json is append-only + idempotent on the effective week.
  - ZERO played games writes NOTHING (rule 5). Every board stays as it was,
    because a zero-state board is not empty-looking — it ranks managers purely
    by which side of the line they took. --allow-empty overrides.

Fetch is a separately-gated step (needs-new-data vs reshapes-existing, playbook
rule 3): by default run_groups reshapes the existing cache; pass --fetch to run
fetch_results.py first (the workflow keeps fetch as its own continue-on-error
step). --no-fetch is the default and needs no API key.

Usage:
    python scripts/run_groups.py                       # all groups, off cache, live week
    python scripts/run_groups.py --as-of-week 6        # replay week 6
    python scripts/run_groups.py --group all --fetch   # fetch first, then reshape
    python scripts/run_groups.py --test --as-of-week 6 # the test fixture
    python scripts/run_groups.py --allow-empty         # force a zero-state board
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
import analytics
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


def timeline_path(group_id, season=None):
    """The LIVE timeline for a group, or a finished season's archive.

    ONE FILE PER SEASON, and the live file is always the current one. Everything
    downstream reads `timeline.json` by that exact name — analytics.select_prior,
    build_week_packet, and app.js's computeMoves — and none of them can tell one
    season's week 6 from another's, because a snapshot is keyed by week ALONE.
    Making the FILE single-season is what makes that safe, without asking three
    separate readers to each learn a season rule."""
    d = utils.WEB_DATA_DIR / group_id
    return d / ("timeline.json" if season is None else f"timeline-{season}.json")


def _week_sort_key(s):
    """Weeks ascending, the unresolvable-week row last.

    Total on purpose: the tuple's first element separates them, but a list with
    two null-week rows would fall through to comparing None with None, which
    raises. Dedupe means that cannot happen today — a latent crash is still a
    crash, and this costs one expression."""
    wk = s.get("as_of_week")
    return (wk is None, wk if wk is not None else -1)


def archive_season(group_id, tl):
    """Move a finished season's timeline aside to timeline-<season>.json.

    ADDITIVE, NEVER DESTRUCTIVE. season.json gets flipped for replays, so the
    same season can be rolled more than once (2025 -> 2026 -> 2025 -> 2026). When
    an archive already exists, an already-archived week is scored history and is
    left exactly as it is; only weeks the archive does not already hold are added.
    Nothing is ever overwritten, which is the same promise the live file makes
    about earlier weeks."""
    old = int(tl["season"])
    dest = timeline_path(group_id, old)
    incoming = tl.get("snapshots", [])
    if dest.exists():
        archived = utils.load_json(dest)
        have = {s.get("as_of_week") for s in archived.get("snapshots", [])}
        added = [s for s in incoming if s.get("as_of_week") not in have]
        archived["snapshots"] = sorted(archived.get("snapshots", []) + added,
                                       key=_week_sort_key)
        kept = len(incoming) - len(added)
        print(f"  [{group_id}] season {old} archive already exists; merged "
              f"{len(added)} new snapshot(s), left {kept} already-archived "
              f"week(s) untouched.")
        out = archived
    else:
        out = {"group_id": group_id, "season": old,
               "snapshots": sorted(incoming, key=_week_sort_key)}
        print(f"  [{group_id}] season {old} rolled over -> {dest.name} "
              f"({len(incoming)} snapshot(s) preserved).")
    utils.save_json_atomic(dest, out)
    return out


def append_timeline(config, snapshot, season=None):
    """Append `snapshot` to the CURRENT season's timeline, replacing any existing
    snapshot for the same effective week (idempotent), keeping snapshots sorted.
    Never rewrites earlier weeks.

    THE FILE IS SCOPED TO ONE SEASON, and this is where that is enforced. Two
    faults lived here before, and they were the same fault seen from two sides:

      1. `season` was stamped only when the file was CREATED, so a timeline that
         survived a rollover kept the previous season's tag forever. All four
         groups declared 2025 while holding a snapshot written by a 2026 run.
         analytics.select_prior refuses a timeline whose declared season is not
         the one being scored — correct against a stale tag, but it meant
         week_move would have stayed null right through the season.
      2. Snapshots deduped on `as_of_week` ALONE, so 2026's week 6 would have
         silently overwritten 2025's week-6 row — scored history destroyed with
         no error, and the last state anyone could compare against replaced by a
         board from a different season.

    Rolling the file fixes both at once and leaves the snapshot SHAPE alone: a
    per-snapshot season tag would also have been correct, but it pushes the
    filtering into every reader, and two of the three readers (build_week_packet,
    app.js) are outside this change. A single-season file is a rule the data
    itself carries, so a reader that knows nothing about seasons still cannot
    reach across one."""
    season = utils.get_season() if season is None else int(season)
    gid = config["group_id"]
    path = timeline_path(gid)
    tl = utils.load_json(path) if path.exists() else None

    if tl is not None:
        declared = tl.get("season")
        if declared is None:
            # Pre-dates the tag, or was hand-edited. Adopt the run's season
            # rather than guess, but say so — an untagged file is exactly the
            # state that let the stale tag go unnoticed for a season.
            print(f"::warning:: [{gid}] timeline.json carries no `season`; "
                  f"adopting {season}. It cannot be verified to hold only that "
                  f"season's weeks.")
        elif int(declared) != season:
            archive_season(gid, tl)
            tl = None                       # the live file starts fresh below

    if tl is None:
        tl = {"group_id": gid, "season": season, "snapshots": []}
    # Re-stamped every run, not only at creation. That single missing line is
    # fault 1 above.
    tl["season"] = season

    snaps = [s for s in tl.get("snapshots", []) if s.get("as_of_week") != snapshot["as_of_week"]]
    snaps.append(snapshot)
    snaps.sort(key=_week_sort_key)
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
    standings = scoring.write_standings(config, picks, as_of_week,
                                        utils.group_draft_status(slug))
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
    timeline = append_timeline(config, build_snapshot(standings, projection, eff))
    print(f"  [{slug}] timeline.json (week {eff})")

    # Board 3 — degrade, don't die (same contract as Board 2). Analytics is a
    # pure RESHAPE of boards 1/2 + the timeline: it computes no probability and
    # reads no cache, so a failure here means a bug in the reshaping, never bad
    # data. Standings must still ship.
    #
    # Runs AFTER append_timeline on purpose, and is handed the freshly-appended
    # timeline object rather than re-reading it: week_move compares against the
    # last snapshot STRICTLY BEFORE this effective week, so appending first is
    # harmless, and passing the object keeps the two from disagreeing if a
    # concurrent run rewrites the file mid-loop.
    try:
        analytics.write_analytics(config, standings, projection, timeline,
                                  as_of_week, eff_week=eff)
        print(f"  [{slug}] analytics.json")
    except Exception as e:  # noqa: BLE001 — analytics must never take down Board 1
        print(f"::warning:: [{slug}] analytics FAILED ({type(e).__name__}: {e}); "
              f"standings.json still written, running degraded (§4).")

    return standings, projection


def main():
    ap = argparse.ArgumentParser(description="Multi-tenant pipeline loop")
    ap.add_argument("--group", default="all", help="group slug or 'all'")
    ap.add_argument("--test", action="store_true", help="run the data/test_picks.json fixture")
    ap.add_argument("--as-of-week", type=int, default=None,
                    help="replay: treat games after week N as unplayed (§7)")
    ap.add_argument("--fetch", action="store_true", help="run fetch_results.py first (default: off)")
    ap.add_argument("--allow-empty", action="store_true",
                    help="write boards even with 0 played games (rule-5 guard "
                         "override; for a deliberate pre-season baseline)")
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

    # ---- rule-5 clobber guard: never regenerate a board off an empty slate --
    # With zero played games every board is still WRITABLE — the arithmetic is
    # defined — but it is meaningless, and worse, it is meaningless in a
    # direction that looks like a result: banked_delta collapses to -line for
    # every OVER pick and +line for every UNDER pick, so the standings rank
    # purely by which side a manager took and the site names a "current leader"
    # before kickoff. Publishing that would overwrite a good board with an
    # artifact of the draft.
    #
    # This is separate from the workflow's week-window gate. That gate stops
    # SCHEDULED runs pre-season; a manual dispatch forces past it, and running
    # one right after the season flip — to populate the new cache — is exactly
    # the sequence that would otherwise deploy this board.
    #
    # Not fatal: pre-season and a dead feed are both normal, and rule 3 says a
    # dead step must not take the pipeline dark. Warn loudly, write nothing,
    # leave every existing board exactly as it was.
    played = utils.count_played_games(season, args.as_of_week)
    if played == 0 and not args.allow_empty:
        horizon = f" through week {args.as_of_week}" if args.as_of_week is not None else ""
        print(f"::warning title=Empty slate — boards left untouched::season {season} "
              f"has 0 played games{horizon}. Skipped scoring for "
              f"{len(slugs)} group(s) rather than overwrite good boards with a "
              f"pre-season artifact (playbook rule 5). Pass --allow-empty to "
              f"force a zero-state board.")
        print(f"\nSKIPPED: 0 played games{horizon} — "
              f"docs/data/<group_id>/ left untouched. Nothing was overwritten.")
        return
    print(f"  slate: {played} played game(s){' (--allow-empty)' if args.allow_empty else ''}")

    for slug in slugs:
        print(f"\n[{slug}]")
        run_group(slug, args.as_of_week)

    print(f"\nDone: {len(slugs)} group(s) scored off the shared "
          f"season-{season} cache -> docs/data/<group_id>/.")


if __name__ == "__main__":
    main()
