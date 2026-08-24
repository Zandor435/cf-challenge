#!/usr/bin/env python3
"""
build_week_packet.py — The structured week state the SVP column reads.

Job: reshape ALREADY-COMMITTED engine output into one narrative packet per
group. This script computes NO football math of its own: every number it emits
is either copied verbatim from the output contract (docs/output-contract.md) or
derived from two contract numbers by a formula the contract itself defines
(e.g. rank from banked_total). The pundit then quotes the packet verbatim
(templates/svp_persona.md, sacred rule 1), so a number invented here would be
laundered into the column as fact.

Reads (all local, NO network):
  docs/data/<group>/standings.json    Board 1, exact arithmetic  — current state
  docs/data/<group>/projection.json   Board 2, labeled projection — p_beat_line
  docs/data/<group>/timeline.json     append-only week history    — PRIOR state
  groups/<group>/picks.json           team/line/direction/conference
  groups/<group>/config.json          roster, display names, stakes passthrough
  data/cfbd_cache.json                game-level detail for bad beats

Writes:
  groups/<group>/output/week_packet.json   (overwrite, regenerated each run)

PRESEASON writes NOTHING and exits 0. Before the first kickoff the cache holds a
full schedule but zero played games: there is no week to resolve and no column
to write. That is a legitimate state, so build_packet() returns None and main()
exits cleanly rather than erroring. The gate keys on the PLAYED-GAME COUNT, not
on `week` being null — a null week with games already played means the cache and
the committed boards disagree, which still fails loud in resolve_week().

NOT docs/data/ — that is the Pages web root and the output contract locks it to
exactly three files. The packet is an internal prompt input, not a published
artifact.

PRIOR-WEEK STATE comes from timeline.json, not from a private history folder.
timeline.json is already append-only, week-keyed and idempotent, and a snapshot
is enough to reconstruct a manager's banked_total/floor/ceiling (sums of the
picks' fields), rank (banked_total desc, floor desc, manager_id) and per-pick
status (CLINCHED floor>0 / DEAD ceiling<0 / LIVE) using the contract's own
formulas. A second history store would be a parallel truth (playbook rule 13)
and circular — packets derived from packets.

"THIS WEEK" IS REALLY "SINCE THE PREVIOUS SNAPSHOT". Snapshots are written per
scored week, and a season can have gaps, so the `comparison` block states the
basis explicitly (prior_week, weeks_elapsed, basis). When no prior snapshot
exists the *_this_week fields are null — never 0.0, which the column would
print as a fact.

Usage:
    python scripts/build_week_packet.py --group panel
    python scripts/build_week_packet.py --group panel --week 16   # guard
"""

import argparse
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils


# --- Tunable detection/scoring constants ------------------------------------
# All narrative ranking knobs live here. Nothing below reads a magic number.

FEUD_MIN_DIVERGENCE = 0.5        # min |deltaA - deltaB| for a feud to rank
FEUD_ADJACENCY_BONUS = 2.0       # bonus when the two are adjacent in the table
COLLAPSE_LOOKBACK_WEEKS = 3      # a collapse is a SLIDE, measured over a window
COLLAPSE_MIN_CEILING_DROP = 2.0  # min ceiling loss (games) across that window
COLLAPSE_TOP_HALF_ONLY = True    # a collapse only reads as one from up high
IRONY_BASE = 0.5                 # floor, so any resolved pick registers at all
IRONY_DELTA_WEIGHT = 1.0         # + this per game of the pick's OWN |banked_delta|
IRONY_LEADER_FLIP_BONUS = 4.0    # MAX flip credit, scaled by share of the swing
IRONY_FLIP_MIN_SHARE = 0.1       # min share of the swing to claim any flip credit
HEATER_MIN_DELTA = 1.5           # min banked gain PER WEEK to count as a heater
HEATER_STREAK_WEIGHT = 1.0       # per consecutive positive week
BAD_BEAT_MAX_MARGIN = 8          # points; wider than this isn't a bad beat
QUIET_WEEK_FLOOR = 1.0           # top score below this => quiet_week
MAX_STORYLINES = 6
MAX_PER_TYPE = 2                 # no single type may monopolize the packet
MAX_BAD_BEATS = 5

# Tiebreak order when scores are equal, straight from the persona template's
# stated Big Thing preference: feud > collapse > irony > heater. Without this a
# pile of equally-scored ironies buries the rarer, better story.
TYPE_PRIORITY = {"feud": 0, "collapse": 1, "irony": 2, "heater": 3, "quiet_week": 4}

# Float noise guard: contract numbers are halves, so 6dp is far past meaningful.
_ND = 6


def _r(x):
    """Round away float-sum noise. None passes through (null stays null)."""
    return None if x is None else round(x, _ND)


def _sub(a, b):
    """a - b, but null if either side is unknown. Never substitutes 0.0 for
    'we don't know' — a fabricated zero is a number the column would print."""
    return None if (a is None or b is None) else _r(a - b)


# --- Paths -------------------------------------------------------------------

def packet_dir(group_id):
    return utils.GROUPS_DIR / group_id / "output"


def packet_path(group_id):
    return packet_dir(group_id) / "week_packet.json"


def _require(path, what):
    """Fail loud on a missing input: no partial packet, non-zero exit."""
    if not Path(path).exists():
        print(f"::error:: [{what}] missing required input {path} — refusing to "
              f"build a partial packet.", file=sys.stderr)
        sys.exit(1)
    return utils.load_json(path)


# --- Contract-defined derivations -------------------------------------------
# Each mirrors a formula stated in docs/output-contract.md. Kept together so a
# contract change has exactly one place to land.

def status_of(floor, ceiling):
    """Contract: CLINCHED if floor > 0; DEAD if ceiling < 0; else LIVE."""
    if floor is None or ceiling is None:
        return None
    if floor > 0:
        return "CLINCHED"
    if ceiling < 0:
        return "DEAD"
    return "LIVE"


def rank_managers(totals):
    """Contract: rank 1-based by banked_total desc, ties by floor desc then
    manager_id. `totals` is {mid: (banked_total, floor)}. Distinct ranks."""
    order = sorted(totals.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
    return {mid: i + 1 for i, (mid, _) in enumerate(order)}


def state_from_snapshot(snapshot):
    """Reconstruct a full prior-week manager state from a timeline snapshot,
    using only contract formulas (sums of the picks' fields + the rank rule)."""
    mgrs = {}
    for m in snapshot.get("managers", []):
        picks = {}
        for p in m.get("picks", []):
            picks[p["team"]] = {
                "banked_delta": p.get("banked_delta"),
                "floor": p.get("floor"),
                "ceiling": p.get("ceiling"),
                "status": status_of(p.get("floor"), p.get("ceiling")),
                "p_beat_line": p.get("p_beat_line"),
            }
        vals = list(picks.values())
        mgrs[m["manager_id"]] = {
            "banked_total": _r(sum(v["banked_delta"] or 0 for v in vals)),
            "floor": _r(sum(v["floor"] or 0 for v in vals)),
            "ceiling": _r(sum(v["ceiling"] or 0 for v in vals)),
            "picks": picks,
        }
    ranks = rank_managers({mid: (m["banked_total"], m["floor"])
                           for mid, m in mgrs.items()})
    for mid, m in mgrs.items():
        m["rank"] = ranks[mid]
    return mgrs


def state_from_standings(standings, projection):
    """Current state, read verbatim off Board 1 (+ Board 2's p_beat_line)."""
    proj = {}
    for m in (projection or {}).get("managers", []):
        for p in m.get("picks", []):
            proj[(m["manager_id"], p["team"])] = p

    mgrs = {}
    for m in standings["managers"]:
        mid = m["manager_id"]
        picks = {}
        for p in m["picks"]:
            pp = proj.get((mid, p["team"]), {})
            picks[p["team"]] = {
                "team": p["team"],
                "conference": p.get("conference"),
                "line": p.get("line"),
                "direction": p.get("direction"),
                "banked_wins": p.get("banked_wins"),
                "games_remaining": p.get("games_remaining"),
                "banked_delta": p.get("banked_delta"),
                "floor": p.get("floor"),
                "ceiling": p.get("ceiling"),
                "status": p.get("status"),
                # Contract's own name. NOT "p_over": for an under pick this is
                # P(under), so calling it p_over would hand the column a number
                # that reads as its exact opposite.
                "p_beat_line": pp.get("p_beat_line"),
            }
        mgrs[mid] = {
            "manager_id": mid,
            "display_name": m.get("display_name"),
            "banked_total": p_total(m, "banked_total"),
            "floor": p_total(m, "floor"),
            "ceiling": p_total(m, "ceiling"),
            "rank": m.get("rank"),
            "picks": picks,
        }
    return mgrs


def p_total(manager, key):
    return _r(manager.get(key))


# --- Week resolution ---------------------------------------------------------

def resolve_week(standings, cache, cli_week):
    """--week N is a GUARD, not a replay: it asserts the week the committed
    boards were scored at. Mismatch is fatal, so a packet can never be built
    against boards the caller thinks are from a different week."""
    eff = standings["meta"].get("as_of_week")
    if eff is None:
        eff = cache.get("week")
    if eff is None:
        print("::error:: cannot resolve the effective week (standings meta "
              "as_of_week is null and the cache has no week).", file=sys.stderr)
        sys.exit(1)
    eff = int(eff)
    if cli_week is not None and int(cli_week) != eff:
        print(f"::error:: --week {cli_week} does not match the committed boards "
              f"(effective week {eff}). Re-run the engine, or drop --week. "
              f"--week is a guard against building a packet on stale boards; it "
              f"does not replay.", file=sys.stderr)
        sys.exit(1)
    return eff


def completed_game_count(cache):
    """How many games in the cache are final.

    The preseason discriminator (see build_packet). Deliberately counts PLAYED
    games rather than reading `week`: a null week is a symptom shared by two
    completely different states — "the season has not started" (benign) and
    "the boards and the cache disagree" (a real fault) — and only the played-game
    count tells them apart.
    """
    return sum(1 for g in (cache.get("games") or []) if g.get("completed"))


def season_is_complete(cache):
    """True when every game the cache holds has been played.

    Derived from the cache the engine already scores off: no clock, no network,
    no second data source. A wall-clock rule (should_run.py's "last kickoff was
    over a week ago") would make the packet's answer depend on the DAY it was
    built rather than on the data in it, so two packets built from one cache
    could disagree. This cannot.

    Scope, stated plainly: this is "every game in the cache is final", and the
    cache holds the season_type it was fetched for. That is the same schedule
    the boards are scored from, so it answers the question the column actually
    has — is there anything left to play for — rather than making a broader
    claim about the sport's calendar.

    An empty or unreadable game list returns False. Not knowing must never
    render as "the season is over": the column prints what it is handed, and
    that is a sentence it would state as fact.
    """
    games = cache.get("games") or []
    if not games:
        return False
    return all(bool(g.get("completed")) for g in games)


def prior_snapshot(timeline, week):
    """The most recent snapshot strictly BEFORE the current week, or None."""
    snaps = [s for s in timeline.get("snapshots", [])
             if s.get("as_of_week") is not None and int(s["as_of_week"]) < week]
    if not snaps:
        return None
    return max(snaps, key=lambda s: int(s["as_of_week"]))


# --- Race --------------------------------------------------------------------

def build_race(cur, prior, config):
    names = {m["manager_id"]: m.get("display_name")
             for m in config.get("managers", [])}
    ordered = sorted(cur.values(), key=lambda m: m["rank"])
    leader_total = ordered[0]["banked_total"] if ordered else None

    rows = []
    for m in ordered:
        mid = m["manager_id"]
        pm = (prior or {}).get(mid)
        rows.append({
            "manager_id": mid,
            "name": m.get("display_name") or names.get(mid) or mid,
            "total_delta": m["banked_total"],
            "gap_to_leader": _sub(leader_total, m["banked_total"]),
            "delta_this_week": _sub(m["banked_total"],
                                    pm["banked_total"] if pm else None),
            "rank": m["rank"],
            # Positive = climbed. Null when the manager has no prior snapshot.
            "rank_change": _sub(pm["rank"] if pm else None, m["rank"]),
        })
    return {"leader": ordered[0]["manager_id"] if ordered else None,
            "standings": rows}


def race_position(rows, manager_ids):
    idx = {r["manager_id"]: r for r in rows}
    out = {}
    for mid in manager_ids:
        r = idx.get(mid)
        if r:
            out[mid] = {"gap_to_leader": r["gap_to_leader"],
                        "delta_this_week": r["delta_this_week"],
                        "rank_change": r["rank_change"]}
    return out


# --- Storyline detection -----------------------------------------------------

def pick_payload(mid, pk, prior_pick):
    """One pick as the column sees it: current numbers verbatim + how the two
    swing fields moved since the prior snapshot."""
    return {
        "manager_id": mid,
        "team": pk["team"],
        "line": pk["line"],
        "direction": pk["direction"],
        "banked_delta": pk["banked_delta"],
        "floor": pk["floor"],
        "ceiling": pk["ceiling"],
        "status": pk["status"],
        "floor_change_this_week": _sub(pk["floor"],
                                       prior_pick["floor"] if prior_pick else None),
        "ceiling_change_this_week": _sub(pk["ceiling"],
                                         prior_pick["ceiling"] if prior_pick else None),
        "p_beat_line": pk["p_beat_line"],
    }


def _prior_pick(prior, mid, team):
    pm = (prior or {}).get(mid)
    return pm["picks"].get(team) if pm else None


def detect_feuds(cur, prior, picks, race_rows):
    """Opposite-side pairs: same canonical team, opposite direction. Detected
    from picks.json alone — the first detection of this anywhere in the repo.

    Same-team SAME-side pairs are a data-integrity violation; they are reported
    loudly on stderr but never fail the packet (enforcement lands upstream)."""
    holders = defaultdict(list)
    for pk in picks:
        holders[pk["team"]].append(pk)

    stories = []
    for team, hs in sorted(holders.items()):
        if len(hs) < 2:
            continue
        dirs = {h["direction"] for h in hs}
        if len(dirs) == 1:
            print(f"::warning:: data integrity: {len(hs)} managers hold {team} on "
                  f"the SAME side ({dirs.pop()}): "
                  f"{', '.join(sorted(h['manager'] for h in hs))}. Packet still "
                  f"built; upstream validation should reject this.",
                  file=sys.stderr)
            continue

        overs = [h for h in hs if h["direction"] == "O"]
        unders = [h for h in hs if h["direction"] == "U"]
        for o in overs:
            for u in unders:
                a, b = o["manager"], u["manager"]
                ca, cb = cur.get(a), cur.get(b)
                if not ca or not cb:
                    continue
                pa, pb = ca["picks"].get(team), cb["picks"].get(team)
                if not pa or not pb:
                    continue
                divergence = abs(pa["banked_delta"] - pb["banked_delta"])
                if divergence < FEUD_MIN_DIVERGENCE:
                    continue
                adjacent = abs(ca["rank"] - cb["rank"]) == 1
                score = divergence + (FEUD_ADJACENCY_BONUS if adjacent else 0.0)
                stories.append({
                    "type": "feud",
                    "narrative_score": _r(score),
                    "managers": [a, b],
                    "picks": [pick_payload(a, pa, _prior_pick(prior, a, team)),
                              pick_payload(b, pb, _prior_pick(prior, b, team))],
                    "race_position": race_position(race_rows, [a, b]),
                    "evidence": (
                        f"{a} ({o['direction']} {o['line']}) and {b} "
                        f"({u['direction']} {u['line']}) hold opposite sides of "
                        f"{team}; banked deltas {pa['banked_delta']:+g} vs "
                        f"{pb['banked_delta']:+g}, diverged {divergence:g} game(s)"
                        + (f"; they sit adjacent at ranks {ca['rank']} and "
                           f"{cb['rank']}" if adjacent else "") + "."),
                })
    return stories


def collapse_baseline(timeline, week):
    """The snapshot COLLAPSE_LOOKBACK_WEEKS back, for measuring a slide.

    Returns (state, weeks_spanned) or (None, None). Falls back to the earliest
    snapshot before `week` when the season isn't that old yet, and reports the
    span it actually used so the evidence can't overstate the window."""
    older = [s for s in timeline.get("snapshots", [])
             if s.get("as_of_week") is not None and int(s["as_of_week"]) < week]
    if not older:
        return None, None
    target = week - COLLAPSE_LOOKBACK_WEEKS
    eligible = [s for s in older if int(s["as_of_week"]) <= target]
    snap = (max(eligible, key=lambda s: int(s["as_of_week"])) if eligible
            else min(older, key=lambda s: int(s["as_of_week"])))
    return state_from_snapshot(snap), week - int(snap["as_of_week"])


def detect_collapses(cur, prior, race_rows, timeline, week):
    """A ceiling falling on someone mid-flight — measured over a WINDOW.

    Week-over-week this is degenerate: exactly one game is played, so a pick's
    ceiling can only ever drop by exactly 1.0, and a 1.0 threshold reduces
    'collapse' to a binary 'did this team lose' that fires for a third of all
    pick-weeks. A slide is cumulative, so measure it across
    COLLAPSE_LOOKBACK_WEEKS and require a real loss of upside over that span."""
    if not prior:
        return []
    base, span = collapse_baseline(timeline, week)
    if not base:
        return []
    n = len(cur)
    half = math.ceil(n / 2)
    stories = []
    for mid, m in cur.items():
        if COLLAPSE_TOP_HALF_ONLY and m["rank"] > half:
            continue
        for team, pk in m["picks"].items():
            bp = base.get(mid, {}).get("picks", {}).get(team)
            change = _sub(pk["ceiling"], bp["ceiling"] if bp else None)
            if change is None or change > -COLLAPSE_MIN_CEILING_DROP:
                continue
            drop = abs(change)
            score = drop * (n - m["rank"] + 1) / n
            stories.append({
                "type": "collapse",
                "narrative_score": _r(score),
                "managers": [mid],
                "lookback_weeks": span,
                "ceiling_change_over_window": _r(change),
                "picks": [pick_payload(mid, pk, _prior_pick(prior, mid, team))],
                "race_position": race_position(race_rows, [mid]),
                "evidence": (
                    f"{mid}'s {team} {pk['direction']} {pk['line']} lost "
                    f"{drop:g} game(s) of ceiling over {span} week(s) "
                    f"({bp['ceiling']:+g} -> {pk['ceiling']:+g}) while sitting "
                    f"rank {m['rank']} of {n}."),
            })
    return stories


def flip_attribution(cur, prior, prior_leader, leader):
    """Who actually moved the lead, and by how much each pick moved it.

    A lead change is a TWO-PARTY event between the manager who held the lead at
    the prior snapshot (old) and the one holding it now (new). Every other
    manager's picks are bystanders to it, however dramatic they were on their
    own terms.

    The swing is how far the pair travelled relative to each other:

        swing = (old - new, at the prior snapshot) + (new - old, now)

    and each of the pair's picks contributed whatever it moved in the flip's
    favour: the new leader gaining, or the old leader shedding. That
    decomposition is EXACT, not a heuristic — a manager's banked_total is by
    contract just the sum of that manager's picks' banked_delta, so the pair's
    per-pick contributions necessarily sum to the swing. `share` is therefore a
    real fraction of a real quantity, which is what makes it safe to scale a
    score by.

    Returns None when nothing flipped, or when the swing is degenerate (<= 0,
    which a consistent pair of snapshots cannot produce, but a hand-edited
    timeline could).
    """
    if not prior or not prior_leader or prior_leader == leader:
        return None
    old, new = prior_leader, leader
    if not all(m in cur and m in prior for m in (old, new)):
        return None
    swing = ((prior[old]["banked_total"] - prior[new]["banked_total"])
             + (cur[new]["banked_total"] - cur[old]["banked_total"]))
    if swing is None or swing <= 0:
        return None

    shares = {}
    for mid, sign in ((new, 1.0), (old, -1.0)):
        for team, pk in cur[mid]["picks"].items():
            pp = _prior_pick(prior, mid, team)
            if not pp or pp["banked_delta"] is None or pk["banked_delta"] is None:
                continue
            # Signed so "helped the flip happen" is positive on both sides: the
            # new leader gaining ground, or the old leader shedding it.
            contribution = sign * (pk["banked_delta"] - pp["banked_delta"])
            if contribution <= 0:
                continue                   # moved against the flip, or not at all
            shares[(mid, team)] = (_r(contribution), contribution / swing)
    return {"old": old, "new": new, "swing": _r(swing), "shares": shares}


def detect_ironies(cur, prior, race_rows, flip):
    """Picks that newly clinched or newly died since the prior snapshot.

    Scored on the pick's OWN resolved magnitude, plus flip credit only for the
    picks that measurably moved the lead (see flip_attribution).

    This replaces a flat IRONY_BASE plus a group-wide leader-flip bonus. That
    version had two defects. It was constant + constant, so a pick resolving at
    +/-0.5 scored identically to one resolving at +/-5.5 and every irony in the
    week tied. And `leader_changed` was a single boolean for the whole group, so
    a bystander's routine clinch was stamped with the same flip bonus as the
    pick that actually took the lead. Together they put ordinary status changes
    above a maximally-diverged feud, contradicting the preference order that
    TYPE_PRIORITY and the persona template both state — and TYPE_PRIORITY could
    not correct it, because it only breaks ties between EQUAL scores.
    """
    if not prior:
        return []
    stories = []
    for mid, m in cur.items():
        for team, pk in m["picks"].items():
            pp = _prior_pick(prior, mid, team)
            if not pp or pp["status"] is None or pk["status"] is None:
                continue
            if pp["status"] == pk["status"]:
                continue
            if pk["status"] not in ("CLINCHED", "DEAD"):
                continue

            score = IRONY_BASE + IRONY_DELTA_WEIGHT * abs(pk["banked_delta"] or 0.0)

            # Flip credit is proportional to the share of the swing this pick
            # actually accounts for, so a pick that single-handedly moved the
            # lead earns the full bonus and a marginal one earns a marginal
            # slice. Below IRONY_FLIP_MIN_SHARE it earns nothing AND is told
            # nothing, rather than handing the column a rounding error to
            # narrate as causation.
            contribution = None
            if flip:
                got = flip["shares"].get((mid, team))
                if got and got[1] >= IRONY_FLIP_MIN_SHARE:
                    contribution, share = got
                    score += IRONY_LEADER_FLIP_BONUS * share

            stories.append({
                "type": "irony",
                "narrative_score": _r(score),
                "managers": [mid],
                # Load-bearing for dedupe (see moment_key), not decoration: two
                # picks of the same manager's that moved in OPPOSITE directions
                # are two different moments.
                "transition": f"{pp['status']}->{pk['status']}",
                # Published in GAMES — the same unit as every other packet
                # number, by plain subtraction of two contract fields. The
                # share is deliberately NOT published: it is a percentage the
                # output contract never defines, and the column prints what it
                # is given (sacred rule 1).
                #
                # flip_swing_total is the pair's total RELATIVE TRAVEL -- the
                # old leader's prior lead plus the new leader's current one --
                # so it is deliberately NOT a margin and matches no gap on the
                # board. Panel wk16: chris led by 38, blaine now leads by 17,
                # swing 55. The evidence string said "the games by which the
                # lead passed", which reads as exactly the margin it isn't, and
                # a pundit quoting it verbatim would have printed a number the
                # boards contradict. Name it as ground that changed hands.
                "flip_contribution": contribution,
                "flip_swing_total": flip["swing"] if contribution is not None else None,
                "picks": [pick_payload(mid, pk, pp)],
                "race_position": race_position(race_rows, [mid]),
                "evidence": (
                    f"{mid}'s {team} {pk['direction']} {pk['line']} went "
                    f"{pp['status']} -> {pk['status']} (floor {pk['floor']:+g}, "
                    f"ceiling {pk['ceiling']:+g})"
                    + (f"; it moved {contribution:+g} of the {flip['swing']:g} "
                       f"game(s) of ground that changed hands between "
                       f"{flip['old']} and {flip['new']} as the lead passed."
                       if contribution is not None else ".")),
            })
    return stories


def heater_streak(timeline, week, mid):
    """The current run of not-losing-ground, read straight off timeline.json.

    Returns (gaining_weeks, span_weeks): how many weeks in the run actually
    banked something, and how many calendar weeks the run covers.

    A ZERO-movement week is NEUTRAL — it neither counts nor breaks. BYE WEEKS
    ARE THE REASON: a team on a bye banks nothing, so the manager's total is
    flat, and treating flat as a break would end a heater because a team didn't
    play rather than because it cooled off. Only LOSING ground ends the run.

    Reporting both numbers is what keeps the evidence honest: with a neutral
    week inside the run, gaining_weeks is no longer "consecutive weeks", so the
    column must be handed "N gaining weeks in the last M" rather than a single
    number it would read as a streak of N straight weeks (sacred rule 1)."""
    snaps = sorted([s for s in timeline.get("snapshots", [])
                    if s.get("as_of_week") is not None
                    and int(s["as_of_week"]) <= week],
                   key=lambda s: int(s["as_of_week"]))
    weeks = [int(s["as_of_week"]) for s in snaps]
    totals = []
    for s in snaps:
        st = state_from_snapshot(s).get(mid)
        totals.append(st["banked_total"] if st else None)

    gaining, oldest = 0, len(totals) - 1
    for i in range(len(totals) - 1, 0, -1):
        a, b = totals[i], totals[i - 1]
        if a is None or b is None:
            break                      # unknown state: the run stops here
        delta = a - b
        if delta < 0:
            break                      # lost ground: the run is over
        if delta > 0:
            gaining += 1               # banked something
        oldest = i - 1                 # zero: neutral, keep walking back
    span = (weeks[-1] - weeks[oldest]) if len(weeks) > 1 else 0
    return gaining, span


def detect_heater(cur, prior, race_rows, timeline, week, weeks_elapsed):
    """The biggest banked gainer of the week, if the RATE clears the floor.

    Scored per week, not per snapshot gap. A raw gain rewards nothing but the
    length of the gap: across a 10-week hole every manager who moved at all
    outscores every other storyline type and the heater permanently owns slot 1.
    At the intended cadence weeks_elapsed == 1 and this is an exact no-op.

    The quoted number stays the raw gain — that is what the boards show and what
    the column must print. Only the internal ranking number is normalized, plus
    the rate, which is published so the pundit can quote it instead of doing
    arithmetic of its own (sacred rule 1).

    BYES, stated deliberately, because the two halves treat them differently:
      - the RATE is per CALENDAR week, so a bye dampens it. A manager whose team
        sat out banked less per week, and is by that measure less hot right now.
        Dividing by weeks-that-had-games instead would need per-manager schedule
        data, coupling this to the cache for a distinction the streak already
        carries.
      - the STREAK is bye-transparent (heater_streak: zero is neutral). A team
        that didn't play didn't cool off.
    Both signals are published so the column can use whichever the week wants."""
    if not prior:
        return []
    gains = [(r["manager_id"], r["delta_this_week"]) for r in race_rows
             if r["delta_this_week"] is not None]
    if not gains:
        return []
    mid, gain = max(gains, key=lambda kv: kv[1])
    elapsed = max(1, weeks_elapsed or 1)
    rate = _r(gain / elapsed)
    if rate < HEATER_MIN_DELTA:
        return []
    gaining, run_span = heater_streak(timeline, week, mid)
    score = rate + HEATER_STREAK_WEIGHT * gaining
    m = cur[mid]
    movers = sorted(m["picks"].values(),
                    key=lambda p: -(p["banked_delta"] or 0))[:2]
    # "N gaining week(s) in the last M" — never "an N-week streak", which a
    # neutral (bye) week inside the run would make false.
    run = (f"{gaining} gaining week(s) in the last {run_span}" if run_span
           else f"{gaining} gaining week(s)")
    return [{
        "type": "heater",
        "narrative_score": _r(score),
        "managers": [mid],
        "gain_per_week": rate,
        "gaining_weeks": gaining,
        "run_span_weeks": run_span,
        "picks": [pick_payload(mid, pk, _prior_pick(prior, mid, pk["team"]))
                  for pk in movers],
        "race_position": race_position(race_rows, [mid]),
        "evidence": (f"{mid} banked {gain:+g} game(s) over {elapsed} week(s) "
                     f"({rate:+g} per week), the group's largest gain, with "
                     f"{run}; now rank {m['rank']} at {m['banked_total']:+g}."),
    }]


def moment_key(story):
    """The underlying EVENT a storyline describes — the dedupe key.

    Deliberately NOT the type. A manager whose four picks all clinch in the same
    week produces four separate storylines describing ONE moment; to the column
    that is a single sentence ("he ran the table"), not four paragraphs.

    Per type:
      irony     (manager, transition) — see below, the transition is required
      feud      (pair, team)          — already one-per-moment by construction
      collapse  (manager)             — one board eroding over one window, even
                                        when two of its picks slid
      heater    (manager)             — only ever one, keyed for uniformity

    THE TRANSITION IS LOAD-BEARING. Keying irony on the manager alone would
    merge "zach's Miami came home" with "zach's other three died" — opposite
    stories about the same person in the same week, and exactly the pair a
    column would contrast rather than combine.
    """
    t = story["type"]
    mids = tuple(sorted(story["managers"]))
    if t == "feud":
        return (t, mids, story["picks"][0]["team"] if story["picks"] else None)
    if t == "irony":
        return (t, mids, story.get("transition"))
    return (t, mids)


def merge_moment(stories):
    """Fold every storyline describing one moment into one, losing no numbers.

    The highest-scoring member is the representative and KEEPS ITS OWN SCORE —
    max, never sum. Summing would re-inflate the exact defect magnitude scoring
    just fixed: a manager with four clinches would out-total any single-pick
    story by construction, and volume would beat significance all over again.

    The others' picks are appended to `picks` rather than dropped, so every
    number the column might want stays in the packet. Silently discarding them
    is what the MAX_PER_TYPE cap was doing before this function existed.
    """
    ordered = sorted(stories, key=lambda s: -s["narrative_score"])
    rep = dict(ordered[0])
    rep["moment_size"] = len(ordered)
    if len(ordered) == 1:
        return rep

    seen = {(p["manager_id"], p["team"]) for p in rep["picks"]}
    extra = []
    for s in ordered[1:]:
        for pk in s["picks"]:
            if (pk["manager_id"], pk["team"]) not in seen:
                seen.add((pk["manager_id"], pk["team"]))
                extra.append(pk)
    if extra:
        rep["picks"] = rep["picks"] + extra
        rep["evidence"] = rep["evidence"].rstrip() + (
            f" Same moment: {len(extra)} more pick(s) — "
            f"{', '.join(pk['team'] for pk in extra)}.")
    return rep


def rank_storylines(stories):
    """Dedupe to one storyline per moment, cap each type, then rank by score
    with the persona's type preference as the tiebreak.

    ORDER MATTERS: dedupe runs FIRST, upstream of the cap. The cap used to be
    the only thing standing between the column and a dozen near-identical
    storylines — which meant it was discarding real moments to do it, with no
    record of what was lost. Panel week 16 emitted 18 storylines describing 8
    moments; the cap silently threw away 16 of them. Deduping first means the
    cap trims MOMENTS, which is what its docstring always claimed it did."""
    grouped = defaultdict(list)
    for s in stories:
        grouped[moment_key(s)].append(s)
    moments = [merge_moment(g) for g in grouped.values()]

    kept, seen = [], defaultdict(int)
    for s in sorted(moments, key=lambda s: (-s["narrative_score"],
                                            TYPE_PRIORITY.get(s["type"], 9))):
        if seen[s["type"]] >= MAX_PER_TYPE:
            continue
        seen[s["type"]] += 1
        kept.append(s)
    kept.sort(key=lambda s: (-s["narrative_score"], TYPE_PRIORITY.get(s["type"], 9)))
    return kept[:MAX_STORYLINES]


def quiet_week_story(race_rows):
    lead = race_rows[0] if race_rows else None
    return {
        "type": "quiet_week",
        "narrative_score": 0.0,
        "managers": [lead["manager_id"]] if lead else [],
        # A quiet week bypasses rank_storylines (it is the fallback used when
        # ranking produced nothing worth telling), so it has to set this itself
        # or it would be the one storyline shape missing the field.
        "moment_size": 1,
        "picks": [],
        "race_position": race_position(race_rows, [lead["manager_id"]]) if lead else {},
        "evidence": ("No storyline cleared the minimum thresholds: no opposite-side "
                     "divergence, no ceiling collapse, no clinch or elimination, no "
                     "heater. The table barely moved."),
    }


# --- Bad beats ---------------------------------------------------------------

def team_games(cache, lo_week, hi_week):
    """Completed games in (lo_week, hi_week], indexed by canonical team name.

    Enrichment, so it parses best-effort (playbook rule 10): a game whose teams
    don't resolve is skipped, never fatal — the alias map is not guaranteed to
    cover every FCS opponent, and a bad beat is garnish on garnish."""
    idx = defaultdict(list)
    for g in cache.get("games", []):
        if not g.get("completed"):
            continue
        wk = g.get("week")
        if wk is None or not (lo_week < int(wk) <= hi_week):
            continue
        hp, ap = g.get("home_points"), g.get("away_points")
        if hp is None or ap is None:
            continue
        for side in ("home", "away"):
            raw = g.get(f"{side}_team")
            try:
                canon = utils.resolve_canonical(raw)
            except Exception:  # noqa: BLE001 — unresolvable opponent: skip, never crash
                continue
            opp_side = "away" if side == "home" else "home"
            mine = hp if side == "home" else ap
            theirs = ap if side == "home" else hp
            idx[canon].append({
                "opponent": g.get(f"{opp_side}_team"),
                "score": f"{mine}-{theirs}",
                "home_away": ("neutral" if g.get("neutral_site")
                              else ("home" if side == "home" else "away")),
                "margin": int(mine - theirs),
                "week": int(wk),
                "won": mine > theirs,
            })
    return idx


def build_bad_beats(cur, cache, lo_week, hi_week):
    """Games in the window that moved a pick the WRONG way.

    LIMITATION (deliberate): the cache holds final scores only — no play-by-play
    — so `how_it_died` is limited to final-score facts. The persona's
    garbage-time/onside-kick texture is not available here, and sacred rule 1
    forbids the column inventing it."""
    idx = team_games(cache, lo_week, hi_week)
    out = []
    for mid, m in cur.items():
        for team, pk in m["picks"].items():
            for g in idx.get(team, []):
                # A win hurts an under; a loss hurts an over.
                hurts = (g["won"] and pk["direction"] == "U") or \
                        (not g["won"] and pk["direction"] == "O")
                if not hurts:
                    continue
                now_dead = pk["status"] == "DEAD"
                if abs(g["margin"]) > BAD_BEAT_MAX_MARGIN and not now_dead:
                    continue
                verb = "beat" if g["won"] else "lost to"
                side = {"home": "at home", "away": "on the road",
                        "neutral": "at a neutral site"}[g["home_away"]]
                how = (f"{team} {verb} {g['opponent']} {g['score']} {side} in week "
                       f"{g['week']} (margin {abs(g['margin'])}); the result cost "
                       f"{mid}'s {pk['direction']} {pk['line']} one game of ceiling"
                       + (f", and the pick is now DEAD." if now_dead else "."))
                out.append({
                    "manager_id": mid,
                    "team": team,
                    "line": pk["line"],
                    "direction": pk["direction"],
                    "game": {"opponent": g["opponent"], "score": g["score"],
                             "home_away": g["home_away"], "margin": abs(g["margin"]),
                             "week": g["week"]},
                    "delta_impact": -1.0,
                    "how_it_died": how,
                })
    # Ugliest first: killed the pick, then closest game, then latest.
    out.sort(key=lambda b: (not b["how_it_died"].endswith("now DEAD."),
                            b["game"]["margin"], -b["game"]["week"]))
    return out[:MAX_BAD_BEATS]



# --- Coda / lead subject separation ------------------------------------------
#
# THE RULE: the coda -- "Bad Beat of the Week" in season, "Worst Pick on the
# Board" in preseason -- must not be about the same subject as the One Big Thing
# lead. It is the column's second beat and its whole job is to widen the frame:
# a coda that re-targets the manager (or the team) the lead just spent 300 words
# on reads as one story told twice, and the direct-address roast lands on
# someone the reader has already watched get worked over.
#
# WHY PYTHON AND NOT THE PROMPT: Week 0 panel filed exactly this. The lead was
# the Blaine/Chris feud over Texas 9.5; the coda was Chris, on Texas. Both beats
# were individually correct against the packet and the model had no instruction
# it was breaking -- the collision is only visible when you hold the two
# selections side by side, which is a property of the PACKET, not of any one
# sentence. So the pool is filtered before the model ever sees it, the same way
# the coda target itself is computed and never chosen by the model.

def storyline_subject(story):
    """(managers, teams) the storyline is ABOUT -- the keys a coda must avoid.

    Both dimensions, not just the manager: a feud is keyed by its team and held
    by two managers, so manager-only exclusion would still allow the Week 0
    failure (lead = Blaine/Chris on Texas, coda = Chris on Texas) through on the
    team. Teams come off the storyline's own `picks`, which for a manager-keyed
    story (collapse, heater, envelope) is that manager's board -- correctly, since
    those are the picks the lead just discussed.
    """
    if not story:
        return set(), set()
    managers = {m for m in (story.get("managers") or []) if m}
    teams = {pk.get("team") for pk in (story.get("picks") or []) if pk.get("team")}
    return managers, teams


def subject_overlap(candidate, managers, teams):
    """How many subject dimensions a coda candidate shares with the lead (0-2)."""
    return ((1 if candidate.get("manager_id") in managers else 0)
            + (1 if candidate.get("team") in teams else 0))


def _lower_gap_count(dropped, winner, gap_key):
    """How many DROPPED candidates sit at a lower gap than the one we kept.

    None when the rows do not carry gap_key (the in-season bad-beat pool), so
    a caller can tell "no lower-gap picks" from "this pool has no gaps".
    """
    if not gap_key or winner is None or gap_key not in winner:
        return None
    ref = winner.get(gap_key)
    if ref is None:
        return None
    n = 0
    for c in dropped:
        g = c.get(gap_key)
        if g is not None and g < ref:
            n += 1
    return n


def exclude_lead_subject(candidates, lead, label="bad-beat", group_id=None,
                         gap_key="market_gap"):
    """Drop coda candidates that collide with the lead's subject.

    Returns (kept, report). `candidates` must already be in selection order --
    this filters, it never re-ranks, so whatever the caller's ordering meant
    (ugliest-first for bad beats) still means it afterwards.

    Three outcomes, and the third is why this never raises:
      1. some candidates survive  -> return them, ordering intact.
      2. NONE survive but the pool was non-empty -> every candidate collides.
         Return the LOWEST-overlap candidate (a shared manager alone beats a
         shared manager AND team), warn loudly, and record it. A degraded coda
         is recoverable; a crashed packet on a Saturday night is not.
      3. the pool was empty to begin with -> nothing to do, no warning. A week
         with no bad beats is normal and already handled downstream.

    TWO COUNTS, and they are not the same number. `excluded` is the audit
    trail: every candidate dropped for sharing the lead's subject.
    `excluded_lower_gap` is the only one the PROMPT may quote -- the dropped
    candidates whose gap is actually LOWER than the one we kept. The prompt
    sentence is "N pick(s) with a lower gap were set aside", and reading
    `excluded` for it overstated N on every real board (panel Week 0: 8 drops,
    exactly 1 of them lower than the coda's -1.044). The model was handed a
    false count and duly hedged a superlative onto it.

    gap_key is None-safe: the in-season bad-beat rows carry no market_gap at
    all, so the count reports None there rather than inventing a comparison
    the data cannot support.
    """
    managers, teams = storyline_subject(lead)
    if not candidates:
        return [], {"excluded": 0, "excluded_lower_gap": 0,
                    "collision_forced": False,
                    "lead_managers": sorted(managers), "lead_teams": sorted(teams)}

    scored = [(subject_overlap(c, managers, teams), i, c)
              for i, c in enumerate(candidates)]
    kept = [c for ov, _, c in scored if ov == 0]
    dropped = [c for ov, _, c in scored if ov != 0]
    winner = kept[0] if kept else min(scored, key=lambda t: (t[0], t[1]))[2]
    report = {
        "excluded": len(candidates) - len(kept),
        "excluded_lower_gap": _lower_gap_count(dropped, winner, gap_key),
        "collision_forced": False,
        "lead_managers": sorted(managers),
        "lead_teams": sorted(teams),
    }
    if kept:
        return kept, report

    # Everything collides. Lowest overlap wins; original order breaks the tie.
    best_ov, _, best = min(scored, key=lambda t: (t[0], t[1]))
    report["collision_forced"] = True
    report["forced_overlap"] = best_ov
    # The kept pick changed, so the comparison baseline did too.
    report["excluded_lower_gap"] = _lower_gap_count(
        [c for c in candidates if c is not best], best, gap_key)
    tag = f"[{group_id}] " if group_id else ""
    print(f"::warning:: {tag}every {label} candidate collides with the One Big "
          f"Thing subject (managers={sorted(managers) or '-'}, "
          f"teams={sorted(teams) or '-'}); falling back to the lowest-overlap "
          f"candidate ({best.get('manager_id')} on {best.get('team')}, "
          f"overlap {best_ov}/2). The column will repeat a subject across both "
          f"beats -- this is the degraded path, not the intended one.")
    return [best], report


# --- Manager profiles --------------------------------------------------------

def build_profiles(cur, picks):
    """Behavioral evidence for character roasts (persona sacred rule 6). All
    counted off the committed board — nothing inferred about a person."""
    by_mgr = defaultdict(list)
    for pk in picks:
        by_mgr[pk["manager"]].append(pk)

    overs = {mid: sum(1 for p in ps if p["direction"] == "O")
             for mid, ps in by_mgr.items()}
    totals = {mid: len(ps) for mid, ps in by_mgr.items()}
    field_n = sum(totals.values())
    field_over_rate = (sum(overs.values()) / field_n) if field_n else 0.0

    profiles = {}
    for mid, ps in by_mgr.items():
        m = cur.get(mid)
        cur_picks = list(m["picks"].values()) if m else []
        scored = [p for p in cur_picks if p["banked_delta"] is not None]
        best = max(scored, key=lambda p: p["banked_delta"]) if scored else None
        worst = min(scored, key=lambda p: p["banked_delta"]) if scored else None
        over_rate = overs[mid] / totals[mid] if totals[mid] else 0.0
        profiles[mid] = {
            "over_count": overs[mid],
            "under_count": totals[mid] - overs[mid],
            "conference_spread": len({p.get("conference") for p in ps}),
            "avg_line": _r(sum(p["line"] for p in ps) / len(ps)) if ps else None,
            "picks_alive": sum(1 for p in cur_picks if p["status"] == "LIVE"),
            "picks_clinched": sum(1 for p in cur_picks if p["status"] == "CLINCHED"),
            "picks_dead": sum(1 for p in cur_picks if p["status"] == "DEAD"),
            "best_pick": ({"team": best["team"], "direction": best["direction"],
                           "line": best["line"],
                           "banked_delta": best["banked_delta"]} if best else None),
            "worst_pick": ({"team": worst["team"], "direction": worst["direction"],
                            "line": worst["line"],
                            "banked_delta": worst["banked_delta"]} if worst else None),
            # Signed gap between this manager's over-rate and the field's.
            # Positive = took more overs than the room.
            "baseline_optimism_vs_field": _r(over_rate - field_over_rate),
        }
    return profiles


def uniform_profile_fields(profiles):
    """The scalar manager_profiles fields that hold the SAME value for every
    manager, mapped to that shared value.

    A value the whole room shares distinguishes nobody, but read one profile at
    a time it looks exactly like a personal stat. Week 16 the column filed "he
    was the only manager with no live picks" off a finished board where
    picks_alive was 0 for all four -- true number, fabricated exclusivity
    (persona sacred rule 7). This names those fields in ONE place so the prompt
    can say plainly which ones set nobody apart.

    Deliberately GENERIC rather than a season-complete picks_alive special
    case. Panel wk16 has two such fields, not one: picks_alive (0 everywhere,
    because the season ended) and conference_spread (4 everywhere, because the
    format mandates four distinct conferences). Fixing only the first would
    leave "the only manager who spread across four conferences" armed and
    sitting next to it. It also self-maintains: while the season runs and
    picks_alive genuinely varies, it simply is not listed.

    Scalars only -- best_pick/worst_pick are dicts and "identical" is not a
    thing worth claiming about them. Needs >= 2 managers, since with one
    manager every field is trivially uniform and the concept is empty. Returns
    {} rather than being omitted, so the key is never conditional.
    """
    if len(profiles) < 2:
        return {}
    rows = list(profiles.values())
    first = rows[0]
    out = {}
    for key, val in first.items():
        if not isinstance(val, (int, float, bool)):
            continue           # best_pick / worst_pick / a null avg_line
        if all(r.get(key) == val for r in rows[1:]):
            out[key] = val
    return out


# --- Build -------------------------------------------------------------------

def build_packet(group_id, cli_week=None):
    season = utils.assert_season_matches_cache()

    web = utils.WEB_DATA_DIR / group_id
    standings = _require(web / "standings.json", group_id)
    projection = _require(web / "projection.json", group_id)
    timeline = _require(web / "timeline.json", group_id)
    # The `test` fixture has no groups/<slug>/ dir — utils.load_group()
    # synthesizes its config from data/test_picks.json (§10.2). Same carve-out
    # run_groups.py makes, so the gitignored test group can exercise the packet
    # across consecutive weeks without fixtures touching production files.
    if group_id != utils.TEST_GROUP_ID:
        _require(utils.GROUPS_DIR / group_id / "config.json", group_id)
        _require(utils.GROUPS_DIR / group_id / "picks.json", group_id)

    config, picks = utils.load_group(group_id)
    cache = utils.load_cache(season)

    # Preseason: the schedule is loaded but nothing has kicked off. There is no
    # week to resolve and, more to the point, nothing for the column to write
    # about — every pick is unresolved and no board has moved. That is a
    # legitimate state, not a failure, so we return None and let main() exit 0
    # having written nothing.
    #
    # The discriminator is the COMPLETED-GAME COUNT, never `week is None`. Those
    # are two different states wearing the same symptom:
    #   - 0 played  -> the season has not started. Benign; handled here.
    #   - >0 played -> the cache and the committed boards disagree about what
    #                  week it is. A real fault, and resolve_week below still
    #                  exits 1 on it. This gate must never widen to swallow that.
    if completed_game_count(cache) == 0:
        n_scheduled = len(cache.get("games") or [])
        print(f"  [{group_id}] preseason: 0 of {n_scheduled} scheduled game(s) "
              f"played in season {season}. No week to resolve and no column to "
              f"write — no packet built (this is not an error).")
        return None

    week = resolve_week(standings, cache, cli_week)
    prior_snap = prior_snapshot(timeline, week)
    prior = state_from_snapshot(prior_snap) if prior_snap else None
    prior_week = int(prior_snap["as_of_week"]) if prior_snap else None
    weeks_elapsed = (week - prior_week) if prior_week is not None else None

    cur = state_from_standings(standings, projection)
    race = build_race(cur, prior, config)
    rows = race["standings"]

    prior_leader = None
    if prior:
        prior_leader = min(prior.items(), key=lambda kv: kv[1]["rank"])[0]
    flip = flip_attribution(cur, prior, prior_leader, race["leader"])

    stories = (detect_feuds(cur, prior, picks, rows)
               + detect_collapses(cur, prior, rows, timeline, week)
               + detect_ironies(cur, prior, rows, flip)
               + detect_heater(cur, prior, rows, timeline, week, weeks_elapsed))
    stories = rank_storylines(stories)
    if not stories or stories[0]["narrative_score"] < QUIET_WEEK_FLOOR:
        stories = [quiet_week_story(rows)]

    lo = prior_week if prior_week is not None else 0
    bad_beats = build_bad_beats(cur, cache, lo, week)
    # The coda may not re-target the lead's subject. stories[0] IS the lead --
    # the prompt tells the column to build Beat 1 from the highest-ranked
    # storyline -- so the pool is filtered against it before the model sees it.
    bad_beats, coda_exclusion = exclude_lead_subject(
        bad_beats, stories[0] if stories else None,
        label="bad-beat", group_id=group_id)
    profiles = build_profiles(cur, picks)

    return {
        "group_id": group_id,
        "week": week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        # Whether anything is still to be played. Without this the column has no
        # way to know the schedule is exhausted, and writes forward-looking prose
        # into a finished season ("only time and more Saturdays will tell") off a
        # board where every pick has already resolved.
        "season_complete": season_is_complete(cache),
        "stakes": config.get("stakes"),
        # The honest basis for every *_this_week field below. weeks_elapsed > 1
        # means "this week" is really "since week <prior_week>" — the column
        # must say so rather than compress a 10-week move into one Saturday.
        "comparison": {
            "prior_week": prior_week,
            "weeks_elapsed": weeks_elapsed,
            "basis": (f"since week {prior_week}" if prior_week is not None
                      else "no prior snapshot — week-over-week movement unknown"),
        },
        "race": race,
        "storylines": stories,
        "bad_beat_candidates": bad_beats,
        # Audit trail for the rule above: which lead subject was excluded, how
        # many candidates it cost, and whether the degraded path was taken.
        "coda_exclusion": coda_exclusion,
        "manager_profiles": profiles,
        # Which of the above distinguish NOBODY this week (persona rule 7).
        "uniform_profile_fields": uniform_profile_fields(profiles),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Build the SVP column's week packet from committed engine output")
    ap.add_argument("--group", required=True, help="group slug (panel/family/church)")
    ap.add_argument("--week", type=int, default=None,
                    help="guard: assert the committed boards are at week N "
                         "(fatal on mismatch; this does NOT replay)")
    args = ap.parse_args()

    packet = build_packet(args.group, args.week)
    if packet is None:
        # Preseason. build_packet already said so; exit 0 without writing a
        # packet, and WITHOUT disturbing any packet already on disk.
        return
    utils.save_json_atomic(packet_path(args.group), packet)
    print(f"  [{args.group}] week {packet['week']} "
          f"({packet['comparison']['basis']}): "
          f"{len(packet['storylines'])} storyline(s) "
          f"[{', '.join(s['type'] for s in packet['storylines'])}], "
          f"{len(packet['bad_beat_candidates'])} bad-beat candidate(s)")


if __name__ == "__main__":
    main()
