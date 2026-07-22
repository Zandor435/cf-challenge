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
and FPI have no historical weekly vintage in CFBD.

A non-leaky calibration is NOT impossible in general — it is impossible only
*with in-season SP+*. Two leak-free designs sidestep SP+'s vintage problem and
are implemented in calibrate_spread.py (report: docs/calibration-report.md):
  (A) fit the projector's logistic to CLOSING SPREADS (set pre-kickoff -> cannot
      encode the result -> leak-free by construction; no SP+ at all), and
  (B) predict season Y from FINAL season Y-1 SP+ (leak-free but stale -> flat).
Prefer calibrate_spread.py for the ACTUAL scale recommendation; THIS tool's
final-SP+ fit remains a leaky diagnostic only.

Caveat (ARCHITECTURE §4): SP+ is also partly preseason-weighted through ~Sept,
so even the (leaky) early-season split lags. Report is split weeks 1-5 vs 6+.

Usage:
    python scripts/calibrate.py             # leaky final-SP+ diagnostic
    python scripts/calibrate.py --archive   # BUILD 2: non-leaky vintage-archive fit
                                            # (activates once the archive is deep enough)
"""

import argparse
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

# --archive mode (BUILD 2) — the non-leaky within-season fit. Gated so it only
# reports once the vintage archive is deep enough to mean something.
ARCHIVE_DIR = utils.DATA_DIR / "ratings_archive"
MIN_ARCHIVE_WEEKS = 6       # need this many distinct pre-game vintages to fit
MIN_ARCHIVE_ROWS = 200      # ...and this many leak-free game rows


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


# --- BUILD 2: non-leaky within-season fit off the vintage archive -----------

def load_archive_snapshots(season):
    """All committed vintage snapshots for `season`, ascending by (week, date).
    week=None (preseason) is normalized to 0. Returns [] if the archive is empty."""
    season_dir = ARCHIVE_DIR / str(season)
    if not season_dir.exists():
        return []
    snaps = []
    for p in sorted(season_dir.glob("*.json")):
        try:
            s = utils.load_json(p)
        except Exception:
            continue
        s["_week"] = 0 if s.get("week") is None else int(s["week"])
        s["_date"] = s.get("date", p.stem)
        snaps.append(s)
    snaps.sort(key=lambda s: (s["_week"], s["_date"]))
    return snaps


def vintage_ratings_before(snaps, target_week):
    """SP+ map {team: rating} from the LATEST snapshot whose reported week is
    strictly before target_week (ratings that existed GOING INTO that week — no
    hindsight). None if the archive doesn't reach back that far."""
    usable = [s for s in snaps if s["_week"] <= target_week - 1]
    if not usable:
        return None
    s = usable[-1]                 # snaps are (week, date)-sorted -> latest vintage
    return {t: (r or {}).get("rating") for t, r in s.get("sp_ratings", {}).items()}


def load_archive_games(season):
    """Completed FBS-vs-FBS games predicted from the vintage SP+ taken BEFORE
    each game's week (leak-free). Returns the same array shape as load_games plus
    the count of distinct vintages actually used."""
    snaps = load_archive_snapshots(season)
    games = utils.season_games(season)
    rh, ra, hf, wk, y = [], [], [], [], []
    skipped_novintage = skipped_unrated = 0
    used_weeks = set()
    for g in games:
        if not g.get("completed"):
            continue
        if g.get("home_classification") != "fbs" or g.get("away_classification") != "fbs":
            continue
        hp, ap = g.get("home_points"), g.get("away_points")
        if hp is None or ap is None or hp == ap:
            continue
        gw = g.get("week")
        if gw is None:
            continue
        vint = vintage_ratings_before(snaps, gw)
        if vint is None:
            skipped_novintage += 1
            continue
        h, a = g.get("home_team"), g.get("away_team")
        rh_v, ra_v = vint.get(h), vint.get(a)
        if rh_v is None or ra_v is None:
            skipped_unrated += 1
            continue
        rh.append(float(rh_v)); ra.append(float(ra_v))
        hf.append(0.0 if g.get("neutral_site") else 1.0)
        wk.append(gw); y.append(1.0 if hp > ap else 0.0)
        used_weeks.add(gw)
    return (np.array(rh), np.array(ra), np.array(hf), np.array(wk), np.array(y),
            len(used_weeks), skipped_novintage, skipped_unrated)


def calibrate_archive(season):
    """Non-leaky within-season SP+ calibration off the vintage archive. This is
    the method that permanently supersedes the market bridge once enough live
    weeks exist (docs/calibration-report.md). Gated: reports insufficiency until
    the archive is deep enough."""
    print("#" * 72)
    print(f"# NON-LEAKY WITHIN-SEASON CALIBRATION — season {season} (vintage archive)")
    print("#" * 72)
    snaps = load_archive_snapshots(season)
    if not snaps:
        print(f"  archive empty (no data/ratings_archive/{season}/). It fills as")
        print("  fetch_results.py runs through the live season. Until then use")
        print("  calibrate_spread.py (market bridge). Nothing to fit.")
        return None

    rh, ra, hf, wk, y, n_weeks, skip_nv, skip_ur = load_archive_games(season)
    print(f"  {len(snaps)} vintage snapshot(s) on disk; {len(y)} leak-free game rows "
          f"across {n_weeks} distinct week(s).")
    print(f"  skipped: {skip_nv} (no vintage before that week), {skip_ur} (team unrated in vintage).")
    if n_weeks < MIN_ARCHIVE_WEEKS or len(y) < MIN_ARCHIVE_ROWS:
        print(f"\n  INSUFFICIENT: need >= {MIN_ARCHIVE_WEEKS} weeks and "
              f"{MIN_ARCHIVE_ROWS} rows to fit; have {n_weeks} / {len(y)}.")
        print("  Keep running the season; use calibrate_spread.py (market bridge)")
        print("  meanwhile. This mode activates automatically once deep enough.")
        return None

    fs, fh, _ = fit(rh, ra, hf, y)
    p_cur = predict(rh, ra, hf, WIN_PROB_POINTS_SCALE, HOME_FIELD_ADVANTAGE_PTS)
    p_fit = predict(rh, ra, hf, fs, fh)
    print(f"\n  [VINTAGE FIT — no hindsight leakage]")
    print(f"    current {WIN_PROB_POINTS_SCALE}/{HOME_FIELD_ADVANTAGE_PTS}: "
          f"Brier={brier(p_cur, y):.4f}  LogLoss={log_loss(p_cur, y):.4f}")
    print(f"    fitted  {fs}/{fh}: Brier={brier(p_fit, y):.4f}  LogLoss={log_loss(p_fit, y):.4f}")
    print("    reliability (deciles, vintage-fitted):")
    print_table(decile_table(p_fit, y))
    print(f"\n  This IS leak-free (vintage-correct SP+). It supersedes the market-bridge")
    print(f"  lower bound in docs/calibration-report.md. Re-fit projector.py to "
          f"({fs}, {fh}) only on Zach's approval — no constant changes here.")
    return {"fitted": (fs, fh), "n": len(y), "weeks": n_weeks}


def main():
    ap = argparse.ArgumentParser(description="SP+ win-prob scale calibration")
    ap.add_argument("--archive", action="store_true",
                    help="BUILD 2: non-leaky within-season fit off data/ratings_archive/")
    args = ap.parse_args()
    if args.archive:
        calibrate_archive(utils.get_season())
        return

    season = utils.get_season()
    print("!" * 72)
    print("! HINDSIGHT-LEAKAGE WARNING: CFBD SP+/FPI have NO weekly vintage (the")
    print("! `week` param is ignored). This cache holds SEASON-FINAL ratings, so the")
    print("! fitted values below are leakage-biased (too-steep scale) and MUST NOT be")
    print("! applied to the live projector. Vintage-correct in-season ratings exist")
    print("! only via Elo (/ratings/elo?year=&week=). For the REAL leak-free scale")
    print("! recommendation, run calibrate_spread.py (spreads + prior-season SP+).")
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
    print("  A non-leaky fit IS possible — just not with in-season SP+. Run")
    print("  calibrate_spread.py (closing spreads + prior-season SP+) for the leak-")
    print("  free scale bracket + recommendation; see docs/calibration-report.md.")


if __name__ == "__main__":
    main()
