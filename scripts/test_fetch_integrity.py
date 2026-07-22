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

Usage:
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

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def archive_snapshot_set():
    """Every archived vintage json (README excluded) — to prove none appears."""
    if not ARCHIVE.exists():
        return set()
    return {p.relative_to(ARCHIVE).as_posix()
            for p in ARCHIVE.rglob("*.json")}


def main():
    if utils.cache_fingerprint() is None:
        print("  [FAIL] cache missing — cannot test integrity")
        sys.exit(1)

    # ---- 1. keyless subprocess leaves cache byte-identical, archives nothing ----
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

    # ---- 2. the pre-write gate rejects incomplete fetches ----------------------
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

    # ---- 3. archive_ratings refuses a cache with no SP+ ratings ----------------
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

    # ---- 4. archive is append-only: idempotent same-day, no dup/clobber --------
    # (BUILD 2 cadence guarantee, now CI-enforced.) Twice-weekly = two dates in
    # one CFB week -> two distinct vintages kept; a same-day re-run is a no-op;
    # a same-date/different-week write keeps the existing file.
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

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
