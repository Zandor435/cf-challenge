#!/usr/bin/env python3
"""
test_projector_correlation.py — Pool odds MUST use shared per-team draws (ARCHITECTURE §3).

Two managers who share teams on opposite sides must come out ANTI-correlated in
the pool simulation: shared teams couple their totals (one's gain is the other's
loss), while unshared picks add independent noise. If the projector drew each
manager's teams independently, that coupling vanishes (corr ~ 0) and P(win pool)
is mis-stated by 5-7 points.

TEST 1 (realistic): two managers, 4 picks each, sharing exactly 2 teams on
opposite sides and 2 unshared teams each. Correlation must be significantly
negative BUT strictly greater than -0.5 — partial overlap cannot produce
near-perfect anti-correlation (that only happens when totals are pure negatives
of each other). Control: fully disjoint managers are ~uncorrelated.

TEST 2 (degenerate): one pick each, opposite sides of ONE team -> corr ~ -1.
Kept as a separate, explicit boundary case.

Both exercise the real projector.simulate_totals path. season is single-source
(season.json), so the synthetic configs carry none.

Usage:
    python scripts/test_projector_correlation.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from projector import simulate_totals

WK = 6          # mid-season: every team still has ~7 remaining games -> real variance
_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _cfg(managers):
    return {"group_id": "corrtest", "count_conference_championship": False,
            "picks_per_manager": None, "min_distinct_conferences": None,
            "managers": [{"manager_id": m, "display_name": m, "email": ""} for m in managers]}


def _pick(mgr, team, line, direction):
    return {"manager": mgr, "team": team, "line": line, "direction": direction, "conference": "x"}


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def test_realistic_partial_overlap():
    print("\n[1] realistic: 4 picks each, 2 shared (opposite sides) + 2 unshared")
    # A and B share Ohio State + Georgia on OPPOSITE sides; each holds 2 others.
    picks = [
        _pick("A", "Ohio State", 10.5, "O"), _pick("A", "Georgia", 9.5, "O"),
        _pick("A", "Alabama", 9.5, "O"),     _pick("A", "Texas", 9.5, "U"),
        _pick("B", "Ohio State", 10.5, "U"), _pick("B", "Georgia", 9.5, "U"),
        _pick("B", "Penn State", 8.5, "O"),  _pick("B", "Michigan", 8.5, "U"),
    ]
    _, totals, pwp = simulate_totals(_cfg(["A", "B"]), picks, as_of_week=WK)
    c = corr(totals["A"], totals["B"])
    check("partial overlap is significantly negative (< -0.1)", c < -0.1, f"corr={c:.3f}")
    check("partial overlap is NOT near-perfect (> -0.5)", c > -0.5, f"corr={c:.3f}")
    check("pool shares sum to 1", abs(sum(pwp.values()) - 1.0) < 1e-9, f"sum={sum(pwp.values()):.4f}")

    # Control: fully disjoint rosters -> ~uncorrelated (isolates sharing as the cause).
    picks_ctrl = [_pick("C", t, 9.5, "O") for t in ("Ohio State", "Georgia", "Alabama", "Texas")]
    picks_ctrl += [_pick("D", t, 8.5, "O") for t in ("Penn State", "Michigan", "Clemson", "Utah")]
    _, tot_ctrl, _ = simulate_totals(_cfg(["C", "D"]), picks_ctrl, as_of_week=WK)
    cc = corr(tot_ctrl["C"], tot_ctrl["D"])
    check("disjoint control is ~uncorrelated (|corr| < 0.15)", abs(cc) < 0.15, f"corr={cc:.3f}")


def test_degenerate_opposite_single_team():
    print("\n[2] degenerate: one pick each, opposite sides of ONE team")
    picks = [_pick("over", "Ohio State", 10.5, "O"), _pick("under", "Ohio State", 10.5, "U")]
    _, totals, _ = simulate_totals(_cfg(["over", "under"]), picks, as_of_week=WK)
    c = corr(totals["over"], totals["under"])
    check("single shared team -> near-perfect anti-correlation (< -0.9)", c < -0.9, f"corr={c:.3f}")
    check("real draw variance (not clinched)", totals["over"].std() > 0, f"std={totals['over'].std():.3f}")


def main():
    test_realistic_partial_overlap()
    test_degenerate_opposite_single_team()
    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
