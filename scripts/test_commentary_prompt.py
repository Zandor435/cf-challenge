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
    check("no stakes: instructs the pundit not to invent any",
          "Do not invent any" in user_text)

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
    check("prior-season: the returning-to-glory sense of 'back' is named",
          "returning-to-glory" in user_text and "back on track" in user_text)
    check("prior-season: the quiet trajectory words are named too",
          all(w in user_text for w in ('"again"', '"still"', '"no longer"')))
    check("prior-season: synonyms are closed off, not just the listed words",
          "or imply by any synonym" in user_text)
    check("prior-season: it says a packet number is a fact about NOW",
          "fact about NOW" in user_text)

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
    print("\nPublished column.json shape:")
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
    check("publish: the target is docs/data/<group>/column.json",
          G.publish_path("family") == utils.WEB_DATA_DIR / "family" / "column.json",
          str(G.publish_path("family")))

    # JSON-serialisable, because it is written straight to the web root.
    try:
        json.dumps(doc)
        ok = True
    except TypeError as e:
        ok, _ = False, e
    check("publish: the document is JSON-serialisable", ok)


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


def test_packet_bookkeeping_is_not_printable():
    """narrative_score orders the storylines; it is not a fact about the pool.

    The family column filed "this family clash carries a narrative score of
    6.338, the highest on the board" -- the scorer's opinion of the story,
    printed as though it were a result, plus raw field names on the page.
    """
    print("\nPacket bookkeeping is banned from the prose:")
    packet = _seeded_packet()
    with sandbox("panel", packet):
        _, user_text, _ = G.build_prompt("panel")
    tail = user_text.split("=== YOUR ASSIGNMENT ===")[1]
    check("the ban names narrative_score", "no narrative_score" in tail)
    check("the ban names moment_size and ranking", "moment_size" in tail
          and "storyline rank" in tail)
    check("and it bans raw field names, with the fix demonstrated",
          "no raw field names" in tail
          and "the market disagrees by" in tail)


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
    test_regeneration_does_not_cite_itself()
    test_coda_superlative_matches_the_selection_rule()
    test_packet_bookkeeping_is_not_printable()
    test_no_prior_season_language()
    test_dry_run()
    test_fail_soft()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
