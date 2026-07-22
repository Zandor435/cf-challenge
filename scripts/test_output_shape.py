#!/usr/bin/env python3
"""
test_output_shape.py — Validates emitted files against docs/output-contract.md.

The contract is the whole point of the keystone commit: every downstream
consumer reads exactly these shapes. This test builds standings / projection /
timeline for the test fixture (mid-season week 6 and final week 14) and asserts:
  - every required key is present with the right type,
  - Board-1 arithmetic invariant: games_remaining == 0 -> floor == ceiling == banked_delta,
  - Board-2 invariants: win_distribution sums to 1; at final, p_beat_line in {0,1}
    and expected_delta == the Board-1 banked_delta (the two boards agree),
  - timeline is append-only + idempotent on the effective week.

No cache writes for the boards (pure builders); timeline idempotency uses a temp
file. Needs the cached completed season present.

Usage:
    python scripts/test_output_shape.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import scoring
import projector
import run_groups

_res = []


def check(name, ok, detail=""):
    _res.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _has_keys(d, keys):
    return isinstance(d, dict) and all(k in d for k in keys)


META_KEYS = {"group_id", "season", "as_of_week", "generated_at", "cache_fetched_at"}
STANDINGS_MGR = {"manager_id", "display_name", "banked_total", "floor", "ceiling", "rank", "picks"}
STANDINGS_PICK = {"team", "conference", "line", "direction", "banked_wins", "banked_losses",
                  "games_remaining", "banked_delta", "floor", "ceiling", "status"}
PROJ_MGR = {"manager_id", "display_name", "expected_total", "p05", "p50", "p95", "p_win_pool", "picks"}
PROJ_PICK = {"team", "conference", "line", "direction", "p_beat_line", "expected_delta",
             "expected_final_wins", "win_distribution"}
TL_PICK = {"team", "banked_delta", "floor", "ceiling", "expected_delta", "p_beat_line"}


def validate_standings(st, label):
    check(f"[{label}] standings.meta keys", _has_keys(st.get("meta", {}), META_KEYS))
    ok_mgr = ok_pick = ok_inv = True
    for m in st["managers"]:
        ok_mgr &= _has_keys(m, STANDINGS_MGR)
        for p in m["picks"]:
            ok_pick &= _has_keys(p, STANDINGS_PICK)
            ok_pick &= p["status"] in ("LIVE", "CLINCHED", "DEAD")
            if p["games_remaining"] == 0:
                ok_inv &= (p["floor"] == p["ceiling"] == p["banked_delta"])
    check(f"[{label}] every manager has the required keys", ok_mgr)
    check(f"[{label}] every pick has the required keys + valid status", ok_pick)
    check(f"[{label}] invariant: games_remaining==0 => floor==ceiling==banked_delta", ok_inv)


def validate_projection(pr, label, final=False, banked_by=None):
    m0 = pr.get("meta", {})
    check(f"[{label}] projection.meta keys (+ratings)", _has_keys(m0, META_KEYS | {"ratings_source", "ratings_asof"}))
    check(f"[{label}] ratings_source == 'SP+'", m0.get("ratings_source") == "SP+")
    ok_mgr = ok_pick = ok_dist = ok_final = True
    for m in pr["managers"]:
        ok_mgr &= _has_keys(m, PROJ_MGR)
        ok_mgr &= 0.0 <= m["p_win_pool"] <= 1.0
        for p in m["picks"]:
            ok_pick &= _has_keys(p, PROJ_PICK)
            dist = p["win_distribution"]
            # emitted probs are 6-decimal rounded; tolerate rounding drift (a real
            # missing-mass bug moves the sum by >> 1e-4).
            ok_dist &= abs(sum(d["prob"] for d in dist) - 1.0) < 1e-4
            ok_dist &= all(_has_keys(d, {"wins", "prob"}) for d in dist)
            if final:
                ok_final &= p["p_beat_line"] in (0.0, 1.0)
                if banked_by is not None:
                    bd = banked_by.get((m["manager_id"], p["team"]))
                    ok_final &= (bd is not None and p["expected_delta"] == bd)
    check(f"[{label}] every manager has the required keys + valid p_win_pool", ok_mgr)
    check(f"[{label}] every pick has the required keys", ok_pick)
    check(f"[{label}] win_distribution sums to 1", ok_dist)
    if final:
        check(f"[{label}] final: p_beat_line in {{0,1}} AND expected_delta==banked_delta", ok_final)


def validate_timeline_snapshot(snap, label):
    ok = isinstance(snap.get("as_of_week"), int) and "generated_at" in snap
    for m in snap["managers"]:
        ok &= _has_keys(m, {"manager_id", "p_win_pool", "picks"})
        for p in m["picks"]:
            ok &= _has_keys(p, TL_PICK)
    check(f"[{label}] timeline snapshot shape", ok)


def main():
    config, picks = utils.load_group(utils.TEST_GROUP_ID)

    # --- Mid-season (week 6): LIVE picks present ---
    st6 = scoring.build_standings(config, picks, as_of_week=6)
    pr6 = projector.build_projection(config, picks, as_of_week=6)
    validate_standings(st6, "wk6")
    validate_projection(pr6, "wk6")
    live = any(p["status"] == "LIVE" for m in st6["managers"] for p in m["picks"])
    check("[wk6] at least one LIVE pick (mid-season state is real)", live)

    # --- Final (week 14): every slate complete -> two boards must agree ---
    st14 = scoring.build_standings(config, picks, as_of_week=14)
    pr14 = projector.build_projection(config, picks, as_of_week=14)
    banked_by = {(m["manager_id"], p["team"]): p["banked_delta"]
                 for m in st14["managers"] for p in m["picks"]}
    zero_width = all(p["games_remaining"] == 0 for m in st14["managers"] for p in m["picks"])
    check("[wk14] every pick has games_remaining==0 (season complete for the slate)", zero_width)
    validate_standings(st14, "wk14")
    validate_projection(pr14, "wk14", final=True, banked_by=banked_by)

    # --- Timeline: append-only + idempotent on the effective week ---
    with tempfile.TemporaryDirectory() as td:
        tl_path = Path(td) / "timeline.json"
        # monkeypatch the write target to the temp dir
        orig = utils.SITE_DATA_DIR
        try:
            utils.SITE_DATA_DIR = Path(td)
            (Path(td) / config["group_id"]).mkdir(parents=True, exist_ok=True)
            snap6 = run_groups.build_snapshot(st6, pr6, 6)
            validate_timeline_snapshot(snap6, "wk6")
            run_groups.append_timeline(config, snap6)
            run_groups.append_timeline(config, snap6)          # re-run same week
            tl = utils.load_json(Path(td) / config["group_id"] / "timeline.json")
            check("timeline idempotent: re-run week 6 does not duplicate",
                  len([s for s in tl["snapshots"] if s["as_of_week"] == 6]) == 1,
                  f"{len(tl['snapshots'])} snapshot(s)")
            run_groups.append_timeline(config, run_groups.build_snapshot(st14, pr14, 14))
            tl = utils.load_json(Path(td) / config["group_id"] / "timeline.json")
            weeks = [s["as_of_week"] for s in tl["snapshots"]]
            check("timeline append-only + sorted", weeks == sorted(weeks) and weeks == [6, 14],
                  f"weeks={weeks}")
        finally:
            utils.SITE_DATA_DIR = orig

    passed, total = sum(_res), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
