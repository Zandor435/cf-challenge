"""Root conftest.py — the bridge that makes scripts/test_*.py real pytest items.

WHY THIS FILE EXISTS
--------------------
Every test in scripts/ was written as a hand-rolled runner: a module-level
``check(name, ok, detail)`` helper appends to a ``_res`` ledger and prints a
PASS/FAIL line, and ``main()`` sums the ledger and ``sys.exit(0 or 1)``. That
runs correctly under ``python scripts/test_x.py`` — which is how CI invoked
each file — but under pytest it was worse than useless:

  * a module with no ``test_*`` function collected ZERO items, so ``pytest -q``
    reported "8 passed" for a 15-file suite; and
  * the two modules that DID have ``test_*`` functions collected 8 items that
    could never fail, because ``check()`` records a failure, it does not raise.
    Eight green ticks were being printed for assertions nobody was checking.

So the conversion could not be "add test functions" alone. Something has to
turn the ledger into a real failure signal. This file is that something.

WHAT IT DOES — three jobs
-------------------------
1. THE CHECK GATE (``pytest_runtest_call``). Before each test in a module that
   owns a ``_res`` ledger, the ledger is cleared; after the test body returns,
   every recorded failure is raised as one AssertionError naming EVERY failed
   check by its human-readable label. Two deliberate properties:

     - It is a wrapper hook, not a per-test call, so it CANNOT be forgotten.
       A ``test_*`` function added to any of these modules tomorrow is gated
       automatically — which is the failure mode ("a new test file that
       silently never runs") this whole exercise exists to close.
     - It reports ALL failures at once rather than stopping at the first, which
       is what the hand-rolled ``RESULT: n/m checks passed`` summary did. A
       naive ``check() -> assert`` rewrite would have lost that, and several of
       these files run 30-150 checks in loops where the SET of failures is the
       diagnosis.

   A test that records zero checks also fails: silently doing nothing is the
   exact thing being fixed here, so it is never a pass.

2. PROCESS-STATE ISOLATION (``_utils_process_state``). ``utils.pin_contract_
   fixture()`` re-points the WHOLE PROCESS's cache reads at the frozen 2025
   contract fixture, and has no inverse. Under one-file-per-process runners
   that was safe, and test_projector_h2h.py even ordered its pinning test last
   on purpose. In a single pytest process it is not safe at all: the first
   module to pin (alphabetically, test_analytics_week_move.py) would silently
   re-point every later module off the live cache, so test_week_packet.py and
   test_preseason_baseline.py would quietly assert against the wrong season.
   Snapshotting and restoring the three globals ``pin_contract_fixture()``
   writes keeps every test reading the cache its own file set up.

3. IMPORT PATH. scripts/ is put on sys.path so the modules import their
   siblings (utils, projector, ...) the same way they do standalone.

Both entry points stay live and stay equivalent: ``python scripts/test_x.py``
still runs ``main()`` and exits 0/1 with the same printout, and none of that
behaviour is routed through this file.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# selftest_10_1.py defines test_cache(season) / test_resolver() / test_failure_path(season)
# / test_season_guard(season), but it is NOT a pytest module and must never become one.
# It is a health check of the LIVE committed cache (this suite pins the frozen 2025
# contract fixture instead) and shells out through `fetch_results.py --simulate-failure`
# twice; it skips-with-reason or branches on the cache's age and season state rather
# than failing on them, and CI runs it as its own blocking step, last. Collecting it
# here would fold a live-data check into the fixture-pinned suite and run its
# subprocesses under every pytest invocation. Its filename already keeps it out of
# `test_*.py` collection; this line makes the exclusion deliberate rather than
# incidental, so widening python_files later cannot quietly pull it in.
collect_ignore = ["scripts/selftest_10_1.py"]


def _render(failures):
    lines = [f"{len(failures)} check(s) failed:"]
    for name, _, detail in failures:
        lines.append(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Clear the module's check ledger before the test; raise on any FAIL after."""
    ledger = getattr(getattr(item, "module", None), "_res", None)
    if ledger is None:
        return (yield)

    ledger.clear()
    result = yield          # a raising test body propagates untouched

    failures = [r for r in ledger if not r[1]]
    if failures:
        raise AssertionError(_render(failures))
    if not ledger:
        raise AssertionError(
            f"{item.nodeid} recorded zero checks — a test that asserts nothing "
            f"is not a passing test")
    return result


@pytest.fixture(autouse=True)
def _utils_process_state():
    """Undo utils.pin_contract_fixture()'s process-wide cache redirect after each test."""
    try:
        import utils
    except Exception:                                   # pragma: no cover
        yield
        return
    saved = (utils.CACHE_PATH, utils._SEASON_CONF, utils._SEASON_CACHE)
    try:
        yield
    finally:
        utils.CACHE_PATH, utils._SEASON_CONF, utils._SEASON_CACHE = saved
