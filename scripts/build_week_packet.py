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
COLLAPSE_MIN_CEILING_DROP = 1.0  # min ceiling loss (games) to count as collapse
COLLAPSE_TOP_HALF_ONLY = True    # a collapse only reads as one from up high
IRONY_BASE = 3.0                 # flat score for a newly clinched/dead pick
IRONY_LEADER_FLIP_BONUS = 4.0    # bonus when the week changed the leader
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


def detect_collapses(cur, prior, race_rows):
    """A ceiling falling on someone who was flying: ceiling loss >= threshold
    while the manager sits in the top half."""
    if not prior:
        return []
    n = len(cur)
    half = math.ceil(n / 2)
    stories = []
    for mid, m in cur.items():
        if COLLAPSE_TOP_HALF_ONLY and m["rank"] > half:
            continue
        for team, pk in m["picks"].items():
            pp = _prior_pick(prior, mid, team)
            change = _sub(pk["ceiling"], pp["ceiling"] if pp else None)
            if change is None or change > -COLLAPSE_MIN_CEILING_DROP:
                continue
            drop = abs(change)
            score = drop * (n - m["rank"] + 1) / n
            stories.append({
                "type": "collapse",
                "narrative_score": _r(score),
                "managers": [mid],
                "picks": [pick_payload(mid, pk, pp)],
                "race_position": race_position(race_rows, [mid]),
                "evidence": (
                    f"{mid}'s {team} {pk['direction']} {pk['line']} lost "
                    f"{drop:g} game(s) of ceiling ({pp['ceiling']:+g} -> "
                    f"{pk['ceiling']:+g}) while sitting rank {m['rank']} of {n}."),
            })
    return stories


def detect_ironies(cur, prior, race_rows, leader_changed):
    """Picks that newly clinched or newly died since the prior snapshot."""
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
            score = IRONY_BASE + (IRONY_LEADER_FLIP_BONUS if leader_changed else 0.0)
            stories.append({
                "type": "irony",
                "narrative_score": _r(score),
                "managers": [mid],
                "picks": [pick_payload(mid, pk, pp)],
                "race_position": race_position(race_rows, [mid]),
                "evidence": (
                    f"{mid}'s {team} {pk['direction']} {pk['line']} went "
                    f"{pp['status']} -> {pk['status']} (floor {pk['floor']:+g}, "
                    f"ceiling {pk['ceiling']:+g})"
                    + ("; the lead changed hands the same week."
                       if leader_changed else ".")),
            })
    return stories


def heater_streak(timeline, week, mid):
    """Consecutive snapshot-to-snapshot banked gains ending at the latest
    transition. Read straight off timeline.json — no extra persistence."""
    snaps = sorted([s for s in timeline.get("snapshots", [])
                    if s.get("as_of_week") is not None
                    and int(s["as_of_week"]) <= week],
                   key=lambda s: int(s["as_of_week"]))
    totals = []
    for s in snaps:
        st = state_from_snapshot(s).get(mid)
        totals.append(st["banked_total"] if st else None)
    streak = 0
    for i in range(len(totals) - 1, 0, -1):
        a, b = totals[i], totals[i - 1]
        if a is None or b is None or a - b <= 0:
            break
        streak += 1
    return streak


def detect_heater(cur, prior, race_rows, timeline, week, weeks_elapsed):
    """The biggest banked gainer of the week, if the RATE clears the floor.

    Scored per week, not per snapshot gap. A raw gain rewards nothing but the
    length of the gap: across a 10-week hole every manager who moved at all
    outscores every other storyline type and the heater permanently owns slot 1.
    At the intended cadence weeks_elapsed == 1 and this is an exact no-op.

    The quoted number stays the raw gain — that is what the boards show and what
    the column must print. Only the internal ranking number is normalized, plus
    the rate, which is published so the pundit can quote it instead of doing
    arithmetic of its own (sacred rule 1)."""
    if not prior:
        return []
    gains = [(r["manager_id"], r["delta_this_week"]) for r in race_rows
             if r["delta_this_week"] is not None]
    if not gains:
        return []
    mid, gain = max(gains, key=lambda kv: kv[1])
    span = max(1, weeks_elapsed or 1)
    rate = _r(gain / span)
    if rate < HEATER_MIN_DELTA:
        return []
    streak = heater_streak(timeline, week, mid)
    score = rate + HEATER_STREAK_WEIGHT * streak
    m = cur[mid]
    movers = sorted(m["picks"].values(),
                    key=lambda p: -(p["banked_delta"] or 0))[:2]
    return [{
        "type": "heater",
        "narrative_score": _r(score),
        "managers": [mid],
        "gain_per_week": rate,
        "picks": [pick_payload(mid, pk, _prior_pick(prior, mid, pk["team"]))
                  for pk in movers],
        "race_position": race_position(race_rows, [mid]),
        "evidence": (f"{mid} banked {gain:+g} game(s) over {span} week(s) "
                     f"({rate:+g} per week), the group's largest gain, on a "
                     f"{streak}-week positive streak; now rank {m['rank']} at "
                     f"{m['banked_total']:+g}."),
    }]


def rank_storylines(stories):
    """Cap each type at MAX_PER_TYPE, then rank by score with the persona's
    type preference as the tiebreak.

    The cap is load-bearing, not cosmetic: a week where many picks clinch or die
    at once produces a dozen ironies all sharing the flat IRONY_BASE score, and
    without a cap they fill every slot and bury the feud the column most wants."""
    kept, seen = [], defaultdict(int)
    for s in sorted(stories, key=lambda s: (-s["narrative_score"],
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


# --- Build -------------------------------------------------------------------

def build_packet(group_id, cli_week=None):
    season = utils.assert_season_matches_cache()

    web = utils.WEB_DATA_DIR / group_id
    standings = _require(web / "standings.json", group_id)
    projection = _require(web / "projection.json", group_id)
    timeline = _require(web / "timeline.json", group_id)
    _require(utils.GROUPS_DIR / group_id / "config.json", group_id)
    _require(utils.GROUPS_DIR / group_id / "picks.json", group_id)

    config, picks = utils.load_group(group_id)
    cache = utils.load_cache(season)

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
    leader_changed = bool(prior_leader and prior_leader != race["leader"])

    stories = (detect_feuds(cur, prior, picks, rows)
               + detect_collapses(cur, prior, rows)
               + detect_ironies(cur, prior, rows, leader_changed)
               + detect_heater(cur, prior, rows, timeline, week, weeks_elapsed))
    stories = rank_storylines(stories)
    if not stories or stories[0]["narrative_score"] < QUIET_WEEK_FLOOR:
        stories = [quiet_week_story(rows)]

    lo = prior_week if prior_week is not None else 0
    bad_beats = build_bad_beats(cur, cache, lo, week)

    return {
        "group_id": group_id,
        "week": week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
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
        "manager_profiles": build_profiles(cur, picks),
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
    utils.save_json_atomic(packet_path(args.group), packet)
    print(f"  [{args.group}] week {packet['week']} "
          f"({packet['comparison']['basis']}): "
          f"{len(packet['storylines'])} storyline(s) "
          f"[{', '.join(s['type'] for s in packet['storylines'])}], "
          f"{len(packet['bad_beat_candidates'])} bad-beat candidate(s)")


if __name__ == "__main__":
    main()
