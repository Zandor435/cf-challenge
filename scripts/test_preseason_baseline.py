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


def test_coda_excludes_lead_subject():
    """Week 0's shipped bug, pinned against the REAL committed panel board.

    The column filed the Blaine/Chris feud over Texas 9.5 as Beat 1 and then
    made Chris, on Texas, the Worst Pick coda -- the same subject twice. Nothing
    in the packet forbade it: Chris's Texas over genuinely was the lowest
    market_gap on the board, so the coda's own selection rule pointed straight
    at the manager the lead had just spent 300 words on.

    This asserts against the live committed picks rather than a fixture, because
    the fixture version of this test is already in test_week_packet.py and what
    could regress HERE is the wiring: selecting worst_pick before the storylines
    exist, or forgetting to pass storylines[0] in.
    """
    print("\nCoda excludes the One Big Thing subject:")
    packet = PB.build_week0_packet("panel")

    lead = packet["storylines"][0]
    coda = packet["worst_pick_on_the_board"]
    excl = packet["coda_exclusion"]
    lead_mgrs, lead_teams = set(excl["lead_managers"]), set(excl["lead_teams"])

    check("the lead is still the Blaine/Chris collision over Texas",
          lead["type"] == "collision"
          and set(lead["managers"]) == {"blaine", "chris"}
          and {p["team"] for p in lead["picks"]} == {"Texas"},
          f"lead={lead['type']} {lead['managers']}")

    # The regression itself, stated two ways so the failure message is obvious
    # whichever dimension breaks.
    check("the coda's MANAGER is not the lead's",
          coda["manager_id"] not in lead_mgrs,
          f"coda manager={coda['manager_id']} lead={sorted(lead_mgrs)}")
    check("the coda's TEAM is not the lead's",
          coda["team"] not in lead_teams,
          f"coda team={coda['team']} lead={sorted(lead_teams)}")
    check("specifically: the coda is NOT chris on Texas (the shipped bug)",
          not (coda["manager_id"] == "chris" and coda["team"] == "Texas"),
          f"coda={coda['manager_id']} on {coda['team']}")

    # It must still be a real, computed choice -- not just "anything but Chris".
    check("the coda is still chosen by market_gap, never by the model",
          "market_gap" in coda["selected_by"]
          and "never chosen by the model" in coda["selected_by"],
          f"selected_by={coda['selected_by']!r}")
    check("the exclusion actually cost candidates (it is not a no-op)",
          excl["excluded"] > 0, f"excluded={excl['excluded']}")
    check("the clean path was taken -- no forced collision on this board",
          excl["collision_forced"] is False, f"{excl}")

    # And the coda is genuinely the worst SURVIVING pick, not an arbitrary one:
    # re-derive the choice off the packet's own board and demand the same answer.
    survivors = [(r["market_gap"], r["team"], m["manager_id"])
                 for m in packet["managers"] for r in m["picks"]
                 if m["manager_id"] not in lead_mgrs and r["team"] not in lead_teams]
    check("there were non-colliding picks to choose from", bool(survivors),
          f"{len(survivors)} survivor(s)")
    if survivors:
        best = min(survivors)
        check("the coda is the LOWEST market_gap among non-colliding picks",
              (coda["market_gap"], coda["team"], coda["manager_id"]) == best,
              f"coda=({coda['market_gap']}, {coda['team']}, "
              f"{coda['manager_id']}) best={best}")


def test_every_collision_reaches_the_column():
    """Family drafted FOUR head-to-head conflicts; all four must be candidates.

    Georgia 9.5, Auburn 6.5, Baylor 6.5 and Mississippi State 4.5 are each held
    by two managers on opposite sides -- the only sharing the section-5 gate
    allows, and the whole reason that group's board is interesting.
    MAX_PER_PRESEASON_TYPE capped the type at two, so two of the four were
    dropped, and WHICH two was decided by SP+ arithmetic rather than by anything
    that happened at the draft.

    Asserted against the REAL committed board, not a fixture, because what can
    regress is the interaction between three separate limits (the per-type cap,
    the total budget and the sort), and a fixture would only pin the one under
    test. Panel rides along as the no-change control: it drafted one collision,
    so every limit here is a no-op for it.
    """
    print("\nEvery head-to-head collision reaches the column:")
    packet = PB.build_week0_packet("family")

    drafted = {(c["team"], c["line"]) for c in packet["collisions"]}
    expected = {("Georgia", 9.5), ("Auburn", 6.5), ("Baylor", 6.5),
                ("Mississippi State", 4.5)}
    check("the packet detects all four drafted conflicts", drafted == expected,
          f"detected={sorted(drafted)}")

    told = {p["team"] for s in packet["storylines"] if s["type"] == "collision"
            for p in s["picks"]}
    check("all four are storyline CANDIDATES, not just a buried block",
          told == {t for t, _ in expected}, f"in storylines={sorted(told)}")

    # Uncapping one type must not eat the list: the other angles still file.
    others = [s["type"] for s in packet["storylines"] if s["type"] != "collision"]
    check("non-collision angles still make the list",
          len(others) >= PB.MIN_NON_COLLISION_SLOTS, f"others={others}")

    # Both sides of every conflict come off picks.json, not off a label.
    picks = {(pk["manager"], pk["team"]): pk["direction"]
             for pk in utils.load_group("family")[1]}
    mismatched = [
        (c["team"], s["manager_id"], s["direction"])
        for c in packet["collisions"] for s in c["sides"]
        if picks.get((s["manager_id"], c["team"])) != s["direction"]]
    check("each side's direction is the manager's ACTUAL pick",
          not mismatched, f"{mismatched}")
    check("every conflict has exactly two sides, and they oppose",
          all(len(c["sides"]) == 2
              and {s["direction"] for s in c["sides"]} == {"O", "U"}
              for c in packet["collisions"]))

    # The control: one collision means every limit in this change is inert.
    panel = PB.build_week0_packet("panel")
    check("panel is unaffected -- one collision, list still capped at the max",
          len(panel["storylines"]) <= PB.MAX_PRESEASON_STORYLINES
          and sum(1 for s in panel["storylines"] if s["type"] == "collision") == 1,
          f"{[s['type'] for s in panel['storylines']]}")


def test_conference_spread_suppressed_where_the_rule_is_advisory():
    """A stat is only a distinction when the group wrote a rule for it.

    Family's min_distinct_conferences is 1, so every legal roster clears it and
    the spread measures nothing anyone agreed to -- but the numbers VARY (3 and
    4 conferences, 25% and 50% shares), so uniform_profile_fields cannot see
    them, and a varying number is exactly the shape a column reads as a personal
    trait. Panel wrote a real minimum of 3, so nothing is suppressed there by
    rule; its largest_conference_share is already withheld by the uniform
    mechanism instead, which is the pairing under test.
    """
    print("\nConference spread is withheld where the rule is advisory:")
    family = PB.build_week0_packet("family")
    panel = PB.build_week0_packet("panel")

    check("family suppresses BOTH conference-spread fields",
          set(family["suppressed_profile_fields"])
          == set(PB.CONFERENCE_SPREAD_FIELDS),
          f"{sorted(family['suppressed_profile_fields'])}")
    check("and says why, rather than shipping a bare list",
          all(isinstance(v, str) and "advisory" in v
              for v in family["suppressed_profile_fields"].values()))
    check("the fields it withholds genuinely VARY (uniform cannot catch them)",
          any(len({p[f] for p in family["manager_profiles"].values()}) > 1
              for f in PB.CONFERENCE_SPREAD_FIELDS),
          f"uniform={family['uniform_profile_fields']}")

    check("panel suppresses nothing by rule -- it wrote a real minimum",
          panel["suppressed_profile_fields"] == {},
          f"{panel['suppressed_profile_fields']}")
    check("panel's share is withheld by the UNIFORM mechanism instead",
          "largest_conference_share" in panel["uniform_profile_fields"],
          f"{panel['uniform_profile_fields']}")

    # The rule, not the group name: a written minimum above 1 suppresses nothing.
    check("the trigger is the written minimum, not the slug",
          bool(PB.suppressed_profile_fields(1))
          and not PB.suppressed_profile_fields(3)
          and PB.suppressed_profile_fields(None) == {})


def test_persona_material_tolerates_absence():
    """Missing persona fields mean less material -- never a crash, never a rule.

    UNAUTHORED, not withheld. This used to lean on the `straight` register,
    which nulled fatal_flaw, running_gag and rival for John, Rachel and Vic;
    that register was retired on 2026-08-25 and all three now author the full
    set. The property it was really testing has nothing to do with tone and
    still holds: a manager who simply never had a field written must reach the
    packet with that key ABSENT, because a packet full of "fatal_flaw": null is
    a checklist of what each manager lacks rather than a description of who
    they are.

    Holly is the live case -- roast like everyone else, with no fatal flaw ever
    authored. If she ever gets one, move this to whoever is still short a
    field rather than deleting it; the packet has to survive a sparse persona
    for as long as personas are optional.
    """
    print("\nPersona material survives absent fields:")
    family = PB.build_week0_packet("family")
    personas = family["manager_personas"]

    holly = personas.get("holly", {})
    check("holly still contributes material", bool(holly), f"{sorted(holly)}")
    check("holly carries no null-valued field",
          all(v not in (None, "") for v in holly.values()))
    check("her unauthored fatal_flaw is absent, not null",
          "fatal_flaw" not in holly, f"{sorted(holly)}")
    check("and the fields she DID author still arrive",
          {"running_gag", "rival"} <= set(holly), f"{sorted(holly)}")

    # The formerly-straight three now author the full set, and it must all
    # reach the packet -- the flip is only real if the material follows it.
    for mid in ("john", "rachel", "vic"):
        block = personas.get(mid, {})
        check(f"{mid} still contributes material", bool(block),
              f"{sorted(block)}")
        check(f"{mid} carries no null-valued field",
              all(v not in (None, "") for v in block.values()))
        check(f"{mid}: the blocks the straight register used to withhold now ship",
              {"fatal_flaw", "running_gag", "rival"} <= set(block),
              f"{sorted(block)}")

    check("a long-standing roast manager still brings the fields they DO have",
          {"fatal_flaw", "running_gag", "rival"} <= set(personas.get("gayden", {})),
          f"{sorted(personas.get('gayden', {}))}")
    check("rival is resolved to a display name, never a raw manager_id",
          personas["gayden"]["rival"] == "Gunner",
          f"{personas['gayden'].get('rival')!r}")

    # No tone constraint reaches the packet: the column does not get gentler.
    check("tone is NOT published -- one pinned voice for every manager",
          not any("tone" in block for block in personas.values()))

    # And a group with no personas.json at all is empty, not fatal.
    check("an absent personas.json yields {} rather than exiting",
          PB.persona_material("nope-no-such-group", {"a": "A"}) == {})


def main():
    print("preseason_baseline.py \u2014 freeze guards, schedule truth, projector reuse")
    test_season_started_guard()
    test_already_frozen_guard()
    test_schedule_lengths()
    test_projector_reuse()
    test_frozen_artifact()
    test_uniform_fields_not_forked()
    test_coda_excludes_lead_subject()
    test_every_collision_reaches_the_column()
    test_conference_spread_suppressed_where_the_rule_is_advisory()
    test_persona_material_tolerates_absence()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
