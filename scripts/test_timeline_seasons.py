#!/usr/bin/env python3
"""
test_timeline_seasons.py — run_groups.append_timeline's season scoping.

WHAT THIS EXISTS TO STOP. A timeline snapshot is keyed by WEEK ALONE — there is
no season inside a snapshot — and timeline.json used to be append-only across
seasons. Two faults followed, and both were silent:

  1. STALE TAG. `season` was stamped only when the file was CREATED, so a
     timeline that survived a rollover kept the previous season's tag forever.
     All four groups declared 2025 while holding a snapshot written by a 2026
     run. analytics.select_prior refuses a timeline whose declared season is not
     the one being scored, so week_move would have stayed null past kickoff.

  2. CROSS-SEASON OVERWRITE. Dedupe compared `as_of_week` only, so 2026's week 6
     would have replaced 2025's week-6 row in place — scored history destroyed
     with no error and nothing in the output to notice it by.

The fix scopes the FILE to one season: the live timeline.json always holds the
current season and declares it, and a rollover moves the previous season aside
to timeline-<season>.json. Everything downstream reads timeline.json by that
exact name and none of it can distinguish one season's week 6 from another's, so
the guarantee has to live in the data rather than in three separate readers.

The invariant, stated once: a snapshot from season A can never be selected when
scoring season B, and a week-6 row from one season can never overwrite another's.

Offline and hermetic — every check runs against a temp directory, so nothing here
reads or writes a committed board.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_timeline_seasons.py
    python scripts/test_timeline_seasons.py
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import run_groups
import analytics

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []

GID = "tl-test"
CONFIG = {"group_id": GID}


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def snapshot(week, total, stamp):
    """A real snapshot via the production builder, so this file cannot drift
    from the shape run_groups actually writes."""
    standings = {
        "meta": {"generated_at": stamp},
        "managers": [{
            "manager_id": "a", "picks": [
                {"team": "Ohio State", "banked_delta": total,
                 "floor": total - 2.0, "ceiling": total + 2.0}]}],
    }
    return run_groups.build_snapshot(standings, None, week)


@contextlib.contextmanager
def sandbox():
    """A temp WEB_DATA_DIR with the group dir made. Patches the utils constant
    the same way test_output_shape.py does — no committed file is touched."""
    with tempfile.TemporaryDirectory() as td:
        orig = utils.WEB_DATA_DIR
        utils.WEB_DATA_DIR = Path(td)
        (Path(td) / GID).mkdir(parents=True, exist_ok=True)
        try:
            yield Path(td)
        finally:
            utils.WEB_DATA_DIR = orig


def quiet(fn):
    """(return value, everything it printed)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn()
    return out, buf.getvalue()


def read(root, name):
    return json.loads((root / GID / name).read_text(encoding="utf-8"))


def weeks(tl):
    return [s.get("as_of_week") for s in tl["snapshots"]]


def test_season_tag_written_every_run():
    """The season tag is written on EVERY run, not only at file creation."""
    # --- 1. the tag is written every run, not only at creation --------------
    print("the season tag")
    with sandbox() as root:
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(1, 1.0, "s1"), season=2025))
        check("a fresh file declares the season being scored",
              read(root, "timeline.json")["season"] == 2025)

        # Corrupt the tag the way a rollover used to leave it, then re-run in the
        # SAME season. The stamp must be repaired, not left alone.
        p = root / GID / "timeline.json"
        stale = json.loads(p.read_text(encoding="utf-8"))
        stale["season"] = None
        p.write_text(json.dumps(stale), encoding="utf-8")
        _, log = quiet(lambda: run_groups.append_timeline(
            CONFIG, snapshot(2, 2.0, "s2"), season=2025))
        tl = read(root, "timeline.json")
        check("an untagged file adopts the run's season", tl["season"] == 2025)
        check("...and says so, because an untagged file is how the stale tag hid",
              "::warning::" in log and "no `season`" in log, log.strip() or "(silent)")
        check("appending in the same season keeps earlier weeks",
              weeks(tl) == [1, 2], str(weeks(tl)))


def test_rollover_rescopes_the_live_file():
    """FAULT 1: a rollover re-scopes the live file and archives what it displaced."""
    # --- 2. FAULT 1: a rollover re-scopes the live file ---------------------
    print("\nrollover — the live file follows the season")
    with sandbox() as root:
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 6.0, "a6"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(16, 16.0, "a16"), season=2025))
        before = read(root, "timeline.json")["snapshots"]

        _, log = quiet(lambda: run_groups.append_timeline(
            CONFIG, snapshot(1, 1.0, "b1"), season=2026))
        live = read(root, "timeline.json")
        arch = read(root, "timeline-2025.json")

        check("the live file now declares the new season", live["season"] == 2026)
        check("the live file holds ONLY the new season's weeks",
              weeks(live) == [1], str(weeks(live)))
        check("the previous season is archived, not deleted",
              (root / GID / "timeline-2025.json").exists())
        check("the archive declares the season it holds", arch["season"] == 2025)
        check("every archived snapshot is byte-identical to what was live",
              arch["snapshots"] == before, f"{len(arch['snapshots'])} snapshot(s)")
        check("the rollover is announced", "rolled over" in log, log.strip() or "(silent)")


def test_a_week_cannot_cross_seasons():
    """FAULT 2: week 6 of one season can never overwrite week 6 of another."""
    # --- 3. FAULT 2: a week cannot cross seasons ---------------------------
    print("\ncross-season overwrite is impossible")
    with sandbox() as root:
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 60.0, "a6"), season=2025))
        old6 = read(root, "timeline.json")["snapshots"][0]
        # The exact collision the old dedupe would have taken in place.
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 99.0, "b6"), season=2026))

        live = read(root, "timeline.json")
        arch = read(root, "timeline-2025.json")
        check("2025 week 6 survives the arrival of 2026 week 6",
              arch["snapshots"] == [old6])
        check("...with its own numbers, unmodified",
              arch["snapshots"][0]["managers"][0]["picks"][0]["banked_delta"] == 60.0)
        check("2026 week 6 is a separate row in a separate file",
              weeks(live) == [6]
              and live["snapshots"][0]["managers"][0]["picks"][0]["banked_delta"] == 99.0)
        check("no file holds two week-6 rows",
              len(live["snapshots"]) == 1 and len(arch["snapshots"]) == 1)


def test_within_one_season_nothing_changed():
    """Inside one season the old append/dedupe/sort contract still holds exactly."""
    # --- 4. within a season, nothing changed -------------------------------
    print("\nwithin one season, the old contract still holds")
    with sandbox() as root:
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 6.0, "x"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 6.5, "y"), season=2025))
        tl = read(root, "timeline.json")
        check("idempotent: re-running a week does not duplicate it",
              weeks(tl) == [6], str(weeks(tl)))
        check("...and the re-run's values win",
              tl["snapshots"][0]["managers"][0]["picks"][0]["banked_delta"] == 6.5)

        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(2, 2.0, "z"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(None, 0.0, "p"), season=2025))
        tl = read(root, "timeline.json")
        check("sorted ascending with the unresolvable-week row last",
              weeks(tl) == [2, 6, None], str(weeks(tl)))
        # Two null-week rows can only exist if dedupe fails, but the sort key is
        # total so it cannot be the thing that raises.
        check("the sort key is total (two null weeks would not raise)",
              sorted([{"as_of_week": None}, {"as_of_week": None}, {"as_of_week": 3}],
                     key=run_groups._week_sort_key)[0]["as_of_week"] == 3)


def test_rearchiving_is_additive():
    """season.json gets flipped for replays, so the same season can roll twice."""
    # --- 5. re-archiving is additive ---------------------------------------
    # season.json gets flipped for replays, so the same season can roll twice.
    print("\nre-archiving a season adds, never rewrites")
    with sandbox() as root:
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 6.0, "first"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(1, 1.0, "b"), season=2026))
        # Flip back to 2025, write weeks 6 (again, different numbers) and 7...
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 99.0, "second"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(7, 7.0, "seventh"), season=2025))
        # ...then forward again, which re-archives 2025 on top of an archive.
        _, log = quiet(lambda: run_groups.append_timeline(
            CONFIG, snapshot(2, 2.0, "b2"), season=2026))
        arch = read(root, "timeline-2025.json")
        wk6 = [s for s in arch["snapshots"] if s["as_of_week"] == 6]
        check("the already-archived week 6 is NOT rewritten",
              len(wk6) == 1
              and wk6[0]["managers"][0]["picks"][0]["banked_delta"] == 6.0,
              f"delta={wk6[0]['managers'][0]['picks'][0]['banked_delta']}")
        check("the week the archive did not have IS added",
              sorted(w for w in weeks(arch) if w is not None) == [6, 7], str(weeks(arch)))
        check("the merge says what it kept and what it added",
              "merged" in log and "untouched" in log, log.strip() or "(silent)")


def test_select_prior_over_a_rolled_timeline():
    """End to end against the reader this was breaking."""
    # --- 6. end to end against analytics.select_prior -----------------------
    # The reader this was breaking. A 2026 week-1 run must not be refused for a
    # season mismatch, and must not reach back into 2025.
    print("\nend to end — analytics.select_prior over a rolled timeline")
    with sandbox() as root:
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(6, 6.0, "a6"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(16, 16.0, "a16"), season=2025))
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(1, 1.0, "b1"), season=2026))
        live = read(root, "timeline.json")

        snap, reason = analytics.select_prior(live, 1, 2026)
        check("2026 week 1: no season mismatch",
              reason != analytics.PRIOR_SEASON_MISMATCH, f"reason={reason}")
        check("2026 week 1: no earlier week yet, so an honest null",
              (snap, reason) == (None, analytics.PRIOR_NO_EARLIER_WEEK))

        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(2, 2.0, "b2"), season=2026))
        live = read(root, "timeline.json")
        snap, reason = analytics.select_prior(live, 2, 2026)
        check("2026 week 2 selects 2026 week 1, not 2025 week 16",
              reason is None and snap["as_of_week"] == 1
              and snap["managers"][0]["picks"][0]["banked_delta"] == 1.0,
              f"week={snap and snap['as_of_week']}")

        # The old behavior's worst case: a week high enough that 2025's weeks
        # would qualify on a plain strictly-before test.
        quiet(lambda: run_groups.append_timeline(CONFIG, snapshot(17, 17.0, "b17"), season=2026))
        live = read(root, "timeline.json")
        snap, _ = analytics.select_prior(live, 17, 2026)
        check("2026 week 17 cannot reach 2025's week 16",
              snap["as_of_week"] == 2, f"selected week {snap['as_of_week']}")
        check("no 2025 snapshot is even present in the live file",
              set(weeks(live)) == {1, 2, 17}, str(weeks(live)))


def main():
    test_season_tag_written_every_run()
    test_rollover_rescopes_the_live_file()
    test_a_week_cannot_cross_seasons()
    test_within_one_season_nothing_changed()
    test_rearchiving_is_additive()
    test_select_prior_over_a_rolled_timeline()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
