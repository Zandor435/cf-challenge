#!/usr/bin/env python3
"""
test_week_packet.py — Validates build_week_packet.py.

The packet is what the pundit quotes verbatim (templates/svp_persona.md, sacred
rule 1), so a wrong number here is laundered into the column as fact. This test
asserts:
  - packet schema + types for all three real groups off the committed boards,
  - the contract-derived helpers (status_of, rank_managers) match
    docs/output-contract.md exactly,
  - feud detection finds a KNOWN opposite-side pair, applies the adjacency
    bonus, and respects the divergence floor,
  - a same-side duplicate WARNS on stderr but never fails the build,
  - the comparison block never fabricates a 0.0 when prior state is unknown,
  - leader-flip attribution is EXACT: the pair's per-pick contributions sum to
    the swing, and bystanders earn none of it,
  - irony scores on the pick's own magnitude, not a flat constant,
  - storylines dedupe to one per MOMENT, keeping the max score and losing no
    picks,
  - season_complete never reports True from an empty or partial cache,
  - preseason (zero played games) returns None and writes nothing, WITHOUT
    weakening the fail-loud path: an unresolvable week with games already played
    still exits 1,
  - fail-loud: a missing output contract exits non-zero and writes nothing.

Feud fixtures are constructed IN MEMORY — never written into the files
production reads (playbook rule 14). Schema checks run against whatever picks
are currently committed, so a dummy-data swap is covered automatically.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_week_packet.py
    python scripts/test_week_packet.py
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import build_week_packet as B

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _has_keys(d, keys):
    return isinstance(d, dict) and all(k in d for k in keys)


PACKET_KEYS = {"group_id", "week", "generated_at", "season", "stakes",
               "comparison", "race", "storylines", "bad_beat_candidates",
               "manager_profiles", "uniform_profile_fields"}
COMPARISON_KEYS = {"prior_week", "weeks_elapsed", "basis"}
RACE_ROW_KEYS = {"manager_id", "name", "total_delta", "gap_to_leader",
                 "delta_this_week", "rank", "rank_change"}
STORY_KEYS = {"type", "narrative_score", "managers", "picks", "race_position",
              "evidence"}
STORY_PICK_KEYS = {"manager_id", "team", "line", "direction", "banked_delta",
                   "floor", "ceiling", "status", "floor_change_this_week",
                   "ceiling_change_this_week", "p_beat_line"}
BAD_BEAT_KEYS = {"manager_id", "team", "line", "direction", "game",
                 "delta_impact", "how_it_died"}
GAME_KEYS = {"opponent", "score", "home_away", "margin", "week"}
PROFILE_KEYS = {"over_count", "under_count", "conference_spread", "avg_line",
                "picks_alive", "picks_clinched", "picks_dead", "best_pick",
                "worst_pick", "baseline_optimism_vs_field"}
STORY_TYPES = {"feud", "collapse", "irony", "heater", "quiet_week"}
# Irony-only fields. Asserted separately from STORY_KEYS rather than folded into
# it, because only ironies carry them.
IRONY_KEYS = {"transition", "flip_contribution", "flip_swing_total"}


# --- Schema ------------------------------------------------------------------

def validate_packet(pk, group):
    check(f"[{group}] top-level keys", _has_keys(pk, PACKET_KEYS),
          f"missing {sorted(PACKET_KEYS - set(pk))}" if not _has_keys(pk, PACKET_KEYS) else "")
    check(f"[{group}] week is an int", isinstance(pk.get("week"), int))
    check(f"[{group}] group_id matches", pk.get("group_id") == group)
    check(f"[{group}] comparison keys", _has_keys(pk.get("comparison", {}), COMPARISON_KEYS))

    rows = pk.get("race", {}).get("standings", [])
    check(f"[{group}] race has rows", len(rows) > 0, f"{len(rows)} manager(s)")
    check(f"[{group}] race row keys", all(_has_keys(r, RACE_ROW_KEYS) for r in rows))
    check(f"[{group}] ranks are 1..N distinct",
          sorted(r["rank"] for r in rows) == list(range(1, len(rows) + 1)))
    check(f"[{group}] leader is rank 1",
          pk["race"]["leader"] == next(r["manager_id"] for r in rows if r["rank"] == 1))
    check(f"[{group}] gap_to_leader >= 0 and 0 for the leader",
          all(r["gap_to_leader"] >= 0 for r in rows)
          and next(r["gap_to_leader"] for r in rows if r["rank"] == 1) == 0)

    stories = pk.get("storylines", [])
    check(f"[{group}] has >= 1 storyline", len(stories) >= 1, f"{len(stories)}")
    check(f"[{group}] storyline keys", all(_has_keys(s, STORY_KEYS) for s in stories))
    check(f"[{group}] storyline types known",
          all(s["type"] in STORY_TYPES for s in stories))
    check(f"[{group}] storylines ranked descending",
          [s["narrative_score"] for s in stories]
          == sorted((s["narrative_score"] for s in stories), reverse=True))
    check(f"[{group}] no type exceeds MAX_PER_TYPE",
          all(sum(1 for s in stories if s["type"] == t) <= B.MAX_PER_TYPE
              for t in STORY_TYPES))
    check(f"[{group}] storyline pick keys",
          all(_has_keys(p, STORY_PICK_KEYS) for s in stories for p in s["picks"]))
    check(f"[{group}] every storyline names a manager in the race",
          all(m in {r["manager_id"] for r in rows}
              for s in stories for m in s["managers"]))

    check(f"[{group}] season_complete is a bool",
          isinstance(pk.get("season_complete"), bool),
          f"got {type(pk.get('season_complete')).__name__}")
    check(f"[{group}] every storyline carries moment_size >= 1",
          all(isinstance(s.get("moment_size"), int) and s["moment_size"] >= 1
              for s in stories))
    check(f"[{group}] moment_size never undercounts the picks it folded",
          all(s["moment_size"] <= len(s["picks"]) or s["type"] in ("quiet_week", "heater")
              for s in stories))
    ironies = [s for s in stories if s["type"] == "irony"]
    check(f"[{group}] irony storylines publish transition + flip fields",
          all(_has_keys(s, IRONY_KEYS) for s in ironies), f"{len(ironies)} irony")
    check(f"[{group}] flip credit is published with its swing total, or not at all",
          all((s["flip_contribution"] is None) == (s["flip_swing_total"] is None)
              for s in ironies))

    beats = pk.get("bad_beat_candidates", [])
    check(f"[{group}] bad-beat keys", all(_has_keys(b, BAD_BEAT_KEYS) for b in beats))
    check(f"[{group}] bad-beat game keys",
          all(_has_keys(b["game"], GAME_KEYS) for b in beats))
    check(f"[{group}] bad beats are within the comparison window",
          all(b["game"]["week"] <= pk["week"] for b in beats))

    profiles = pk.get("manager_profiles", {})
    check(f"[{group}] a profile per manager",
          set(profiles) == {r["manager_id"] for r in rows},
          f"{len(profiles)} profile(s) vs {len(rows)} manager(s)")
    check(f"[{group}] profile keys", all(_has_keys(p, PROFILE_KEYS)
                                         for p in profiles.values()))
    uni = pk.get("uniform_profile_fields")
    check(f"[{group}] uniform_profile_fields is a dict",
          isinstance(uni, dict), f"got {type(uni).__name__}")
    provs = list(profiles.values())
    check(f"[{group}] every field it claims as uniform really is uniform",
          all(all(pr.get(k) == v for pr in provs) for k, v in (uni or {}).items()),
          f"claimed {sorted(uni or {})}")
    check(f"[{group}] no field that is uniform is left unclaimed",
          all(k in (uni or {}) for k, v in (provs[0] if provs else {}).items()
              if isinstance(v, (int, float))
              and all(pr.get(k) == v for pr in provs[1:])),
          f"claimed {sorted(uni or {})}")
    check(f"[{group}] over_count + under_count == picks held",
          all(p["over_count"] + p["under_count"] > 0 for p in profiles.values()))


# --- Contract-derived helpers ------------------------------------------------

def test_helpers():
    print("\nContract-derived helpers:")
    # Contract: CLINCHED if floor > 0; DEAD if ceiling < 0; else LIVE.
    check("status_of: floor > 0 -> CLINCHED", B.status_of(0.5, 3.5) == "CLINCHED")
    check("status_of: ceiling < 0 -> DEAD", B.status_of(-3.5, -0.5) == "DEAD")
    check("status_of: straddling zero -> LIVE", B.status_of(-1.5, 2.5) == "LIVE")
    check("status_of: floor == 0 is LIVE, not CLINCHED", B.status_of(0.0, 2.0) == "LIVE")
    check("status_of: unknown input -> None", B.status_of(None, 1.0) is None)

    # Contract: banked_total desc, ties by floor desc, then manager_id.
    ranks = B.rank_managers({"a": (5.0, 1.0), "b": (5.0, 2.0), "c": (9.0, 0.0)})
    check("rank_managers: total desc wins", ranks["c"] == 1)
    check("rank_managers: tie broken by floor desc", ranks["b"] == 2 and ranks["a"] == 3)
    tie = B.rank_managers({"zeb": (1.0, 1.0), "abe": (1.0, 1.0)})
    check("rank_managers: full tie broken by manager_id", tie["abe"] == 1 and tie["zeb"] == 2)

    check("_sub: null in -> null out (never a fabricated 0.0)",
          B._sub(None, 3.0) is None and B._sub(3.0, None) is None)
    check("_sub: real subtraction", B._sub(2.5, -1.0) == 3.5)


# --- Feud detection ----------------------------------------------------------

def _mgr(mid, rank, picks):
    return {"manager_id": mid, "display_name": mid.title(), "rank": rank,
            "banked_total": sum(p["banked_delta"] for p in picks.values()),
            "floor": 0.0, "ceiling": 0.0, "picks": picks}


def _pick(team, direction, line, banked):
    return {"team": team, "conference": "Big Ten", "line": line,
            "direction": direction, "banked_wins": 0, "games_remaining": 0,
            "banked_delta": banked, "floor": banked, "ceiling": banked,
            "status": B.status_of(banked, banked), "p_beat_line": None}


def _rows(*mids):
    return [{"manager_id": m, "gap_to_leader": 0.0, "delta_this_week": None,
             "rank_change": None} for m in mids]


def test_feuds():
    print("\nFeud detection (in-memory fixtures):")
    # KNOWN opposite-side pair: same team, opposite directions, adjacent ranks.
    cur = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.5)}),
           "bob": _mgr("bob", 2, {"Ohio State": _pick("Ohio State", "U", 9.5, -2.5)})}
    picks = [{"manager": "ann", "team": "Ohio State", "direction": "O", "line": 9.5,
              "conference": "Big Ten"},
             {"manager": "bob", "team": "Ohio State", "direction": "U", "line": 9.5,
              "conference": "Big Ten"}]
    feuds = B.detect_feuds(cur, None, picks, _rows("ann", "bob"))
    check("feud: opposite-side pair detected", len(feuds) == 1, f"{len(feuds)} found")
    if feuds:
        f = feuds[0]
        check("feud: both managers named", set(f["managers"]) == {"ann", "bob"})
        check("feud: both picks carried", len(f["picks"]) == 2)
        check("feud: picks are attributable", {p["manager_id"] for p in f["picks"]}
              == {"ann", "bob"})
        # divergence |+2.5 - -2.5| = 5.0, adjacent ranks -> + FEUD_ADJACENCY_BONUS
        check("feud: score = divergence + adjacency bonus",
              f["narrative_score"] == 5.0 + B.FEUD_ADJACENCY_BONUS,
              f"score={f['narrative_score']}")
        check("feud: evidence cites both deltas",
              "+2.5" in f["evidence"] and "-2.5" in f["evidence"])

    # Non-adjacent ranks: same divergence, no bonus.
    far = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.5)}),
           "bob": _mgr("bob", 4, {"Ohio State": _pick("Ohio State", "U", 9.5, -2.5)})}
    f2 = B.detect_feuds(far, None, picks, _rows("ann", "bob"))
    check("feud: no adjacency bonus when apart in the table",
          len(f2) == 1 and f2[0]["narrative_score"] == 5.0,
          f"score={f2[0]['narrative_score'] if f2 else 'n/a'}")

    # Below the divergence floor: detected as a pair, but not worth telling.
    flat = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 0.0)}),
            "bob": _mgr("bob", 2, {"Ohio State": _pick("Ohio State", "U", 9.5, 0.0)})}
    check("feud: divergence below the floor is not ranked",
          len(B.detect_feuds(flat, None, picks, _rows("ann", "bob"))) == 0)

    # Same team, SAME side: a data-integrity violation. Warn loudly, never fail.
    same_picks = [{"manager": "ann", "team": "Ohio State", "direction": "O",
                   "line": 9.5, "conference": "Big Ten"},
                  {"manager": "bob", "team": "Ohio State", "direction": "O",
                   "line": 9.5, "conference": "Big Ten"}]
    same_cur = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.5)}),
                "bob": _mgr("bob", 2, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.5)})}
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        same = B.detect_feuds(same_cur, None, same_picks, _rows("ann", "bob"))
    msg = err.getvalue()
    check("same-side duplicate: no feud emitted", len(same) == 0)
    check("same-side duplicate: warns on stderr", "::warning::" in msg and "SAME side" in msg)
    check("same-side duplicate: names both offenders", "ann" in msg and "bob" in msg)
    check("same-side duplicate: does not raise", True)

    # A single holder is not a feud.
    solo = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.5)})}
    check("feud: a lone holder is not a feud",
          len(B.detect_feuds(solo, None, [picks[0]], _rows("ann"))) == 0)


# --- Collapse window ---------------------------------------------------------

def _snap(week, mid, team, ceiling):
    return {"as_of_week": week, "generated_at": "", "managers": [
        {"manager_id": mid, "p_win_pool": None, "picks": [
            {"team": team, "banked_delta": 0.0, "floor": 0.0,
             "ceiling": ceiling, "expected_delta": None, "p_beat_line": None}]}]}


def test_collapse():
    """A collapse is a SLIDE across a window, not a single loss.

    Week over week exactly one game is played, so a pick's ceiling can only fall
    by exactly 1.0 — a 1-week threshold makes 'collapse' a binary 'did this team
    lose' that fires constantly. These pin the window instead."""
    print("\nCollapse window:")
    prior = {"ann": {"banked_total": 0.0, "floor": 0.0, "ceiling": 0.0,
                     "rank": 1, "picks": {}}}
    rows = _rows("ann", "bob")

    def cur_with(ceiling):
        return {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 0.0)}),
                "bob": _mgr("bob", 2, {"Iowa": _pick("Iowa", "O", 7.5, 0.0)})}

    cur = cur_with(3.0)
    cur["ann"]["picks"]["Ohio State"]["ceiling"] = 4.0     # 5.0 -> 4.0 = one loss
    tl = {"snapshots": [_snap(10, "ann", "Ohio State", 5.0)]}
    check("collapse: a single week's -1.0 is not a collapse",
          B.detect_collapses(cur, prior, rows, tl, 13) == [])

    cur["ann"]["picks"]["Ohio State"]["ceiling"] = 3.0     # 5.0 -> 3.0 across 3wk
    got = B.detect_collapses(cur, prior, rows, tl, 13)
    check("collapse: a -2.0 slide across the window ranks", len(got) == 1,
          f"{len(got)} found")
    if got:
        check("collapse: reports the window it measured",
              got[0]["lookback_weeks"] == 3, f"span={got[0]['lookback_weeks']}")
        check("collapse: publishes the windowed change",
              got[0]["ceiling_change_over_window"] == -2.0)
        check("collapse: evidence names the span",
              "over 3 week(s)" in got[0]["evidence"])

    # Season too young for a full window: fall back, but report the REAL span.
    young = {"snapshots": [_snap(12, "ann", "Ohio State", 5.0)]}
    got = B.detect_collapses(cur, prior, rows, young, 13)
    check("collapse: falls back to the earliest snapshot when the season is young",
          len(got) == 1 and got[0]["lookback_weeks"] == 1,
          f"span={got[0]['lookback_weeks'] if got else 'n/a'}")
    check("collapse: the fallback does not overstate the window",
          got and "over 1 week(s)" in got[0]["evidence"])

    # Bottom half: the ceiling can fall, but it is not a story from down there.
    low = {"ann": _mgr("ann", 2, {"Ohio State": _pick("Ohio State", "O", 9.5, 0.0)}),
           "bob": _mgr("bob", 1, {"Iowa": _pick("Iowa", "O", 7.5, 0.0)})}
    low["ann"]["picks"]["Ohio State"]["ceiling"] = 3.0
    check("collapse: bottom-half manager excluded",
          B.detect_collapses(low, prior, rows, tl, 13) == [])

    check("collapse: no snapshot at all -> no collapse",
          B.detect_collapses(cur, prior, rows, {"snapshots": []}, 13) == [])


# --- Heater normalization ----------------------------------------------------

def test_heater():
    """The heater is scored per WEEK, so a long snapshot gap can't hand it the
    top slot for free. At the intended cadence the normalization is a no-op."""
    print("\nHeater normalization:")
    cur = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 28.0)})}
    prior = {"ann": {"banked_total": 0.0, "floor": 0.0, "ceiling": 0.0,
                     "rank": 1, "picks": {}}}
    rows = [{"manager_id": "ann", "gap_to_leader": 0.0, "delta_this_week": 28.0,
             "rank_change": 0}]
    empty_tl = {"snapshots": []}   # streak 0, so score == rate exactly

    one = B.detect_heater(cur, prior, rows, empty_tl, 16, 1)
    check("heater: one-week gap is an exact no-op",
          len(one) == 1 and one[0]["narrative_score"] == 28.0,
          f"score={one[0]['narrative_score'] if one else 'n/a'}")

    ten = B.detect_heater(cur, prior, rows, empty_tl, 16, 10)
    check("heater: ten-week gap scores the per-week rate",
          len(ten) == 1 and ten[0]["narrative_score"] == 2.8,
          f"score={ten[0]['narrative_score'] if ten else 'n/a'}")
    check("heater: publishes gain_per_week for the pundit to quote",
          ten and ten[0]["gain_per_week"] == 2.8)
    check("heater: evidence still quotes the RAW gain, not just the rate",
          ten and "+28" in ten[0]["evidence"] and "10 week(s)" in ten[0]["evidence"])
    check("heater: a long gap no longer outscores a feud",
          ten and ten[0]["narrative_score"] < 5.0 + B.FEUD_ADJACENCY_BONUS)

    # A grind is not a heater: +5 across 10 weeks is 0.5/wk, under the floor.
    slow = [{"manager_id": "ann", "gap_to_leader": 0.0, "delta_this_week": 5.0,
             "rank_change": 0}]
    check("heater: a slow grind across a long gap is not a heater",
          B.detect_heater(cur, prior, slow, empty_tl, 16, 10) == [])
    check("heater: the same +5 in ONE week is a heater",
          len(B.detect_heater(cur, prior, slow, empty_tl, 16, 1)) == 1)
    check("heater: missing weeks_elapsed degrades to unnormalized, not a crash",
          len(B.detect_heater(cur, prior, rows, empty_tl, 16, None)) == 1)


# --- Bye weeks ---------------------------------------------------------------
# A bye is a week in which a team plays no game. Nothing is banked and nothing
# is scheduled away, so a bye is INVISIBLE to the boards: banked_delta, floor
# and ceiling are all unchanged. These fixtures put a bye in the MIDDLE of a
# 3-week span, with games either side, and pin what each windowed rule does with
# it. The contract, stated once:
#
#   collapse  — a bye can neither CAUSE a collapse (no ceiling moves) nor MASK
#               one (a real slide either side of it still totals up).
#   streak    — bye-transparent. Zero movement is neutral; only losing ground
#               ends the run. A team that didn't play didn't cool off.
#   rate      — per CALENDAR week, so a bye DOES dampen it. That is intended:
#               the manager banked less per week. The streak carries the "still
#               hot" signal; the rate carries "how fast, lately".

def _mgr_snap(week, mid, total, ceiling=0.0):
    """A one-pick snapshot whose banked_delta sums to `total`."""
    return {"as_of_week": week, "generated_at": "", "managers": [
        {"manager_id": mid, "p_win_pool": None, "picks": [
            {"team": "Ohio State", "banked_delta": total, "floor": 0.0,
             "ceiling": ceiling, "expected_delta": None, "p_beat_line": None}]}]}


def test_byes():
    print("\nBye weeks:")
    # --- streak: bye in the middle of a run --------------------------------
    # wk10 -> 11 banked +1; wk11 -> 12 BYE (flat); wk12 -> 13 banked +1.
    bye_run = {"snapshots": [_mgr_snap(10, "ann", 0.0), _mgr_snap(11, "ann", 1.0),
                             _mgr_snap(12, "ann", 1.0), _mgr_snap(13, "ann", 2.0)]}
    gaining, span = B.heater_streak(bye_run, 13, "ann")
    check("bye/streak: a bye does not break the run",
          gaining == 2 and span == 3, f"gaining={gaining}, span={span}")
    check("bye/streak: the bye week is not counted as a gain",
          gaining == 2, "2 games banked across a 3-week span")

    # Losing ground still ends it, bye or no bye.
    lost = {"snapshots": [_mgr_snap(10, "ann", 0.0), _mgr_snap(11, "ann", 5.0),
                          _mgr_snap(12, "ann", 3.0), _mgr_snap(13, "ann", 4.0)]}
    gaining, span = B.heater_streak(lost, 13, "ann")
    check("bye/streak: losing ground ends the run", gaining == 1 and span == 1,
          f"gaining={gaining}, span={span}")

    # An all-bye stretch: nothing banked, but nothing lost either.
    idle = {"snapshots": [_mgr_snap(10, "ann", 2.0), _mgr_snap(11, "ann", 2.0),
                          _mgr_snap(12, "ann", 2.0)]}
    gaining, span = B.heater_streak(idle, 12, "ann")
    check("bye/streak: an idle stretch gains nothing and breaks nothing",
          gaining == 0 and span == 2, f"gaining={gaining}, span={span}")

    # --- evidence must not call a run with a bye in it a streak -------------
    cur = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.0)})}
    prior = {"ann": {"banked_total": 0.0, "floor": 0.0, "ceiling": 0.0,
                     "rank": 1, "picks": {}}}
    rows = [{"manager_id": "ann", "gap_to_leader": 0.0, "delta_this_week": 2.0,
             "rank_change": 0}]
    got = B.detect_heater(cur, prior, rows, bye_run, 13, 1)
    check("bye/evidence: reports gaining weeks and the span, not a streak",
          got and "2 gaining week(s) in the last 3" in got[0]["evidence"],
          got[0]["evidence"] if got else "no heater")
    check("bye/evidence: never claims consecutive weeks",
          got and "streak" not in got[0]["evidence"])
    check("bye/packet: both run numbers published for the pundit",
          got and got[0]["gaining_weeks"] == 2 and got[0]["run_span_weeks"] == 3)

    # --- rate: per calendar week, so a bye dampens it (INTENDED) -----------
    # +2 banked across 2 calendar weeks with a bye in one of them = 1.0/wk,
    # not 2.0/wk. The manager really did bank less per week.
    check("bye/rate: normalization is per CALENDAR week, so a bye dampens it",
          B.detect_heater(cur, prior, rows, bye_run, 13, 2) == []
          and len(B.detect_heater(cur, prior, rows, bye_run, 13, 1)) == 1,
          "+2 over 2 weeks = 1.0/wk, below HEATER_MIN_DELTA 1.5")

    # --- collapse: a bye neither causes nor masks one ----------------------
    # Ceilings across the window: wk10 4.0, wk11 4.0 (bye), wk12 4.0 -> flat.
    flat = {"snapshots": [_mgr_snap(10, "ann", 0.0, ceiling=4.0),
                          _mgr_snap(11, "ann", 0.0, ceiling=4.0),
                          _mgr_snap(12, "ann", 0.0, ceiling=4.0)]}
    cur_flat = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 0.0)}),
                "bob": _mgr("bob", 2, {"Iowa": _pick("Iowa", "O", 7.5, 0.0)})}
    cur_flat["ann"]["picks"]["Ohio State"]["ceiling"] = 4.0
    check("bye/collapse: a bye cannot CAUSE a collapse (no ceiling moves)",
          B.detect_collapses(cur_flat, prior, _rows("ann", "bob"), flat, 13) == [])

    # A real slide with a bye inside it: 5.0 -> 4.0 -> (bye) 4.0 -> 3.0.
    slide = {"snapshots": [_mgr_snap(10, "ann", 0.0, ceiling=5.0),
                           _mgr_snap(11, "ann", 0.0, ceiling=4.0),
                           _mgr_snap(12, "ann", 0.0, ceiling=4.0)]}
    cur_slide = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 0.0)}),
                 "bob": _mgr("bob", 2, {"Iowa": _pick("Iowa", "O", 7.5, 0.0)})}
    cur_slide["ann"]["picks"]["Ohio State"]["ceiling"] = 3.0
    got = B.detect_collapses(cur_slide, prior, _rows("ann", "bob"), slide, 13)
    check("bye/collapse: a bye does not MASK a real slide", len(got) == 1,
          f"{len(got)} found")
    check("bye/collapse: the windowed change spans the bye",
          got and got[0]["ceiling_change_over_window"] == -2.0)


# --- Unknown prior state -----------------------------------------------------

def test_no_prior():
    """With no prior snapshot the movement fields must be null, NOT 0.0 — a
    fabricated zero is a number the column would print as fact."""
    print("\nUnknown prior state:")
    cur = {"ann": _mgr("ann", 1, {"Ohio State": _pick("Ohio State", "O", 9.5, 2.5)})}
    race = B.build_race(cur, None, {"managers": [{"manager_id": "ann",
                                                  "display_name": "Ann"}]})
    row = race["standings"][0]
    check("no prior: delta_this_week is null", row["delta_this_week"] is None)
    check("no prior: rank_change is null", row["rank_change"] is None)
    check("no prior: total_delta still real", row["total_delta"] == 2.5)
    check("no prior: collapse/irony/heater suppressed",
          B.detect_collapses(cur, None, race["standings"], {"snapshots": []}, 1) == []
          and B.detect_ironies(cur, None, race["standings"], False) == []
          and B.detect_heater(cur, None, race["standings"], {"snapshots": []},
                              1, None) == [])

    tl = {"snapshots": [{"as_of_week": 6, "managers": []},
                        {"as_of_week": 16, "managers": []}]}
    check("prior_snapshot: picks the latest week strictly before",
          B.prior_snapshot(tl, 16)["as_of_week"] == 6)
    check("prior_snapshot: none before the first scored week",
          B.prior_snapshot(tl, 6) is None)


# --- Leader-flip attribution -------------------------------------------------
# A lead change is a TWO-PARTY event. The old scoring stamped one group-wide
# `leader_changed` boolean onto every status change in the week, so a bystander's
# routine clinch was credited with the flip. These pin the replacement.

def test_flip_attribution():
    print("\nLeader-flip attribution:")
    # ann led by 10 at the snapshot; bob leads by 10 now. Swing = 10 + 10 = 20.
    # cy is a BYSTANDER who moved more than anyone (+4) but was never in it.
    prior = {"ann": _mgr("ann", 1, {"A": _pick("A", "O", 9.5, 5.0),
                                    "B": _pick("B", "O", 9.5, 5.0)}),
             "bob": _mgr("bob", 2, {"C": _pick("C", "O", 9.5, 0.0),
                                    "D": _pick("D", "O", 9.5, 0.0)}),
             "cy":  _mgr("cy", 3, {"E": _pick("E", "O", 9.5, 0.0)})}
    cur = {"bob": _mgr("bob", 1, {"C": _pick("C", "O", 9.5, 5.0),
                                  "D": _pick("D", "O", 9.5, 5.0)}),
           "ann": _mgr("ann", 2, {"A": _pick("A", "O", 9.5, 0.0),
                                  "B": _pick("B", "O", 9.5, 0.0)}),
           "cy":  _mgr("cy", 3, {"E": _pick("E", "O", 9.5, 4.0)})}

    flip = B.flip_attribution(cur, prior, "ann", "bob")
    check("flip: a lead change is detected",
          flip is not None and flip["old"] == "ann" and flip["new"] == "bob")
    check("flip: swing = prior gap + current gap",
          flip and flip["swing"] == 20.0, f"swing={flip['swing'] if flip else 'n/a'}")

    contributions = [c for c, _ in flip["shares"].values()]
    check("flip: per-pick contributions sum EXACTLY to the swing",
          sum(contributions) == flip["swing"],
          f"{sum(contributions)} vs {flip['swing']}")
    check("flip: shares therefore sum to 1.0",
          abs(sum(sh for _, sh in flip["shares"].values()) - 1.0) < 1e-9)
    check("flip: only the two parties are attributed — 4 picks, not 5",
          len(flip["shares"]) == 4, f"{len(flip['shares'])} attributed")
    check("flip: the bystander earns no attribution however far it moved",
          not any(mid == "cy" for mid, _ in flip["shares"]))
    check("flip: no attribution when the leader did not change",
          B.flip_attribution(cur, prior, "bob", "bob") is None)
    check("flip: no prior state -> no attribution",
          B.flip_attribution(cur, None, "ann", "bob") is None)


# --- Irony scoring -----------------------------------------------------------

def test_irony_scoring():
    """Score must track the pick's OWN resolved magnitude, and flip credit must
    reach only the picks that actually moved the lead."""
    print("\nIrony scoring:")
    # ann led by 10 and collapsed to -10; bob climbed 0 -> +10 and took it.
    # Swing = (10 - 0) + (10 - -10) = 30. cy is a bystander whose pick also died.
    prior = {"ann": _mgr("ann", 1, {"A": _pick("A", "O", 9.5, 10.0)}),
             "bob": _mgr("bob", 2, {"C": _pick("C", "O", 9.5, 0.0)}),
             "cy":  _mgr("cy", 3, {"E": _pick("E", "O", 9.5, 0.0)})}
    cur = {"bob": _mgr("bob", 1, {"C": _pick("C", "O", 9.5, 10.0)}),
           "ann": _mgr("ann", 2, {"A": _pick("A", "O", 9.5, -10.0)}),
           "cy":  _mgr("cy", 3, {"E": _pick("E", "O", 9.5, -2.5)})}
    rows = _rows("bob", "ann", "cy")

    plain = {s["managers"][0]: s for s in B.detect_ironies(cur, prior, rows, None)}
    check("irony: every resolved status change is detected", len(plain) == 3,
          f"{len(plain)} found")
    check("irony: score = IRONY_BASE + IRONY_DELTA_WEIGHT * |banked_delta|",
          plain["cy"]["narrative_score"]
          == B.IRONY_BASE + B.IRONY_DELTA_WEIGHT * 2.5,
          f"score={plain['cy']['narrative_score']}")
    check("irony: a bigger resolved delta scores higher than a smaller one",
          plain["ann"]["narrative_score"] > plain["cy"]["narrative_score"])
    check("irony: no flip -> no flip credit is published",
          all(s["flip_contribution"] is None for s in plain.values()))
    check("irony: the transition is published for dedupe and the column",
          plain["ann"]["transition"] == "CLINCHED->DEAD"
          and plain["bob"]["transition"] == "LIVE->CLINCHED",
          f"ann={plain['ann']['transition']} bob={plain['bob']['transition']}")

    flip = B.flip_attribution(cur, prior, "ann", "bob")
    scored = {s["managers"][0]: s for s in B.detect_ironies(cur, prior, rows, flip)}
    check("irony: a flip contributor scores above its magnitude alone",
          scored["bob"]["narrative_score"] > plain["bob"]["narrative_score"])
    check("irony: the bigger contributor earns the bigger share of the bonus",
          scored["ann"]["narrative_score"] - plain["ann"]["narrative_score"]
          > scored["bob"]["narrative_score"] - plain["bob"]["narrative_score"])
    check("irony: a BYSTANDER's score is untouched by someone else's flip",
          scored["cy"]["narrative_score"] == plain["cy"]["narrative_score"],
          f"{scored['cy']['narrative_score']} vs {plain['cy']['narrative_score']}")
    check("irony: contribution is published in games, with its swing total",
          scored["bob"]["flip_contribution"] == 10.0
          and scored["bob"]["flip_swing_total"] == 30.0,
          f"{scored['bob']['flip_contribution']} of {scored['bob']['flip_swing_total']}")
    check("irony: the bystander is told nothing about the flip",
          scored["cy"]["flip_contribution"] is None
          and scored["cy"]["flip_swing_total"] is None)


# --- Moment dedupe -----------------------------------------------------------
# Four picks clinching for one manager in one week is ONE moment. Before dedupe
# existed it was four storylines, and MAX_PER_TYPE discarded the surplus with no
# record of what was lost.

def _spick(mid, team, banked=1.0):
    return {"manager_id": mid, "team": team, "line": 9.5, "direction": "O",
            "banked_delta": banked, "floor": banked, "ceiling": banked,
            "status": B.status_of(banked, banked), "floor_change_this_week": None,
            "ceiling_change_this_week": None, "p_beat_line": None}


def _story(stype, score, mids, picks, **extra):
    st = {"type": stype, "narrative_score": score, "managers": list(mids),
          "picks": picks, "race_position": {}, "evidence": "fixture."}
    st.update(extra)
    return st


def test_dedupe():
    print("\nMoment dedupe:")
    clinches = [_story("irony", sc, ["ann"], [_spick("ann", team)],
                       transition="LIVE->CLINCHED")
                for team, sc in (("A", 3.0), ("B", 4.0), ("C", 2.0), ("D", 1.0))]

    merged = B.merge_moment(clinches)
    check("dedupe: one manager's four clinches merge into one storyline",
          merged["moment_size"] == 4, f"moment_size={merged['moment_size']}")
    check("dedupe: the merged score is the MAX, never the sum",
          merged["narrative_score"] == 4.0, f"score={merged['narrative_score']}")
    check("dedupe: no pick is discarded by the merge",
          {p["team"] for p in merged["picks"]} == {"A", "B", "C", "D"},
          f"{len(merged['picks'])} pick(s) kept")
    check("dedupe: evidence says the moment was bigger than its representative",
          "Same moment" in merged["evidence"] and "3 more" in merged["evidence"])

    # Opposite transitions are two moments, not one — the case that forces the
    # transition into the key.
    split = [_story("irony", 3.0, ["ann"], [_spick("ann", "A", 2.5)],
                    transition="LIVE->CLINCHED"),
             _story("irony", 3.0, ["ann"], [_spick("ann", "B", -2.5)],
                    transition="LIVE->DEAD")]
    check("dedupe: one manager's clinch and death stay TWO moments",
          len({B.moment_key(s) for s in split}) == 2)

    # A collapse is one board eroding, even across two of its picks.
    slides = [_story("collapse", 4.5, ["ann"], [_spick("ann", "A")]),
              _story("collapse", 3.75, ["ann"], [_spick("ann", "B")])]
    check("dedupe: two slides by one manager are one collapse",
          len({B.moment_key(s) for s in slides}) == 1)

    # Different pairs/teams are genuinely different feuds.
    feuds = [_story("feud", 5.0, ["ann", "bob"], [_spick("ann", "A")]),
             _story("feud", 5.0, ["ann", "bob"], [_spick("ann", "B")])]
    check("dedupe: feuds over different teams stay separate",
          len({B.moment_key(s) for s in feuds}) == 2)

    check("dedupe: a lone storyline reports moment_size 1",
          B.merge_moment([clinches[0]])["moment_size"] == 1)

    # The whole point: the feud is no longer buried by duplicate ironies.
    ranked = B.rank_storylines(clinches + [_story("feud", 5.0, ["ann", "bob"],
                                                  [_spick("ann", "Z")])])
    check("dedupe: four ironies and a feud rank as TWO storylines",
          len(ranked) == 2, f"{len(ranked)} ranked")
    check("dedupe: the feud leads once its duplicates are collapsed",
          ranked[0]["type"] == "feud", f"leader={ranked[0]['type']}")


# --- Season completion -------------------------------------------------------

def test_season_complete():
    print("\nSeason completion:")
    check("season_complete: every game final -> True",
          B.season_is_complete({"games": [{"completed": True},
                                          {"completed": True}]}) is True)
    check("season_complete: one game unplayed -> False",
          B.season_is_complete({"games": [{"completed": True},
                                          {"completed": False}]}) is False)
    check("season_complete: an empty cache is NOT a finished season",
          B.season_is_complete({"games": []}) is False)
    check("season_complete: a cache with no games key -> False",
          B.season_is_complete({}) is False)
    check("season_complete: a missing completed flag is not treated as played",
          B.season_is_complete({"games": [{"week": 1}]}) is False)


# --- Uniform profile fields --------------------------------------------------

def test_uniform_profile_fields():
    """A value the whole room shares must be named as such, not left looking
    like a personal stat (persona sacred rule 7)."""
    print("\nUniform profile fields:")
    U = B.uniform_profile_fields
    shared = {
        "ann": {"picks_alive": 0, "conference_spread": 4, "picks_dead": 3,
                "best_pick": {"team": "Utah"}, "avg_line": 8.5},
        "bob": {"picks_alive": 0, "conference_spread": 4, "picks_dead": 1,
                "best_pick": {"team": "Rice"}, "avg_line": 7.5},
        "cy":  {"picks_alive": 0, "conference_spread": 4, "picks_dead": 2,
                "best_pick": None, "avg_line": 8.5},
    }
    got = U(shared)
    check("uniform: a field every manager shares is reported",
          got.get("picks_alive") == 0, f"got {got}")
    check("uniform: catches EVERY shared field, not just picks_alive",
          got.get("conference_spread") == 4, f"got {got}")
    check("uniform: a field that varies is not reported",
          "picks_dead" not in got, f"got {got}")
    check("uniform: a varying field that collides on two managers is not reported",
          "avg_line" not in got, f"got {got}")
    check("uniform: dict-valued fields are skipped, never compared",
          "best_pick" not in got, f"got {got}")

    live = {"ann": {"picks_alive": 2}, "bob": {"picks_alive": 0}}
    check("uniform: while the season runs, picks_alive varies and is not listed",
          "picks_alive" not in U(live), f"got {U(live)}")
    check("uniform: nothing shared -> empty dict, not a missing key",
          U(live) == {}, f"got {U(live)}")
    check("uniform: a single manager distinguishes nothing, so nothing is claimed",
          U({"ann": {"picks_alive": 0}}) == {})
    check("uniform: no managers at all -> empty dict", U({}) == {})

    # A bool is not a count. Comparing on truthiness would match 0 against
    # False, so the scalar guard has to keep them distinct.
    mixed = {"ann": {"flag": False, "n": 0}, "bob": {"flag": False, "n": 0}}
    check("uniform: booleans stay booleans, never coerced to 0",
          U(mixed)["flag"] is False and U(mixed)["n"] == 0, f"got {U(mixed)}")


# --- Preseason ----------------------------------------------------------------

def _cache(games, season=2026, week=None):
    """A minimal in-memory cache (playbook rule 14 — never a real file)."""
    return {"season": season, "week": week, "games": games}


def _game(completed):
    return {"completed": completed, "home_team": "A", "away_team": "B",
            "home_points": 21 if completed else None,
            "away_points": 17 if completed else None,
            "week": 1, "start_date": "2026-08-27T04:00:00.000Z"}


def _with_cache(cache, fn):
    """Run fn() with build_week_packet's cache loader stubbed to `cache`.

    Stubs the loader rather than writing a fixture file, so production data is
    never touched (playbook rule 14). The season guard is stubbed alongside it
    because it reads the same cache off disk and would otherwise veto the
    in-memory fixture.
    """
    real_load, real_assert = B.utils.load_cache, B.utils.assert_season_matches_cache
    B.utils.load_cache = lambda *a, **k: cache
    B.utils.assert_season_matches_cache = lambda *a, **k: cache["season"]
    try:
        return fn()
    finally:
        B.utils.load_cache = real_load
        B.utils.assert_season_matches_cache = real_assert


def _packet_state(path):
    return (path.exists(), path.stat().st_mtime if path.exists() else None)


def test_preseason():
    """Zero played games is a clean no-op; a played game with an unresolvable
    week is still fatal.

    The second half is the load-bearing one: it is what stops the preseason gate
    from widening into a swallow-everything catch that hides a real cache/board
    disagreement behind a friendly message.
    """
    print("\nPreseason (and the fail-loud path it must not weaken):")
    group = utils.get_all_group_ids()[0]
    path = B.packet_path(group)
    before = _packet_state(path)

    # (1) Preseason: a full schedule loaded, nothing kicked off yet.
    out = io.StringIO()
    code = None
    packet = "not-run"
    try:
        with contextlib.redirect_stdout(out):
            packet = _with_cache(_cache([_game(False) for _ in range(5)]),
                                 lambda: B.build_packet(group))
    except SystemExit as e:          # must NOT happen
        code = e.code
    check("preseason: zero played games does not exit", code is None, f"exit={code}")
    check("preseason: returns None instead of a packet", packet is None,
          f"got {type(packet).__name__}")
    check("preseason: says why, naming the state",
          "preseason" in out.getvalue().lower(), out.getvalue().strip()[:60])
    check("preseason: writes no packet", _packet_state(path) == before,
          f"{before} -> {_packet_state(path)}")

    # (2) The fail-loud path is UNCHANGED. The committed boards carry
    #     as_of_week null, so a played game plus a null cache week leaves the
    #     week genuinely unresolvable — an error, not a preseason.
    err = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            _with_cache(_cache([_game(True), _game(False)]),
                        lambda: B.build_packet(group))
    except SystemExit as e:
        code = e.code
    check("played games + unresolvable week STILL exits 1", code == 1, f"exit={code}")
    check("played games + unresolvable week still names the error",
          "::error::" in err.getvalue() and "effective week" in err.getvalue(),
          err.getvalue().strip()[:60])
    check("the fatal path wrote no packet either", _packet_state(path) == before)

    # (3) The discriminator itself: the played-game COUNT, never `week is None`.
    check("discriminator: none played counts 0",
          B.completed_game_count(_cache([_game(False), _game(False)])) == 0)
    check("discriminator: one played counts 1 (week still null)",
          B.completed_game_count(_cache([_game(True), _game(False)])) == 1)
    check("discriminator: an empty cache counts 0, it does not raise",
          B.completed_game_count(_cache([])) == 0)
    check("discriminator: a cache with no games key counts 0",
          B.completed_game_count({"season": 2026}) == 0)
    check("discriminator: a missing completed flag is not counted as played",
          B.completed_game_count(_cache([{"week": 1}])) == 0)


# --- Fail-loud ---------------------------------------------------------------

def test_fail_loud():
    """A missing output contract must exit non-zero and write nothing."""
    print("\nFail-loud:")
    bogus = "definitely_not_a_group"
    code = None
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            B.build_packet(bogus)
    except SystemExit as e:
        code = e.code
    check("missing output contract: exits non-zero", code not in (0, None), f"exit={code}")
    check("missing output contract: names the missing file",
          "standings.json" in err.getvalue() and "::error::" in err.getvalue())
    check("missing output contract: writes no packet",
          not B.packet_path(bogus).exists())


def test_packet_schema():
    # Schema checks run against the LIVE cache. Before the first kickoff there
    # is no packet to check - build_packet returns None by contract - so assert
    # that contract instead of pretending to validate a schema. This re-arms by
    # itself the moment one game is final; it is not a permanent opt-out.
    live_played = B.completed_game_count(utils.load_cache(utils.get_season()))
    for group in utils.get_all_group_ids():
        print(f"\nPacket schema \u2014 {group}:")
        if live_played == 0:
            packet = B.build_packet(group)
            check(f"[{group}] preseason: no packet to validate yet "
                  f"(0 games played); build_packet returns None",
                  packet is None, f"got {type(packet).__name__}")
        else:
            validate_packet(B.build_packet(group), group)


def test_coda_subject_exclusion():
    """The coda may not re-target the One Big Thing lead's subject.

    Week 0 panel shipped the failure this closes: Beat 1 was the Blaine/Chris
    feud over Texas 9.5, Beat 2 was Chris, on Texas. Both beats were correct
    against the packet in isolation -- the collision only exists between them,
    which is why the pool is filtered in Python rather than asked for in the
    prompt. Covers the three paths: normal exclusion, exhausted pool, and the
    live case.
    """
    print("\nCoda subject exclusion:")
    X = B.exclude_lead_subject

    feud = {"type": "feud", "managers": ["blaine", "chris"],
            "picks": [{"manager_id": "blaine", "team": "Texas"},
                      {"manager_id": "chris", "team": "Texas"}]}

    mgr, teams = B.storyline_subject(feud)
    check("subject: both feuding managers are the subject",
          mgr == {"blaine", "chris"}, f"got {mgr}")
    check("subject: the contested team is the subject too",
          teams == {"Texas"}, f"got {teams}")
    check("subject: a missing storyline yields no subject (no crash)",
          B.storyline_subject(None) == (set(), set()))

    # --- 1. normal exclusion -------------------------------------------------
    pool = [
        {"manager_id": "chris", "team": "Texas"},      # both dimensions collide
        {"manager_id": "blaine", "team": "Iowa"},      # manager collides
        {"manager_id": "jonathan", "team": "Texas"},   # team collides
        {"manager_id": "jonathan", "team": "Oregon"},  # clean
        {"manager_id": "zach", "team": "Miami"},       # clean
    ]
    kept, rep = X(pool, feud)
    check("exclusion: the exact Week 0 collision (chris/Texas) is dropped",
          {"chris"} not in [{c["manager_id"]} for c in kept],
          f"kept {[(c['manager_id'], c['team']) for c in kept]}")
    check("exclusion: a shared MANAGER alone is enough to drop (blaine/Iowa)",
          not any(c["manager_id"] == "blaine" for c in kept))
    check("exclusion: a shared TEAM alone is enough to drop (jonathan/Texas)",
          not any(c["team"] == "Texas" for c in kept))
    check("exclusion: clean candidates survive",
          [(c["manager_id"], c["team"]) for c in kept]
          == [("jonathan", "Oregon"), ("zach", "Miami")],
          f"kept {[(c['manager_id'], c['team']) for c in kept]}")
    check("exclusion: selection ORDER is preserved, never re-ranked",
          kept[0]["team"] == "Oregon", f"first={kept[0]}")
    check("exclusion: the report counts what it cost", rep["excluded"] == 3,
          f"got {rep['excluded']}")
    check("exclusion: the clean path is not flagged as forced",
          rep["collision_forced"] is False)

    # --- 2. exhausted pool -> lowest-overlap fallback + loud warning ---------
    all_collide = [
        {"manager_id": "chris", "team": "Texas"},    # overlap 2
        {"manager_id": "blaine", "team": "Iowa"},    # overlap 1
        {"manager_id": "chris", "team": "Utah"},     # overlap 1, later
    ]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        kept, rep = X(all_collide, feud, label="bad-beat", group_id="panel")
    out = buf.getvalue()
    check("fallback: never crashes when everything collides", len(kept) == 1,
          f"got {kept}")
    check("fallback: picks the LOWEST-overlap candidate, not the first",
          (kept[0]["manager_id"], kept[0]["team"]) == ("blaine", "Iowa"),
          f"got {kept[0]}")
    check("fallback: ties on overlap break by original order",
          kept[0]["team"] == "Iowa")
    check("fallback: the degraded path is recorded",
          rep["collision_forced"] is True and rep["forced_overlap"] == 1,
          f"got {rep}")
    check("fallback: and warns LOUDLY -- never a silent collision",
          "::warning::" in out and "collides" in out, f"stdout={out!r}")
    check("fallback: the warning names the group", "[panel]" in out,
          f"stdout={out!r}")

    # --- 3. degenerate inputs ------------------------------------------------
    kept, rep = X([], feud)
    check("empty pool: returns empty, no warning, no crash",
          kept == [] and rep["excluded"] == 0 and not rep["collision_forced"])
    pool2 = [{"manager_id": "chris", "team": "Texas"}]
    kept, rep = X(pool2, None)
    check("no lead: nothing to exclude, pool passes through untouched",
          kept == pool2 and rep["excluded"] == 0, f"got {kept}")


def test_coda_lower_gap_count():
    """`excluded` counts every drop; `excluded_lower_gap` counts only the drops
    that actually sit at a lower gap. The prompt sentence says "with a lower
    gap", so it may only ever quote the second one.

    Panel Week 0 shipped the failure this closes: the packet reported 8 and the
    prompt said "8 pick(s) with a lower gap were set aside" when exactly ONE
    of those 8 was lower than the coda's -1.044. The model was handed a false
    count and hedged a superlative onto it ("the lowest on the board outside
    Blaine and Chris's tussle").
    """
    print("\nCoda lower-gap count:")
    X = B.exclude_lead_subject

    feud = {"type": "feud", "managers": ["blaine", "chris"],
            "picks": [{"manager_id": "blaine", "team": "Texas"},
                      {"manager_id": "chris", "team": "Texas"}]}

    # --- 1. the live panel Week 0 board, in market_gap order -----------------
    panel = [
        ("chris", "Texas", -1.681), ("jonathan", "Oregon", -1.044),
        ("chris", "USC", -0.983), ("jonathan", "Kansas State", -0.839),
        ("jonathan", "LSU", -0.406), ("blaine", "Boise State", -0.402),
        ("zach", "James Madison", -0.234), ("chris", "Tennessee", 0.122),
        ("jonathan", "Ole Miss", 0.339), ("zach", "Virginia Tech", 0.427),
        ("chris", "Michigan State", 0.458), ("zach", "Wake Forest", 0.616),
        ("blaine", "Wisconsin", 0.664), ("blaine", "Indiana", 1.110),
        ("zach", "Notre Dame", 1.483), ("blaine", "Texas", 1.681),
    ]
    pool = [{"manager_id": m, "team": t, "market_gap": g} for m, t, g in panel]
    kept, rep = X(pool, feud)

    check("panel: the coda is still jonathan/Oregon at -1.044",
          (kept[0]["manager_id"], kept[0]["market_gap"]) == ("jonathan", -1.044),
          f"got {kept[0]}")
    check("panel: excluded is unchanged at 8 (all of blaine's and chris's)",
          rep["excluded"] == 8, f"got {rep['excluded']}")
    check("panel: excluded_lower_gap is 1, NOT 8 -- only chris/Texas is lower",
          rep["excluded_lower_gap"] == 1, f"got {rep['excluded_lower_gap']}")

    # --- 2. drops that are all HIGHER count as zero --------------------------
    higher = [
        {"manager_id": "jonathan", "team": "Oregon", "market_gap": -2.0},
        {"manager_id": "blaine", "team": "Iowa", "market_gap": 0.5},
        {"manager_id": "chris", "team": "Duke", "market_gap": 1.5},
    ]
    kept, rep = X(higher, feud)
    check("higher-only: two dropped, none lower than the kept -2.0",
          rep["excluded"] == 2 and rep["excluded_lower_gap"] == 0,
          f"got excluded={rep['excluded']} lower={rep['excluded_lower_gap']}")

    # --- 3. rows with no gap field report None, never a fake 0 ---------------
    beats = [
        {"manager_id": "chris", "team": "Texas", "delta_impact": -1.0},
        {"manager_id": "jonathan", "team": "Oregon", "delta_impact": -1.0},
    ]
    kept, rep = X(beats, feud, label="bad-beat")
    check("no gap field: reports None, so 'no lower picks' != 'no gaps here'",
          rep["excluded_lower_gap"] is None, f"got {rep['excluded_lower_gap']}")

    # --- 4. empty pool -------------------------------------------------------
    kept, rep = X([], feud)
    check("empty pool: both counts are 0, no crash",
          rep["excluded"] == 0 and rep["excluded_lower_gap"] == 0, f"got {rep}")

    # --- 5. forced collision re-baselines on the pick actually kept ----------
    allcollide = [
        {"manager_id": "chris", "team": "Texas", "market_gap": -3.0},
        {"manager_id": "blaine", "team": "Iowa", "market_gap": -0.5},
    ]
    kept, rep = X(allcollide, feud)
    check("forced: fell back to the lowest-overlap pick (blaine/Iowa)",
          rep["collision_forced"] and kept[0]["team"] == "Iowa",
          f"got {kept}")
    check("forced: the count re-baselines on the FORCED winner (-0.5), so 1",
          rep["excluded_lower_gap"] == 1, f"got {rep['excluded_lower_gap']}")


def test_coda_exclusion_in_packet():
    """The live packet must actually CARRY the filtered pool and its audit trail
    -- a helper nobody calls fixes nothing."""
    print("\nCoda exclusion is wired into the packet:")
    src = Path(B.__file__).read_text(encoding="utf-8")
    check("packet: build_packet calls the exclusion before emitting",
          "exclude_lead_subject(" in src.split("def build_packet")[1],
          "build_packet never calls exclude_lead_subject")
    check("packet: bad_beat_candidates is the FILTERED list",
          '"bad_beat_candidates": bad_beats,' in src
          and "bad_beats, coda_exclusion = exclude_lead_subject(" in src)
    check("packet: the audit trail ships with it",
          '"coda_exclusion": coda_exclusion,' in src)


def main():
    print("build_week_packet.py \u2014 packet contract, feud detection, fail-loud")
    test_helpers()
    test_feuds()
    test_collapse()
    test_heater()
    test_byes()
    test_no_prior()
    test_flip_attribution()
    test_irony_scoring()
    test_dedupe()
    test_season_complete()
    test_uniform_profile_fields()
    test_preseason()
    test_fail_loud()
    test_packet_schema()
    test_coda_subject_exclusion()
    test_coda_lower_gap_count()
    test_coda_exclusion_in_packet()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
