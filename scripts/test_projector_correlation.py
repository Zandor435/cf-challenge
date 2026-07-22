#!/usr/bin/env python3
"""
test_projector_correlation.py — Pool odds MUST use shared per-team draws (ARCHITECTURE §3).

Two managers on OPPOSITE sides of the SAME team must come out anti-correlated in
the pool simulation: when the team over-performs, the over-manager gains exactly
what the under-manager loses. If the projector drew each manager's teams
independently, that coupling vanishes (corr ~ 0) and P(win pool) is mis-stated by
5-7 points. This test asserts the coupling is real by exercising the actual
projector.simulate_totals path.

Control: two managers on DIFFERENT teams are ~uncorrelated — proving the negative
correlation above comes from SHARING one team's draw, not from the machinery.

Usage:
    python scripts/test_projector_correlation.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from projector import simulate_totals

_res = []


def check(name, ok, detail=""):
    _res.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _config(managers):
    return {"group_id": "corrtest", "season": 2025,
            "count_conference_championship": False,
            "picks_per_manager": None, "min_distinct_conferences": None,
            "managers": [{"manager_id": m, "display_name": m, "email": ""} for m in managers]}


def corr(a, b):
    return float(np.corrcoef(a, b)[0, 1])


def main():
    # Week 6 → each team has ~7 remaining games → real draw variance.
    WK = 6

    # Opposite sides of ONE team → must be strongly negatively correlated.
    cfg = _config(["over", "under"])
    picks = [
        {"manager": "over",  "team": "Ohio State", "line": 10.5, "direction": "O", "conference": "Big Ten"},
        {"manager": "under", "team": "Ohio State", "line": 10.5, "direction": "U", "conference": "Big Ten"},
    ]
    _, totals, pwp = simulate_totals(cfg, picks, as_of_week=WK)
    c = corr(totals["over"], totals["under"])
    check("opposite sides of one team are negatively correlated", c < -0.9, f"corr={c:.3f}")
    check("shared draw has real variance (not clinched)", totals["over"].std() > 0,
          f"std={totals['over'].std():.3f}")
    check("pool shares sum to 1", abs(sum(pwp.values()) - 1.0) < 1e-9, f"sum={sum(pwp.values()):.4f}")

    # Control: DIFFERENT teams → ~uncorrelated (isolates the sharing as the cause).
    cfg2 = _config(["a", "b"])
    picks2 = [
        {"manager": "a", "team": "Ohio State", "line": 10.5, "direction": "O", "conference": "Big Ten"},
        {"manager": "b", "team": "Alabama",    "line": 9.5,  "direction": "O", "conference": "SEC"},
    ]
    _, totals2, _ = simulate_totals(cfg2, picks2, as_of_week=WK)
    c2 = corr(totals2["a"], totals2["b"])
    check("different teams are ~uncorrelated (control)", abs(c2) < 0.15, f"corr={c2:.3f}")

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
