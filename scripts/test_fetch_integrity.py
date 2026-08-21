#!/usr/bin/env python3
"""
test_fetch_integrity.py — the cache is only ever replaced by a WHOLE fetch.

A failed-auth or incomplete fetch must write NOTHING to data/cfbd_cache.json and
must archive NOTHING (playbook rules 3/5, ARCHITECTURE §4/§6). This test proves it
three ways:

  1. Keyless subprocess run: `fetch_results.py` with CFB_API_KEY="" exits non-zero
     BEFORE any write, and the cache file is byte-identical afterwards, with no new
     ratings-archive snapshot.
  2. The pre-write gate `assert_fetch_complete` raises on zero games and on
     empty SP+ (both caught by main -> degraded_exit -> no write).
  3. `archive_ratings` refuses to snapshot a cache with no SP+ ratings.

The keyless case forces CFB_API_KEY="" in the child env; utils.load_env_file uses
setdefault, so the empty value is NOT overwritten by a real .env key — the child
takes the auth-fail path without any network call.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_fetch_integrity.py
    python scripts/test_fetch_integrity.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import fetch_results as fr
from cfbd_client import CFBDError

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "ratings_archive"

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def archive_snapshot_set():
    """Every archived vintage json (README excluded) — to prove none appears."""
    if not ARCHIVE.exists():
        return set()
    return {p.relative_to(ARCHIVE).as_posix()
            for p in ARCHIVE.rglob("*.json")}


def _require_cache():
    """main() opened with this guard and exited 1 on a missing cache.

    Kept loud rather than turned into a skip: sections 1 and 4 compare the cache
    before and after a keyless run, and a suite that quietly skipped them would
    report green while proving nothing about the file it exists to protect.
    """
    assert utils.cache_fingerprint() is not None, (
        "cache missing - cannot test integrity")


def test_keyless_run_writes_nothing():
    """A failed-auth fetch leaves data/cfbd_cache.json byte-identical and archives nothing."""
    print("\n[1] keyless subprocess leaves cache byte-identical, archives nothing")
    _require_cache()
    before_hash = utils.cache_fingerprint()          # raw-bytes sha256 via utils (guard-safe)
    before_arch = archive_snapshot_set()
    env = dict(os.environ, CFB_API_KEY="")           # force the auth-fail path, no network
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_results.py"), "--season", "2025"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    after_hash = utils.cache_fingerprint()
    after_arch = archive_snapshot_set()

    check("keyless fetch exits non-zero (writes nothing)", proc.returncode != 0,
          f"returncode={proc.returncode}")
    check("keyless fetch mentions the missing key", "CFB_API_KEY" in (proc.stdout + proc.stderr))
    check("cache byte-identical after keyless run", before_hash == after_hash,
          f"{before_hash} vs {after_hash}")
    check("no ratings-archive snapshot written by keyless run", before_arch == after_arch,
          f"new={sorted(after_arch - before_arch)}")


def test_pre_write_gate_rejects_incomplete_fetches():
    """assert_fetch_complete is what stops a partial fetch reaching the writer."""
    print("\n[2] the pre-write gate rejects incomplete fetches")
    ok = False
    try:
        fr.assert_fetch_complete([], {"Ohio State": {"rating": 20.0}})
    except CFBDError:
        ok = True
    check("assert_fetch_complete raises on ZERO games", ok)

    ok = False
    try:
        fr.assert_fetch_complete([{"home_team": "A"}], {})
    except CFBDError:
        ok = True
    check("assert_fetch_complete raises on EMPTY SP+", ok)

    ok = True
    try:
        fr.assert_fetch_complete([{"home_team": "A"}], {"A": {"rating": 1.0}})
    except CFBDError:
        ok = False
    check("assert_fetch_complete passes when games AND SP+ present", ok)


def test_archive_refuses_a_ratingless_cache():
    """archive_ratings must not snapshot a cache with no SP+ (tempdir ARCHIVE_DIR)."""
    print("\n[3] archive_ratings refuses a cache with no SP+ ratings")
    with tempfile.TemporaryDirectory() as td:
        saved = fr.ARCHIVE_DIR
        try:
            fr.ARCHIVE_DIR = Path(td)
            fr.archive_ratings({
                "season": 2025, "fetched_at": "2026-09-15T12:00:00+00:00",
                "week": 3, "source": "CFBD", "sp_ratings": {}, "fpi_ratings": {},
            })
            wrote = list(Path(td).rglob("*.json"))
        finally:
            fr.ARCHIVE_DIR = saved
    check("archive_ratings writes nothing when SP+ is empty", wrote == [],
          f"wrote={[p.name for p in wrote]}")


def test_archive_is_append_only():
    """BUILD 2 cadence guarantee, CI-enforced.

    Twice-weekly = two dates in one CFB week -> two distinct vintages kept; a
    same-day re-run is a no-op; a same-date/different-week write keeps the
    existing file. ARCHIVE_DIR is redirected to a tempdir throughout, so
    data/ratings_archive/ is never touched (CLAUDE.md P2 #14).
    """
    print("\n[4] archive is append-only: idempotent same-day, no dup/clobber")

    def cache(date, week, ratings):
        return {"season": 2025, "fetched_at": f"{date}T12:00:00+00:00", "week": week,
                "source": "CFBD", "sp_ratings": {t: {"rating": r} for t, r in ratings.items()},
                "fpi_ratings": {}}

    with tempfile.TemporaryDirectory() as td:
        saved = fr.ARCHIVE_DIR
        try:
            fr.ARCHIVE_DIR = Path(td)
            fr.archive_ratings(cache("2026-09-15", 3, {"A": 10, "B": 5}))   # write
            fr.archive_ratings(cache("2026-09-15", 3, {"A": 11, "B": 5}))   # same day -> skip
            fr.archive_ratings(cache("2026-09-18", 3, {"A": 12, "B": 4}))   # new date, same wk
            fr.archive_ratings(cache("2026-09-22", 4, {"A": 13, "B": 3}))   # new week
            fr.archive_ratings(cache("2026-09-15", 5, {"A": 99}))           # same date/diff wk -> keep
            season_dir = Path(td) / "2025"
            files = sorted(p.name for p in season_dir.glob("*.json"))
            import json as _json
            first = _json.loads((season_dir / "2026-09-15.json").read_text())
        finally:
            fr.ARCHIVE_DIR = saved
    check("twice-weekly cadence keeps distinct-date vintages (no dup)",
          files == ["2026-09-15.json", "2026-09-18.json", "2026-09-22.json"],
          f"files={files}")
    check("same-date re-run never clobbers the first snapshot",
          first["week"] == 3 and first["sp_ratings"]["A"]["rating"] == 10,
          f"first={first.get('week')}/{first.get('sp_ratings',{}).get('A')}")


def main():
    test_keyless_run_writes_nothing()
    test_pre_write_gate_rejects_incomplete_fetches()
    test_archive_refuses_a_ratingless_cache()
    test_archive_is_append_only()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
