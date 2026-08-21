#!/usr/bin/env python3
"""
test_enter_draft.py — Locks the enter_draft.py contract (no network, no writes
into groups/).

Covers:
  1. the parse grammar — header mode, inline manager, fillers, list markers,
     case chaos, the three number forms, and a missing number,
  2. the resolution rules — resolve_team's ambiguity guard is fatal and is never
     laundered by the rewrite ladder; the stored string round-trips to a
     reference KEY and is never a display_name,
  3. conference/line are DERIVED from the reference and a dictated number that
     disagrees is fatal, not an override,
  4. the failure taxonomy — all six acceptance failures surface in ONE run,
  5. no partial writes — a failing block writes nothing, and --dry-run (the
     default) writes nothing even when the block is clean,
  6. the artifact this script writes PASSES validate_group_data unchanged.

Runs both ways, and they are equivalent: pytest collects one test per
numbered section above and conftest.py raises on any check() the section
recorded as FAIL; the standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_enter_draft.py
    python scripts/test_enter_draft.py
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import enter_draft as ed
import validate_team_names as gate

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
#
# This file used to keep a FAILURES list of bare labels instead. Same information,
# different name — but the gate looks for _res, so the rename is what lets these
# twelve sections fail under pytest at all. The printed line is unchanged.
_res = []


def check(label, ok, detail=""):
    _res.append((label, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"  [{detail}]" if detail else ""))


def codes(problems):
    return sorted(p["code"] for p in problems)


def run(text, config, group_id="panel"):
    """Stages 1-4 without touching disk."""
    picks, traces, problems, failed = ed.parse_block(text, config)
    rprobs, _ = ed.roster_problems(group_id, config, picks, failed)
    return picks, traces, problems + rprobs


CLEAN = """Zach:
  Ohio State under nine and a half
  georgia UNDER 9.5
- Miami (FL) under ten and a half
  zach's BYU under eight and a half

chris takes Indiana under 10.5
CHRIS TAKES OLE MISS UNDER SEVEN AND A HALF
chris - georgia tech under six and a half
4) chris texas tech under 10.5

Blaine:
Ohio State over nine and a half
texas a and m over 8.5
blaine picks Virginia over seven and a half
* blaine Utah over 8.5

jonathan takes Memphis over seven and a half
jonathan oklahoma state over 6.5
Jonathan: Alabama over eight and a half
jonathan takes James Madison over
"""

# One block, six deliberate defects: misspelled team, bare ambiguous token, a
# team absent from the reference, a duplicate same-side pair, a dictated line
# that disagrees with the frozen reference, and a wrong pick count.
BAD = """Zach:
Ohio Stat under nine and a half
Georgia under ten and a half
Miami under ten and a half
BYU under eight and a half

chris takes Indiana under 10.5
chris takes Ole Miss under seven and a half
chris takes Georgia Tech under 6.5
chris takes Texas Tech under 10.5
chris takes Alabama over eight and a half

Blaine:
North Dakota State over seven and a half
Alabama over eight and a half
Virginia over 7.5
Utah over 8.5

jonathan takes Memphis over seven and a half
jonathan takes Oklahoma State over 6.5
jonathan takes James Madison over eight and a half
jonathan takes Auburn over six and a half
"""


_CONFIG = _REFERENCE = _CLEAN_RUN = None


def _config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = utils.load_group_config("panel")
    return _CONFIG


def _reference():
    global _REFERENCE
    if _REFERENCE is None:
        _REFERENCE = gate.load_win_totals()
    return _REFERENCE


def _clean_run():
    """run(CLEAN, config), memoised.

    Sections 2-5 all read the same parse of the same clean block -- main()
    computed it once in section 2 and sections 3-5 reached back for it. Memoising
    keeps that sharing while letting any one of them run alone under `pytest -k`.
    """
    global _CLEAN_RUN
    if _CLEAN_RUN is None:
        _CLEAN_RUN = run(CLEAN, _config())
    return _CLEAN_RUN


def test_number_grammar():
    """[1] the three number forms, plus a missing and an unreadable one."""
    print("[1] number grammar")
    for span, expect in [(["nine", "and", "a", "half"], 9.5),
                         (["9", "and", "a", "half"], 9.5),
                         (["nine", "point", "five"], 9.5),
                         (["8.5"], 8.5),
                         (["eleven"], 11.0),
                         ([], None)]:
        got, err = ed.parse_number(span)
        check(f"parse_number({span!r}) -> {expect!r}", got == expect and not err,
              f"got {got!r} err={err!r}")
    got, err = ed.parse_number(["a", "bunch"])
    check("unreadable number is an error, not a silent None", got is None and err)


def test_clean_block_parses_to_a_valid_roster():
    """[2] header mode, inline manager, fillers, list markers, case chaos."""
    print("\n[2] clean block parses to a full, valid roster")
    picks, traces, problems = _clean_run()
    check("no problems", not problems, str(codes(problems)))
    check("16 picks", len(picks) == 16, str(len(picks)))
    check("directions are uppercase O/U only",
          {p["direction"] for p in picks} <= {"O", "U"})
    check("two manager headers recognized",
          len([t for t in traces if t["kind"] == "header"]) == 2)
    check("' and ' -> '&' rewrite fired and is reported",
          any(t.get("note") == "rewrote ' and ' -> '&'"
              and t["stored"] == "Texas A&M"
              for t in traces if t["kind"] == "pick"))


def test_every_trace_row_carries_line_provenance():
    """[3] a line is either dictated-and-matched or taken from the reference."""
    print("\n[3] every trace row carries line provenance")
    _, traces, _ = _clean_run()
    rows = [t for t in traces if t["kind"] == "pick"]
    check("every row has a line_source",
          all(t["line_source"] in ("dictated and matched", "taken from reference")
              for t in rows))
    check("the un-dictated line is labelled 'taken from reference'",
          [t["line_source"] for t in rows if t["stored"] == "James Madison"]
          == ["taken from reference"])
    check("a dictated-and-matched line is labelled as such",
          [t["line_source"] for t in rows if t["stored"] == "Ohio State"]
          == ["dictated and matched"] * 2)


def test_stored_names_round_trip_to_reference_keys():
    """[4] the stored string is a reference KEY and is never a display_name."""
    print("\n[4] stored names round-trip to a reference KEY, never a display_name")
    picks, _, _ = _clean_run()
    reference = _reference()
    for p in picks:
        key = utils.normalize_team_name(p["team"])
        check(f"{p['team']!r} is a reference key", key in reference)
    stored = {p["team"] for p in picks}
    check("dictated 'Miami (FL)' stored as canonical 'Miami' (the reference KEY)",
          "Miami" in stored and "Miami (FL)" not in stored)


def test_conference_and_line_are_derived():
    """[5] conference and line come from the reference, never from the dictation."""
    print("\n[5] conference and line are DERIVED from the reference")
    picks, _, _ = _clean_run()
    reference = _reference()
    mismatched = [p for p in picks
                  if reference[utils.normalize_team_name(p["team"])]["conference"]
                  != p["conference"]
                  or float(reference[utils.normalize_team_name(p["team"])]["win_total"])
                  != p["line"]]
    check("no pick's conference/line differs from the reference", not mismatched,
          str(mismatched[:2]))


def test_ambiguity_guard_is_fatal():
    """[6] resolve_team's guard is fatal and the rewrite ladder cannot launder it."""
    print("\n[6] the ambiguity guard is fatal and is never laundered")
    config = _config()
    _, _, probs = run("zach Miami over ten and a half\n", config)
    check("bare 'Miami' -> E-AMBIGUOUS-TEAM",
          "E-AMBIGUOUS-TEAM" in codes(probs), str(codes(probs)))
    _, _, probs = run("zach USC over eight and a half\n", config)
    check("bare 'USC' -> E-AMBIGUOUS-TEAM",
          "E-AMBIGUOUS-TEAM" in codes(probs), str(codes(probs)))
    try:
        ed.resolve_ladder("Miami and")            # a rewrite variant exists...
        check("rewrite ladder cannot launder an ambiguous token", False)
    except utils.AmbiguityError:
        check("rewrite ladder cannot launder an ambiguous token", True)
    except utils.UnknownTeamError:
        check("rewrite ladder cannot launder an ambiguous token", False,
              "resolved past the guard")


def test_dictated_line_disagreement_is_fatal():
    """[7] a dictated line that disagrees with the reference is fatal, not an override."""
    print("\n[7] a dictated line that disagrees is fatal, not an override")
    _, _, probs = run("zach Georgia under ten and a half\n", _config())
    check("E-LINE-DISAGREES raised", "E-LINE-DISAGREES" in codes(probs),
          str(codes(probs)))


def test_all_acceptance_failures_surface_together():
    """[8] all six acceptance failures surface in ONE run."""
    print("\n[8] all six acceptance failures surface in ONE run")
    picks, _, problems = run(BAD, _config())
    found = set(codes(problems))
    for code in ["E-UNKNOWN-TEAM", "E-AMBIGUOUS-TEAM", "E-LINE-DISAGREES",
                 "E-SAME-TEAM-SAME-SIDE", "E-PICK-COUNT",
                 "E-DISTINCT-CONFERENCES"]:
        check(f"{code} present", code in found)
    check("reported together in one run (>= 6 problems)", len(problems) >= 6,
          str(len(problems)))


def test_not_in_reference_fires_on_drift():
    """[9] E-NOT-IN-REFERENCE fires when a canonical team has no line.

    canonical and the reference are 1:1 today, so this guard is only reachable
    with a reference that is missing a team — which is exactly the drift it
    exists to catch. The trimmed reference lives in a tempdir and the module
    globals are restored in a finally, so data/ is never touched.
    """
    print("\n[9] E-NOT-IN-REFERENCE fires when a canonical team has no line")
    with tempfile.TemporaryDirectory() as td:
        trimmed = Path(td) / "ref_trimmed.json"
        raw = utils.load_json(ed.DEFAULT_REFERENCE)
        raw["teams"].pop("Utah")
        trimmed.write_text(json.dumps(raw), encoding="utf-8")
        saved_path, saved_cache = gate.WIN_TOTALS_PATH, gate._WIN_TOTALS
        try:
            gate.WIN_TOTALS_PATH, gate._WIN_TOTALS = trimmed, None
            _, _, probs = run("blaine Utah over 8.5\n", _config())
            check("E-NOT-IN-REFERENCE raised",
                  "E-NOT-IN-REFERENCE" in codes(probs), str(codes(probs)))
        finally:
            gate.WIN_TOTALS_PATH, gate._WIN_TOTALS = saved_path, saved_cache


def test_artifact_passes_validate_group_data():
    """[10] the produced artifact passes validate_group_data unchanged."""
    print("\n[10] the produced artifact passes validate_group_data unchanged")
    config = _config()
    picks, _, problems = run(CLEAN, config)
    payload = ed.build_payload("panel", "final", picks, ed.DEFAULT_REFERENCE, 2026)
    checked, errors = gate.validate_group_data("panel", config, payload["picks"])
    check("gate reports no errors", not errors, str(errors[:2]))
    check("gate checked all 16", checked == 16, str(checked))
    check("draft_status is final", payload["draft_status"] == "final")
    check("_note does not carry the dummy-data warning forward",
          "DUMMY" not in payload["_note"])


def test_no_partial_writes():
    """[11] no partial writes — and every path here writes into a tempdir only.

    --out is pointed at a TemporaryDirectory for the whole block, so the failing
    run, the dry run and the two --write runs all stay out of groups/
    (CLAUDE.md P2 #14).
    """
    print("\n[11] no partial writes")
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "picks.json"

        code = _main_quietly("--group", "panel", "--in",
                             str(_write(td, "bad.txt", BAD)),
                             "--out", str(target), "--write")
        check("a failing block exits 1", code == ed.EXIT_INVALID, str(code))
        check("a failing block writes nothing", not target.exists())
        check("and leaves no .tmp behind",
              not (target.parent / "picks.json.tmp").exists())

        code = _main_quietly("--group", "panel", "--in",
                             str(_write(td, "clean.txt", CLEAN)),
                             "--out", str(target))
        check("dry run (the default) exits 0", code == ed.EXIT_OK, str(code))
        check("dry run writes nothing even when the block is clean",
              not target.exists())

        code = _main_quietly("--group", "panel", "--in",
                             str(_write(td, "clean.txt", CLEAN)),
                             "--out", str(target), "--write")
        check("--write commits", code == ed.EXIT_OK and target.exists(), str(code))
        code = _main_quietly("--group", "panel", "--in",
                             str(_write(td, "clean.txt", CLEAN)),
                             "--out", str(target), "--write")
        check("re-writing over a 'final' file is refused without --force",
              code == ed.EXIT_INVALID, str(code))


def test_default_target_path():
    """[12] default target resolves to groups/<group>/picks.json (read only)."""
    print("\n[12] default target resolves to groups/<group>/picks.json")
    check("default out path",
          (utils.GROUPS_DIR / "panel" / "picks.json").exists())


def main():
    test_number_grammar()
    test_clean_block_parses_to_a_valid_roster()
    test_every_trace_row_carries_line_provenance()
    test_stored_names_round_trip_to_reference_keys()
    test_conference_and_line_are_derived()
    test_ambiguity_guard_is_fatal()
    test_dictated_line_disagreement_is_fatal()
    test_all_acceptance_failures_surface_together()
    test_not_in_reference_fires_on_drift()
    test_artifact_passes_validate_group_data()
    test_no_partial_writes()
    test_default_target_path()

    print()
    failed = [label for label, ok, _ in _res if not ok]
    if failed:
        print(f"FAILED \u2014 {len(failed)} check(s): {failed}")
        return 1
    print("All enter_draft checks passed.")
    return 0


def _write(td, name, text):
    p = Path(td) / name
    p.write_text(text, encoding="utf-8")
    return p


def _main_quietly(*args):
    """enter_draft.main() with argv swapped and its report swallowed — the
    report itself is asserted elsewhere; here only the exit code and the
    on-disk effect matter."""
    argv = sys.argv
    try:
        sys.argv = ["enter_draft.py", *args]
        with contextlib.redirect_stdout(io.StringIO()):
            return ed.main()
    finally:
        sys.argv = argv


if __name__ == "__main__":
    sys.exit(main())
