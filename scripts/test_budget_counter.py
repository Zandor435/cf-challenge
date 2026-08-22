#!/usr/bin/env python3
"""
test_budget_counter.py — the rule-6 tally counts HTTP attempts, not images.

data/gemini_budget.json is spend protection: the cumulative UTC-daily request
count per provider, with a loud ::warning:: past --daily-warn. It used to be
bumped ONCE per image at every call site, BEFORE the request went out — but
_post_with_retries() can send the same image up to 1 + 3 transient + 3
rate-limited = 7 times, and the tally never saw any of the retries. A counter
that under-reads by up to 7x is not protecting anything. The count now lives
inside the retry shell, one bump per do_post() attempt, which is the only place
that can see them. This test proves it against a mocked transport: nothing
here opens a socket, and the budget file is a temp path.

  1. two transient errors then 200  -> tally 3 (not 1)
  2. two 429s then 200, warn at 2   -> tally 3, the ::warning:: prints once
  3. a call that dies after its last retry still bills every attempt sent
  4. gen_gemini / gen_openai / gemini_image.generate thread the budget through
  5. no budget passed -> nothing counted, nothing written (keeps the kwarg opt-in)

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_budget_counter.py
    python scripts/test_budget_counter.py
"""

import base64
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_owner_images as goi
import gemini_image

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


class _Resp:
    """The slice of requests.Response the retry shell and the wrappers read."""

    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise requests.HTTPError(f"HTTP {self.status_code}")


_PNG = b"\x89PNG\r\n\x1a\nfake"
GEMINI_OK = {"candidates": [{"content": {"parts": [
    {"inlineData": {"data": base64.b64encode(_PNG).decode()}}]}}]}
OPENAI_OK = {"data": [{"b64_json": base64.b64encode(_PNG).decode()}]}


def _scripted(outcomes):
    """A do_post() that replays `outcomes` in order: an exception instance is
    raised, anything else is returned. Records how many times it was called."""
    calls = []

    def do_post(*_a, **_k):
        calls.append(1)
        item = outcomes[len(calls) - 1]
        if isinstance(item, BaseException):
            raise item
        return item
    do_post.calls = calls
    return do_post


@contextlib.contextmanager
def _sandbox():
    """Temp budget file + no real sleeping. Restores both on exit."""
    saved_file, saved_sleep = goi.BUDGET_FILE, goi.time.sleep
    with tempfile.TemporaryDirectory() as tmp:
        goi.BUDGET_FILE = Path(tmp) / "gemini_budget.json"
        goi.time.sleep = lambda _s: None
        try:
            yield goi.BUDGET_FILE
        finally:
            goi.BUDGET_FILE, goi.time.sleep = saved_file, saved_sleep


def _tally(path):
    return json.loads(path.read_text())["requests"]


def test_every_attempt_is_counted():
    """Two transient failures + one success = three requests on the tally."""
    print("\n[1] transient retries are counted")
    with _sandbox() as bfile:
        budget = goi.load_budget()
        post = _scripted([requests.ConnectionError("reset"),
                          requests.Timeout("slow"),
                          _Resp(200, {"ok": True})])
        resp = goi._post_with_retries(post, budget=budget, provider="gemini")
        check("the call still returns the 200", resp.status_code == 200)
        check("the transport was hit three times", len(post.calls) == 3,
              f"calls={len(post.calls)}")
        check("the tally says 3, not 1", _tally(bfile).get("gemini") == 3,
              f"tally={_tally(bfile)}")
        check("bump_budget's running total agrees",
              sum(budget["requests"].values()) == 3, f"budget={budget}")


def test_rate_limit_retries_count_and_the_warning_trips():
    """429s are requests too, and the threshold fires on the attempt that crosses it."""
    print("\n[2] 429 retries are counted and the warning trips at the threshold")
    with _sandbox() as bfile:
        budget = goi.load_budget()
        post = _scripted([_Resp(429, text="slow down"),
                          _Resp(429, text="slow down"),
                          _Resp(200, {"ok": True})])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            goi._post_with_retries(post, budget=budget, provider="openai",
                                   daily_warn=2)
        printed = out.getvalue()
        check("tally counts all three sends", _tally(bfile).get("openai") == 3,
              f"tally={_tally(bfile)}")
        check("::warning:: printed exactly once (attempt 3 is the first past 2)",
              printed.count("::warning::") == 1, repr(printed))
        check("the warning names the threshold and the tally",
              "daily tally 3" in printed and "past the 2 threshold" in printed,
              repr(printed))


def test_a_dead_call_still_bills_every_attempt():
    """Spend happened whether or not an image came back; the tally must say so."""
    print("\n[3] a persistent failure is billed for every attempt it made")
    with _sandbox() as bfile:
        budget = goi.load_budget()
        post = _scripted([requests.ConnectionError("1"), requests.ConnectionError("2"),
                          requests.ConnectionError("3"), requests.ConnectionError("4")])
        raised = False
        try:
            goi._post_with_retries(post, budget=budget, provider="gemini")
        except requests.ConnectionError:
            raised = True
        check("re-raises after the last transient retry", raised)
        expected = 1 + len(goi.TRANSIENT_WAITS)
        check(f"sent {expected} times (first try + every transient retry)",
              len(post.calls) == expected, f"calls={len(post.calls)}")
        check(f"tally says {expected}", _tally(bfile).get("gemini") == expected,
              f"tally={_tally(bfile)}")


def test_provider_wrappers_thread_the_budget_through():
    """The three real call paths reach the shell with the budget attached."""
    print("\n[4] gen_gemini / gen_openai / gemini_image.generate count per attempt")
    with _sandbox() as bfile:
        budget = goi.load_budget()

        saved = goi.requests.post
        try:
            goi.requests.post = _scripted([requests.ConnectionError("x"),
                                           _Resp(200, GEMINI_OK)])
            img = goi.gen_gemini("k", b"photo", "image/png", "p", "3:4", budget=budget)
            check("gen_gemini returns the image after one retry", img == _PNG)
            check("gen_gemini billed 2 on gemini", _tally(bfile).get("gemini") == 2,
                  f"tally={_tally(bfile)}")

            goi.requests.post = _scripted([_Resp(503, text="busy"),
                                           _Resp(200, OPENAI_OK)])
            img = goi.gen_openai("k", b"photo", "image/png", "p", "1024x1536",
                                 budget=budget)
            check("gen_openai returns the image after one 5xx retry", img == _PNG)
            check("gen_openai billed 2 on openai", _tally(bfile).get("openai") == 2,
                  f"tally={_tally(bfile)}")
        finally:
            goi.requests.post = saved

        saved = gemini_image.requests.post
        try:
            gemini_image.requests.post = _scripted([requests.Timeout("t"),
                                                    _Resp(200, GEMINI_OK)])
            img = gemini_image.generate("k", "gemini-3-pro-image", [], "p", "16:9",
                                        budget=budget)
            check("gemini_image.generate returns the image after one retry", img == _PNG)
            check("gemini_image.generate billed 2 more on gemini (4 total)",
                  _tally(bfile).get("gemini") == 4, f"tally={_tally(bfile)}")
        finally:
            gemini_image.requests.post = saved


def test_no_budget_means_no_counting():
    """The kwarg is opt-in: a caller that passes nothing writes nothing."""
    print("\n[5] without a budget the shell counts nothing")
    with _sandbox() as bfile:
        post = _scripted([requests.ConnectionError("x"), _Resp(200, {"ok": True})])
        goi._post_with_retries(post)
        check("two attempts were made", len(post.calls) == 2)
        check("no budget file was written", not bfile.exists())


def main():
    test_every_attempt_is_counted()
    test_rate_limit_retries_count_and_the_warning_trips()
    test_a_dead_call_still_bills_every_attempt()
    test_provider_wrappers_thread_the_budget_through()
    test_no_budget_means_no_counting()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
