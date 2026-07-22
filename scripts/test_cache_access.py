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

Usage:
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


def main():
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

    ok = True
    if cache_violations:
        ok = False
        print("CACHE ACCESS GUARD FAILED — cache touched in code outside utils.py:")
        for v in cache_violations:
            print(f"  {v}")
        print("  Use utils.load_cache(season) / peek_cache() / save_cache() instead.\n")
    if banked_violations:
        ok = False
        print("RAW-INDEX GUARD FAILED — raw banked W/L read outside utils.py "
              "(the team_state owner) / fetch_results.py (the producer):")
        for v in banked_violations:
            print(f"  {v}")
        print("  Use utils.team_state(team, group_config, as_of_week) instead — it "
              "honors count_conference_championship and --as-of-week.\n")

    if not ok:
        sys.exit(1)
    print(f"Access guards OK: {scanned} scripts scanned — no code outside utils.py "
          f"touches the cache, and no raw banked-index reads outside "
          f"utils.py/fetch_results.py.")


if __name__ == "__main__":
    main()
