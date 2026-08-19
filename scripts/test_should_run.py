#!/usr/bin/env python3
"""
test_should_run.py — the scheduled pipeline's run gate (CLAUDE.md rule 7).

This gate decides whether the daily cron does any work at all, so both of its
failure directions are expensive and neither is loud on its own:

  stuck OPEN   every quiet day burns API budget against the free tier (rule 6).
  stuck CLOSED the site silently goes stale — a green run that did nothing
               looks identical to a green run that updated everything, which
               is the exact failure rule 3 exists to prevent.

So the RUN escapes are tested as carefully as the SKIPs. The nastiest case is
the season flip: the moment season.json moves to the new year, the committed
cache still holds LAST season's dates, so every window test says "no games"
forever and the fetch that would fix it never runs. The gate must break that
deadlock by itself.

Fixtures are synthetic caches built in a tempdir — never written into the
files production reads (CLAUDE.md P2 #14).

Usage:
    python scripts/test_should_run.py
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import should_run as sr

SEASON = 2026
NOW = datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc)   # a Sunday, 13:00 UTC

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def cache(season=SEASON, offsets_h=(), extra=None):
    """A cache whose games kick off at NOW + each offset (hours)."""
    games = [{"start_date": (NOW + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
              "week": 1, "completed": h < 0} for h in offsets_h]
    return {"season": season, "games": games, **(extra or {})}


def verdict(c, present=True, force=False, now=NOW):
    return sr.decide(now, present, c, SEASON, sr.DEFAULT_LOOKBACK_HOURS,
                     sr.DEFAULT_SETTLE_HOURS, force)


def main():
    print("should_run gate")

    # ---- RUN escapes: these must never be blockable ------------------------
    run, why = verdict(cache(offsets_h=(+100,)), force=True)
    check("manual dispatch always runs, even on a dead day", run, why)

    run, why = verdict({}, present=False)
    check("missing cache runs (a fetch must populate it)", run, why)

    # The deadlock this gate exists to survive.
    run, why = verdict(cache(season=SEASON - 1, offsets_h=(-24 * 300,)))
    check("season flip runs despite last season's stale dates", run, why)
    check("...and says why in the reason", "season flip" in why, why)

    run, why = verdict(cache(offsets_h=()))
    check("cache with no games runs (rebuild it)", run, why)

    # ---- the window --------------------------------------------------------
    run, why = verdict(cache(offsets_h=(-20,)))
    check("game 20h ago -> run", run, why)

    run, why = verdict(cache(offsets_h=(-2,)))
    check("game 2h ago (still in progress) -> skip, settle window", not run, why)

    run, why = verdict(cache(offsets_h=(-40, +40)))
    check("game 40h ago is outside lookback -> skip", not run, why)

    # A whole Saturday slate, the ordinary Sunday-morning case.
    slate = tuple(range(-21, -9))
    run, why = verdict(cache(offsets_h=slate))
    check("full Saturday slate -> run", run, why)

    # ---- season edges ------------------------------------------------------
    run, why = verdict(cache(offsets_h=(+24 * 10,)))
    check("before first kickoff -> skip (pre-season)", not run, why)
    check("...self-removing: names the real first kickoff, no date literal",
          "pre-season" in why and "2026-09" in why, why)

    run, why = verdict(cache(offsets_h=(-24 * 30,)))
    check("30 days after the last game -> skip (season complete)", not run, why)

    # ---- bye week, called out explicitly -----------------------------------
    run, why = verdict(cache(offsets_h=(-24 * 6, +24 * 6)))
    check("gap either side -> skip as a BYE WEEK, not a quiet day",
          not run and "bye week" in why, why)

    run, why = verdict(cache(offsets_h=(-24 * 3, +24 * 2)))
    check("game 3 days back, another in 2 -> quiet day, not a bye",
          not run and "bye week" not in why, why)

    # ---- malformed input is tolerated, not fatal (rule 10) -----------------
    c = cache(offsets_h=(-20,))
    c["games"].append({"start_date": "not-a-date", "week": 1})
    c["games"].append({"week": 1})
    run, why = verdict(c)
    check("unparseable start_date is skipped, not fatal", run, why)

    # ---- fail-open, end to end through main() ------------------------------
    # A corrupt cache must produce run=true, exit 0 — never a silent skip.
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "corrupt.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        out = Path(td) / "gh_output"
        out.write_text("", encoding="utf-8")
        env = {**os.environ, "GITHUB_OUTPUT": str(out)}
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "should_run.py"),
             "--cache", str(bad)],
            capture_output=True, text=True, env=env)
        emitted = out.read_text(encoding="utf-8")
        check("corrupt cache fails OPEN (run=true), never a silent skip",
              "run=true" in emitted, emitted.strip().replace("\n", " | "))
        check("...and exits 0 so the workflow branches rather than errors",
              proc.returncode == 0, f"rc={proc.returncode}")
        check("...and warns loudly", "::warning" in proc.stdout, proc.stdout.strip()[:90])

    # ---- the emitted contract the workflow branches on ---------------------
    with tempfile.TemporaryDirectory() as td:
        cpath = Path(td) / "cache.json"
        cpath.write_text(json.dumps(cache(offsets_h=(+24 * 10,))), encoding="utf-8")
        out = Path(td) / "gh_output"
        out.write_text("", encoding="utf-8")
        env = {**os.environ, "GITHUB_OUTPUT": str(out)}
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "should_run.py"),
             "--cache", str(cpath), "--now", "2026-09-13T13:00:00Z"],
            capture_output=True, text=True, env=env)
        emitted = out.read_text(encoding="utf-8")
        # season.json is 2025 today and the fixture is 2026, so this exercises
        # the flip escape through the real entry point.
        check("emits a run= line the workflow can branch on",
              any(l.startswith("run=") for l in emitted.splitlines()), emitted.strip())
        check("emits a reason= line", "reason=" in emitted, emitted.strip())
        check("exit 0 on a normal decision", proc.returncode == 0, f"rc={proc.returncode}")

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
