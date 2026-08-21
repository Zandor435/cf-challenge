#!/usr/bin/env python3
"""
analytics.py — Board 3: the analytics data layer (ARCHITECTURE §3, §10).

Emits docs/data/<group_id>/analytics.json per docs/output-contract.md, written
in the same loop as standings.json / projection.json / timeline.json.

THE RULE THIS FILE EXISTS TO ENFORCE: the analytics PAGE is a renderer and
nothing else. Every number a manager sees on it is computed here, in Python, as
reproducible arithmetic — the site iterates and prints. Zero computation in JS,
ever, including sorts and ratios. If the page needs a number, it gets a key.

BOARD SEPARATION IS PART OF THE CONTRACT. Every module carries an explicit
`board` field:
  "exact"      — derived from banked results and the floor/ceiling envelope.
                 Reproducible by hand off standings.json. No model.
  "projection" — derived from the Poisson-binomial / shared-draw model. The
                 renderer MUST label these as a projection.
No module ships without one, and no module mixes both kinds of number without
per-field marking. Today exactly one module (championship_odds) is projection.

Modules: race, championship_odds, best_worst, paths, portfolio.
`leverage` is a reserved top-level key, pinned to null this run so the shape is
stable and the renderer can branch on it — the "games that matter" math is the
only real new arithmetic on this page and it lands in its own commit against
this contract.

Nothing here re-derives a number another board already owns: p_win_pool is
PLUMBED from projection.json, ranks/floors/ceilings come from standings.json,
and week-over-week movement from timeline.json. Never reads the cache or the raw
banked index directly — utils owns both (guarded).

Usage:
    python scripts/analytics.py --group all
    python scripts/analytics.py --group church --as-of-week 6
    python scripts/analytics.py --test --as-of-week 6
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils

BOARD_EXACT = "exact"
BOARD_PROJECTION = "projection"
VALID_BOARDS = (BOARD_EXACT, BOARD_PROJECTION)

# Top-level module keys, in emission order. `leverage` is deliberately in the
# list and deliberately null this run (see build_analytics).
MODULE_KEYS = ("race", "championship_odds", "best_worst", "paths", "portfolio")


# --- Small helpers -----------------------------------------------------------

def _r2(x):
    return None if x is None else round(float(x), 2)


def _r6(x):
    return None if x is None else round(float(x), 6)


def _ident(m):
    """The identity pair every module repeats, so the renderer never has to join
    two modules together just to print a name."""
    return {"manager_id": m["manager_id"],
            "display_name": m.get("display_name", m["manager_id"])}


def _rank_map(rows):
    """{manager_id -> 1-based rank} using EXACTLY scoring.py's ordering:
    banked_total desc, floor desc, manager_id asc. Written as a pure function
    over (manager_id, banked_total, floor) so it can also rank a historical
    timeline snapshot — which carries no `rank` field — by the same rule that
    produced today's ranks. A second, subtly-different ordering here is how
    week_move ends up reporting moves that never happened."""
    ordered = sorted(rows, key=lambda r: (-r["banked_total"], -r["floor"], r["manager_id"]))
    return {r["manager_id"]: i for i, r in enumerate(ordered, 1)}


# Why prior selection refused, when it did. These never reach the renderer —
# week_move is null for every one of them alike. They exist so the RUN LOG can
# say WHICH null this is, because two of them are ordinary, one is ordinary or a
# production fault depending on a second fact, and before this they were
# indistinguishable from each other and from a real measured move.
PRIOR_NO_TIMELINE     = "no_timeline"        # ordinary: group has never scored
PRIOR_NO_EARLIER_WEEK = "no_earlier_week"    # ordinary: this is the first week
PRIOR_WEEK_UNRESOLVED = "week_unresolved"    # preseason (ordinary) OR a fault
PRIOR_SEASON_MISMATCH = "season_mismatch"    # the timeline is another season's


def any_games_banked(standings):
    """Has ANY drafted team played a game yet?

    Read off the standings being reshaped rather than off the cache: this is the
    board's OWN answer, so the discriminator cannot disagree with the numbers it
    is describing — the same reason nothing else in this file re-derives a value
    another board owns. scoring.py emits banked_wins/banked_losses on every pick,
    and both are 0 on every pick exactly when nothing has been played.

    This is the only thing separating benign preseason from a real fault when the
    effective week is unresolvable. See _report_prior."""
    for m in standings.get("managers", []):
        for p in m.get("picks", []):
            if (p.get("banked_wins") or 0) or (p.get("banked_losses") or 0):
                return True
    return False


def select_prior(timeline, eff_week, season=None):
    """(snapshot, reason) — the most recent timeline snapshot STRICTLY BEFORE the
    week being scored, or (None, why-not).

    Strictly-before matters: run_groups appends the current week's snapshot
    before analytics runs, so "the latest snapshot" would be this week and every
    week_move would come out 0 — a wrong number wearing the costume of a real
    one. Honest null beats a confident zero.

    AN UNRESOLVABLE WEEK IS NOT A WILDCARD. eff_week is None whenever neither
    --as-of-week nor the cache supplies a week, which is the ordinary preseason
    state. That used to short-circuit the strictly-before test to True, so EVERY
    snapshot qualified and max() handed back the highest week on file. For the
    2026 preseason that was a week-16 snapshot left behind by the 2025 replay,
    and every week_move the site printed was correct arithmetic against a board
    from another season. With no "now" there is no "before": refuse, and emit
    null. The reasoning that rejects a confident zero rejects a confident wrong
    number for exactly the same reason.

    A DECLARED SEASON THAT DISAGREES IS ALSO A REFUSAL. timeline.json is
    append-only across seasons and its snapshots are keyed by week ALONE, so last
    season's week 6 sits in the same list as this season's and the two are
    indistinguishable by week — once this season reaches week 7, a plain
    strictly-before test would happily reach back across the rollover. When the
    file DECLARES a season and it is not the one being scored, nothing in it can
    be shown to belong to this season, so none of it is eligible.

    When the file declares no season at all this check does NOT fire: the
    snapshots carry no season of their own, and inventing one here would be a
    guarantee the data does not make. The week test then stands alone."""
    if not timeline:
        return None, PRIOR_NO_TIMELINE
    if eff_week is None:
        return None, PRIOR_WEEK_UNRESOLVED
    declared = timeline.get("season")
    if season is not None and declared is not None and int(declared) != int(season):
        return None, PRIOR_SEASON_MISMATCH
    snaps = [s for s in timeline.get("snapshots", [])
             if isinstance(s.get("as_of_week"), int) and s["as_of_week"] < eff_week]
    if not snaps:
        return None, PRIOR_NO_EARLIER_WEEK
    return max(snaps, key=lambda s: s["as_of_week"]), None


def prior_snapshot(timeline, eff_week, season=None):
    """The snapshot alone, for callers that do not care why it is missing."""
    return select_prior(timeline, eff_week, season)[0]


def _report_prior(reason, group_id, timeline, season, played):
    """Say which null this is, once per group per run.

    Two of the four reasons are unremarkable and stay silent — a group that has
    never scored, and the first scored week of a season, both legitimately have
    no earlier snapshot and always will. The two that get a line are the ones
    that look alike in the output and are not alike at all:

      preseason     no week anywhere AND nothing banked. Benign; null is the
                    true answer. A plain line, not a warning.
      missing week  no week anywhere but games HAVE been banked. That is a
                    fault — the cache lost its `week` tag while the season is
                    live — and it must not ride out disguised as preseason.

    Deliberately NOT one branch: collapsing them is precisely how the second
    case would go dark behind the first."""
    if reason == PRIOR_WEEK_UNRESOLVED and played:
        print(f"::warning:: [{group_id}] no effective week (--as-of-week and the "
              f"cache's `week` are both null) but games have already been banked "
              f"— week_move is null for every manager. This is NOT the preseason "
              f"case: the cache lost its week tag mid-season. Check fetch_results.py.")
    elif reason == PRIOR_WEEK_UNRESOLVED:
        print(f"  [{group_id}] preseason (no effective week, nothing banked) — "
              f"week_move and prior_week null for every manager.")
    elif reason == PRIOR_SEASON_MISMATCH:
        print(f"::warning:: [{group_id}] timeline.json declares season "
              f"{timeline.get('season')} but this run scores {season}; no snapshot "
              f"in it is eligible, so week_move is null. Note run_groups."
              f"append_timeline stamps `season` only when it CREATES the file, so "
              f"a timeline that survived a rollover keeps the old season's tag.")


def _snapshot_rows(snap):
    """Reconstruct (manager_id, banked_total, floor) from a timeline snapshot.
    The snapshot stores per-PICK banked_delta/floor, not manager totals, so the
    totals are re-summed and rounded exactly the way scoring.py rounds them."""
    rows = []
    for m in snap.get("managers", []):
        picks = m.get("picks", [])
        rows.append({
            "manager_id": m["manager_id"],
            "banked_total": round(sum(p.get("banked_delta") or 0 for p in picks), 2),
            "floor": round(sum(p.get("floor") or 0 for p in picks), 2),
        })
    return rows


# --- Module 1 — race (exact) -------------------------------------------------

def build_race(standings, prior):
    """Per manager: banked_total, rank, week_move, floor, ceiling,
    gap_to_leader, ceiling_remaining. Every field is standings arithmetic;
    week_move is the only one that reaches outside, into timeline.json.

    week_move is SIGNED PLACES GAINED (prev_rank - rank): +2 climbed two spots,
    -1 slipped one, 0 held. null — never 0 — when there is no prior snapshot or
    the manager did not appear in it.

    gap_to_leader is >= 0 and is 0 for the leader. ceiling_remaining is
    ceiling - banked_total: the points still physically on the table."""
    managers = standings.get("managers", [])
    prev_ranks = _rank_map(_snapshot_rows(prior)) if prior else {}
    leader_total = managers[0]["banked_total"] if managers else None

    rows = []
    for m in managers:
        prev = prev_ranks.get(m["manager_id"])
        rows.append({
            **_ident(m),
            "rank": m["rank"],
            "banked_total": _r2(m["banked_total"]),
            "floor": _r2(m["floor"]),
            "ceiling": _r2(m["ceiling"]),
            "gap_to_leader": (_r2(leader_total - m["banked_total"])
                              if leader_total is not None else None),
            "ceiling_remaining": _r2(m["ceiling"] - m["banked_total"]),
            "week_move": (prev - m["rank"]) if prev is not None else None,
        })
    return {
        "board": BOARD_EXACT,
        "prior_week": prior.get("as_of_week") if prior else None,
        "leader_id": managers[0]["manager_id"] if managers else None,
        "managers": rows,
    }


# --- Module 2 — championship_odds (PROJECTION) -------------------------------

def build_championship_odds(standings, projection, prior):
    """p_win_pool, PLUMBED from projection.json — not recomputed. The pool sim
    uses shared per-team draws (ARCHITECTURE §3); recomputing it here off the
    per-pick distributions would silently drop that anti-correlation and print a
    different, wronger number under the same label.

    week_move here is a CHANGE IN PROBABILITY (p_win_pool minus the prior
    snapshot's p_win_pool), not a change in places — race owns places. Signed,
    6 decimals to match the projector's precision. null when the projector
    degraded, when there is no prior snapshot, or when either side's p_win_pool
    is missing.

    The whole module is board=projection: the renderer must label it."""
    proj_by = {m["manager_id"]: m for m in (projection or {}).get("managers", [])}
    prior_by = {m["manager_id"]: m for m in (prior or {}).get("managers", [])}

    rows = []
    for m in standings.get("managers", []):
        mid = m["manager_id"]
        now = proj_by.get(mid, {}).get("p_win_pool")
        was = prior_by.get(mid, {}).get("p_win_pool")
        rows.append({
            **_ident(m),
            "p_win_pool": _r6(now),
            "week_move": _r6(now - was) if (now is not None and was is not None) else None,
        })

    # Sorted by odds desc (nulls last, then manager_id) so the renderer iterates
    # and prints — sorting in JS is computation, and this module is its own
    # ranking rather than standings order.
    rows.sort(key=lambda r: (r["p_win_pool"] is None, -(r["p_win_pool"] or 0.0), r["manager_id"]))
    return {
        "board": BOARD_PROJECTION,
        "prior_week": prior.get("as_of_week") if prior else None,
        # False when the projector degraded this run: every p_win_pool is null
        # and the page should say the projection is unavailable rather than
        # print a column of blanks.
        "available": bool(proj_by),
        "managers": rows,
    }


# --- Module 3 — best_worst (exact) -------------------------------------------

def _pick_entry(m, p):
    """One pick flattened with its owner, for steal / bust / mvp / anchor."""
    return {
        **_ident(m),
        "team": p["team"],
        "conference": p["conference"],
        "line": p["line"],
        "direction": p["direction"],
        "delta": _r2(p["banked_delta"]),
    }


def _extremes(entries, want_max):
    """ALL entries tied at the extreme, never one arbitrarily-chosen winner.
    Empty list in, empty list out."""
    if not entries:
        return []
    best = max(e["delta"] for e in entries) if want_max else min(e["delta"] for e in entries)
    return [e for e in entries if e["delta"] == best]


def build_best_worst(standings):
    """Group-wide steal (largest POSITIVE banked delta) and bust (largest
    NEGATIVE), plus each manager's own best (mvp) and worst (anchor) pick.

    steal/bust are sign-gated: a group where nobody is above their line has no
    steal and emits []. mvp/anchor are RELATIVE — a manager's best pick is still
    their best pick even if it is under water — so they are not sign-gated. Ties
    emit every tied entry; a one-pick manager is their own mvp and anchor, which
    is the honest answer rather than a bug."""
    all_entries = []
    rows = []
    for m in standings.get("managers", []):
        entries = [_pick_entry(m, p) for p in m.get("picks", [])]
        all_entries.extend(entries)
        rows.append({
            **_ident(m),
            "mvp": _extremes(entries, want_max=True),
            "anchor": _extremes(entries, want_max=False),
        })

    positives = [e for e in all_entries if e["delta"] > 0]
    negatives = [e for e in all_entries if e["delta"] < 0]
    return {
        "board": BOARD_EXACT,
        "steal": _extremes(positives, want_max=True),
        "bust": _extremes(negatives, want_max=False),
        "managers": rows,
    }


# --- Module 4 — paths (exact) ------------------------------------------------

def _cmp(basis, my_field, my_value, operator, other, other_field, other_value):
    """The specific comparison that produced a path state, as OPERANDS rather
    than prose — so the renderer can show the reasoning ("your ceiling 4.5 is
    below Blaine's floor 9.0") instead of an unexplained label."""
    return {
        "basis": basis,
        "my_field": my_field,
        "my_value": _r2(my_value),
        "operator": operator,
        "other_manager_id": other["manager_id"] if other else None,
        "other_display_name": (other.get("display_name", other["manager_id"])
                               if other else None),
        "other_field": other_field,
        "other_value": _r2(other_value),
    }


def _path_state(me, others, leader):
    """Entirely floor/ceiling envelope comparisons — no new math, nothing the
    reader cannot check against standings.json by eye.

    Evaluated in this order, and the order IS the contract:
      1. eliminated        my ceiling < some manager's floor
                           (binding comparison = the highest such floor)
      2. clinched          my floor > every other manager's ceiling
      3. controls_destiny  my ceiling > every other floor AND I currently lead
      4. needs_help        otherwise — my ceiling can still reach the leader's
                           floor, but I cannot get there alone

    eliminated is tested FIRST because it is the only strictly-dominating fact:
    if someone's guaranteed minimum already exceeds my maximum, no later rule
    should be able to paint that as alive.

    needs_help is the closed-set fallback and reports `>=` against the leader's
    floor where the four literal rules use `>`. That single `>=` is what keeps
    the state set closed: at ceiling == leader_floor I am not eliminated (a tie
    is still reachable) but I cannot win outright alone — which is exactly
    needs_help. Without it, a group tied at zero (pre-draft, dummy picks, or a
    group with no scored weeks) would fall through all four rules into no state
    at all.

    Week 1 puts everyone in controls_destiny/needs_help. That is correct, and
    the page says so — it is not special-cased here."""
    if not others:
        # Sole manager: clinched is vacuously true (there is no other ceiling to
        # clear). Say so explicitly rather than emit a comparison against nobody.
        return "clinched", _cmp("sole_manager", "floor", me["floor"], None, None, None, None)

    top_ceiling = max(others, key=lambda o: o["ceiling"])
    top_floor = max(others, key=lambda o: o["floor"])

    if me["ceiling"] < top_floor["floor"]:
        return "eliminated", _cmp("ceiling_vs_highest_floor", "ceiling", me["ceiling"],
                                  "<", top_floor, "floor", top_floor["floor"])
    if me["floor"] > top_ceiling["ceiling"]:
        return "clinched", _cmp("floor_vs_highest_ceiling", "floor", me["floor"],
                                ">", top_ceiling, "ceiling", top_ceiling["ceiling"])
    if me["ceiling"] > top_floor["floor"] and me["rank"] == 1:
        return "controls_destiny", _cmp("ceiling_vs_highest_floor", "ceiling", me["ceiling"],
                                        ">", top_floor, "floor", top_floor["floor"])
    return "needs_help", _cmp("ceiling_vs_leader_floor", "ceiling", me["ceiling"],
                              ">=", leader, "floor", leader["floor"])


def build_paths(standings):
    managers = standings.get("managers", [])
    leader = managers[0] if managers else None
    rows = []
    for me in managers:
        others = [o for o in managers if o["manager_id"] != me["manager_id"]]
        state, comparison = _path_state(me, others, leader)
        rows.append({**_ident(me), "state": state, "comparison": comparison})
    return {"board": BOARD_EXACT, "leader_id": leader["manager_id"] if leader else None,
            "managers": rows}


# --- Module 5 — portfolio (exact) --------------------------------------------

def build_portfolio(standings):
    """Every pick a manager holds, with the concentration number.

    share_of_delta = |this pick's banked_delta| / Σ|all their picks' banked_delta|
    — the fraction of the manager's TOTAL SWING that this one pick accounts for.
    Absolute on both sides on purpose: the question it answers is "how much of
    everything that has happened to me is this one team", so a -6.0 anchor is
    60% of a portfolio that also holds +4.0, and a manager's shares sum to 1.0.
    A signed numerator over a signed total would blow up near a net-zero manager
    and print shares above 100%.

    null — never 0, never a ZeroDivisionError — when that absolute total is 0,
    which is exactly the pre-draft / dummy / nothing-played case, where the true
    answer is "unknown", not "this pick is 0% of nothing"."""
    rows = []
    for m in standings.get("managers", []):
        picks = m.get("picks", [])
        abs_total = round(sum(abs(p["banked_delta"]) for p in picks), 2)
        rows.append({
            **_ident(m),
            "banked_total": _r2(m["banked_total"]),
            "absolute_total": abs_total,
            "picks": [{
                "team": p["team"],
                "conference": p["conference"],
                "line": p["line"],
                "direction": p["direction"],
                "banked_delta": _r2(p["banked_delta"]),
                "floor": _r2(p["floor"]),
                "ceiling": _r2(p["ceiling"]),
                "status": p["status"],
                "games_remaining": p["games_remaining"],
                "share_of_delta": (_r6(abs(p["banked_delta"]) / abs_total)
                                   if abs_total else None),
            } for p in picks],
        })
    return {"board": BOARD_EXACT, "managers": rows}


# --- Assembly ----------------------------------------------------------------

def build_analytics(config, standings, projection=None, timeline=None,
                    as_of_week=None, eff_week=None):
    """Full analytics.json object for a group (no I/O).

    Pure reshaping of boards that are already built — it computes no win
    probability and reads no cache, so it cannot disagree with the
    standings.json / projection.json sitting next to it."""
    if eff_week is None:
        eff_week = (as_of_week if as_of_week is not None
                    else utils.cache_meta(utils.get_season())["week"])

    season = utils.get_season()
    # Refusals are reported, not swallowed: a null week_move produced by a
    # live-season fault must not read the same as one produced by preseason.
    prior, reason = select_prior(timeline, eff_week, season)
    if reason is not None:
        _report_prior(reason, config.get("group_id"), timeline, season,
                      any_games_banked(standings))

    cm = utils.cache_meta(season)
    return {
        "meta": {
            "group_id": config["group_id"],
            "season": season,
            "as_of_week": as_of_week,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_fetched_at": cm["fetched_at"],
        },
        "race": build_race(standings, prior),
        "championship_odds": build_championship_odds(standings, projection, prior),
        "best_worst": build_best_worst(standings),
        "paths": build_paths(standings),
        "portfolio": build_portfolio(standings),
        # Reserved. "Games that matter" is the only genuinely NEW math on this
        # page and it ships in its own commit against this contract; pinning the
        # key to null now keeps the shape stable, so the renderer branches on
        # `leverage === null` rather than on whether the key exists.
        "leverage": None,
    }


def write_analytics(config, standings, projection=None, timeline=None,
                    as_of_week=None, eff_week=None):
    out = build_analytics(config, standings, projection, timeline, as_of_week, eff_week)
    path = utils.WEB_DATA_DIR / config["group_id"] / "analytics.json"
    utils.save_json_atomic(path, out)
    return out


def load_timeline(group_id):
    """The committed timeline for a group, or None if it has never been scored.
    A brand-new group is not an error — week_move is simply null."""
    path = utils.WEB_DATA_DIR / group_id / "timeline.json"
    return utils.load_json(path) if path.exists() else None


def main():
    import scoring
    import projector

    ap = argparse.ArgumentParser(description="Board 3 — analytics data layer")
    ap.add_argument("--group", default="all", help="group slug or 'all'")
    ap.add_argument("--test", action="store_true", help="run the data/test_picks.json fixture")
    ap.add_argument("--as-of-week", type=int, default=None,
                    help="replay: treat games after week N as unplayed (§7)")
    args = ap.parse_args()

    slugs = [utils.TEST_GROUP_ID] if args.test else (
        utils.get_all_group_ids() if args.group == "all" else [args.group])

    utils.assert_season_matches_cache()          # §6 season single-source guard

    for slug in slugs:
        config, picks = utils.load_group(slug)
        # The standalone entry point rebuilds both inputs, so it can never emit
        # an analytics board that disagrees with the standings next to it.
        standings = scoring.build_standings(config, picks, args.as_of_week,
                                            utils.group_draft_status(slug))
        try:
            projection = projector.build_projection(config, picks, args.as_of_week)
        except Exception as e:  # noqa: BLE001 — same degrade contract as run_groups
            print(f"::warning:: [{slug}] projector FAILED ({type(e).__name__}: {e}); "
                  f"championship_odds will be null (degraded).")
            projection = None
        out = write_analytics(config, standings, projection, load_timeline(slug),
                              args.as_of_week)
        states = {}
        for m in out["paths"]["managers"]:
            states[m["state"]] = states.get(m["state"], 0) + 1
        tally = ", ".join(f"{k}={v}" for k, v in sorted(states.items())) or "(none)"
        print(f"  [{slug}] analytics.json — {len(out['race']['managers'])} managers, "
              f"paths: {tally}")


if __name__ == "__main__":
    main()
