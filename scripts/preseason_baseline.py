#!/usr/bin/env python3
"""
preseason_baseline.py — One-time SP+-anchored preseason baseline (ARCHITECTURE §7, §10.3).

Job: freeze "what SP+ thought at the draft" as an expected-win total per FBS
team. The gap between this frozen line and the live projection is the drift
story (§7). It is NOT a scoring input: nothing in scoring.py or projector.py
reads it, and it is written exactly ONCE and never reseeded (§6).

HOW THE NUMBER IS MADE — and the one thing that must never change:
    expected_wins(team) = sum over the team's scheduled 2026 regular-season
    games of projector.game_win_prob(SP+ team, SP+ opponent, home, neutral)

That is the SAME function, the SAME logistic and the SAME tuned constants
(scale 13.5, HFA 4.0, market-bridge calibrated) the live projection uses. Same
code path, different inputs — preseason SP+ here, live SP+ there. That identity
is the entire point: a drift number computed against a second, parallel
probability model would measure the difference between two models, not the
movement of one team. There is no probability math in this file. It imports it.

§7 SUPERSEDED ON ONE POINT: §7 describes a manual Claude + Zach game-by-game
pass, written before projector.py existed. There is no manual pass. The
projector's function is the baseline.

TIME-CRITICAL, and this is why the freeze guards below are absolute: preseason
SP+ exists only until the first games are played (2026-08-27). Once results land,
CFBD's ratings weight them and the preseason input is gone for good — it cannot
be refetched, because /ratings/sp returns only the CURRENT rating for a season.

Reads (all local, NO network):
  data/team_win_totals_2026.json   the FBS reference + the Vegas win-total line
  data/cfbd_cache.json             2026 schedule + preseason SP+ (via utils)

Writes:
  data/preseason_baseline_2026.json   WRITE-ONCE (see the freeze guards)

Built over EVERY FBS team in the reference, not just drafted teams: the freeze
is time-boxed by the calendar and the draft has not happened, so tying one to
the other would lose the input. Schedule length is read off the real slate per
team — 8 teams play 11, 1 plays 13 — never assumed to be 12 (§1).

Usage:
    python scripts/preseason_baseline.py
    python scripts/preseason_baseline.py --force      # overwrite an existing file
    python scripts/preseason_baseline.py --dry-run    # compute + report, write nothing
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import projector

REFERENCE_PATH = utils.DATA_DIR / "team_win_totals_2026.json"
OUTPUT_PATH = utils.DATA_DIR / "preseason_baseline_2026.json"

# Exit codes, distinct so a caller (or a test) can tell the guards apart.
EXIT_SEASON_STARTED = 2
EXIT_ALREADY_FROZEN = 3
EXIT_BAD_INPUT = 4


# --- Freeze guards -----------------------------------------------------------

def assert_preseason(cache):
    """The season must not have started. ABSOLUTE — --force does not bypass it.

    --force exists to overwrite a file, which is recoverable. This is not: once
    a game is final, CFBD's SP+ weights results and the preseason vintage is
    unrecoverable, so a "forced" run after kickoff would not rebuild the
    baseline — it would silently write a DIFFERENT, results-contaminated number
    under the same name and call it the draft-day expectation. That is exactly
    the silent-wrong-data failure the playbook forbids, so there is no flag for
    it.
    """
    played = sum(1 for g in (cache.get("games") or []) if g.get("completed"))
    if played:
        print(f"::error:: REFUSING to build the preseason baseline: the cache "
              f"holds {played} completed game(s), so its SP+ ratings already "
              f"weight results and are no longer the preseason vintage. This "
              f"baseline can only be built before the first kickoff, and there "
              f"is deliberately no --force for this guard.", file=sys.stderr)
        sys.exit(EXIT_SEASON_STARTED)


def assert_not_frozen(out_path, force):
    """The output is write-once. --force is the only way past this one."""
    if out_path.exists() and not force:
        print(f"::error:: REFUSING to overwrite {out_path.name}: the preseason "
              f"baseline is FROZEN and written exactly once (§6/§7) — the gap "
              f"between it and the live projection is the story, and reseeding "
              f"it silently erases that gap. Pass --force if you genuinely mean "
              f"to replace it.", file=sys.stderr)
        sys.exit(EXIT_ALREADY_FROZEN)


# --- The baseline ------------------------------------------------------------

def load_reference():
    ref = utils.load_json(REFERENCE_PATH)
    teams = ref.get("teams") or {}
    if not teams:
        print(f"::error:: {REFERENCE_PATH.name} carries no teams.", file=sys.stderr)
        sys.exit(EXIT_BAD_INPUT)
    return ref, teams


def team_baseline(team, entry, sp_ratings):
    """One team's frozen row. All probability math is projector's, not ours."""
    # group_config {} -> count_conference_championship False (utils default, §1).
    # In preseason nothing is played, so remaining_games IS the full schedule.
    state = utils.team_state(team, {})
    if state["games_scheduled"] == 0:
        print(f"::error:: {team} has no scheduled 2026 games in the cache; "
              f"refusing to freeze a zero-game baseline.", file=sys.stderr)
        sys.exit(EXIT_BAD_INPUT)

    probs = projector.remaining_win_probs(state, sp_ratings)
    expected_wins = float(sum(probs))
    line = entry.get("win_total")
    rec = sp_ratings.get(team) or {}

    return {
        "display_name": entry.get("display_name", team),
        "conference": entry.get("conference"),
        "sp_rating": rec.get("rating"),
        "sp_ranking": rec.get("ranking"),
        # Off the REAL slate per team (8 teams at 11, 1 at 13) — never assumed 12.
        "games_scheduled": state["games_scheduled"],
        "expected_wins": round(expected_wins, 4),
        "vegas_win_total": line,
        # Positive = SP+ is MORE bullish on this team than the market.
        "delta_vs_vegas": (None if line is None
                           else round(expected_wins - float(line), 4)),
    }


def build_baseline(cache, reference, teams, sp_ratings):
    rows = {team: team_baseline(team, entry, sp_ratings)
            for team, entry in sorted(teams.items())}

    fetched_at = cache.get("fetched_at") or ""
    lengths = {}
    for r in rows.values():
        lengths[r["games_scheduled"]] = lengths.get(r["games_scheduled"], 0) + 1

    return {
        "meta": {
            "_note": ("FROZEN preseason baseline (ARCHITECTURE §7). Written ONCE, "
                      "before the first 2026 kickoff, and never reseeded — the gap "
                      "between this and the live projection IS the drift story. "
                      "Not a scoring input: nothing reads it to score a board."),
            "season": cache.get("season"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "frozen": True,
            "sp_vintage": {
                # The preseason vintage this is anchored to. Unrecoverable once
                # games are played, so it is recorded, not just implied.
                "cache_fetched_at": fetched_at,
                "date": fetched_at[:10] if len(fetched_at) >= 10 else None,
                "cache_week": cache.get("week"),
                "completed_games_at_freeze": 0,
                "teams_rated": len(sp_ratings),
                "archive": f"data/ratings_archive/{cache.get('season')}/"
                           f"{fetched_at[:10]}.json" if len(fetched_at) >= 10 else None,
            },
            # Recorded by READING projector's constants, never by copying their
            # values here — a literal would drift silently the moment they are
            # re-fitted, and the baseline would misreport how it was made.
            "projector": {
                "function": "projector.game_win_prob",
                "model": "logistic on the SP+ point margin, home field applied "
                         "to the margin; expected_wins = sum of per-game P(win)",
                "home_field_advantage_pts": projector.HOME_FIELD_ADVANTAGE_PTS,
                "win_prob_points_scale": projector.WIN_PROB_POINTS_SCALE,
                "fcs_fallback_rating": projector.FCS_FALLBACK_RATING,
                "calibration": "scale/HFA jointly fitted on the leak-free market "
                               "bridge (calibrate_spread.py, 2026-07, 2021-2025)",
                "same_code_path_as_live_projection": True,
            },
            "reference": {
                "path": f"data/{REFERENCE_PATH.name}",
                "source": reference.get("source"),
                "teams": len(rows),
            },
            "counts": {
                "teams": len(rows),
                "scheduled_game_lengths": dict(sorted(lengths.items())),
                "teams_with_a_line": sum(1 for r in rows.values()
                                         if r["vegas_win_total"] is not None),
            },
        },
        "teams": rows,
    }


def report(baseline, n=10):
    """Where SP+ and the market disagree most, both directions."""
    rows = [(t, r) for t, r in baseline["teams"].items()
            if r["delta_vs_vegas"] is not None]
    rows.sort(key=lambda tr: tr[1]["delta_vs_vegas"], reverse=True)
    meta = baseline["meta"]
    print(f"\n  season {meta['season']}: {meta['counts']['teams']} FBS teams, "
          f"schedule lengths {meta['counts']['scheduled_game_lengths']}, "
          f"SP+ vintage {meta['sp_vintage']['date']}")
    print(f"  projector: scale={meta['projector']['win_prob_points_scale']}, "
          f"HFA={meta['projector']['home_field_advantage_pts']}, "
          f"FCS fallback={meta['projector']['fcs_fallback_rating']}")

    def block(title, items):
        print(f"\n  {title}")
        print(f"    {'team':<24}{'xW':>7}{'line':>7}{'delta':>8}{'g':>4}")
        for t, r in items:
            print(f"    {t:<24}{r['expected_wins']:>7.2f}"
                  f"{r['vegas_win_total']:>7.1f}{r['delta_vs_vegas']:>+8.2f}"
                  f"{r['games_scheduled']:>4}")

    block(f"SP+ HIGHEST vs the market (top {n})", rows[:n])
    block(f"SP+ LOWEST vs the market (bottom {n})", list(reversed(rows[-n:])))


def main():
    ap = argparse.ArgumentParser(
        description="Freeze the one-time SP+ preseason baseline (write-once)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing baseline (does NOT bypass the "
                         "season-has-started guard)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute and report, write nothing")
    ap.add_argument("--out", default=None, help="output path (default: "
                                                "data/preseason_baseline_2026.json)")
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else OUTPUT_PATH

    print("=" * 60)
    print("PRESEASON BASELINE (frozen, write-once)")
    print("=" * 60)

    season = utils.assert_season_matches_cache()      # §6 single-source guard
    cache = utils.load_cache(season)

    assert_preseason(cache)                            # absolute
    if not args.dry_run:
        assert_not_frozen(out_path, args.force)        # --force bypasses this one

    reference, teams = load_reference()
    sp_ratings = utils.season_sp_ratings(season)
    if not sp_ratings:
        print("::error:: the cache carries no SP+ ratings; there is nothing to "
              "anchor a baseline to.", file=sys.stderr)
        sys.exit(EXIT_BAD_INPUT)

    baseline = build_baseline(cache, reference, teams, sp_ratings)
    report(baseline)

    if args.dry_run:
        print(f"\n  dry run: nothing written (would write {out_path}).")
        return
    utils.save_json_atomic(out_path, baseline)
    print(f"\n  FROZEN: {out_path} "
          f"({baseline['meta']['counts']['teams']} teams). Do not regenerate.")


if __name__ == "__main__":
    main()
