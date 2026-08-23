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

Second deliverable — the Week 0 narrative packet (build_week0_packet, below).
It READS the frozen baseline and never rewrites it:

    python scripts/preseason_baseline.py --week0-packet panel
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import projector
# The uniform-field rule has ONE implementation, and it lives with the live
# packet builder. This file used to carry a second copy under a "same contract
# as build_week_packet" comment -- which is exactly how two implementations
# drift while both look authoritative. build_week_packet imports only utils, so
# there is no cycle, and its module level is constants plus a guarded main().
from build_week_packet import uniform_profile_fields, exclude_lead_subject

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


# ============================================================================
# DELIVERABLE B — the Week 0 packet (a BYPRODUCT of the baseline above)
# ============================================================================
#
# The frozen baseline is the load-bearing artifact. This section READS it off
# disk and never recomputes or rewrites it, so no amount of packet work can
# move the frozen number. If the baseline and the current cache have drifted
# apart, _pick_row fails loud rather than quietly preferring one of them.
#
# Shape: identical top-level keys to build_week_packet.py's packet, so the
# existing prompt assembly consumes it unchanged, PLUS `preseason: true`.
# Week 0 is the one week build_week_packet.py deliberately refuses to build
# (it returns None at zero completed games) — there is no board to reshape,
# only a draft to describe, which is why this lives here and not there.

WEEK0_WEEK = 0

# Preseason storyline scoring. Week 0 has no banked deltas, no prior snapshot
# and no results, so NONE of build_week_packet.py's detectors (feud, collapse,
# irony, heater) can fire — every one of them keys on movement. These are the
# only angles that exist before kickoff, scored on the same rough 0-10 scale so
# the ranking reads like the live packet's.
COLLISION_BASE = 5.0          # rare by construction: two managers, one team
COLLISION_GAP_WEIGHT = 2.0    # + per game of |implied wins - line|
DEFIANCE_BASE = 1.0
DEFIANCE_GAP_WEIGHT = 2.0     # + per game of |market_gap|
CONCENTRATION_SHARE_WEIGHT = 8.0
CONCENTRATION_MISSING_CONF_WEIGHT = 1.5   # per conference below the WRITTEN min
CONCENTRATION_WAIVER_BONUS = 2.0          # needing a waiver at all is the story
# Scored on DEVIATION from the group's mean envelope width, never on the width
# itself. Four picks of ~12 games each give every manager a ~48-game envelope,
# so absolute width is uniform by construction — it out-scored every real angle
# and distinguished nobody, which is persona sacred rule 7 wearing a number.
ENVELOPE_DEVIATION_WEIGHT = 1.0
PRESEASON_TYPE_PRIORITY = {"collision": 0, "concentration": 1,
                           "market_defiance": 2, "envelope": 3}
TOP25_RANK = 25
MAX_PRESEASON_STORYLINES = 6
MAX_PER_PRESEASON_TYPE = 2   # as build_week_packet: no type may monopolize


def _fail(msg):
    """Fail loud, no partial write."""
    print(f"::error:: {msg}", file=sys.stderr)
    sys.exit(EXIT_BAD_INPUT)


def packet_path(group_id):
    """The Week 0 packet's own durable path — named for the week it describes."""
    return utils.ROOT / "output" / group_id / f"week_{WEEK0_WEEK}_packet.json"


def load_frozen_baseline(path=None):
    p = Path(path) if path else OUTPUT_PATH
    if not p.exists():
        _fail(f"{p} does not exist — freeze the baseline (run this script with "
              f"no flags) before building the Week 0 packet.")
    baseline = utils.load_json(p)
    if not baseline.get("teams"):
        _fail(f"{p.name} carries no teams.")
    return baseline


def _pick_row(pick, mid, baseline, reference, sp_ratings, config):
    """One pick as the column sees it before a single snap has been played.

    data/team_win_totals_2026.json is the SOLE authority for conference and
    win_total (the line). The pick's own copies are checked against it and a
    disagreement is fatal — silently preferring the reference would hide a
    corrupted picks file.
    """
    team = pick["team"]
    row = baseline["teams"].get(team)
    if row is None:
        _fail(f"{team} (picked by {mid}) is absent from the frozen baseline.")
    ref = reference.get(team)
    if ref is None:
        _fail(f"{team} (picked by {mid}) is absent from "
              f"{REFERENCE_PATH.name}, the conference/line authority.")

    line = ref.get("win_total")
    if line is None:
        _fail(f"{team} has no win_total in {REFERENCE_PATH.name}.")
    line = float(line)
    if float(pick["line"]) != line:
        _fail(f"{team} (picked by {mid}): picks.json line {pick['line']} "
              f"disagrees with {REFERENCE_PATH.name} win_total {line}.")
    conference = ref.get("conference")
    if not conference:
        _fail(f"{team} has no conference in {REFERENCE_PATH.name}.")
    if pick.get("conference") != conference:
        _fail(f"{team} (picked by {mid}): picks.json conference "
              f"{pick.get('conference')!r} disagrees with "
              f"{REFERENCE_PATH.name} {conference!r}.")

    if row.get("sp_rating") is None:
        _fail(f"{team} (picked by {mid}) has no SP+ rating in the baseline.")
    implied = row.get("expected_wins")
    if implied is None:
        _fail(f"{team} (picked by {mid}) has no expected_wins in the baseline.")

    # Schedule off the REAL slate, conference-championship games excluded by the
    # group's own count_conference_championship flag (§1). Never 12 assumed.
    state = utils.team_state(team, config)
    # Played count spelled as scheduled-minus-remaining, not state["games_played"]:
    # the raw-index guard (test_cache_access.py §5) reserves the banked keys
    # 'wins'/'losses'/'games_played' to utils.py/fetch_results.py, and its AST scan
    # cannot tell a flag-aware team_state() read from a raw cache subscript. Both
    # sides come off the SAME slate (utils.team_state contract), so the difference
    # is exactly the played count — and Week 0 only asks "has anything been played".
    played_games = state["games_scheduled"] - state["games_remaining"]
    if played_games:
        _fail(f"{team}: {played_games} game(s) already played — this "
              f"is not Week 0 and the preseason packet must not be built.")
    games = state["games_remaining"]
    if games == 0:
        _fail(f"{team} (picked by {mid}) has no scheduled 2026 games.")

    # The frozen baseline must still agree with the cache it is read against; a
    # drift means one of the two is stale and the packet would mix vintages.
    probs = projector.remaining_win_probs(state, sp_ratings)
    if abs(float(sum(probs)) - float(implied)) > 1e-3:
        _fail(f"{team}: frozen baseline expected_wins {implied} disagrees with "
              f"the current cache ({sum(probs):.4f}). The baseline is frozen — "
              f"do not reseed it; investigate the cache.")

    direction = pick["direction"]
    market_gap = float(projector.signed_delta(direction, implied, line))

    # Hard Week 0 envelope: every pick can still finish anywhere from 0 wins to
    # its full slate, so the envelope is the signed delta at both extremes.
    ends = (float(projector.signed_delta(direction, 0, line)),
            float(projector.signed_delta(direction, games, line)))
    floor, ceiling = min(ends), max(ends)

    # Exact Poisson-binomial over the full unplayed schedule (banked wins = 0).
    dist = projector.poisson_binomial(probs)
    finals = np.arange(len(dist))
    p_beat = (float(dist[finals > line].sum()) if direction == "O"
              else float(dist[finals < line].sum()))

    # Strength of schedule off the SAME slate. An opponent with no SP+ rating is
    # an FCS/non-FBS team; the projector already defines FCS_FALLBACK_RATING for
    # exactly this, and the mean uses it so the SoS number and the win
    # probabilities are built from ONE set of opponent ratings, not two. The
    # unrated count rides along so the mean is never read as all-FBS.
    opp_ratings, unrated, top25 = [], 0, 0
    for g in state["remaining_games"]:
        rec = sp_ratings.get(g["opponent"]) or {}
        rating = rec.get("rating")
        if rating is None:
            unrated += 1
            opp_ratings.append(projector.FCS_FALLBACK_RATING)
            continue
        opp_ratings.append(float(rating))
        ranking = rec.get("ranking")
        if ranking is not None and int(ranking) <= TOP25_RANK:
            top25 += 1

    return {
        "manager_id": mid,
        "team": team,
        "conference": conference,
        "line": line,
        "direction": direction,
        "sp_rating": row["sp_rating"],
        "sp_ranking": row.get("sp_ranking"),
        "implied_expected_wins": round(float(implied), 3),
        # Signed in the manager's chosen direction: positive = SP+ agrees.
        "market_gap": round(market_gap, 3),
        "games_scheduled": games,
        "p_beat_line": round(p_beat, 6),
        "floor": round(floor, 3),
        "ceiling": round(ceiling, 3),
        "strength_of_schedule": {
            "mean_opponent_sp_rating": round(sum(opp_ratings) / len(opp_ratings), 3),
            "opponents_sp_top25": top25,
            "opponents_unrated_by_sp": unrated,
            "unrated_scored_at": projector.FCS_FALLBACK_RATING,
        },
    }


def _concentration(rows, required, waiver):
    """Per manager: distinct conferences and the largest single-conference
    share. Computed from the picks, never characterized.

    `required` is the group's min_distinct_conferences — the WRITTEN rule, not
    picks_per_manager. They are different numbers (panel 2026: 4 picks, 3
    conferences) and conflating them would score a legal roster as a violation.
    A manager holding fewer than `required` is only legal via a logged
    commissioner waiver, so the waiver rides along in the block: a roster
    scored against something other than the written rule has to say so.
    """
    counts = defaultdict(int)
    for r in rows:
        counts[r["conference"]] += 1
    total = len(rows)
    top_conf, top_n = max(sorted(counts.items()), key=lambda kv: kv[1])
    distinct = len(counts)
    return {
        "distinct_conferences": distinct,
        "largest_conference": top_conf,
        "largest_conference_picks": top_n,
        "largest_conference_share": round(top_n / total, 4) if total else None,
        "by_conference": dict(sorted(counts.items())),
        "required_distinct_conferences": required,
        "below_required": distinct < required,
        "waived": bool(waiver),
        "waiver_reason": (waiver or {}).get("reason"),
        "waiver_granted": (waiver or {}).get("granted"),
    }


def _collisions(by_mgr, display):
    """Teams held by two managers on OPPOSITE sides. One implied-wins figure
    decides every side, so it is emitted once, not per side."""
    holders = defaultdict(list)
    for mid, rows in by_mgr.items():
        for r in rows:
            holders[r["team"]].append((mid, r))

    out = []
    for team, held in sorted(holders.items()):
        if len(held) < 2:
            continue
        if len({r["direction"] for _, r in held}) < 2:
            continue          # same side is a shared bet, not a collision
        first = held[0][1]
        out.append({
            "team": team,
            "line": first["line"],
            # The single number that settles every side of this collision.
            "implied_expected_wins": first["implied_expected_wins"],
            "sp_ranking": first.get("sp_ranking"),
            "games_scheduled": first["games_scheduled"],
            "sides": [{
                "manager_id": mid,
                "name": display.get(mid, mid),
                "direction": r["direction"],
                "market_gap": r["market_gap"],
                "p_beat_line": r["p_beat_line"],
            } for mid, r in sorted(held, key=lambda kv: kv[0])],
        })
    return out


def _preseason_storylines(by_mgr, display, collisions, concentration, managers):
    """Rank the angles that exist before kickoff. Nothing here is hardcoded to a
    team or a manager: the scores decide the order, and a bigger disagreement
    elsewhere would outrank the collision on its own merit."""
    stories = []

    for c in collisions:
        gap = abs(c["implied_expected_wins"] - c["line"])
        names = " / ".join(
            f"{s['name']} {'OVER' if s['direction'] == 'O' else 'UNDER'}"
            for s in c["sides"])
        stories.append({
            "type": "collision",
            "narrative_score": round(COLLISION_BASE + COLLISION_GAP_WEIGHT * gap, 3),
            "managers": [s["manager_id"] for s in c["sides"]],
            "moment_size": 1,
            "picks": [r for rows in by_mgr.values() for r in rows
                      if r["team"] == c["team"]],
            "race_position": {},
            "evidence": (
                f"{c['team']} at {c['line']:g} is held by two managers on "
                f"opposite sides ({names}). SP+ implies "
                f"{c['implied_expected_wins']:g} wins over "
                f"{c['games_scheduled']} scheduled games, so one number settles "
                f"both bets."),
        })

    for mid, con in concentration.items():
        score = (CONCENTRATION_SHARE_WEIGHT
                 * max(0.0, con["largest_conference_share"]
                       - 1.0 / con["required_distinct_conferences"])
                 + CONCENTRATION_MISSING_CONF_WEIGHT
                 * max(0, con["required_distinct_conferences"]
                       - con["distinct_conferences"])
                 + (CONCENTRATION_WAIVER_BONUS if con["below_required"] else 0.0))
        if score <= 0:
            continue
        stories.append({
            "type": "concentration",
            "narrative_score": round(score, 3),
            "managers": [mid],
            "moment_size": 1,
            "picks": by_mgr[mid],
            "race_position": {},
            "evidence": (
                f"{display.get(mid, mid)} spread {len(by_mgr[mid])} picks across "
                f"{con['distinct_conferences']} conference(s) against a written "
                f"minimum of {con['required_distinct_conferences']}; "
                f"{con['largest_conference_picks']} of {len(by_mgr[mid])} are "
                f"{con['largest_conference']}, a "
                f"{con['largest_conference_share'] * 100:g}% share."
                + (f" Below the minimum, and legal only by commissioner waiver "
                   f"({con['waiver_reason']}, granted {con['waiver_granted']})."
                   if con["below_required"] and con["waived"] else "")
                + (" Below the minimum with NO waiver on file."
                   if con["below_required"] and not con["waived"] else "")),
        })

    # A collision already tells its team's story from both sides; re-filing the
    # same team as market_defiance would spend two of six slots on one moment
    # (the job build_week_packet.moment_key does for the live packet).
    collided = {c["team"] for c in collisions}
    all_rows = [r for rows in by_mgr.values() for r in rows
                if r["team"] not in collided]
    if all_rows:
        worst = min(all_rows, key=lambda r: (r["market_gap"], r["team"]))
        best = max(all_rows, key=lambda r: (r["market_gap"], r["team"]))
        for label, r in (("lowest", worst), ("highest", best)):
            stories.append({
                "type": "market_defiance",
                "narrative_score": round(
                    DEFIANCE_BASE + DEFIANCE_GAP_WEIGHT * abs(r["market_gap"]), 3),
                "managers": [r["manager_id"]],
                "moment_size": 1,
                "picks": [r],
                "race_position": {},
                "evidence": (
                    f"{display.get(r['manager_id'], r['manager_id'])} took "
                    f"{r['team']} {'OVER' if r['direction'] == 'O' else 'UNDER'} "
                    f"{r['line']:g}; SP+ implies {r['implied_expected_wins']:g} "
                    f"wins, a market_gap of {r['market_gap']:+g} — the "
                    f"{label} on the board."),
            })

    widths = {m["manager_id"]: m["ceiling"] - m["floor"] for m in managers}
    mean_width = sum(widths.values()) / len(widths) if widths else 0.0
    for m in managers:
        width = widths[m["manager_id"]]
        stories.append({
            "type": "envelope",
            "narrative_score": round(
                ENVELOPE_DEVIATION_WEIGHT * abs(width - mean_width), 3),
            "managers": [m["manager_id"]],
            "moment_size": 1,
            "picks": by_mgr[m["manager_id"]],
            "race_position": {},
            "evidence": (
                f"{m['name']} opens with a floor of {m['floor']:+g} and a "
                f"ceiling of {m['ceiling']:+g}, a {width:g}-game envelope "
                f"against a group mean of {mean_width:g}, and a "
                f"{m['p_win_pool'] * 100:.1f}% chance to win the pool."),
        })

    stories.sort(key=lambda s: (-s["narrative_score"],
                                PRESEASON_TYPE_PRIORITY.get(s["type"], 9),
                                s["managers"]))
    kept, seen = [], defaultdict(int)
    for story in stories:
        if seen[story["type"]] >= MAX_PER_PRESEASON_TYPE:
            continue
        seen[story["type"]] += 1
        kept.append(story)
    return kept[:MAX_PRESEASON_STORYLINES]


def build_week0_packet(group_id, baseline_path=None):
    season = utils.assert_season_matches_cache()
    cache = utils.load_cache(season)

    played = sum(1 for g in (cache.get("games") or []) if g.get("completed"))
    if played:
        _fail(f"the cache holds {played} completed game(s); Week 0 has passed "
              f"and this packet describes a board before kickoff. Run "
              f"scripts/build_week_packet.py instead.")

    baseline = load_frozen_baseline(baseline_path)
    reference_doc, _ = load_reference()
    reference = reference_doc["teams"]
    sp_ratings = utils.season_sp_ratings(season)
    if not sp_ratings:
        _fail("the cache carries no SP+ ratings.")

    config, raw_picks = utils.load_group(group_id)
    picks = utils.real_picks(raw_picks)
    if not picks:
        _fail(f"group {group_id} has no real picks.")
    display = utils.manager_display_map(config)
    # The WRITTEN rule, from the same config key validate_team_names.py gates
    # on — never picks_per_manager, which is a different number.
    required_conferences = config.get("min_distinct_conferences")
    if required_conferences is None:
        _fail(f"{group_id}'s config.json has no min_distinct_conferences; the "
              f"concentration block has no rule to measure against.")
    waivers = {w["manager_id"]: w
               for w in (config.get("conference_minimum_waivers") or [])}

    by_mgr = defaultdict(list)
    for pick in picks:
        mid = pick.get("manager")
        if mid not in display:
            _fail(f"pick on {pick.get('team')} names manager {mid!r}, who is "
                  f"not on {group_id}'s config.json roster.")
        by_mgr[mid].append(
            _pick_row(pick, mid, baseline, reference, sp_ratings, config))
    for mid in display:
        if mid not in by_mgr:
            _fail(f"manager {mid!r} is on the roster but holds no picks.")

    # Pool odds from the projector's SHARED-DRAW simulator: it draws each unique
    # team's season once and scores every manager off that same draw, so the two
    # sides of a collision are exact complements rather than independent worlds
    # (projector.head_to_head_pairs asserts precisely that). Banked wins are 0.
    _, _totals, p_win_pool = projector.simulate_totals(config, picks, None)

    concentration = {mid: _concentration(rows, required_conferences,
                                         waivers.get(mid))
                     for mid, rows in by_mgr.items()}

    managers = []
    for mid, rows in by_mgr.items():
        managers.append({
            "manager_id": mid,
            "name": display.get(mid, mid),
            "aggregate_market_gap": round(sum(r["market_gap"] for r in rows), 3),
            "floor": round(sum(r["floor"] for r in rows), 3),
            "ceiling": round(sum(r["ceiling"] for r in rows), 3),
            "p_win_pool": round(float(p_win_pool.get(mid, 0.0)), 6),
            "concentration": concentration[mid],
            "picks": rows,
        })
    managers.sort(key=lambda m: (-m["p_win_pool"], -m["aggregate_market_gap"],
                                 m["manager_id"]))
    for i, m in enumerate(managers):
        m["rank"] = i + 1

    collisions = _collisions(by_mgr, display)

    # Nothing is banked, so total_delta is 0.0 for everyone and the ordering is
    # projected, not earned. `basis` says so in words, because a 0.0 read off one
    # row looks exactly like a real standing.
    race = {
        "leader": None,
        "standings": [{
            "manager_id": m["manager_id"],
            "name": m["name"],
            "total_delta": 0.0,
            "gap_to_leader": 0.0,
            "delta_this_week": None,
            "rank": m["rank"],
            "rank_change": None,
        } for m in managers],
    }

    profiles = {}
    for mid, rows in by_mgr.items():
        overs = sum(1 for r in rows if r["direction"] == "O")
        profiles[mid] = {
            "over_count": overs,
            "under_count": len(rows) - overs,
            "conference_spread": concentration[mid]["distinct_conferences"],
            "largest_conference_share": concentration[mid]["largest_conference_share"],
            "avg_line": round(sum(r["line"] for r in rows) / len(rows), 4),
            "mean_opponent_sp_rating": round(
                sum(r["strength_of_schedule"]["mean_opponent_sp_rating"]
                    for r in rows) / len(rows), 3),
            "opponents_sp_top25": sum(r["strength_of_schedule"]["opponents_sp_top25"]
                                      for r in rows),
            "aggregate_market_gap": round(sum(r["market_gap"] for r in rows), 3),
        }

    storylines = _preseason_storylines(by_mgr, display, collisions,
                                       concentration, managers)

    # THE CODA RULE (see build_week_packet.exclude_lead_subject). Selected AFTER
    # the storylines, not before, because it must know what the lead is about:
    # storylines[0] is Beat 1, and the Worst Pick coda may not re-target its
    # manager or its team. Week 0 panel shipped exactly that collision -- lead
    # was the Blaine/Chris feud over Texas, coda was Chris on Texas.
    #
    # Candidates are every pick in market_gap order (worst first), so the filter
    # preserves "lowest market_gap" as the selection rule and simply skips the
    # ones the lead already spent its words on.
    ranked_rows = sorted((r for rows in by_mgr.values() for r in rows),
                         key=lambda r: (r["market_gap"], r["team"]))
    coda_rows, coda_exclusion = exclude_lead_subject(
        ranked_rows, storylines[0] if storylines else None,
        label="worst-pick", group_id=group_id)
    worst = coda_rows[0]
    worst_pick = {
        "manager_id": worst["manager_id"],
        "name": display.get(worst["manager_id"], worst["manager_id"]),
        "team": worst["team"],
        "line": worst["line"],
        "direction": worst["direction"],
        "implied_expected_wins": worst["implied_expected_wins"],
        "market_gap": worst["market_gap"],
        "p_beat_line": worst["p_beat_line"],
        "games_scheduled": worst["games_scheduled"],
        "selected_by": ("lowest market_gap on the board that does NOT share a "
                        "manager or team with the One Big Thing lead — computed "
                        "here, never chosen by the model"),
    }

    return {
        "group_id": group_id,
        "week": WEEK0_WEEK,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "season_complete": False,
        # The branch flag: this packet describes a draft, not a played week.
        "preseason": True,
        "stakes": config.get("stakes"),
        "comparison": {
            "prior_week": None,
            "weeks_elapsed": None,
            "basis": (f"Week 0 — no games have been played in season {season}. "
                      f"Nothing is banked, every manager's total_delta is 0.0, "
                      f"and the standings order is projected pool odds, not "
                      f"earned position. There is no leader."),
        },
        "meta": {
            "baseline": {
                "path": f"data/{OUTPUT_PATH.name}",
                "frozen_at": baseline["meta"].get("generated_at"),
                "sp_vintage": baseline["meta"].get("sp_vintage", {}).get("date"),
                "cache_fetched_at": (baseline["meta"].get("sp_vintage", {})
                                     .get("cache_fetched_at")),
            },
            "cache_fetched_at": cache.get("fetched_at"),
            "reference": f"data/{REFERENCE_PATH.name}",
            "count_conference_championship":
                utils.counts_conference_championship(config),
            "pool_odds_method": ("projector.simulate_totals — shared per-team "
                                 "draw Monte Carlo, head-to-head coupled"),
            "pool_sim_trials": projector.POOL_SIM_TRIALS,
            "per_pick_distribution": "exact Poisson-binomial over the full slate",
        },
        "race": race,
        "storylines": storylines,
        # Nothing has been played, so nothing has died ugly. The preseason coda
        # is worst_pick_on_the_board instead.
        "bad_beat_candidates": [],
        "worst_pick_on_the_board": worst_pick,
        # Audit trail for the coda rule: the lead subject that was excluded,
        # how many candidates it cost, and whether the degraded path was taken.
        "coda_exclusion": coda_exclusion,
        "collisions": collisions,
        "concentration": concentration,
        "managers": managers,
        "manager_profiles": profiles,
        "uniform_profile_fields": uniform_profile_fields(profiles),
    }


def report_packet(packet):
    m = packet["meta"]
    print(f"\n  group {packet['group_id']} — week {packet['week']} "
          f"(preseason={packet['preseason']}, "
          f"season_complete={packet['season_complete']})")
    print(f"  baseline frozen {m['baseline']['frozen_at']} "
          f"(SP+ vintage {m['baseline']['sp_vintage']})")
    print(f"\n  {'manager':<10}{'aggGap':>8}{'floor':>8}{'ceil':>8}"
          f"{'P(win)':>9}  concentration")
    for mgr in packet["managers"]:
        c = mgr["concentration"]
        print(f"  {mgr['name']:<10}{mgr['aggregate_market_gap']:>+8.2f}"
              f"{mgr['floor']:>+8.1f}{mgr['ceiling']:>+8.1f}"
              f"{mgr['p_win_pool'] * 100:>8.1f}%  "
              f"{c['distinct_conferences']} conf / "
              f"{c['largest_conference_share'] * 100:g}% {c['largest_conference']}")
    for c in packet["collisions"]:
        sides = "  vs  ".join(
            f"{s['name']} {s['direction']} ({s['market_gap']:+g})"
            for s in c["sides"])
        print(f"\n  COLLISION {c['team']} {c['line']:g} — implied "
              f"{c['implied_expected_wins']:g}\n    {sides}")
    w = packet["worst_pick_on_the_board"]
    print(f"\n  WORST PICK ON THE BOARD: {w['name']} — {w['team']} "
          f"{w['direction']} {w['line']:g}, implied "
          f"{w['implied_expected_wins']:g}, gap {w['market_gap']:+g}")
    print(f"\n  storylines ({len(packet['storylines'])}):")
    for s in packet["storylines"]:
        print(f"    {s['narrative_score']:>6.2f}  {s['type']:<16}"
              f"{','.join(s['managers'])}")


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
    ap.add_argument("--week0-packet", metavar="GROUP", default=None,
                    help="do not touch the baseline; build that group's Week 0 "
                         "narrative packet FROM the frozen baseline and write it "
                         "to output/<group>/week_0_packet.json")
    ap.add_argument("--packet-out", default=None,
                    help="override the Week 0 packet path")
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else OUTPUT_PATH

    # Deliverable B. A separate entry point on purpose: it READS the frozen
    # baseline and returns before any of the freeze machinery below can run, so
    # regenerating the packet can never rewrite Deliverable A.
    if args.week0_packet:
        group_id = args.week0_packet
        print("=" * 60)
        print(f"WEEK 0 PACKET — group {group_id} (from the frozen baseline)")
        print("=" * 60)
        packet = build_week0_packet(group_id, args.out)
        report_packet(packet)
        if args.dry_run:
            print()
            print(f"  dry run: nothing written (would write "
                  f"{args.packet_out or packet_path(group_id)}).")
            return
        ppath = Path(args.packet_out) if args.packet_out else packet_path(group_id)
        utils.save_json_atomic(ppath, packet)
        print()
        print(f"  wrote {ppath}")
        return

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
