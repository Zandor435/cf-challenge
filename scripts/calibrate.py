#!/usr/bin/env python3
"""
calibrate.py — Backtest the rating->win-probability model (ARCHITECTURE §3, §4).

The SP+ differential -> win-probability scaling in projector.py was inherited,
not validated. This tool backtests it against a COMPLETED season in the cache:

  For every completed FBS-vs-FBS regular-season game, predict the home win
  probability from the rating differential + home-field constant, then measure
  calibration (decile reliability table), Brier score, and log loss. It then
  FITS the (scale, home-field) pair that minimizes season log loss, and does the
  whole thing for SP+ and FPI so they can be A/B'd.

This is a PERMANENT tool — re-run each offseason against the new completed
season. It does NOT change any constant; it reports and recommends. Constant
changes wait for Zach's approval.

*** HINDSIGHT-LEAKAGE WARNING — READ BEFORE TRUSTING ANY FITTED VALUE ***
CFBD's SP+ endpoint (/ratings/sp?year=YYYY) returns the SEASON-FINAL rating
only. Its `week` parameter is accepted but IGNORED (verified: Indiana 2024 SP+
= 20.1 at no-week, week=3, week=10; 2025 cache value == live value == week=4
value). So the SP+ block in data/cfbd_cache.json holds END-OF-SEASON ratings
that already encode every game's result. Predicting week-3 games with them is
HINDSIGHT LEAKAGE: games look more decided than they were live, the optimizer
compensates by SHARPENING (a too-steep, too-small scale), and that steep scale
would run OVERCONFIDENT on live in-season SP+. The fitted numbers this tool
prints from a final-SP+ cache are therefore biased and MUST NOT be applied to
the live projector. The inherited constant is safer than a leaky fit.
Vintage-correct in-season ratings are available ONLY via Elo
(/ratings/elo?year=&week=&seasonType=, verified to change week-by-week); SP+
and FPI have no historical weekly vintage in CFBD. A non-leaky SP+ calibration
is not possible with CFBD data — see the STEP 1 report / ARCHITECTURE §4.

Caveat (ARCHITECTURE §4): SP+ is also partly preseason-weighted through ~Sept,
so even the (leaky) early-season split lags. Report is split weeks 1-5 vs 6+.

Usage:
    python scripts/calibrate.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from projector import HOME_FIELD_ADVANTAGE_PTS, WIN_PROB_POINTS_SCALE

EARLY_MAX_WEEK = 5          # weeks 1-5 = SP+ preseason-weighted window (§4)
EPS = 1e-15                 # log-loss clip
RATINGS = [
    ("SP+", utils.season_sp_ratings, "rating"),
    ("FPI", utils.season_fpi_ratings, "fpi"),
]


def load_games(rating_map, key):
    """Completed FBS-vs-FBS regular-season games with both teams rated.
    Returns arrays: rating diff (home-away), home_factor (0 neutral / 1 home), week, y."""
    season = utils.get_season()
    games = utils.season_games(season)           # season-guarded (utils owns cache I/O)
    rh, ra, hf, wk, y = [], [], [], [], []
    skipped_unrated = 0
    for g in games:
        if not g.get("completed"):
            continue
        if g.get("home_classification") != "fbs" or g.get("away_classification") != "fbs":
            continue
        hp, ap = g.get("home_points"), g.get("away_points")
        if hp is None or ap is None or hp == ap:   # drop non-final + exact ties
            continue
        h, a = g.get("home_team"), g.get("away_team")
        rec_h, rec_a = rating_map.get(h), rating_map.get(a)
        if not rec_h or not rec_a or rec_h.get(key) is None or rec_a.get(key) is None:
            skipped_unrated += 1
            continue
        rh.append(float(rec_h[key])); ra.append(float(rec_a[key]))
        hf.append(0.0 if g.get("neutral_site") else 1.0)
        wk.append(g.get("week"))
        y.append(1.0 if hp > ap else 0.0)
    return (np.array(rh), np.array(ra), np.array(hf),
            np.array(wk), np.array(y), skipped_unrated)


def predict(rh, ra, hf, scale, hfa):
    margin = (rh - ra) + hfa * hf
    return 1.0 / (1.0 + np.exp(-margin / scale))


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def decile_table(p, y):
    """10 equal-count bins by predicted prob (quantile deciles). Returns rows of
    (decile, n, mean_pred, actual_rate, gap=actual-pred)."""
    order = np.argsort(p, kind="stable")
    rows = []
    for i, idx in enumerate(np.array_split(order, 10), 1):
        if len(idx) == 0:
            continue
        mp, ar = float(p[idx].mean()), float(y[idx].mean())
        rows.append((i, len(idx), mp, ar, ar - mp))
    return rows


def fit(rh, ra, hf, y):
    """Grid-search (scale, hfa) minimizing log loss, coarse then fine."""
    def best_over(scales, hfas):
        best = (None, None, np.inf)
        for s in scales:
            for h in hfas:
                ll = log_loss(predict(rh, ra, hf, s, h), y)
                if ll < best[2]:
                    best = (s, h, ll)
        return best
    s0, h0, _ = best_over(np.arange(4.0, 30.01, 0.5), np.arange(-1.0, 8.01, 0.5))
    s1, h1, ll1 = best_over(np.arange(max(1.0, s0 - 0.5), s0 + 0.5001, 0.05),
                            np.arange(h0 - 0.5, h0 + 0.5001, 0.05))
    return round(float(s1), 2), round(float(h1), 2), ll1


def print_table(rows):
    print(f"    {'decile':6s} {'n':>4s} {'mean_pred':>10s} {'actual':>8s} {'gap':>8s}")
    for d, n, mp, ar, gap in rows:
        print(f"    {d:<6d} {n:>4d} {mp:>10.3f} {ar:>8.3f} {gap:>+8.3f}")


def scope_report(label, rh, ra, hf, y, scale, hfa):
    p = predict(rh, ra, hf, scale, hfa)
    print(f"\n  --- {label}  (n={len(y)}, scale={scale}, hfa={hfa}) ---")
    print_table(decile_table(p, y))
    print(f"    Brier={brier(p, y):.4f}   LogLoss={log_loss(p, y):.4f}")


def calibrate_rating(name, rating_map, key):
    print("\n" + "=" * 72)
    print(f"RATING SYSTEM: {name}")
    print("=" * 72)
    rh, ra, hf, wk, y, skipped = load_games(rating_map, key)
    if len(y) == 0:
        print(f"  no rated games for {name} (is it in the cache?) — skipping.")
        return None
    early = wk <= EARLY_MAX_WEEK
    print(f"  {len(y)} rated FBS-v-FBS games ({int(early.sum())} in wk1-{EARLY_MAX_WEEK}, "
          f"{int((~early).sum())} in wk{EARLY_MAX_WEEK+1}+); {skipped} skipped (unrated team)")

    # --- CURRENT constants (projector.py) ---
    print(f"\n  [CURRENT constants: scale={WIN_PROB_POINTS_SCALE}, hfa={HOME_FIELD_ADVANTAGE_PTS}]")
    scope_report("OVERALL", rh, ra, hf, y, WIN_PROB_POINTS_SCALE, HOME_FIELD_ADVANTAGE_PTS)
    scope_report(f"WEEKS 1-{EARLY_MAX_WEEK} (early / preseason-weighted)",
                 rh[early], ra[early], hf[early], y[early], WIN_PROB_POINTS_SCALE, HOME_FIELD_ADVANTAGE_PTS)
    scope_report(f"WEEKS {EARLY_MAX_WEEK+1}+ (late)",
                 rh[~early], ra[~early], hf[~early], y[~early], WIN_PROB_POINTS_SCALE, HOME_FIELD_ADVANTAGE_PTS)

    # --- FITTED constants (minimize season log loss) ---
    fs, fh, fll = fit(rh, ra, hf, y)
    p_cur = predict(rh, ra, hf, WIN_PROB_POINTS_SCALE, HOME_FIELD_ADVANTAGE_PTS)
    print("\n  [FITTED vs CURRENT — minimizing overall log loss]")
    print(f"    {'':16s}{'scale':>8s}{'hfa':>8s}{'Brier':>9s}{'LogLoss':>9s}")
    print(f"    {'current':16s}{WIN_PROB_POINTS_SCALE:>8.2f}{HOME_FIELD_ADVANTAGE_PTS:>8.2f}"
          f"{brier(p_cur, y):>9.4f}{log_loss(p_cur, y):>9.4f}")
    p_fit = predict(rh, ra, hf, fs, fh)
    print(f"    {'fitted':16s}{fs:>8.2f}{fh:>8.2f}{brier(p_fit, y):>9.4f}{log_loss(p_fit, y):>9.4f}")

    # fitted, on the splits (does one global fit help early AND late?)
    scope_report(f"FITTED on WEEKS 1-{EARLY_MAX_WEEK}", rh[early], ra[early], hf[early], y[early], fs, fh)
    scope_report(f"FITTED on WEEKS {EARLY_MAX_WEEK+1}+", rh[~early], ra[~early], hf[~early], y[~early], fs, fh)

    return {"name": name, "n": len(y), "fitted": (fs, fh),
            "brier_cur": brier(p_cur, y), "ll_cur": log_loss(p_cur, y),
            "brier_fit": brier(p_fit, y), "ll_fit": log_loss(p_fit, y)}


def main():
    season = utils.get_season()
    print("!" * 72)
    print("! HINDSIGHT-LEAKAGE WARNING: CFBD SP+/FPI have NO weekly vintage (the")
    print("! `week` param is ignored). This cache holds SEASON-FINAL ratings, so the")
    print("! fitted values below are leakage-biased (too-steep scale) and MUST NOT be")
    print("! applied to the live projector. Vintage-correct in-season ratings exist")
    print("! only via Elo (/ratings/elo?year=&week=). See the STEP 1 report.")
    print("!" * 72)
    print("#" * 72)
    print(f"# CALIBRATION BACKTEST — season {season}  (final-SP+ / LEAKY — diagnostic only)")
    print(f"# current projector constants: WIN_PROB_POINTS_SCALE={WIN_PROB_POINTS_SCALE}, "
          f"HOME_FIELD_ADVANTAGE_PTS={HOME_FIELD_ADVANTAGE_PTS}")
    print("#" * 72)

    results = [r for r in (calibrate_rating(n, fn(season), k) for n, fn, k in RATINGS) if r]

    print("\n" + "#" * 72)
    print("# LEAKY DIAGNOSTIC (do NOT adopt — final-SP+ fit, see leakage warning)")
    print("#" * 72)
    for r in sorted(results, key=lambda x: x["ll_fit"]):
        print(f"  {r['name']:5s} n={r['n']}: fitted scale={r['fitted'][0]}, hfa={r['fitted'][1]} "
              f"-> Brier {r['brier_cur']:.4f}->{r['brier_fit']:.4f}, "
              f"LogLoss {r['ll_cur']:.4f}->{r['ll_fit']:.4f}")
    print("\n  These fitted scales are biased TOO STEEP by hindsight leakage and are")
    print("  NOT a recommendation. No constant changed — projector.py holds "
          f"scale={WIN_PROB_POINTS_SCALE}, hfa={HOME_FIELD_ADVANTAGE_PTS}.")
    print("  A non-leaky SP+ fit is impossible with CFBD (no vintage SP+); the only")
    print("  vintage-correct path is Elo. Decision pending (see STEP 1 report).")


if __name__ == "__main__":
    main()
