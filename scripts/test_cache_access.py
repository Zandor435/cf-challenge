#!/usr/bin/env python3
"""
test_cache_access.py — Mechanical guards on cache + raw-index access (ARCHITECTURE §4, §5).

Two AST guards, same pattern (walk the tree, not raw lines, so comments/docstrings
that merely mention a name are ignored — only real code access counts):

GUARD 1 — cache I/O ownership. Nothing outside utils.py may reference the
CACHE_PATH symbol or a bare cache-path string literal in code. A stale/wrong-
season cache read directly (bypassing utils.load_cache's season assertion) would
score a clean but wrong board. Sanctioned access is via utils only:
load_cache(season) / peek_cache() / save_cache().

GUARD 2 — raw banked-index ownership (§5). fetch_results.build_team_index writes
a FLAG-BLIND, as-of-blind per-team index into the cache
(cache["teams"][t] = {"wins","losses","games_played",...}). A scorer that reads
those raw fields bypasses utils.team_state — which is the ONLY place that honors
count_conference_championship and --as-of-week — and would silently mis-count.
So nothing outside utils.py (the sanctioned reader, via team_state) and
fetch_results.py (the producer) may read a raw banked key ('wins' / 'losses' /
'games_played') by subscript or .get().

Needs no API key — safe to run in CI on every commit.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_cache_access.py
    python scripts/test_cache_access.py
"""

import ast
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SELF = Path(__file__).name

CACHE_OWNER = {"utils.py"}                       # only utils.py touches cache I/O
CACHE_STEM = "cfbd" + "_cache"                    # split so this file doesn't self-match

# fetch_results.py PRODUCES the raw index (writes the keys); utils.py is the only
# sanctioned READER (via team_state). Everyone else must go through team_state.
BANKED_OWNER = {"utils.py", "fetch_results.py"}
BANKED_KEYS = {"wins", "losses", "games_played"}


# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
#
# This file is the one that had no ledger: main() built two violation lists,
# printed them and exited 1. The lists and the printed remediation advice are
# unchanged; they are now also RECORDED, so the guards cannot collect as pytest
# items that are structurally incapable of failing.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _is_cache_path_literal(value):
    """True for a bare path literal like 'data/cfbd_cache.json' — a string with
    no whitespace (so prose docstrings that merely mention the file are ignored)."""
    return (isinstance(value, str) and CACHE_STEM in value
            and not any(c.isspace() for c in value))


def _const_str(node):
    """The string value if `node` is a string Constant, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def scan_cache(tree):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "CACHE_PATH":
            hits.append((node.lineno, "CACHE_PATH symbol"))
        elif isinstance(node, ast.Attribute) and node.attr == "CACHE_PATH":
            hits.append((node.lineno, ".CACHE_PATH attribute"))
        elif isinstance(node, ast.Constant) and _is_cache_path_literal(node.value):
            hits.append((node.lineno, f"path literal {node.value!r}"))
    return hits


def scan_banked(tree):
    """Flag raw banked-index reads: x["wins"] / x['losses'] / x['games_played']
    and x.get("wins") etc. Output code uses distinct 'banked_wins'/'banked_losses'
    keys, so exact-match on the raw keys has no false positives."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            key = _const_str(node.slice)               # py3.9+: slice is the expr
            if key in BANKED_KEYS:
                hits.append((node.lineno, f"raw banked-index subscript [{key!r}]"))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get" and node.args):
            key = _const_str(node.args[0])
            if key in BANKED_KEYS:
                hits.append((node.lineno, f"raw banked-index .get({key!r})"))
    return hits


_SCAN = None


def _scan():
    """Walk every script in scripts/ once and collect both guards' violations.

    Memoised: main() made a single pass and applied both guards to it, and the
    parse is the expensive part. Neither guard mutates anything, so sharing the
    result between the two tests is the same computation main() did.
    """
    global _SCAN
    if _SCAN is None:
        scanned, cache_violations, banked_violations = 0, [], []
        for p in sorted(SCRIPTS.glob("*.py")):
            if p.name == SELF:
                continue
            scanned += 1
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            if p.name not in CACHE_OWNER:
                for lineno, what in scan_cache(tree):
                    cache_violations.append(f"{p.name}:{lineno}: {what}")
            if p.name not in BANKED_OWNER:
                for lineno, what in scan_banked(tree):
                    banked_violations.append(f"{p.name}:{lineno}: {what}")
        _SCAN = (scanned, cache_violations, banked_violations)
    return _SCAN


def test_cache_io_ownership():
    """GUARD 1: nothing outside utils.py may touch cache I/O."""
    scanned, cache_violations, _ = _scan()
    if cache_violations:
        print("CACHE ACCESS GUARD FAILED — cache touched in code outside utils.py:")
        for v in cache_violations:
            print(f"  {v}")
        print("  Use utils.load_cache(season) / peek_cache() / save_cache() instead.\n")
    check(f"no code outside utils.py touches the cache ({scanned} scripts scanned)",
          not cache_violations, "; ".join(cache_violations))


def test_raw_banked_index_ownership():
    """GUARD 2: nothing outside utils.py / fetch_results.py may read a raw banked key."""
    scanned, _, banked_violations = _scan()
    if banked_violations:
        print("RAW-INDEX GUARD FAILED — raw banked W/L read outside utils.py "
              "(the team_state owner) / fetch_results.py (the producer):")
        for v in banked_violations:
            print(f"  {v}")
        print("  Use utils.team_state(team, group_config, as_of_week) instead — it "
              "honors count_conference_championship and --as-of-week.\n")
    check(f"no raw banked-index read outside utils.py/fetch_results.py "
          f"({scanned} scripts scanned)",
          not banked_violations, "; ".join(banked_violations))


def main():
    test_cache_io_ownership()
    test_raw_banked_index_ownership()

    scanned, _, _ = _scan()
    passed, total = sum(1 for r in _res if r[1]), len(_res)
    if passed != total:
        sys.exit(1)
    print(f"Access guards OK: {scanned} scripts scanned — no code outside utils.py "
          f"touches the cache, and no raw banked-index reads outside "
          f"utils.py/fetch_results.py.")


if __name__ == "__main__":
    main()
