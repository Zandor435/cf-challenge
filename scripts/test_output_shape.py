#!/usr/bin/env python3
"""
test_output_shape.py — Validates emitted files against docs/output-contract.md.

The contract is the whole point of the keystone commit: every downstream
consumer reads exactly these shapes. This test builds standings / projection /
timeline / analytics for the test fixture (mid-season week 6 and final week 14)
and asserts:
  - every required key is present with the right type,
  - Board-1 arithmetic invariant: games_remaining == 0 -> floor == ceiling == banked_delta,
  - Board-2 invariants: win_distribution sums to 1; at final, p_beat_line in {0,1}
    and expected_delta == the Board-1 banked_delta (the two boards agree),
  - timeline is append-only + idempotent on the effective week,
  - Board-3 (analytics) SHAPE — the keys, the reserved null `leverage`, and the
    board-separation rule: every module carries a "board" of "exact" or
    "projection". Deliberately shape, not values: the values are standings
    arithmetic that Board-1's own checks already pin, and re-asserting them here
    would just duplicate the thing being tested. Plus a degenerate pass (no
    managers / all-zero picks) so the pre-draft + dummy-data path is pinned as
    honest nulls rather than a crash.

No cache writes for the boards (pure builders); timeline idempotency uses a temp
file.

SOURCE OF TRUTH: data/fixtures/contract_cache.json, NOT the live cache. This
test used to score off data/cfbd_cache.json, which silently made its
final-state checks depend on the live cache holding a COMPLETED season. The
moment season.json flips, the new season has zero played games, and
"[wk14] every pick has games_remaining==0" fails on every push until December
— reading like a scoring regression when nothing regressed. Pinning a frozen
fixture is what CLAUDE.md P0 #2 asks for: a fixed fixture set must produce the
same scoring output regardless of what the live source is doing.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_output_shape.py
    python scripts/test_output_shape.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import scoring
import projector
import run_groups
import analytics

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


META_KEYS = {"group_id", "season", "as_of_week", "generated_at", "cache_fetched_at"}
STANDINGS_MGR = {"manager_id", "display_name", "banked_total", "floor", "ceiling", "rank", "picks"}
STANDINGS_PICK = {"team", "conference", "line", "direction", "banked_wins", "banked_losses",
                  "games_remaining", "banked_delta", "floor", "ceiling", "status"}
PROJ_MGR = {"manager_id", "display_name", "expected_total", "p05", "p50", "p95", "p_win_pool", "picks"}
PROJ_PICK = {"team", "conference", "line", "direction", "p_beat_line", "expected_delta",
             "expected_final_wins", "win_distribution",
             "remaining_games", "outlook", "pace"}
PROJ_GAME = {"week", "opponent", "home_away", "neutral", "p_win", "p_win_pct", "bucket"}
PROJ_OUTLOOK = {"likely_wins", "toss_ups", "likely_losses"}
PROJ_PACE = {"banked_games", "actual_wins", "expected_wins", "delta", "state"}
BUCKETS = {"likely_win", "toss_up", "likely_loss"}
TL_PICK = {"team", "banked_delta", "floor", "ceiling", "expected_delta", "p_beat_line"}

# --- Board 3 (analytics.json) ------------------------------------------------
ANALYTICS_TOP = {"meta", "race", "championship_odds", "best_worst", "paths",
                 "portfolio", "leverage"}
# Modules that must carry a "board" field. `leverage` is excluded on purpose:
# it is the reserved null this run, not a module yet.
ANALYTICS_MODULES = ("race", "championship_odds", "best_worst", "paths", "portfolio")
IDENT = {"manager_id", "display_name"}
RACE_MGR = IDENT | {"rank", "banked_total", "floor", "ceiling", "gap_to_leader",
                    "ceiling_remaining", "week_move"}
ODDS_MGR = IDENT | {"p_win_pool", "week_move"}
BW_ENTRY = IDENT | {"team", "conference", "line", "direction", "delta"}
BW_MGR = IDENT | {"mvp", "anchor"}
PATH_MGR = IDENT | {"state", "comparison"}
PATH_CMP = {"basis", "my_field", "my_value", "operator", "other_manager_id",
            "other_display_name", "other_field", "other_value"}
PATH_STATES = ("clinched", "controls_destiny", "needs_help", "eliminated")
PORT_MGR = IDENT | {"banked_total", "absolute_total", "picks"}
PORT_PICK = {"team", "conference", "line", "direction", "banked_delta", "floor",
             "ceiling", "status", "games_remaining", "share_of_delta"}


def validate_standings(st, label):
    check(f"[{label}] standings.meta keys", _has_keys(st.get("meta", {}), META_KEYS))
    ok_mgr = ok_pick = ok_inv = True
    for m in st["managers"]:
        ok_mgr &= _has_keys(m, STANDINGS_MGR)
        for p in m["picks"]:
            ok_pick &= _has_keys(p, STANDINGS_PICK)
            ok_pick &= p["status"] in ("LIVE", "CLINCHED", "DEAD")
            if p["games_remaining"] == 0:
                ok_inv &= (p["floor"] == p["ceiling"] == p["banked_delta"])
    check(f"[{label}] every manager has the required keys", ok_mgr)
    check(f"[{label}] every pick has the required keys + valid status", ok_pick)
    check(f"[{label}] invariant: games_remaining==0 => floor==ceiling==banked_delta", ok_inv)


def validate_projection(pr, label, final=False, banked_by=None):
    m0 = pr.get("meta", {})
    check(f"[{label}] projection.meta keys (+ratings)", _has_keys(m0, META_KEYS | {"ratings_source", "ratings_asof"}))
    check(f"[{label}] ratings_source == 'SP+'", m0.get("ratings_source") == "SP+")
    ok_mgr = ok_pick = ok_dist = ok_final = True
    ok_game = ok_outlook = ok_pace = ok_empty = True
    for m in pr["managers"]:
        ok_mgr &= _has_keys(m, PROJ_MGR)
        ok_mgr &= 0.0 <= m["p_win_pool"] <= 1.0
        for p in m["picks"]:
            ok_pick &= _has_keys(p, PROJ_PICK)

            # remaining_games: one row per remaining game, schedule order, and
            # the bucket tally must account for every one of them.
            games = p["remaining_games"]
            ok_game &= all(_has_keys(g, PROJ_GAME) for g in games)
            ok_game &= all(g["bucket"] in BUCKETS for g in games)
            ok_game &= all(0.0 <= g["p_win"] <= 1.0 for g in games)
            ok_game &= all(g["home_away"] in ("home", "away") for g in games)
            weeks = [g["week"] for g in games if g["week"] is not None]
            ok_game &= weeks == sorted(weeks)
            ol = p["outlook"]
            ok_outlook &= _has_keys(ol, PROJ_OUTLOOK)
            ok_outlook &= sum(ol.values()) == len(games)

            # pace: preseason is null (never 0), otherwise delta reconciles.
            pc = p["pace"]
            ok_pace &= _has_keys(pc, PROJ_PACE)
            if pc["banked_games"] == 0:
                ok_pace &= (pc["state"] == "preseason"
                            and pc["expected_wins"] is None
                            and pc["delta"] is None)
            else:
                ok_pace &= pc["state"] in ("ahead", "on_pace", "behind")
                ok_pace &= abs((pc["actual_wins"] - pc["expected_wins"])
                               - pc["delta"]) < 5e-3

            dist = p["win_distribution"]
            # emitted probs are 6-decimal rounded; tolerate rounding drift (a real
            # missing-mass bug moves the sum by >> 1e-4).
            ok_dist &= abs(sum(d["prob"] for d in dist) - 1.0) < 1e-4
            ok_dist &= all(_has_keys(d, {"wins", "prob"}) for d in dist)
            if final:
                ok_final &= p["p_beat_line"] in (0.0, 1.0)
                # Nothing left to play: no game rows, and every bucket empty.
                ok_empty &= (games == [] and sum(p["outlook"].values()) == 0)
                if banked_by is not None:
                    bd = banked_by.get((m["manager_id"], p["team"]))
                    ok_final &= (bd is not None and p["expected_delta"] == bd)
    check(f"[{label}] every manager has the required keys + valid p_win_pool", ok_mgr)
    check(f"[{label}] every pick has the required keys", ok_pick)
    check(f"[{label}] win_distribution sums to 1", ok_dist)
    check(f"[{label}] every remaining_games row is well-formed + week-ordered", ok_game)
    check(f"[{label}] outlook counts account for every remaining game", ok_outlook)
    check(f"[{label}] pace reconciles (null in preseason, delta exact otherwise)", ok_pace)
    if final:
        check(f"[{label}] final: p_beat_line in {{0,1}} AND expected_delta==banked_delta", ok_final)
        check(f"[{label}] final: remaining_games empty AND outlook all zero", ok_empty)


def validate_analytics_boards(an, label):
    """BOARD SEPARATION IS PART OF THE CONTRACT (docs/output-contract.md).

    Its own check, separate from the shape walk, because it is the field the
    renderer keys off to decide whether a projection LABEL is required. A module
    that silently loses its board, or gains a third value nobody handles, would
    otherwise ship model output dressed as banked fact — the one failure this
    file exists to make impossible."""
    ok_present = ok_valid = True
    for key in ANALYTICS_MODULES:
        mod = an.get(key)
        ok_present &= isinstance(mod, dict) and "board" in mod
        ok_valid &= isinstance(mod, dict) and mod.get("board") in analytics.VALID_BOARDS
    check(f"[{label}] every analytics module carries a 'board' field", ok_present,
          f"{len(ANALYTICS_MODULES)} modules")
    check(f"[{label}] every 'board' is 'exact' or 'projection'", ok_valid)
    # The one module that is model output, pinned by name: if the projection
    # board ever silently flips to "exact" the page stops labeling it.
    check(f"[{label}] championship_odds is board='projection'",
          an["championship_odds"]["board"] == "projection")
    check(f"[{label}] every other module is board='exact'",
          all(an[k]["board"] == "exact" for k in ANALYTICS_MODULES
              if k != "championship_odds"))


def validate_analytics(an, label, expect_odds=True):
    """SHAPE, not values. The numbers are standings arithmetic already pinned by
    validate_standings; what can silently rot here is the shape the renderer
    walks."""
    check(f"[{label}] analytics top-level keys", set(an.keys()) == ANALYTICS_TOP,
          f"got {sorted(an.keys())}")
    check(f"[{label}] analytics.meta keys (no draft_status / ratings_*)",
          _has_keys(an.get("meta", {}), META_KEYS) and set(an["meta"]) == META_KEYS)
    check(f"[{label}] leverage reserved null (this run)", an["leverage"] is None)

    validate_analytics_boards(an, label)

    # race
    race = an["race"]
    ok = _has_keys(race, {"board", "prior_week", "leader_id", "managers"})
    for m in race["managers"]:
        ok &= _has_keys(m, RACE_MGR)
        ok &= m["gap_to_leader"] >= 0                       # leader-relative, never negative
        ok &= m["ceiling_remaining"] >= 0                   # ceiling can't sit below banked
        ok &= m["week_move"] is None or isinstance(m["week_move"], int)
    check(f"[{label}] race shape + gap/ceiling_remaining non-negative", ok)

    # championship_odds (projection board)
    odds = an["championship_odds"]
    ok = _has_keys(odds, {"board", "prior_week", "available", "managers"})
    ok &= odds["available"] is expect_odds
    probs = []
    for m in odds["managers"]:
        ok &= _has_keys(m, ODDS_MGR)
        p = m["p_win_pool"]
        ok &= p is None or 0.0 <= p <= 1.0
        # week_move is a probability DELTA here, not a rank move (contract).
        ok &= m["week_move"] is None or -1.0 <= m["week_move"] <= 1.0
        probs.append(-1.0 if p is None else p)
    # emitted pre-sorted by p_win_pool desc — the renderer iterates, it never sorts
    ok &= probs == sorted(probs, reverse=True)
    check(f"[{label}] championship_odds shape + pre-sorted by p_win_pool desc", ok)

    # best_worst — every field an array, always, so ties render as ties
    bw = an["best_worst"]
    ok = _has_keys(bw, {"board", "steal", "bust", "managers"})
    ok &= isinstance(bw["steal"], list) and isinstance(bw["bust"], list)
    ok &= all(_has_keys(e, BW_ENTRY) for e in bw["steal"] + bw["bust"])
    ok &= all(e["delta"] > 0 for e in bw["steal"])          # steal is sign-gated
    ok &= all(e["delta"] < 0 for e in bw["bust"])           # bust is sign-gated
    for m in bw["managers"]:
        ok &= _has_keys(m, BW_MGR)
        ok &= isinstance(m["mvp"], list) and isinstance(m["anchor"], list)
        ok &= all(_has_keys(e, BW_ENTRY) for e in m["mvp"] + m["anchor"])
    check(f"[{label}] best_worst shape, arrays-always, steal/bust sign-gated", ok)

    # paths — closed state set, and a comparison for every state
    paths = an["paths"]
    ok = _has_keys(paths, {"board", "leader_id", "managers"})
    for m in paths["managers"]:
        ok &= _has_keys(m, PATH_MGR)
        ok &= m["state"] in PATH_STATES                     # closed set — no fallthrough
        ok &= _has_keys(m["comparison"], PATH_CMP)          # never an unexplained label
    check(f"[{label}] paths shape, closed state set, comparison always present", ok)

    # portfolio — the concentration number, null-safe
    port = an["portfolio"]
    ok = _has_keys(port, {"board", "managers"})
    for m in port["managers"]:
        ok &= _has_keys(m, PORT_MGR)
        shares = []
        for p in m["picks"]:
            ok &= _has_keys(p, PORT_PICK)
            s = p["share_of_delta"]
            # null (not 0, not a divide error) exactly when there is no swing yet
            ok &= (s is None) if m["absolute_total"] == 0 else (0.0 <= s <= 1.0)
            if s is not None:
                shares.append(s)
        ok &= (not shares) or abs(sum(shares) - 1.0) < 1e-4  # shares partition the swing
    check(f"[{label}] portfolio shape, share_of_delta null-safe + sums to 1", ok)


def validate_timeline_snapshot(snap, label):
    ok = isinstance(snap.get("as_of_week"), int) and "generated_at" in snap
    for m in snap["managers"]:
        ok &= _has_keys(m, {"manager_id", "p_win_pool", "picks"})
        for p in m["picks"]:
            ok &= _has_keys(p, TL_PICK)
    check(f"[{label}] timeline snapshot shape", ok)


_BOARDS = None


def _boards():
    """The four boards main() built once off the frozen contract fixture.

    pin_contract_fixture() runs on EVERY entry, not just the first: it re-points
    the whole process at data/fixtures/contract_cache.json and conftest.py
    restores utils' globals after each test, so a memoised board set alone would
    leave later tests reading the live cache -- which is exactly the silent
    source-drift this file's SOURCE OF TRUTH note exists to prevent. Pinning is
    three globals and one json load; the board derivation is what gets cached.
    """
    global _BOARDS
    season = utils.pin_contract_fixture()
    if _BOARDS is None:
        print(f"  (scoring off the frozen contract fixture, season {season})")
        config, picks = utils.load_group(utils.TEST_GROUP_ID)
        st6 = scoring.build_standings(config, picks, as_of_week=6)
        pr6 = projector.build_projection(config, picks, as_of_week=6)
        st14 = scoring.build_standings(config, picks, as_of_week=14)
        pr14 = projector.build_projection(config, picks, as_of_week=14)
        _BOARDS = {"season": season, "config": config, "picks": picks,
                   "st6": st6, "pr6": pr6, "st14": st14, "pr14": pr14}
    return _BOARDS


def _tl14(b):
    """A week-6 snapshot to measure week 14's moves against."""
    return {"group_id": b["config"]["group_id"], "season": b["season"],
            "snapshots": [run_groups.build_snapshot(b["st6"], b["pr6"], 6)]}


def test_midseason_boards():
    """Mid-season (week 6): LIVE picks present."""
    print("\n--- Mid-season (week 6): LIVE picks present ---")
    b = _boards()
    st6, pr6 = b["st6"], b["pr6"]
    validate_standings(st6, "wk6")
    validate_projection(pr6, "wk6")
    live = any(p["status"] == "LIVE" for m in st6["managers"] for p in m["picks"])
    check("[wk6] at least one LIVE pick (mid-season state is real)", live)


def test_final_week_boards_agree():
    """Final (week 14): every slate complete -> Board 1 and Board 2 must agree."""
    print("\n--- Final (week 14): every slate complete -> two boards must agree ---")
    b = _boards()
    st14, pr14 = b["st14"], b["pr14"]
    banked_by = {(m["manager_id"], p["team"]): p["banked_delta"]
                 for m in st14["managers"] for p in m["picks"]}
    zero_width = all(p["games_remaining"] == 0 for m in st14["managers"] for p in m["picks"])
    check("[wk14] every pick has games_remaining==0 (season complete for the slate)", zero_width)
    validate_standings(st14, "wk14")
    validate_projection(pr14, "wk14", final=True, banked_by=banked_by)


def test_analytics_week6_no_timeline():
    """Board 3 with no timeline: week_move must be null everywhere, NOT zero."""
    print("\n--- Board 3: analytics, week 6 (no timeline) ---")
    b = _boards()
    an6 = analytics.build_analytics(b["config"], b["st6"], b["pr6"],
                                    timeline=None, as_of_week=6)
    validate_analytics(an6, "wk6-analytics")
    check("[wk6-analytics] no timeline => week_move null (never 0)",
          all(m["week_move"] is None for m in an6["race"]["managers"])
          and an6["race"]["prior_week"] is None)


def test_analytics_week14_week_move():
    """The only place week_move is exercised end to end against a real prior week."""
    print("\n--- Board 3: analytics, week 14 (week-6 snapshot in hand) ---")
    b = _boards()
    config, st14, pr14 = b["config"], b["st14"], b["pr14"]
    tl14 = _tl14(b)
    an14 = analytics.build_analytics(config, st14, pr14, timeline=tl14, as_of_week=14)
    validate_analytics(an14, "wk14-analytics")
    check("[wk14-analytics] prior snapshot => week_move is an int, prior_week=6",
          an14["race"]["prior_week"] == 6
          and all(isinstance(m["week_move"], int) for m in an14["race"]["managers"]))
    # Ranks are a permutation, so the places gained across the group must net to 0.
    check("[wk14-analytics] week_move sums to 0 across the group (ranks permute)",
          sum(m["week_move"] for m in an14["race"]["managers"]) == 0)
    # The current week's own snapshot must NOT be the comparison point — that is
    # what would turn every move into a confident, wrong 0.
    tl14_self = {"group_id": config["group_id"], "season": b["season"],
                 "snapshots": tl14["snapshots"] + [run_groups.build_snapshot(st14, pr14, 14)]}
    an14b = analytics.build_analytics(config, st14, pr14, timeline=tl14_self, as_of_week=14)
    check("[wk14-analytics] current week's own snapshot is not the baseline",
          an14b["race"]["prior_week"] == 6
          and [m["week_move"] for m in an14b["race"]["managers"]]
              == [m["week_move"] for m in an14["race"]["managers"]])


def test_analytics_degraded_projector():
    """Board 3 degrades the way Board 2 does — it does not take the page down."""
    print("\n--- Board 3: analytics, projector down ---")
    b = _boards()
    st14 = b["st14"]
    an_nop = analytics.build_analytics(b["config"], st14, None,
                                       timeline=_tl14(b), as_of_week=14)
    validate_analytics(an_nop, "wk14-analytics-degraded", expect_odds=False)
    check("[wk14-analytics-degraded] projector down => p_win_pool/week_move all null",
          all(m["p_win_pool"] is None and m["week_move"] is None
              for m in an_nop["championship_odds"]["managers"]))
    check("[wk14-analytics-degraded] exact modules still populated",
          len(an_nop["race"]["managers"]) == len(st14["managers"])
          and len(an_nop["portfolio"]["managers"]) == len(st14["managers"]))


def test_analytics_predraft_zero_state():
    """Pre-draft / dummy-data / no-scored-weeks: honest nulls, never a crash.

    Two managers tied at zero with an unplayed pick each — the literal shape of
    a group whose draft is in but whose season has not started.
    """
    print("\n--- Board 3: analytics, pre-draft zero state ---")
    b = _boards()

    def _zero_mgr(mid, rank):
        return {"manager_id": mid, "display_name": mid.title(), "banked_total": 0.0,
                "floor": 0.0, "ceiling": 0.0, "rank": rank,
                "picks": [{"team": "Ohio State", "conference": "Big Ten", "line": 9.5,
                           "direction": "O", "banked_wins": 0, "banked_losses": 0,
                           "games_remaining": 12, "banked_delta": 0.0, "floor": 0.0,
                           "ceiling": 0.0, "status": "LIVE"}]}
    st_zero = {"meta": b["st14"]["meta"], "managers": [_zero_mgr("a", 1), _zero_mgr("b", 2)]}
    an_zero = analytics.build_analytics(b["config"], st_zero, None,
                                        timeline=None, as_of_week=1)
    validate_analytics(an_zero, "predraft-analytics", expect_odds=False)
    check("[predraft-analytics] share_of_delta is null, not a divide error",
          all(p["share_of_delta"] is None
              for m in an_zero["portfolio"]["managers"] for p in m["picks"]))
    check("[predraft-analytics] steal/bust empty when nothing has swung",
          an_zero["best_worst"]["steal"] == [] and an_zero["best_worst"]["bust"] == [])
    check("[predraft-analytics] every manager still lands in a valid path state",
          all(m["state"] in PATH_STATES for m in an_zero["paths"]["managers"]))


def test_analytics_empty_group():
    """A group with no managers at all (a config entered before its draft)."""
    print("\n--- Board 3: analytics, group with no managers ---")
    b = _boards()
    an_empty = analytics.build_analytics(b["config"], {"managers": []}, None,
                                         None, as_of_week=1)
    validate_analytics(an_empty, "empty-analytics", expect_odds=False)
    check("[empty-analytics] no managers => empty modules, no leader",
          an_empty["race"]["managers"] == [] and an_empty["race"]["leader_id"] is None
          and an_empty["paths"]["managers"] == [])


def test_timeline_append_only_and_idempotent():
    """Timeline: append-only + idempotent on the effective week.

    WEB_DATA_DIR is redirected into a TemporaryDirectory for the whole block, so
    nothing here writes to docs/data/ (CLAUDE.md P2 #14).
    """
    print("\n--- Timeline: append-only + idempotent on the effective week ---")
    b = _boards()
    config, st6, pr6, st14, pr14 = b["config"], b["st6"], b["pr6"], b["st14"], b["pr14"]
    with tempfile.TemporaryDirectory() as td:
        tl_path = Path(td) / "timeline.json"
        # monkeypatch the write target to the temp dir
        orig = utils.WEB_DATA_DIR
        try:
            utils.WEB_DATA_DIR = Path(td)
            (Path(td) / config["group_id"]).mkdir(parents=True, exist_ok=True)
            snap6 = run_groups.build_snapshot(st6, pr6, 6)
            validate_timeline_snapshot(snap6, "wk6")
            run_groups.append_timeline(config, snap6)
            run_groups.append_timeline(config, snap6)          # re-run same week
            tl = utils.load_json(Path(td) / config["group_id"] / "timeline.json")
            check("timeline idempotent: re-run week 6 does not duplicate",
                  len([s for s in tl["snapshots"] if s["as_of_week"] == 6]) == 1,
                  f"{len(tl['snapshots'])} snapshot(s)")
            run_groups.append_timeline(config, run_groups.build_snapshot(st14, pr14, 14))
            tl = utils.load_json(Path(td) / config["group_id"] / "timeline.json")
            weeks = [s["as_of_week"] for s in tl["snapshots"]]
            check("timeline append-only + sorted", weeks == sorted(weeks) and weeks == [6, 14],
                  f"weeks={weeks}")
        finally:
            utils.WEB_DATA_DIR = orig


def main():
    test_midseason_boards()
    test_final_week_boards_agree()
    test_analytics_week6_no_timeline()
    test_analytics_week14_week_move()
    test_analytics_degraded_projector()
    test_analytics_predraft_zero_state()
    test_analytics_empty_group()
    test_timeline_append_only_and_idempotent()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
