#!/usr/bin/env python3
"""
test_suite_collection.py — every test file must actually contribute tests.

This is the guard that replaces the wall of explicit `- name: test_x.py` steps
in .github/workflows/tests.yml. Those steps existed because `pytest scripts/`
collected ZERO items from twelve of the fourteen test files, so the only way to
be sure a file ran was to name it. Naming each file works, and it worked for a
year — but it fails silently in exactly one direction, which is the direction
that actually happens: a NEW test file added without someone remembering to add
a workflow step never runs, and nothing goes red to say so.

Now that every file is collected, the steps are redundant and the risk inverts:
the suite could shrink back without anyone noticing. So the invariant is checked
here instead of trusted, statically over the AST (no imports, no execution — it
must stay cheap and must not care what the other files do at runtime):

  1. Every scripts/test_*.py defines at least one top-level `test_*` function.
     A file with none collects nothing and is invisible to `pytest`.
  2. Every one of those functions contains at least one `check(...)` call or a
     bare `assert`. A test function that records nothing passes unconditionally
     — which is precisely what the eight "passing" projector tests were doing
     before conftest.py's gate existed.
  3. selftest_10_1.py is NOT named test_*.py. It must stay uncollected: it
     shells out through fetch_results.py --simulate-failure and exits 4 once the
     committed cache ages past the 10-day staleness ceiling, so CI runs it last
     with continue-on-error. Collecting it would move that known, worsening
     failure inside the blocking pytest step. Its filename is the only thing
     keeping it out, so the filename is pinned here.

Note this file cannot check itself into correctness — it is subject to rules 1
and 2 like every other file, and skips only its own name when enumerating.

Runs both ways, and they are equivalent: pytest collects one test per rule and
conftest.py raises on any check() the rule recorded as FAIL; the standalone
runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_suite_collection.py
    python scripts/test_suite_collection.py
"""

import ast
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SELF = Path(__file__).name

# The one hand-rolled runner that must never become a pytest module. See rule 3
# above and the collect_ignore note in conftest.py.
UNCOLLECTED_RUNNER = "selftest_10_1.py"

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _test_files():
    return [p for p in sorted(SCRIPTS.glob("test_*.py")) if p.name != SELF]


def _top_level_tests(tree):
    return [n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")]


def _records_something(fn):
    """True if the function body contains a check(...) call or an assert."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "check":
                return True
            if isinstance(f, ast.Attribute) and f.attr == "check":
                return True
    return False


def test_every_test_file_defines_collectable_tests():
    """RULE 1: a scripts/test_*.py with no top-level test_* function is invisible."""
    print("[1] every test file defines at least one top-level test_* function")
    files = _test_files()
    check("there are test files to check at all", len(files) > 0, f"{len(files)} found")
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        names = [n.name for n in _top_level_tests(tree)]
        check(f"{p.name} defines a top-level test_*", bool(names),
              f"{len(names)} found" if names else "NONE — pytest collects nothing here")


def test_no_test_function_is_vacuous():
    """RULE 2: a test that records nothing passes unconditionally."""
    print("\n[2] every test_* function records a check() or asserts")
    for p in _test_files():
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        empty = [n.name for n in _top_level_tests(tree) if not _records_something(n)]
        check(f"{p.name} has no assertion-free test function", not empty,
              f"vacuous: {empty}" if empty else "")


def test_the_hand_rolled_runner_stays_uncollected():
    """RULE 3: selftest_10_1.py must not be pulled into the blocking pytest step."""
    print("\n[3] the non-blocking hand-rolled runner stays out of collection")
    runner = SCRIPTS / UNCOLLECTED_RUNNER
    check(f"{UNCOLLECTED_RUNNER} still exists", runner.exists())
    check(f"{UNCOLLECTED_RUNNER} does not match the test_*.py collection pattern",
          not UNCOLLECTED_RUNNER.startswith("test_"), UNCOLLECTED_RUNNER)
    # It really does define test_*-named functions — which is exactly why the
    # filename is load-bearing rather than incidental.
    tree = ast.parse(runner.read_text(encoding="utf-8"), filename=str(runner))
    check("...and it does define test_*-named functions, so the name is load-bearing",
          bool(_top_level_tests(tree)),
          str([n.name for n in _top_level_tests(tree)]))


def main():
    test_every_test_file_defines_collectable_tests()
    test_no_test_function_is_vacuous()
    test_the_hand_rolled_runner_stays_uncollected()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
