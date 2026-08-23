#!/usr/bin/env python3
"""
test_preseason_baseline.py — Validates preseason_baseline.py.

The baseline is written ONCE, before the first 2026 kickoff, and can never be
rebuilt afterwards: CFBD's SP+ weights results the moment games are final, and
/ratings/sp only ever returns the CURRENT rating for a season. So the two things
worth testing are the guards that stop it being written wrong, and the arithmetic
that decides what gets frozen.

Asserts:
  - the freeze guard fires on a non-zero completed-game count (and --force does
    NOT bypass it — that guard is deliberately absolute),
  - the freeze guard fires when the output already exists, and --force is the
    one way past THAT one,
  - schedule length is read off the real slate: the 8 eleven-game teams and the
    one thirteen-game team get their true counts, never a hardcoded 12,
  - expected wins are the projector's own numbers — pinned by substituting
    projector.game_win_prob and watching the sum follow it — so this file can
    never quietly grow a second, parallel probability model,
  - expected wins land in a sane range for a known team.

DURABILITY: these checks must keep working after 2026-08-27, when the live cache
stops being preseason. So the guard and reuse checks run on in-memory fixtures
(playbook rule 14), the schedule-length checks use games_scheduled (which counts
played and unplayed alike), and the sane-range check reads the COMMITTED frozen
artifact rather than recomputing it. Nothing here depends on the season not
having started.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_preseason_baseline.py
    python scripts/test_preseason_baseline.py
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import projector
import preseason_baseline as PB

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _exit_code(fn):
    """Run fn() with output captured; return its SystemExit code, or None."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            fn()
    except SystemExit as e:
        return e.code, out.getvalue() + err.getvalue()
    return None, out.getvalue() + err.getvalue()


def _game(completed):
    return {"completed": completed, "week": 1,
            "home_team": "A", "away_team": "B",
            "home_points": 21 if completed else None,
            "away_points": 17 if completed else None}


# --- Guard 1: the season has started -----------------------------------------

def test_season_started_guard():
    print("\nFreeze guard 1 (the season has started):")
    code, msg = _exit_code(lambda: PB.assert_preseason(
        {"games": [_game(False) for _ in range(5)]}))
    check("preseason cache passes the guard", code is None, f"exit={code}")

    code, msg = _exit_code(lambda: PB.assert_preseason(
        {"games": [_game(True), _game(False), _game(False)]}))
    check("one completed game trips the guard", code == PB.EXIT_SEASON_STARTED,
          f"exit={code}")
    check("the guard says WHY it refused",
          "::error::" in msg and "completed game" in msg, msg.strip()[:70])
    check("the guard names the count it saw", "1 completed game" in msg,
          msg.strip()[:70])

    code, _ = _exit_code(lambda: PB.assert_preseason(
        {"games": [_game(True) for _ in range(700)]}))
    check("a full season of results also trips it",
          code == PB.EXIT_SEASON_STARTED, f"exit={code}")

    # The signature carries no force parameter at all, so there is no way to
    # call past it. Pinned because a later "just add --force here too" would
    # silently swap a results-contaminated rating in under the same filename.
    import inspect
    params = list(inspect.signature(PB.assert_preseason).parameters)
    check("there is deliberately no --force path through this guard",
          params == ["cache"], f"params={params}")

    check("an empty cache does not trip it (nothing played)",
          _exit_code(lambda: PB.assert_preseason({"games": []}))[0] is None)
    check("a cache with no games key does not raise",
          _exit_code(lambda: PB.assert_preseason({}))[0] is None)


# --- Guard 2: already frozen --------------------------------------------------

def test_already_frozen_guard():
    print("\nFreeze guard 2 (already frozen):")
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "preseason_baseline_2026.json"
        code, _ = _exit_code(lambda: PB.assert_not_frozen(missing, False))
        check("a missing output file passes the guard", code is None, f"exit={code}")

        existing = Path(td) / "already_there.json"
        existing.write_text('{"teams": {}}', encoding="utf-8")
        code, msg = _exit_code(lambda: PB.assert_not_frozen(existing, False))
        check("an existing output file trips the guard",
              code == PB.EXIT_ALREADY_FROZEN, f"exit={code}")
        check("the guard says WHY it refused",
              "::error::" in msg and "FROZEN" in msg, msg.strip()[:70])
        check("the guard names the file it refused to overwrite",
              existing.name in msg, msg.strip()[:70])

        code, _ = _exit_code(lambda: PB.assert_not_frozen(existing, True))
        check("--force is the one way past THIS guard", code is None, f"exit={code}")

        check("the guard never deletes or truncates what it refused",
              json.loads(existing.read_text(encoding="utf-8")) == {"teams": {}})

    # The two guards are distinguishable by exit code, so a caller can tell
    # "too late" apart from "already done".
    check("the two guards use distinct exit codes",
          PB.EXIT_SEASON_STARTED != PB.EXIT_ALREADY_FROZEN,
          f"{PB.EXIT_SEASON_STARTED} vs {PB.EXIT_ALREADY_FROZEN}")
    check("both guard exit codes are non-zero",
          PB.EXIT_SEASON_STARTED and PB.EXIT_ALREADY_FROZEN)


# --- Schedule length is read, never assumed ----------------------------------

# Off the real 2026 slate. games_scheduled counts played and unplayed alike, so
# these stay true all season and this check does not rot at kickoff.
ELEVEN_GAME_TEAMS = ["Boise State", "Colorado State", "Fresno State",
                     "Oregon State", "San Diego State", "Texas State",
                     "Utah State", "Washington State"]
THIRTEEN_GAME_TEAM = "San José State"


def test_schedule_lengths():
    print("\nSchedule length is read off the slate, never assumed:")
    ref = utils.load_json(PB.REFERENCE_PATH)["teams"]
    sp = utils.season_sp_ratings(utils.get_season())

    lengths = {}
    for team in ref:
        row = PB.team_baseline(team, ref[team], sp)
        lengths[team] = row["games_scheduled"]

    for team in ELEVEN_GAME_TEAMS:
        check(f"11-game team gets 11, not 12 — {team}", lengths.get(team) == 11,
              f"got {lengths.get(team)}")
    check(f"13-game team gets 13, not 12 — {THIRTEEN_GAME_TEAM}",
          lengths.get(THIRTEEN_GAME_TEAM) == 13,
          f"got {lengths.get(THIRTEEN_GAME_TEAM)}")

    hist = {}
    for n in lengths.values():
        hist[n] = hist.get(n, 0) + 1
    check("the slate is NOT uniformly 12", len(hist) > 1, f"histogram={hist}")
    check("every team has a real schedule (no zero-game rows)",
          all(n > 0 for n in lengths.values()), f"histogram={hist}")
    check("no team exceeds 13 regular-season games",
          all(n <= 13 for n in lengths.values()), f"histogram={hist}")
    check("exactly 8 teams play 11", hist.get(11) == 8, f"got {hist.get(11)}")
    check("exactly 1 team plays 13", hist.get(13) == 1, f"got {hist.get(13)}")


# --- The number IS the projector's ------------------------------------------

def test_projector_reuse():
    """Substitute the projector's win-prob function and watch expected_wins
    follow. If this file ever grows its own probability model, the substitution
    stops moving the answer and this fails."""
    print("\nThe expected-win number is the projector's:")
    fake_state = {
        "team": "Fixture U",
        "games_scheduled": 10,
        "games_played": 0,
        "games_remaining": 10,
        "remaining_games": [{"opponent": f"Opp {i}", "home_away": "home",
                             "week": i + 1, "neutral": False} for i in range(10)],
    }
    real_team_state, real_prob = PB.utils.team_state, projector.game_win_prob
    PB.utils.team_state = lambda *a, **k: fake_state
    try:
        for p in (0.0, 0.25, 0.5, 1.0):
            projector.game_win_prob = (lambda p: (lambda *a, **k: p))(p)
            row = PB.team_baseline("Fixture U", {"win_total": 5.5}, {})
            check(f"expected_wins tracks the projector's p={p} over 10 games",
                  abs(row["expected_wins"] - 10 * p) < 1e-9,
                  f"got {row['expected_wins']}")
        projector.game_win_prob = (lambda *a, **k: 0.5)
        row = PB.team_baseline("Fixture U", {"win_total": 5.5}, {})
        check("delta_vs_vegas = expected_wins - the line",
              abs(row["delta_vs_vegas"] - (5.0 - 5.5)) < 1e-9,
              f"got {row['delta_vs_vegas']}")
        row = PB.team_baseline("Fixture U", {"win_total": None}, {})
        check("a team with no line gets a null delta, not a crash",
              row["delta_vs_vegas"] is None)
    finally:
        PB.utils.team_state, projector.game_win_prob = real_team_state, real_prob

    # And the real function is genuinely the projector's, not a local copy.
    check("team_baseline routes through projector.remaining_win_probs",
          projector.remaining_win_probs.__module__ == "projector")
    check("no local win-probability model in preseason_baseline.py",
          "exp(" not in Path(PB.__file__).read_text(encoding="utf-8"))


# --- The frozen artifact ------------------------------------------------------

def test_frozen_artifact():
    """Reads what is COMMITTED. Durable: it never recomputes, so it keeps
    passing after the cache stops being preseason."""
    print("\nThe frozen artifact:")
    if not PB.OUTPUT_PATH.exists():
        check("the frozen baseline is committed", False,
              f"{PB.OUTPUT_PATH.name} not found — run preseason_baseline.py")
        return
    b = utils.load_json(PB.OUTPUT_PATH)
    meta, teams = b.get("meta", {}), b.get("teams", {})

    check("meta records the season", meta.get("season") == utils.get_season(),
          f"got {meta.get('season')}")
    check("meta records the SP+ vintage date",
          bool((meta.get("sp_vintage") or {}).get("date")),
          str((meta.get("sp_vintage") or {}).get("date")))
    check("meta records it was frozen with zero games played",
          (meta.get("sp_vintage") or {}).get("completed_games_at_freeze") == 0)
    check("meta records the generation timestamp", bool(meta.get("generated_at")))
    proj = meta.get("projector") or {}
    check("meta records the projector scale",
          isinstance(proj.get("win_prob_points_scale"), (int, float)),
          str(proj.get("win_prob_points_scale")))
    check("meta records the projector HFA",
          isinstance(proj.get("home_field_advantage_pts"), (int, float)),
          str(proj.get("home_field_advantage_pts")))
    check("meta names the function it reused",
          proj.get("function") == "projector.game_win_prob",
          str(proj.get("function")))

    check("every FBS team in the reference is present",
          len(teams) == len(utils.load_json(PB.REFERENCE_PATH)["teams"]),
          f"{len(teams)} rows")
    check("expected wins never exceed the games played for",
          all(r["expected_wins"] <= r["games_scheduled"] + 1e-9
              for r in teams.values()))
    check("expected wins are never negative",
          all(r["expected_wins"] >= 0 for r in teams.values()))
    check("delta is consistently xW minus the line",
          all(abs(r["delta_vs_vegas"] - (r["expected_wins"] - r["vegas_win_total"]))
              < 1e-3 for r in teams.values() if r["vegas_win_total"] is not None))

    # A known team, sanity-ranged rather than pinned to a literal: SP+'s #1 in
    # a 12-game Big Ten season should land clearly above .500 and below perfect.
    osu = teams.get("Ohio State", {})
    check("Ohio State plays 12", osu.get("games_scheduled") == 12,
          f"got {osu.get('games_scheduled')}")
    check("Ohio State expected wins are sane (8.0-11.5)",
          8.0 <= osu.get("expected_wins", -1) <= 11.5,
          f"got {osu.get('expected_wins')}")
    check("Ohio State sits within 2 wins of its market line",
          abs(osu.get("delta_vs_vegas", 99)) <= 2.0,
          f"delta={osu.get('delta_vs_vegas')}")

    # The whole board should be plausible, not just one team.
    deltas = [r["delta_vs_vegas"] for r in teams.values()
              if r["delta_vs_vegas"] is not None]
    check("no team disagrees with the market by more than 3 wins",
          all(abs(d) <= 3.0 for d in deltas),
          f"max |delta| = {max(abs(d) for d in deltas):.2f}")
    check("SP+ and the market broadly agree (mean |delta| < 1)",
          sum(abs(d) for d in deltas) / len(deltas) < 1.0,
          f"mean |delta| = {sum(abs(d) for d in deltas) / len(deltas):.3f}")


def test_uniform_fields_not_forked():
    """The uniform-field rule has exactly ONE implementation (de-fork guard).

    preseason_baseline.py used to carry its own _uniform_fields under a "same
    contract as build_week_packet.uniform_profile_fields" comment. Two bodies
    behind one contract is the drift setup the playbook's one-engine rule exists
    to stop: the Week 0 packet and the live packet would start disagreeing about
    which fields distinguish nobody, and the prompt's persona-rule-7 guard would
    silently protect one path and not the other.

    Three layers, because each catches a different way of re-forking:
      1. IDENTITY   — the two names resolve to the SAME function object. Fails
                      the moment anyone re-defines a local copy.
      2. SOURCE     — no second `def ...uniform...` body in preseason_baseline.
                      Catches a copy under a fresh name that identity would miss.
      3. BEHAVIOUR  — both entry points return byte-identical output on one
                      shared fixture set, including the edge cases (<2 managers,
                      non-scalar values, a field that varies by one manager).
    """
    import build_week_packet as BWP

    check("the two entry points are the SAME function object",
          PB.uniform_profile_fields is BWP.uniform_profile_fields,
          f"preseason={PB.uniform_profile_fields!r} live={BWP.uniform_profile_fields!r}")

    src = Path(PB.__file__).read_text(encoding="utf-8")
    dupes = [ln.strip() for ln in src.splitlines()
             if ln.startswith("def ") and "uniform" in ln]
    check("preseason_baseline defines no uniform-field function of its own",
          not dupes, "; ".join(dupes))

    # Same fixtures through both names. Byte-identical, not merely equal-ish:
    # json.dumps pins key order and value types, so a copy that returned
    # {'x': 1.0} where the original returns {'x': 1} would still be caught.
    fixtures = {
        "all four share every field": {
            "a": {"picks_alive": 0, "conference_spread": 4},
            "b": {"picks_alive": 0, "conference_spread": 4},
            "c": {"picks_alive": 0, "conference_spread": 4},
            "d": {"picks_alive": 0, "conference_spread": 4},
        },
        "one manager breaks one field": {
            "a": {"picks_alive": 0, "conference_spread": 4},
            "b": {"picks_alive": 1, "conference_spread": 4},
        },
        "nothing shared": {"a": {"picks_alive": 0}, "b": {"picks_alive": 2}},
        "single manager is trivially uniform -> {}": {"a": {"picks_alive": 0}},
        "empty": {},
        "non-scalars are skipped": {
            "a": {"best_pick": {"team": "Texas"}, "avg_line": None, "n": 3},
            "b": {"best_pick": {"team": "Texas"}, "avg_line": None, "n": 3},
        },
        "bool and int are both scalars": {
            "a": {"flag": True, "n": 2}, "b": {"flag": True, "n": 2},
        },
    }
    for label, profiles in fixtures.items():
        left = json.dumps(PB.uniform_profile_fields(profiles), sort_keys=True)
        right = json.dumps(BWP.uniform_profile_fields(profiles), sort_keys=True)
        check(f"identical uniform-field output — {label}", left == right,
              f"preseason={left} live={right}")


def main():
    print("preseason_baseline.py \u2014 freeze guards, schedule truth, projector reuse")
    test_season_started_guard()
    test_already_frozen_guard()
    test_schedule_lengths()
    test_projector_reuse()
    test_frozen_artifact()
    test_uniform_fields_not_forked()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
