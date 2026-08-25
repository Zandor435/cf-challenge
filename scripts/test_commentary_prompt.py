#!/usr/bin/env python3
"""
test_commentary_prompt.py — Validates generate_commentary.py's dry-run path.

Commentary is garnish, but two of its properties are load-bearing and easy to
break silently:
  1. FAIL-SOFT. Live mode must exit 0 on ANY failure, or a dead API takes down
     standings and the deploy (playbook rule 3). utils.load_json() raises
     SystemExit, which `except Exception` does NOT catch — this test pins that.
  2. NO FABRICATED FRAMES. The packet's movement fields are named *_this_week
     but measure the gap to the previous snapshot. When that gap isn't one week
     the prompt must say so, or the pundit compresses a multi-week move into one
     Saturday: true numbers, false claim. The same applies to a finished season:
     if the prompt doesn't say the schedule is exhausted, the column writes
     forward-looking prose into a season that already ended.

Everything here is a DRY RUN — no network call is made, and no OPENAI_API_KEY is
needed or read. Fixtures are written to a temp dir with utils.GROUPS_DIR
redirected, so production files are never touched (playbook rule 14).

PRESEASON: build_week_packet returns None before the first kickoff (there is no
column to write yet), but these checks are about PROMPT ASSEMBLY and must not go
dark for the months before Week 1. So the seed packet is built off the SAME
committed boards with the cache stubbed to a played slate — see seed_packet().

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_commentary_prompt.py
    python scripts/test_commentary_prompt.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import build_week_packet as B
import generate_commentary as G

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


@contextlib.contextmanager
def sandbox(group, packet):
    """Redirect groups/ to a temp tree holding just this packet, so the
    generator reads a fixture and writes its preview outside the repo."""
    orig = utils.GROUPS_DIR
    with tempfile.TemporaryDirectory() as td:
        utils.GROUPS_DIR = Path(td)
        out = Path(td) / group / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "week_packet.json").write_text(
            json.dumps(packet, indent=2), encoding="utf-8")
        try:
            yield out
        finally:
            utils.GROUPS_DIR = orig


@contextlib.contextmanager
def no_api_key():
    """A machine with NO key from ANY source -- not the environment, not a .env.

    Popping os.environ alone is NOT enough, and that was the bug. run_live
    calls utils.load_env_file(), which reads ROOT/.env and setdefault()s the
    key straight back in before the key check ever runs. On any machine with a
    real .env -- which is most of them, and every developer machine that has
    ever generated a column -- the no-key branch was unreachable, so the two
    checks below passed or failed on whether a gitignored file happened to
    exist. A test whose verdict depends on the machine is worse than one that
    simply fails.

    Stubbing the loader models the absent .env, which IS the condition under
    test: the code path being asserted is "no key was found anywhere", not
    "the loader was never called".
    """
    key = os.environ.pop("OPENAI_API_KEY", None)
    orig_loader = utils.load_env_file
    utils.load_env_file = lambda *a, **kw: None
    try:
        yield
    finally:
        utils.load_env_file = orig_loader
        os.environ.pop("OPENAI_API_KEY", None)
        if key is not None:
            os.environ["OPENAI_API_KEY"] = key


def test_prompt_shape():
    print("\nPrompt shape:")
    packet = _seeded_packet()
    with sandbox("panel", packet):
        system_text, user_text, pk = G.build_prompt("panel")

    persona = (utils.ROOT / "templates" / "svp_persona.md").read_text(encoding="utf-8")
    check("system content is the persona template verbatim", system_text == persona,
          f"{len(system_text)} chars")
    check("system carries the fixed sign-off",
          "Don't take the points personally" in system_text)
    check("packet round-trips", pk["group_id"] == packet["group_id"])

    check("user block embeds the packet JSON", '"storylines"' in user_text
          and '"manager_profiles"' in user_text)
    check("user block carries the column memory section",
          "COLUMN MEMORY" in user_text)
    check("user block carries a last-column section",
          "LAST PUBLISHED COLUMN" in user_text)
    check("no prior column -> says so rather than faking one",
          "first column of the season" in user_text)
    check("user block issues the assignment", "YOUR ASSIGNMENT" in user_text
          and "Beat 1" in user_text and "Beat 2" in user_text)
    check("assignment forbids inventing play-by-play",
          "do NOT invent drives" in user_text)
    check("assignment demands verbatim numbers",
          "verbatim" in user_text)
    check("memory sent to the model excludes bookkeeping",
          '"nicknames"' in user_text and '"columns"' not in user_text.split(
              "=== LAST PUBLISHED COLUMN ===")[0].split("COLUMN MEMORY")[1])
    # (validate_prompt_shape used to `return user_text`; main() never read it and
    # pytest warns on a non-None test return, so the dead return is gone. Every
    # check above still runs against the same user_text.)


def test_basis_warning():
    """The comparison basis must be stated honestly for all three gap shapes."""
    print("\nComparison basis (the *_this_week honesty guard):")
    base_packet = _seeded_packet()
    multi = json.loads(json.dumps(base_packet))
    multi["comparison"] = {"prior_week": 6, "weeks_elapsed": 10,
                           "basis": "since week 6"}
    with sandbox("panel", multi):
        _, user_text, _ = G.build_prompt("panel")
    check("10-week gap: forbids calling it 'this week'",
          "Do not call that 'this week'" in user_text)
    check("10-week gap: offers the honest phrasing",
          "since week 6" in user_text and "over the last 10 weeks" in user_text)

    one = json.loads(json.dumps(base_packet))
    one["comparison"] = {"prior_week": 15, "weeks_elapsed": 1,
                         "basis": "since week 15"}
    with sandbox("panel", one):
        _, user_text, _ = G.build_prompt("panel")
    check("1-week gap: 'this week' is allowed",
          "'This week' is literal" in user_text)
    check("1-week gap: no prohibition emitted",
          "Do not call that 'this week'" not in user_text)

    none = json.loads(json.dumps(base_packet))
    none["comparison"] = {"prior_week": None, "weeks_elapsed": None,
                          "basis": "no prior snapshot — week-over-week movement unknown"}
    with sandbox("panel", none):
        _, user_text, _ = G.build_prompt("panel")
    check("no prior snapshot: forbids movement talk entirely",
          "Do not describe week-over-week movement at all" in user_text)


def test_stakes():
    print("\nStakes passthrough:")
    base_packet = _seeded_packet()
    no_stakes = json.loads(json.dumps(base_packet))
    no_stakes["stakes"] = None
    with sandbox("panel", no_stakes):
        _, user_text, _ = G.build_prompt("panel")
    # The wording moved from "Do not invent any" to a named-phrase ban when
    # that short form leaked (see test_invented_stakes_ban). Assert the
    # BEHAVIOUR -- a None is stated and inventing is forbidden -- not one
    # sentence, so the next rewrite of the block does not fail this test.
    check("no stakes: instructs the pundit not to invent any",
          "packet.stakes is None" in user_text
          and "Do not invent stakes" in user_text)

    with_stakes = json.loads(json.dumps(base_packet))
    with_stakes["stakes"] = "loser buys dinner"
    with sandbox("panel", with_stakes):
        _, user_text, _ = G.build_prompt("panel")
    check("stakes: passed through by their actual terms",
          "loser buys dinner" in user_text)


def test_season_complete():
    """A finished season has to be stated, not left implicit in the JSON."""
    print("\nSeason completion in the prompt:")
    base_packet = _seeded_packet()
    over = json.loads(json.dumps(base_packet))
    over["season_complete"] = True
    with sandbox("panel", over):
        _, user_text, _ = G.build_prompt("panel")
    check("season over: the prompt says so in plain words",
          "THE SEASON IS OVER" in user_text)
    check("season over: forward-looking prose is forbidden",
          "Do not write forward-looking prose" in user_text)

    live = json.loads(json.dumps(base_packet))
    live["season_complete"] = False
    with sandbox("panel", live):
        _, user_text, _ = G.build_prompt("panel")
    check("season live: no end-of-season framing is imposed",
          "THE SEASON IS OVER" not in user_text
          and "games remain to be played" in user_text)

    # An older packet with no such key must not be guessed either way.
    unknown = json.loads(json.dumps(base_packet))
    unknown.pop("season_complete", None)
    with sandbox("panel", unknown):
        _, user_text, _ = G.build_prompt("panel")
    check("season unknown: the prompt refuses to claim either way",
          "UNKNOWN" in user_text and "Do not claim either" in user_text)
    check("season unknown: does not fall back to 'over'",
          "THE SEASON IS OVER" not in user_text)


def test_uniform_fields():
    """A value the whole room shares must be stated as sharing, or the column
    reads one profile and writes exclusivity (persona sacred rule 7)."""
    print("\nUniform profile fields in the prompt:")
    base_packet = _seeded_packet()
    shared = json.loads(json.dumps(base_packet))
    shared["uniform_profile_fields"] = {"picks_alive": 0, "conference_spread": 4}
    with sandbox("panel", shared):
        _, user_text, _ = G.build_prompt("panel")
    check("uniform: the prompt names the fields that distinguish nobody",
          "DISTINGUISH NOBODY" in user_text)
    check("uniform: it lists them with their shared value",
          "picks_alive (0)" in user_text and "conference_spread (4)" in user_text)
    check("uniform: the banned constructions are spelled out",
          "the only one who" in user_text and "nobody else" in user_text)
    check("uniform: a group-wide statement is still permitted",
          "fact about the whole group" in user_text)

    varied = json.loads(json.dumps(base_packet))
    varied["uniform_profile_fields"] = {}
    with sandbox("panel", varied):
        _, user_text, _ = G.build_prompt("panel")
    check("uniform: nothing shared -> no prohibition is imposed",
          "DISTINGUISH NOBODY" not in user_text
          and "Every manager_profiles field varies" in user_text)

    # An older packet predating the field must not be told either way.
    absent = json.loads(json.dumps(base_packet))
    absent.pop("uniform_profile_fields", None)
    with sandbox("panel", absent):
        _, user_text, _ = G.build_prompt("panel")
    check("uniform: a packet without the field does not crash the prompt",
          isinstance(user_text, str) and len(user_text) > 0)
    check("uniform: and claims no fields are shared",
          "DISTINGUISH NOBODY" not in user_text)


def test_dry_run():
    """--dry-run writes a complete preview and makes no network call."""
    print("\nDry run:")
    base_packet = _seeded_packet()
    with sandbox("panel", base_packet) as out:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = G.run_dry("panel")
        preview = out / "commentary_prompt_preview.txt"
        text = preview.read_text(encoding="utf-8") if preview.exists() else ""
        wrote_column = list(out.glob("column_week_*.md"))
        wrote_memory = (out / "column_memory.json").exists()

    check("dry run: exits 0", code == 0)
    check("dry run: writes the preview", bool(text), f"{len(text)} chars")
    check("dry run: preview labels itself a dry run", "DRY RUN" in text)
    check("dry run: preview names the model and params",
          f"model={G.OPENAI_MODEL}" in text and "temperature=" in text)
    check("dry run: preview contains both prompt halves",
          "----- SYSTEM" in text and "----- USER" in text)
    check("dry run: files no column", not wrote_column)
    check("dry run: creates no column memory", not wrote_memory)


def test_fail_soft():
    """Live mode must exit 0 on every failure path — including SystemExit."""
    print("\nFail-soft:")
    base_packet = _seeded_packet()
    argv, key = sys.argv, os.environ.pop("OPENAI_API_KEY", None)
    try:
        # No key from ANY source: the earliest live failure. Must run inside
        # no_api_key() -- see there; popping the env var alone leaves .env
        # free to put the key straight back before run_live checks it.
        with no_api_key():
            # Guard the guard: prove the stub actually holds against a real
            # .env, or this whole block silently goes back to testing nothing.
            utils.load_env_file()
            check("no key: a .env on disk cannot put the key back",
                  not os.environ.get("OPENAI_API_KEY"))

            sys.argv = ["generate_commentary.py", "--group", "panel"]
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = G.main()
        check("live without a key: exits 0", code == 0, f"returned {code}")
        check("live without a key: warns loudly", "::warning::" in err.getvalue())
        check("live without a key: names the missing key",
              "OPENAI_API_KEY" in err.getvalue())
        check("live without a key: says the pipeline is unaffected",
              "unaffected" in err.getvalue())

        # Key present, packet missing: fails after the key check, still exit 0.
        os.environ["OPENAI_API_KEY"] = "test-key-never-used"
        sys.argv = ["generate_commentary.py", "--group", "definitely_not_a_group"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = G.main()
        check("live with a missing packet: exits 0", code == 0, f"returned {code}")
        check("live with a missing packet: names the cause",
              "week_packet.json" in err.getvalue())

        # SystemExit must be caught too — utils.load_json() exits, it does not
        # raise, so `except Exception` alone would let the pipeline step die.
        orig = G.build_prompt
        try:
            def _exiting_prompt(_g):
                sys.exit(1)
            G.build_prompt = _exiting_prompt
            sys.argv = ["generate_commentary.py", "--group", "panel"]
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = G.main()
            check("live: a SystemExit inside the run is caught and exits 0",
                  code == 0, f"returned {code}")
            check("live: SystemExit path still warns",
                  "::warning::" in err.getvalue())
        finally:
            G.build_prompt = orig

        # Dry run stays LOUD — developer-facing failures must not be swallowed.
        sys.argv = ["generate_commentary.py", "--group", "definitely_not_a_group",
                    "--dry-run"]
        raised = False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                G.main()
        except Exception:  # noqa: BLE001 — asserting it propagates
            raised = True
        check("dry run: a missing packet raises rather than exiting 0", raised)
    finally:
        sys.argv = argv
        os.environ.pop("OPENAI_API_KEY", None)
        if key is not None:
            os.environ["OPENAI_API_KEY"] = key


def _played_slate(cache, through_week=1):
    """The REAL schedule with everything up to `through_week` marked final.

    Derived from the live cache rather than hand-written so the team names are
    the ones the committed picks actually reference — a hand-rolled slate would
    index to nothing and quietly drop bad-beat coverage. Scores are constant and
    the home side always wins: these checks assert prompt assembly, not football.
    In memory only; the cache on disk is never touched (playbook rule 14).
    """
    games = []
    for g in cache.get("games") or []:
        g = dict(g)
        if g.get("week") is not None and int(g["week"]) <= through_week:
            g["completed"] = True
            g["home_points"] = 21
            g["away_points"] = 17
        games.append(g)
    return {**cache, "week": through_week, "games": games}


def seed_packet(group):
    """The packet every validator below sandboxes and mutates.

    Prefer the real one. In preseason build_packet returns None by contract, so
    rebuild it off the same committed boards against a stubbed played slate: the
    week resolves, the packet is contract-shaped, and the checks keep their teeth
    year-round. Re-arms automatically once real games are final.
    """
    packet = B.build_packet(group)
    if packet is not None:
        return packet

    print(f"  (preseason: 0 games played, so no live packet exists. Seeding from "
          f"the committed {group} boards against a stubbed week-1 slate.)")
    cache = _played_slate(utils.load_cache(utils.get_season()))
    real_load, real_assert = B.utils.load_cache, B.utils.assert_season_matches_cache
    B.utils.load_cache = lambda *a, **k: cache
    B.utils.assert_season_matches_cache = lambda *a, **k: cache["season"]
    try:
        packet = B.build_packet(group)
    finally:
        B.utils.load_cache = real_load
        B.utils.assert_season_matches_cache = real_assert
    if packet is None:
        print("::error:: could not seed a packet even from a played slate.",
              file=sys.stderr)
        sys.exit(1)
    return packet


_SEEDED = None


def _seeded_packet():
    """seed_packet('panel'), built once per process.

    main() seeded one packet and passed the same object to all seven validators,
    so this is the same sharing, memoised. Under pytest a single -k selection
    seeds its own copy, which is strictly stronger.
    """
    global _SEEDED
    if _SEEDED is None:
        _SEEDED = seed_packet("panel")
    return _SEEDED


def test_no_prior_season_language():
    """The prompt must ban implied-prior-season trajectory claims.

    Week 0 panel wrote "Chris is betting on a Texas resurgence" and "you're
    hoping for a turnaround". Neither is a number the column got wrong -- both
    describe a 2025 season that the packet does not carry and the pipeline has
    never fetched (the cache is 2026 only). It is sacred rule 1 fabrication
    wearing sportswriting idiom, and it is invisible at the sentence level,
    which is why the vocabulary is named explicitly in the prompt rather than
    left to "do not invent facts".
    """
    print("\nPrior-season language suppression:")
    base_packet = _seeded_packet()
    with sandbox("panel", base_packet):
        _, user_text, _ = G.build_prompt("panel")

    check("prior-season: the block is present and titled",
          "NO PRIOR-SEASON LANGUAGE" in user_text)
    check("prior-season: it states the packet carries no history",
          "carries no history" in user_text)

    # Every word the brief named, plus the ones that leak the same claim.
    for word in ("resurgence", "comeback", "bounce-back", "return to form",
                 "turnaround", "rebound", "redemption", "rebuild"):
        check(f"prior-season: '{word}' is named as banned", word in user_text)
    check("prior-season: the returning sense of 'back' is named",
          "in any returning sense" in user_text
          and "back on track" in user_text)
    check("prior-season: the quiet trajectory words are named too",
          all(w in user_text for w in ("again", "still", "no longer")))
    check("prior-season: synonyms are closed off, not just the listed words",
          "swap in a near-synonym" in user_text)
    check("prior-season: it states that no history is available at all",
          "every claim about a trajectory is invented" in user_text)

    # In-season the block must NOT forbid the movement the packet does measure,
    # or it would gag the week-over-week story the column exists to tell.
    live = json.loads(json.dumps(base_packet))
    live["preseason"] = False
    live["season_complete"] = False
    with sandbox("panel", live):
        _, live_text, _ = G.build_prompt("panel")
    check("prior-season: in season, week-over-week movement stays permitted",
          "The ONE movement you may describe" in live_text
          and "WITHIN this season" in live_text)
    check("prior-season: and the ban itself still applies in season",
          "NO PRIOR-SEASON LANGUAGE" in live_text)

    # In preseason nothing has been played at all, so no carve-out is offered.
    pre = json.loads(json.dumps(base_packet))
    pre["preseason"] = True
    with sandbox("panel", pre):
        _, pre_text, _ = G.build_prompt("panel")
    # Asserted on the carve-out SENTENCE, not the phrase "week-over-week
    # movement" -- that also appears in the packet's own comparison basis
    # ("no prior snapshot - week-over-week movement unknown"), so the phrase
    # alone would pass here for the wrong reason.
    check("prior-season: preseason gets no week-over-week carve-out",
          "NO PRIOR-SEASON LANGUAGE" in pre_text
          and "The ONE movement you may describe" not in pre_text)


def test_suppressed_fields():
    """The by-rule half of the withholding uniform_profile_fields opens.

    uniform_line covers fields that HAPPEN to be equal this week. These are the
    fields the group's own rules make meaningless, and they are the case the
    uniform mechanism structurally cannot catch: family's conference numbers
    vary (3 and 4 conferences, 25% and 50% shares) while measuring nothing
    anyone agreed to, because that group's written minimum is 1 and every legal
    roster clears it. A varying number is exactly the shape a column reads as a
    personal trait, so it has to be named in prose.
    """
    print("\nSuppressed-by-rule profile fields in the prompt:")
    base = _seeded_packet()

    reason = "the written minimum is 1, so the spread is advisory."
    packet = json.loads(json.dumps(base))
    packet["suppressed_profile_fields"] = {
        "conference_spread": reason, "largest_conference_share": reason}
    with sandbox("family", packet):
        _, user_text, _ = G.build_prompt("family")
    check("suppressed: the prompt names them as measuring nothing",
          "MEASURE NOTHING IN THIS GROUP" in user_text)
    check("suppressed: both fields are named",
          "conference_spread" in user_text
          and "largest_conference_share" in user_text)
    check("suppressed: the reason travels with them", reason in user_text)
    # Counted over the INSTRUCTION block only. The packet itself is dumped
    # verbatim further down and legitimately carries the reason once per field;
    # what must not repeat is the sentence the model is being instructed with.
    head = user_text.split("=== COLUMN MEMORY")[0]
    check("suppressed: a shared reason is printed ONCE, not per field",
          head.count(reason) == 1, f"count={head.count(reason)}")
    check("suppressed: unlike a uniform field, it may not be stated group-wide",
          "not even as a fact about the whole group" in user_text)

    # Distinct reasons must each survive.
    two = json.loads(json.dumps(base))
    two["suppressed_profile_fields"] = {"a": "reason one.", "b": "reason two."}
    with sandbox("family", two):
        _, user_text, _ = G.build_prompt("family")
    check("suppressed: distinct reasons are both carried",
          "reason one." in user_text and "reason two." in user_text)

    # A group with a real rule imposes nothing, and neither does an old packet.
    for label, mutate in (("empty", lambda d: d.update(
                              {"suppressed_profile_fields": {}})),
                          ("absent", lambda d: d.pop(
                              "suppressed_profile_fields", None))):
        packet = json.loads(json.dumps(base))
        mutate(packet)
        with sandbox("panel", packet):
            _, user_text, _ = G.build_prompt("panel")
        check(f"suppressed: {label} -> no prohibition is imposed",
              "MEASURE NOTHING" not in user_text)


def test_head_to_head_named_in_prose():
    """Collisions are stated in the prompt, not left 12KB down in the JSON.

    The same reasoning as basis_warning and uniform_line: a block buried in the
    packet is not an instruction. Family drafted four opposite-side conflicts
    and they are that group's signature drama, so the count and both sides of
    each are named -- with the directions coming off the packet's own sides,
    which come off picks.json.
    """
    print("\nHead-to-head conflicts named in the prompt:")
    base = _seeded_packet()
    base["preseason"] = True
    base["comparison"]["weeks_elapsed"] = None
    packet = json.loads(json.dumps(base))
    packet["collisions"] = [
        {"team": "Georgia", "line": 9.5, "sides": [
            {"manager_id": "vic", "name": "Vic", "direction": "O"},
            {"manager_id": "holly", "name": "Holly", "direction": "U"}]},
        {"team": "Auburn", "line": 6.5, "sides": [
            {"manager_id": "devin", "name": "Devin", "direction": "O"},
            {"manager_id": "rachel", "name": "Rachel", "direction": "U"}]},
    ]
    with sandbox("family", packet):
        _, user_text, _ = G.build_prompt("family")
    check("h2h: the prompt names the section", "HEAD-TO-HEAD:" in user_text)
    check("h2h: it states the COUNT", "2 team(s)" in user_text)
    check("h2h: both teams and lines appear",
          "Georgia 9.5" in user_text and "Auburn 6.5" in user_text)
    check("h2h: each side is named with its direction spelled out",
          "Vic OVER" in user_text and "Holly UNDER" in user_text
          and "Devin OVER" in user_text and "Rachel UNDER" in user_text)

    # No collisions, and a normal week, both impose nothing.
    none_ = json.loads(json.dumps(packet))
    none_["collisions"] = []
    with sandbox("family", none_):
        _, user_text, _ = G.build_prompt("family")
    check("h2h: no collisions -> the line is absent",
          "HEAD-TO-HEAD:" not in user_text)

    live = json.loads(json.dumps(packet))
    live["preseason"] = False
    with sandbox("family", live):
        _, user_text, _ = G.build_prompt("family")
    check("h2h: a played week is unchanged (the line is preseason-only)",
          "HEAD-TO-HEAD:" not in user_text)


def test_persona_material_carries_no_tone_rule():
    """Persona material reaches the prompt, and absence is never a constraint.

    John, Rachel and Vic carry no fatal_flaw, running_gag or rival -- authored
    that way on purpose. That means SVP has less material about them; it must
    not mean SVP is instructed to go easier on them. The register is one pinned
    voice for every group and every manager (ARCHITECTURE S12).
    """
    print("\nPersona material and the absence of a tone rule:")
    base = _seeded_packet()
    packet = json.loads(json.dumps(base))
    packet["manager_personas"] = {
        "john": {"epithet": "The Counselor",
                 "backstory": "McComb, Mississippi.",
                 "draft_tendency": "Backed Ole Miss."},
        "gayden": {"epithet": "The Backpass Assassin",
                   "fatal_flaw": "No casual setting.",
                   "running_gag": "Michael Bradley.",
                   "rival": "Gunner"},
    }
    with sandbox("family", packet):
        _, user_text, _ = G.build_prompt("family")
    check("personas: the section is present",
          "MANAGER PERSONAS" in user_text)
    check("personas: a manager with few fields still reaches the column",
          "The Counselor" in user_text and "McComb, Mississippi." in user_text)
    check("personas: a manager with many keeps all of them",
          "No casual setting." in user_text and "Michael Bradley." in user_text
          and "Gunner" in user_text)
    check("personas: fewer fields is stated as less material, not soft handling",
          "not owed a softer column" in user_text
          and "Same voice for everyone" in user_text)
    check("personas: no tone/register instruction is derived from the fields",
          "tone" not in user_text.split("MANAGER PERSONAS")[1].split(
              "=== COLUMN MEMORY")[0].lower())

    for label, mutate in (("empty", lambda d: d.update({"manager_personas": {}})),
                          ("absent", lambda d: d.pop("manager_personas", None))):
        packet = json.loads(json.dumps(base))
        mutate(packet)
        with sandbox("family", packet):
            _, user_text, _ = G.build_prompt("family")
        check(f"personas: {label} -> the section is simply omitted",
              "MANAGER PERSONAS" not in user_text and len(user_text) > 0)


def test_publish_doc():
    """The site's copy computes nothing: paragraphs and the count are made here."""
    print("\nPublished week_<N>.json shape:")
    packet = _seeded_packet()
    packet["preseason"] = True
    column = "First para, two sentences.\n\nSecond para.\r\n\r\nThird para.\n"
    doc = G.publish_doc("family", packet, column)

    check("publish: meta identifies the group, season and week",
          doc["meta"]["group_id"] == "family"
          and doc["meta"]["season"] == packet["season"]
          and doc["meta"]["week"] == packet["week"], f"{doc['meta']}")
    check("publish: the preseason flag is the packet's, not a second opinion",
          doc["meta"]["preseason"] is True)
    check("publish: it names the source .md so the two files stay traceable",
          doc["meta"]["source"].endswith(
              f"column_week_{packet['week']}.md"), doc["meta"]["source"])
    check("publish: prose is pre-split into paragraphs",
          doc["column"]["paragraphs"] ==
          ["First para, two sentences.", "Second para.", "Third para."],
          f"{doc['column']['paragraphs']}")
    check("publish: CRLF does not produce a phantom paragraph",
          all(p.strip() == p and p for p in doc["column"]["paragraphs"]))
    check("publish: the word count is computed here, not in JS",
          doc["column"]["word_count"] == len(column.split()),
          f"{doc['column']['word_count']}")
    check("publish: the target is docs/data/<group>/columns/week_<N>.json",
          G.publish_path("family", 3)
          == utils.WEB_DATA_DIR / "family" / "columns" / "week_3.json",
          str(G.publish_path("family", 3)))
    check("publish: each week is its own file, so filing one cannot touch another",
          G.publish_path("family", 3) != G.publish_path("family", 4))

    # JSON-serialisable, because it is written straight to the web root.
    try:
        json.dumps(doc)
        ok = True
    except TypeError as e:
        ok, _ = False, e
    check("publish: the document is JSON-serialisable", ok)


def test_archive_index():
    """The manifest is derived, idempotent, carries no prose, and never mutates
    an already-filed column.

    This is the test for the bug the archive exists to prevent: publishing used
    to write a single newest-only column.json, so week 1 erased week 0 from the
    site while the .md sources piled up unpublished.
    """
    print("\nArchive index (docs/data/<group>/columns/):")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        real_web = utils.WEB_DATA_DIR
        utils.WEB_DATA_DIR = root
        try:
            d = G.columns_dir("family")
            d.mkdir(parents=True)

            check("index: no published columns at all -> None, not an empty file",
                  G.build_index("family") is None and not G.index_path("family").exists())

            def file_week(week, pre, stamp, words):
                utils.save_json_atomic(G.publish_path("family", week), {
                    "meta": {"group_id": "family", "season": 2026, "week": week,
                             "preseason": pre, "generated_at": stamp,
                             "model": "gpt-4o",
                             "source": f"groups/family/output/column_week_{week}.md"},
                    "column": {"paragraphs": [f"Prose for week {week}."],
                               "word_count": words},
                })

            file_week(0, True, "2026-08-23T12:00:00+00:00", 344)
            idx = G.build_index("family")
            check("index: one filed column is counted", idx["count"] == 1)

            # Week 10 before week 2, so a lexical sort of the filenames would put
            # week_10 after week_2 and the "newest" would be wrong.
            file_week(10, False, "2026-11-01T12:00:00+00:00", 400)
            file_week(2, False, "2026-09-07T12:00:00+00:00", 380)
            idx = G.build_index("family")
            check("index: every filed column appears", idx["count"] == 3)
            check("index: newest first, by week as an integer (not lexically)",
                  [c["week"] for c in idx["columns"]] == [10, 2, 0],
                  str([c["week"] for c in idx["columns"]]))
            check("index: week 0 is an ordinary entry, not a special case",
                  idx["columns"][-1]["week"] == 0
                  and idx["columns"][-1]["preseason"] is True)
            check("index: each entry names the file its prose lives in",
                  all((d / c["file"]).exists() for c in idx["columns"]))
            # Scoped to the entries: the $note explains where prose lives and
            # naturally contains the word, which is documentation, not data.
            check("index: the manifest carries NO prose",
                  "Prose for week" not in json.dumps(idx)
                  and all(set(c) == {"week", "preseason", "generated_at",
                                     "word_count", "file"}
                          for c in idx["columns"]))
            check("index: the filing date is carried through verbatim",
                  idx["columns"][-1]["generated_at"] == "2026-08-23T12:00:00+00:00")

            # Idempotence is byte-level: nothing in the manifest is a clock
            # reading, so a republish with nothing new produces no git churn.
            first = G.index_path("family").read_bytes()
            G.build_index("family")
            check("index: rebuilding with nothing new is byte-identical",
                  G.index_path("family").read_bytes() == first)

            # The guarantee that matters: filing a week opens that week only.
            before = {w: G.publish_path("family", w).read_bytes() for w in (0, 2, 10)}
            file_week(11, False, "2026-11-08T12:00:00+00:00", 390)
            G.build_index("family")
            check("index: filing a new week mutates no already-filed column",
                  all(G.publish_path("family", w).read_bytes() == b
                      for w, b in before.items()))
            # Re-filing a week after a prompt fix is intended to replace THAT
            # week and is the reason publishing stays idempotent per week.
            keep = {w: G.publish_path("family", w).read_bytes() for w in (0, 10, 11)}
            file_week(2, False, "2026-09-07T12:00:00+00:00", 999)
            G.build_index("family")
            check("index: re-filing one week rewrites that file and no other",
                  json.loads(G.publish_path("family", 2).read_text(
                      encoding="utf-8"))["column"]["word_count"] == 999
                  and all(G.publish_path("family", w).read_bytes() == b
                          for w, b in keep.items()))

            # Defensive per file (playbook rule 10).
            (d / "week_9.json").write_text("{not json", encoding="utf-8")
            idx = G.build_index("family")
            check("index: an unreadable week file is skipped, not fatal",
                  idx is not None and 9 not in [c["week"] for c in idx["columns"]]
                  and idx["count"] == 4)
        finally:
            utils.WEB_DATA_DIR = real_web


def test_regeneration_does_not_cite_itself():
    """A rewritten week must not be handed its own discarded draft as a callback.

    Filing is idempotent: the same week is regenerated after a prompt fix, and
    on that path the newest name in column_memory is the file about to be
    overwritten. Panel's first Week 0 column carried the prior-season language
    a6c8aad bans and the coda collision 8e327ce forbids; feeding it back in as
    "last published column" is the most direct way to get both returned.
    """
    print("\nA regenerated week does not cite itself:")
    packet = _seeded_packet()
    week = packet["week"]

    with sandbox("panel", packet) as out:
        (out / f"column_week_{week}.md").write_text(
            "SUPERSEDED DRAFT: a Texas resurgence.", encoding="utf-8")
        (out / f"column_week_{week - 1}.md").write_text(
            "GENUINELY LAST WEEK.", encoding="utf-8")
        (out / "column_memory.json").write_text(json.dumps({
            "group_id": "panel", "nicknames": {}, "feuds": [],
            "character_bits": {},
            "columns": [f"column_week_{week - 1}.md", f"column_week_{week}.md"],
        }), encoding="utf-8")
        _, user_text, _ = G.build_prompt("panel")

        check("the week being rewritten is not offered back as a callback",
              "SUPERSEDED DRAFT" not in user_text)
        check("the genuinely previous column still is",
              "GENUINELY LAST WEEK." in user_text)

        # And the skip is scoped to that one week, not to callbacks generally.
        name, _text = G.last_column_text("panel", G.load_memory("panel"))
        check("without a skip, the newest column is still the newest",
              name == f"column_week_{week}.md", f"{name}")

    # The season's first column has nothing to skip TO, and says so plainly
    # rather than falling back to some other week's prose.
    with sandbox("panel", packet) as out:
        (out / f"column_week_{week}.md").write_text("DRAFT", encoding="utf-8")
        (out / "column_memory.json").write_text(json.dumps({
            "group_id": "panel", "columns": [f"column_week_{week}.md"],
        }), encoding="utf-8")
        _, user_text, _ = G.build_prompt("panel")
    check("a first column reports no callbacks rather than citing its draft",
          "this is the first column of the season" in user_text
          and "DRAFT" not in user_text)


def test_coda_superlative_matches_the_selection_rule():
    """The prompt must describe the coda the way the packet actually chose it.

    THE SHIPPED BUG. The instruction read "chosen by computation - the lowest
    market_gap on the board", which was true until the coda-exclusion rule
    landed. After it, the coda is the lowest gap among the picks the lead did
    NOT already cover, and on both Week 0 boards a lower gap sat on the lead's
    own side -- panel Chris/Texas at -1.681 against a -1.044 coda, family
    John/Miami at -1.223 against -0.898. The prompt was instructing the model
    to write a superlative the packet contradicts, and both columns wrote it.
    Same failure family as uniform_profile_fields: true number, invented
    exclusivity, one level up -- a ranking claim the selection rule never made.
    """
    print("\nThe coda superlative matches how the coda was chosen:")
    base = _seeded_packet()
    base["preseason"] = True
    base["comparison"]["weeks_elapsed"] = None
    base["worst_pick_on_the_board"] = {
        "manager_id": "jonathan", "name": "Jonathan", "team": "Oregon",
        "line": 10.5, "direction": "O", "market_gap": -1.044}

    excluded = json.loads(json.dumps(base))
    excluded["coda_exclusion"] = {"excluded": 8, "collision_forced": False,
                                  "lead_managers": ["blaine", "chris"],
                                  "lead_teams": ["Texas"]}
    with sandbox("panel", excluded):
        _, user_text, _ = G.build_prompt("panel")
    check("exclusion ran: the prompt forbids the board-wide superlative",
          "NOT the lowest on the board and you may not call it that" in user_text)
    check("exclusion ran: it says how many lower picks were set aside",
          "8 pick(s) with a lower gap" in user_text)
    check("exclusion ran: the stale flat claim is gone",
          "chosen by computation - the lowest market_gap on the board"
          not in user_text)
    check("exclusion ran: the coda is still computed, never model-chosen",
          "chosen by computation, never by you" in user_text)

    # THE COUNT SPLIT. `excluded` is every subject-collision drop;
    # `excluded_lower_gap` is the subset actually below the kept pick. The
    # sentence says "with a lower gap", so it must quote the second. Panel
    # Week 0 shipped 8 here when the true answer was 1, and the column hedged
    # a superlative onto the false count.
    split = json.loads(json.dumps(base))
    split["coda_exclusion"] = {"excluded": 8, "excluded_lower_gap": 1,
                               "collision_forced": False,
                               "lead_managers": ["blaine", "chris"],
                               "lead_teams": ["Texas"]}
    with sandbox("panel", split):
        _, user_text, _ = G.build_prompt("panel")
    check("count split: the prompt quotes the LOWER-GAP count (1)",
          "1 pick(s) with a lower gap" in user_text, user_text[-400:])
    check("count split: the total drop count (8) never reaches the prompt",
          "8 pick(s) with a lower gap" not in user_text)
    check("count split: the board-wide superlative is still forbidden",
          "NOT the lowest on the board and you may not call it that" in user_text)

    # Drops happened but none of them is lower. The superlative must NOT come
    # back in on that technicality.
    nolower = json.loads(json.dumps(base))
    nolower["coda_exclusion"] = {"excluded": 8, "excluded_lower_gap": 0,
                                 "collision_forced": False,
                                 "lead_managers": ["blaine", "chris"],
                                 "lead_teams": ["Texas"]}
    with sandbox("panel", nolower):
        _, user_text, _ = G.build_prompt("panel")
    check("no lower drops: it does not claim any pick was lower",
          "pick(s) with a lower gap" not in user_text)
    check("no lower drops: it says picks were set aside, none of them lower",
          "none of them sits at a lower gap" in user_text)
    check("no lower drops: the superlative stays banned anyway",
          "still may not call this the lowest on the board" in user_text)

    clean = json.loads(json.dumps(base))
    clean["coda_exclusion"] = {"excluded": 0, "collision_forced": False,
                               "lead_managers": [], "lead_teams": []}
    with sandbox("panel", clean):
        _, user_text, _ = G.build_prompt("panel")
    check("nothing excluded: the superlative IS allowed, and is stated",
          "the lowest market_gap on the board, and nothing was set aside"
          in user_text)
    check("nothing excluded: no prohibition is imposed",
          "you may not call it that" not in user_text)

    # An older packet with no audit block gets the conservative reading: with
    # no evidence that nothing was excluded, the superlative is not licensed.
    absent = json.loads(json.dumps(base))
    absent.pop("coda_exclusion", None)
    with sandbox("panel", absent):
        _, user_text, _ = G.build_prompt("panel")
    check("no audit block: the prompt still builds",
          isinstance(user_text, str) and "Worst Pick on the Board" in user_text)


def test_prior_season_ban_is_a_word_test():
    """The ban must be enforceable without the writer adjudicating intent.

    THE DIAGNOSIS. The list version of this block reached the model intact --
    the assembled panel prompt carried it and every term in it exactly once --
    and the model wrote "once again in the fray" anyway, twice, in two
    independent generations across both groups. So it was never an assembly
    bug. The block named the WORDS but justified them as a CONCEPT ("any
    construction that places a team on a trajectory"), which hands the writer
    a test it can pass itself: "once again" there means "as usual", not
    "unlike last season", so the concept test clears it and the word ships.

    The fix is a string test with no interpretive step, and these checks pin
    the three properties that make it one: the words are banned AS WORDS, the
    innocent grammatical contexts are named as still banned, and there is a
    rejection clause so "I could not rephrase it" has a defined answer that is
    not a hedge.
    """
    print("\nThe prior-season ban is a word test, not a theme test:")
    with sandbox("panel", _seeded_packet()):
        _, user_text, _ = G.build_prompt("panel")

    check("banned AS WORDS, explicitly, not as themes",
          "BANNED AS WORDS, NOT AS THEMES" in user_text)
    check("and in every grammatical context",
          "FORBIDDEN IN EVERY GRAMMATICAL CONTEXT" in user_text)
    check("the model is told NOT to adjudicate whether a use is really historical",
          "Do not stop to decide whether a particular use is" in user_text
          and "if the word is in the sentence, the sentence is wrong" in user_text)

    # The innocent contexts are the ones that actually get written, so each is
    # named as still banned rather than left to inference.
    for label, phrase in (
            ("the 'as usual' idiom", "is once again in the fray"),
            ("a weather metaphor", "the waters have risen again"),
            ("a use naming no season", "in the Big Ten again this year"),
            ("a roster description", "a returning starter"),
            ("a present-tense 'still'", "he is still the favorite")):
        check(f"innocent context named as still banned — {label}",
              phrase in user_text, phrase)

    # `return`/`returning` were not on the old list at all and are the exact
    # gap the brief called out.
    check("'return' and 'returning' are on the list",
          "return, " in user_text and "returning," in user_text)

    check("the rejection clause exists and is deletion, not hedging",
          "IF YOU WRITE ONE, DELETE THE SENTENCE" in user_text
          and "Do not hedge it" in user_text)
    check("a shorter column is stated as the correct outcome",
          "A column one sentence shorter is correct" in user_text)

    # Stated once at 18% of a 55KB prompt is what failed. The self-check is at
    # the very end, after the packet dump and after the assignment.
    # Asserted on the self-check SENTENCE, not on a trailing window: "again"
    # appears in several nearby instructions, so a window test would pass here
    # for the wrong reason.
    selfcheck = user_text[user_text.rindex("Before you return it"):]
    selfcheck = selfcheck[:selfcheck.index("Return ONLY")]
    check("the banned words are restated in the closing self-check",
          "banned prior-season word" in selfcheck
          and all(w in selfcheck for w in
                  ("again", "still", "returning", "resurgence", "turnaround")),
          f"selfcheck={selfcheck[:100]!r}")
    check("and the self-check prescribes deletion, matching the block above",
          "delete any sentence that carries one" in selfcheck)
    check("the self-check is the LAST instruction before the return line",
          user_text.rindex("read the column back")
          < user_text.rindex("Return ONLY the column text"))


def test_house_style_structures_and_percentages():
    """Panel wk0: "The packet gives Blaine a 0.86467 chance" -- two faults.

    The reader has never seen the packet and does not know one exists, and a
    probability is a decimal in the artifact but a percentage on the page.
    Family got the same construction right one column over, so the target is
    quoted rather than described.

    Consolidated with the narrative_score/raw-field-name ban rather than filed
    beside it: "don't name the packet", "don't print a field name" and "don't
    print narrative_score" are one rule seen from three sides, and three
    statements of one rule is how they drift apart.
    """
    print("\nHouse style -- internal structures and probability rendering:")
    with sandbox("panel", _seeded_packet()):
        _, user_text, _ = G.build_prompt("panel")
    tail = user_text.split("=== YOUR ASSIGNMENT ===")[1]

    check("the block is present and stated at the assignment, not in the header",
          "HOUSE STYLE" in tail)

    # 1 -- internal structures.
    check("naming an internal data structure is banned outright",
          "NEVER NAME AN INTERNAL DATA STRUCTURE" in tail)
    for word in ("packet", "cache", "manifest", "baseline", "storyline",
                 "coda", "character bits", "column memory"):
        check(f"structure named as unprintable — {word}", word in tail)
    check("the shipped failure is quoted",
          "The packet gives Blaine a 0.86467 chance" in tail)
    check("permitted attributions are offered, so there is somewhere to go",
          "the model" in tail and "our projection" in tail
          and "the numbers" in tail)

    # 2 -- raw field names and bookkeeping (the consolidated a6fcac3 rule).
    check("raw field names still banned, with the fix demonstrated",
          "NEVER PRINT A RAW FIELD NAME" in tail
          and "the market disagrees by 0.669" in tail
          and "a market_gap of" in tail)
    check("narrative_score and moment_size still banned",
          "narrative_score" in tail and "moment_size" in tail
          and "storyline ordering" in tail)

    # 3 -- probabilities.
    check("probabilities are rounded percentages",
          "PROBABILITIES ARE ROUNDED PERCENTAGES" in tail)
    check("both worked examples are given",
          "0.86467 is 86%" in tail and "0.13533 is 14%" in tail)
    check("the decimal form is banned outright",
          "The decimal form never appears" in tail)
    check("one decimal is allowed only where the half carries the point",
          "One decimal place is allowed only" in tail
          and "two or more decimals are never correct" in tail)
    check("family's correct construction is quoted as the target",
          "The implied odds give him a 66.5% chance" in tail)
    # Without this carve-out the rule contradicts sacred rule 1 (numbers
    # verbatim), and a model handed two conflicting rules follows neither.
    check("the conflict with print-numbers-verbatim is resolved explicitly",
          "THIS IS THE ONE EXCEPTION" in tail
          and "does not license rounding anything else" in tail)


def test_length_guidance_survives_the_bans():
    """The constraints cost the column 150 words; the target has to be a floor.

    221613d landed three prohibition blocks at once -- the word-level
    prior-season ban, the house style, the probability rounding -- and the next
    panel column came back at 252 words against a ~400 target, with Beat 1 at
    160 against 250-300. Nothing in it was wrong. There was just less of it,
    which is the predictable reading of a page of bans plus "A column one
    sentence shorter is correct": the safest column is the short one.

    So the length rule has to do two things the old "Roughly 400 words total"
    did not. It has to state a FLOOR, not just a rough size, and it has to name
    what to do with the space a deleted sentence leaves -- because "delete the
    sentence" without "and write another one" is an instruction to shrink.
    These checks pin both, plus the per-beat number, plus the transitions line
    that came from the same reflex one level down.
    """
    print("\nLength guidance, positioned so the bans cannot clip the column:")
    with sandbox("panel", _seeded_packet()):
        _, user_text, _ = G.build_prompt("panel")
    tail = user_text.split("=== YOUR ASSIGNMENT ===")[1]

    check("length is stated at the assignment, beside the other constraints",
          "LENGTH: TARGET 350-450 WORDS TOTAL" in tail)
    check("Beat 1 carries its own number, named as the One Big Thing",
          "Beat 1 (the One Big Thing)" in tail and "250-300 of them" in tail)

    # The whole point: the bans stay absolute, and are told not to shorten.
    check("the bans are explicitly NOT relaxed by the length rule",
          "Nothing above is relaxed here" in tail
          and "the prose bans are absolute" in tail)
    check("...but are told not to clip the column short",
          "they must not clip the column short" in tail)
    check("a deleted sentence is replaced, not simply removed",
          "When a sentence has to go, replace it" in tail
          and "rather than shortening the column overall" in tail)
    check("expansion is pointed at prose, not at more numbers",
          "context, character, what is at stake" in tail
          and "earns its length in voice, not in stat recitation" in tail)
    check("the observed failure length is named as a floor breach",
          "250 words is not a tighter column, it is an unfinished one" in tail)

    # The old rough-size line must be gone, or the prompt carries two targets.
    check("the superseded 'Roughly 400 words total' line is gone",
          "Roughly 400 words total" not in user_text)

    # Same reflex, one level down: a constrained writer reaches for scaffolding.
    check("mechanical transitions are named, with the shipped example quoted",
          "Write the joins in plain speech" in tail
          and "fortified by Texas's implications" in tail)
    check("and it stays one line, not a fourth constraint block",
          "say the plain thing instead" in tail
          and tail.count("Write the joins in plain speech") == 1)

    # It has to reach the model AFTER the packet dump, like the other two
    # blocks -- a length target stated in the header is the one that failed.
    check("length guidance sits after the packet, in the assignment",
          user_text.rindex("LENGTH: TARGET")
          > user_text.rindex("=== WEEK PACKET"))


def test_market_gap_space_ban():
    """The raw-field-name ban must catch the unhyphenated form too.

    House style banned "market_gap" and panel wk0 filed "The market gap here
    is a negative 1.044" -- the same field name read aloud with the underscore
    dropped. A ban written against one spelling is a ban against one spelling.
    """
    print("\nRaw field names are banned with or without the underscore:")
    base = _seeded_packet()
    with sandbox("panel", base):
        _, user_text, _ = G.build_prompt("panel")

    check("space form: the prompt says the space is not a disguise",
          "THE SPACE IS NOT A DISGUISE" in user_text)
    check("space form: 'market gap' is named as banned, not just market_gap",
          '"market gap" and "the market gap" are the same violation'
          in user_text.replace(chr(8220), '"').replace(chr(8221), '"'))
    check("space form: the shipped failure is quoted verbatim",
          "The market gap here is a negative 1.044" in user_text)
    check("space form: the other spaced field names are covered too",
          all(t in user_text for t in ("delta impact", "narrative score",
                                       "moment size", "implied expected wins")))
    check("space form: the two-word packet leaks are banned as phrases",
          "packet says" in user_text and "packet gives" in user_text)
    check("space form: the underscore ban itself is still there",
          "market_gap" in user_text)
    check("space form: the read-back check names it",
          "no field name is read aloud with the underscore removed"
          in user_text)


def test_superlative_ban():
    """No ranking claims, and a hedge is not an escape hatch.

    The coda block already forbade "the lowest on the board". Panel wk0
    answered with "the lowest on the board OUTSIDE Blaine and Chris's tussle"
    -- hedged into technical truth and still a ranking claim the packet never
    made. So this is a word test, like the prior-season ban, not a concept.
    """
    print("\nSuperlatives about rank or magnitude are banned:")
    base = _seeded_packet()
    with sandbox("panel", base):
        _, user_text, _ = G.build_prompt("panel")

    check("superlatives: the block is present",
          "NO SUPERLATIVES ABOUT RANK OR MAGNITUDE" in user_text)
    check("superlatives: every banned word is named",
          all(w in user_text for w in ("lowest", "highest", "only", "most",
                                       "biggest", "smallest", "worst", "best",
                                       "sharpest")))
    check("superlatives: hedges are named as the same violation",
          "A HEDGE DOES NOT RESCUE ONE" in user_text)
    check("superlatives: the specific hedge forms are listed",
          all(h in user_text for h in ("second-lowest", "one of the",
                                       "the lowest outside")))
    check("superlatives: the shipped failure is quoted verbatim",
          "the lowest on the board outside Blaine and Chris's tussle"
          in user_text)
    check("superlatives: it says what to do INSTEAD, not just what to avoid",
          "WHAT TO DO INSTEAD" in user_text
          and "describe what the pick IS" in user_text)
    check("superlatives: the read-back check names it",
          "no superlative or hedged superlative ranks" in user_text)


def test_invented_stakes_ban():
    """packet.stakes is None means no prize, no motivation, no consequence.

    "This group has no declared stakes. Do not invent any." named the field
    but not the behaviour, and panel wk0 filed "both playing for pride and,
    possibly, the chance to needle the other until next year".
    """
    print("\nInvented stakes are banned by name:")
    base = _seeded_packet()
    base.pop("stakes", None)
    with sandbox("panel", base):
        _, user_text, _ = G.build_prompt("panel")

    check("stakes: the None is named directly",
          "packet.stakes is None" in user_text)
    check("stakes: motivations and what they are playing for are covered",
          "motivations for winning" in user_text)
    check("stakes: the specific phrases are banned by name",
          all(t in user_text for t in ("playing for pride", "bragging rights",
                                       "the chance to needle")))
    check("stakes: winner-gets / loser-suffers is covered",
          "what the winner gets or the loser suffers" in user_text)
    check("stakes: the shipped failure is quoted verbatim",
          "both playing for pride and, possibly, the chance to needle the "
          "other until next year" in user_text)
    check("stakes: there is no soft version offered",
          "There is no soft version of this" in user_text)
    check("stakes: the read-back check names it",
          "nothing says or implies what anyone is playing for" in user_text)

    # A group that HAS stakes still gets to use them.
    withstakes = _seeded_packet()
    withstakes["stakes"] = "loser buys dinner"
    with sandbox("panel", withstakes):
        _, user_text, _ = G.build_prompt("panel")
    check("stakes: a real declared stake is still passed through",
          "loser buys dinner" in user_text
          and "packet.stakes is None" not in user_text)


def main():
    print("generate_commentary.py \u2014 prompt assembly, dry run, fail-soft")
    test_prompt_shape()
    test_basis_warning()
    test_stakes()
    test_season_complete()
    test_uniform_fields()
    test_suppressed_fields()
    test_head_to_head_named_in_prose()
    test_persona_material_carries_no_tone_rule()
    test_publish_doc()
    test_archive_index()
    test_regeneration_does_not_cite_itself()
    test_coda_superlative_matches_the_selection_rule()
    test_prior_season_ban_is_a_word_test()
    test_house_style_structures_and_percentages()
    test_market_gap_space_ban()
    test_superlative_ban()
    test_invented_stakes_ban()
    test_length_guidance_survives_the_bans()
    test_no_prior_season_language()
    test_dry_run()
    test_fail_soft()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
