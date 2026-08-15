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
  - fail-loud: a missing output contract exits non-zero and writes nothing.

Feud fixtures are constructed IN MEMORY — never written into the files
production reads (playbook rule 14). Schema checks run against whatever picks
are currently committed, so a dummy-data swap is covered automatically.

Usage:
    python scripts/test_week_packet.py
"""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import build_week_packet as B

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _has_keys(d, keys):
    return isinstance(d, dict) and all(k in d for k in keys)


PACKET_KEYS = {"group_id", "week", "generated_at", "season", "stakes",
               "comparison", "race", "storylines", "bad_beat_candidates",
               "manager_profiles"}
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
    check(f"[{group}] over_count + under_count == picks held",
          all(p["over_count"] + p["under_count"] > 0 for p in profiles.values()))


# --- Contract-derived helpers ------------------------------------------------

def validate_helpers():
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


def validate_feuds():
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


def validate_collapse():
    """A collapse is a SLIDE across a window, not a single loss.

    Week over week exactly one game is played, so a pick's ceiling can only fall
    by exactly 1.0 — a 1-week threshold makes 'collapse' a binary 'did this team
    lose' that fires constantly. These pin the window instead."""
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

def validate_heater():
    """The heater is scored per WEEK, so a long snapshot gap can't hand it the
    top slot for free. At the intended cadence the normalization is a no-op."""
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


def validate_byes():
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

def validate_no_prior():
    """With no prior snapshot the movement fields must be null, NOT 0.0 — a
    fabricated zero is a number the column would print as fact."""
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


# --- Fail-loud ---------------------------------------------------------------

def validate_fail_loud():
    """A missing output contract must exit non-zero and write nothing."""
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


def main():
    print("build_week_packet.py — packet contract, feud detection, fail-loud\n")

    print("Contract-derived helpers:")
    validate_helpers()

    print("\nFeud detection (in-memory fixtures):")
    validate_feuds()

    print("\nCollapse window:")
    validate_collapse()

    print("\nHeater normalization:")
    validate_heater()

    print("\nBye weeks:")
    validate_byes()

    print("\nUnknown prior state:")
    validate_no_prior()

    print("\nFail-loud:")
    validate_fail_loud()

    for group in utils.get_all_group_ids():
        print(f"\nPacket schema — {group}:")
        validate_packet(B.build_packet(group), group)

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
