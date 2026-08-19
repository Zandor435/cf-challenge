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
     Saturday: true numbers, false claim.

Everything here is a DRY RUN — no network call is made, and no OPENAI_API_KEY is
needed or read. Fixtures are written to a temp dir with utils.GROUPS_DIR
redirected, so production files are never touched (playbook rule 14).

Usage:
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

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
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


def validate_prompt_shape(packet):
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
    return user_text


def validate_basis_warning(base_packet):
    """The comparison basis must be stated honestly for all three gap shapes."""
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


def validate_stakes(base_packet):
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


def validate_dry_run(base_packet):
    """--dry-run writes a complete preview and makes no network call."""
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


def validate_fail_soft(base_packet):
    """Live mode must exit 0 on every failure path — including SystemExit."""
    argv, key = sys.argv, os.environ.pop("OPENAI_API_KEY", None)
    try:
        # No key: the earliest live failure.
        sys.argv = ["generate_commentary.py", "--group", "panel"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = G.main()
        check("live without a key: exits 0", code == 0, f"returned {code}")
        check("live without a key: warns loudly", "::warning::" in err.getvalue())
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


def main():
    print("generate_commentary.py — prompt assembly, dry run, fail-soft\n")
    packet = B.build_packet("panel")

    print("Prompt shape:")
    validate_prompt_shape(packet)

    print("\nComparison basis (the *_this_week honesty guard):")
    validate_basis_warning(packet)

    print("\nStakes passthrough:")
    validate_stakes(packet)

    print("\nDry run:")
    validate_dry_run(packet)

    print("\nFail-soft:")
    validate_fail_soft(packet)

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
