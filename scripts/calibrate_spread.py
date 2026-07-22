#!/usr/bin/env python3
"""
calibrate_spread.py — LEAK-FREE calibration of the points->win-probability scale
(ARCHITECTURE §4/§12). The companion to calibrate.py.

calibrate.py backtests the SP+ scale against a completed season, but CFBD's SP+
is season-FINAL only (the `week` param is ignored), so that fit is HINDSIGHT-
LEAKED and biased too steep — it is a *diagnostic*, not a recommendation. This
tool produces the leak-free evidence calibrate.py cannot, via two designs that
never use in-season SP+:

  PATH A — CLOSING SPREADS (primary). A closing line is set BEFORE kickoff and
    cannot encode the result, so calibrating spread -> P(win) is leak-free by
    construction and involves no SP+ at all. Fits the projector's logistic form
    p = 1/(1+exp(-margin/scale)) with margin = the market-implied home margin
    (-spread). Fit on 2021-2023, evaluated out-of-sample on 2024-2025.

  PATH B — PRIOR-SEASON SP+ (the bracket). Predict season Y from FINAL season
    Y-1 SP+. Leak-free (Y-1 ratings pre-date every Y game) but STALE, so it is
    biased FLAT (too-large scale) — the UPPER bound. The leaky within-season fit
    is biased STEEP (too-small scale) — the LOWER bound. Together they bracket
    the true vintage-in-season SP+ scale.

  UNITS BRIDGE. Path A calibrates points-in-*spread*-units. To apply it to an
    SP+ differential we must know the SP+diff <-> spread scale. Regressing the
    two gives a nominal slope ~1 (same point scale) BUT SP+diff is a noisier
    predictor, so the calibration-correct bridge regresses the sharp market on
    SP+diff (attenuation) and inflates the scale accordingly.

Changes NO constant — reports and recommends; the projector holds until Zach
decides (same discipline as calibrate.py). Re-run each offseason.

Data: fetches /lines, /ratings/sp, /games for BACKTEST_YEARS into a *gitignored*
local cache (data/backtest_cache/, NEVER the season-guarded production cache —
that holds only the live season). ~3 calls/year; well under the 1,000/mo budget.

Usage:
    python scripts/calibrate_spread.py            # use cache if present, else fetch
    python scripts/calibrate_spread.py --refresh  # force a re-fetch
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from cfbd_client import CFBDClient
from projector import HOME_FIELD_ADVANTAGE_PTS, WIN_PROB_POINTS_SCALE

BACKTEST_YEARS = [2021, 2022, 2023, 2024, 2025]
TRAIN_YEARS = [2021, 2022, 2023]
OOS_YEARS = [2024, 2025]
# Closing-line source preference. Bovada carries ~100% of FBS-vs-FBS games in
# every backtest year, so it is the consistent closing book; the rest are
# fallbacks for the rare gap.
BOOK_PREF = ["Bovada", "DraftKings", "consensus", "William Hill (New Jersey)", "ESPN Bet"]
LEAKY_SINGLE_SEASON = 7.1     # the leak-contaminated single-season fit on record
EPS = 1e-15

CACHE_DIR = utils.DATA_DIR / "backtest_cache"


# --- data (gitignored backtest cache — NOT the production cache) -------------

def fetch_backtest_data(refresh=False):
    """{'lines'|'sp'|'games': {year: payload}} for BACKTEST_YEARS. Reads the
    gitignored cache if present; otherwise (or with --refresh) pulls from CFBD.
    Deliberately does NOT touch data/cfbd_cache.json (season-guarded, live-only)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    need_fetch = refresh
    for kind in ("lines", "sp", "games"):
        p = CACHE_DIR / f"{kind}.json"
        if p.exists() and not refresh:
            raw = json.loads(p.read_text(encoding="utf-8"))
            out[kind] = {int(k): v for k, v in raw.items()}
        else:
            need_fetch = True
    if not need_fetch:
        return out

    client = CFBDClient(utils.get_api_key())
    data = {"lines": {}, "sp": {}, "games": {}}
    for y in BACKTEST_YEARS:
        data["lines"][y] = client.get("/lines", {"year": y, "seasonType": "regular"})
        data["sp"][y] = client.get("/ratings/sp", {"year": y})
        data["games"][y] = client.get("/games", {"year": y, "seasonType": "regular"})
        print(f"  fetched {y}: lines={len(data['lines'][y])} sp={len(data['sp'][y])} "
              f"games={len(data['games'][y])}")
    for kind, payload in data.items():
        (CACHE_DIR / f"{kind}.json").write_text(json.dumps(payload), encoding="utf-8")
    print(f"  [{client.call_count} CFBD calls] cached -> {CACHE_DIR}")
    return {k: {int(kk): vv for kk, vv in v.items()} for k, v in data.items()}


# --- model / metrics ---------------------------------------------------------

def sigmoid(m, scale):
    return 1.0 / (1.0 + np.exp(-m / scale))


def brier(p, y):
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_scale_only(m, y):
    """1-param logistic scale (margin already carries HFA — a spread does)."""
    best = (None, np.inf)
    for s in np.arange(4.0, 30.001, 0.5):
        ll = log_loss(sigmoid(m, s), y)
        if ll < best[1]:
            best = (s, ll)
    for s in np.arange(max(1.0, best[0] - 0.5), best[0] + 0.5001, 0.05):
        ll = log_loss(sigmoid(m, s), y)
        if ll < best[1]:
            best = (s, ll)
    return round(float(best[0]), 2)


def fit_scale_hfa(diff, home, y):
    """2-param (scale, hfa): margin = diff + hfa*home. Coarse then fine grid."""
    def best_over(scales, hfas):
        b = (None, None, np.inf)
        for s in scales:
            for h in hfas:
                ll = log_loss(sigmoid(diff + h * home, s), y)
                if ll < b[2]:
                    b = (s, h, ll)
        return b
    s0, h0, _ = best_over(np.arange(4.0, 30.01, 0.5), np.arange(-1.0, 8.01, 0.5))
    s1, h1, _ = best_over(np.arange(max(1.0, s0 - .5), s0 + .5001, .05),
                          np.arange(h0 - .5, h0 + .5001, .05))
    return round(float(s1), 2), round(float(h1), 2)


def wilson(k, n, z=1.96):
    """95% Wilson score interval for a binomial rate (better than normal near 0/1)."""
    if n == 0:
        return (0.0, 0.0)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    hw = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - hw), min(1.0, c + hw))


def decile_table(p, y, nbins=10):
    """Equal-count bins by predicted prob; each row carries a 95% CI on actual."""
    order = np.argsort(p, kind="stable")
    rows = []
    for i, idx in enumerate(np.array_split(order, nbins), 1):
        if len(idx) == 0:
            continue
        k, n = int(y[idx].sum()), len(idx)
        lo, hi = wilson(k, n)
        rows.append((i, n, float(p[idx].mean()), k / n, lo, hi, k / n - float(p[idx].mean())))
    return rows


def print_deciles(rows):
    print("      {:>3} {:>4} {:>9} {:>7} {:>15} {:>7}".format(
        "bin", "n", "mean_pred", "actual", "95% CI", "gap"))
    for i, n, mp, ar, lo, hi, gap in rows:
        print("      {:>3} {:>4} {:>9.3f} {:>7.3f} [{:>5.3f},{:>5.3f}] {:>+7.3f}".format(
            i, n, mp, ar, lo, hi, gap))


# --- adapters (leak-free feature builders) -----------------------------------

def closing_spread(game):
    """Preferred-book closing spread (home perspective, neg = home favored)."""
    provs = {l["provider"]: l for l in game.get("lines", []) if l.get("spread") is not None}
    for b in BOOK_PREF:
        if b in provs:
            return float(provs[b]["spread"]), b
    if provs:
        p = next(iter(provs))
        return float(provs[p]["spread"]), p
    return None, None


def spread_games(lines_by_year, years):
    """FBS-vs-FBS decided games -> (market home margin = -spread, home_win)."""
    m, y = [], []
    coverage = {}
    for yr in years:
        tot = used = 0
        for g in lines_by_year[yr]:
            if g.get("homeClassification") != "fbs" or g.get("awayClassification") != "fbs":
                continue
            tot += 1
            sp, _ = closing_spread(g)
            if sp is None:
                continue
            hs, as_ = g.get("homeScore"), g.get("awayScore")
            if hs is None or as_ is None or hs == as_:
                continue
            m.append(-sp)
            y.append(1.0 if hs > as_ else 0.0)
            used += 1
        coverage[yr] = (tot, used)
    return np.array(m), np.array(y), coverage


def sp_map(sp_by_year, year):
    return {r["team"]: float(r["rating"]) for r in sp_by_year[year] if r.get("rating") is not None}


def sp_games(games_by_year, sp_by_year, pred_years, offset):
    """FBS-vs-FBS decided games rated from SP+ of (game_year + offset).
    offset -1 = prior-season (leak-free); 0 = within-season (leaky)."""
    diff, home, y = [], [], []
    dropped = 0
    for gy in pred_years:
        rmap = sp_map(sp_by_year, gy + offset)
        for g in games_by_year[gy]:
            if g.get("homeClassification") != "fbs" or g.get("awayClassification") != "fbs":
                continue
            if not g.get("completed"):
                continue
            hp, ap = g.get("homePoints"), g.get("awayPoints")
            if hp is None or ap is None or hp == ap:
                continue
            ht, at = g.get("homeTeam"), g.get("awayTeam")
            if ht not in rmap or at not in rmap:
                dropped += 1
                continue
            diff.append(rmap[ht] - rmap[at])
            home.append(0.0 if g.get("neutralSite") else 1.0)
            y.append(1.0 if hp > ap else 0.0)
    return np.array(diff), np.array(home), np.array(y), dropped


# --- the three reports -------------------------------------------------------

def path_a(lines_by_year):
    print("=" * 76)
    print("PATH A — CLOSING SPREADS (leak-free; book preference:", BOOK_PREF[0], "first)")
    print("=" * 76)
    m_all, y_all, cov = spread_games(lines_by_year, BACKTEST_YEARS)
    for yr in BACKTEST_YEARS:
        tot, used = cov[yr]
        print(f"  {yr}: FBS-v-FBS={tot:>4}  closing-line coverage={used/tot*100:5.1f}%  used={used:>4}")
    scale = fit_scale_only(m_all, y_all)
    p = sigmoid(m_all, scale)
    print(f"\n  [FULL 2021-2025] n={len(y_all)}  fitted scale = {scale}  (SPREAD points)")
    print(f"    fitted : Brier={brier(p, y_all):.4f}  LogLoss={log_loss(p, y_all):.4f}")
    pc = sigmoid(m_all, WIN_PROB_POINTS_SCALE)
    print(f"    proj {WIN_PROB_POINTS_SCALE} : Brier={brier(pc, y_all):.4f}  LogLoss={log_loss(pc, y_all):.4f}")
    print("    reliability (deciles, fitted scale):")
    print_deciles(decile_table(p, y_all))

    m_tr, y_tr, _ = spread_games(lines_by_year, TRAIN_YEARS)
    m_te, y_te, _ = spread_games(lines_by_year, OOS_YEARS)
    s_tr = fit_scale_only(m_tr, y_tr)
    p_te = sigmoid(m_te, s_tr)
    print(f"\n  [TRAIN {TRAIN_YEARS[0]}-{TRAIN_YEARS[-1]}] scale = {s_tr}   "
          f"train LogLoss={log_loss(sigmoid(m_tr, s_tr), y_tr):.4f}")
    print(f"  [OOS  {OOS_YEARS[0]}-{OOS_YEARS[-1]}] @train scale {s_tr}: "
          f"Brier={brier(p_te, y_te):.4f}  LogLoss={log_loss(p_te, y_te):.4f}  "
          f"(refit-on-OOS = {fit_scale_only(m_te, y_te)})")
    print("    OOS reliability (@ train scale):")
    print_deciles(decile_table(p_te, y_te))
    return scale, s_tr


def path_b(games_by_year, sp_by_year):
    print("\n" + "=" * 76)
    print("PATH B — PRIOR-SEASON FINAL SP+ (predict Y from Y-1; leak-free but stale)")
    print("=" * 76)
    d, h, y, dropped = sp_games(games_by_year, sp_by_year, [2022, 2023, 2024, 2025], -1)
    sB, hB = fit_scale_hfa(d, h, y)
    p = sigmoid(d + hB * h, sB)
    print(f"  predict 2022-2025 from FINAL SP+ of 2021-2024   n={len(y)} "
          f"({dropped} dropped — team unrated in Y-1)")
    print(f"  fitted: scale = {sB}, hfa = {hB}   Brier={brier(p, y):.4f}  LogLoss={log_loss(p, y):.4f}")
    pc = sigmoid(d + HOME_FIELD_ADVANTAGE_PTS * h, WIN_PROB_POINTS_SCALE)
    print(f"  proj ({WIN_PROB_POINTS_SCALE}/{HOME_FIELD_ADVANTAGE_PTS}) same data: "
          f"Brier={brier(pc, y):.4f}  LogLoss={log_loss(pc, y):.4f}")
    print("  reliability (deciles, prior-season fitted):")
    print_deciles(decile_table(p, y))

    dw, hw, yw, _ = sp_games(games_by_year, sp_by_year, BACKTEST_YEARS, 0)
    sW, hWfa = fit_scale_hfa(dw, hw, yw)
    print(f"\n  within-season LEAKY fit (2021-2025 pooled): scale = {sW}, hfa = {hWfa}")
    print(f"    (generalizes the single-season leaky {LEAKY_SINGLE_SEASON}; both too steep)")
    return sB, sW


def units_bridge(lines_by_year, sp_by_year, path_a_scale):
    print("\n" + "=" * 76)
    print("UNITS BRIDGE — SP+ differential vs closing spread (same games, same-year SP+)")
    print("=" * 76)
    xs, ys = [], []
    for yr in BACKTEST_YEARS:
        rmap = sp_map(sp_by_year, yr)
        for g in lines_by_year[yr]:
            if g.get("homeClassification") != "fbs" or g.get("awayClassification") != "fbs":
                continue
            ht, at = g.get("homeTeam"), g.get("awayTeam")
            if ht not in rmap or at not in rmap:
                continue
            sp, _ = closing_spread(g)
            if sp is None:
                continue
            xs.append(-sp)            # market-implied home margin
            ys.append(rmap[ht] - rmap[at])   # SP+ differential (no HFA)
    xs, ys = np.array(xs), np.array(ys)
    r = np.corrcoef(xs, ys)[0, 1]
    fslope, fint = np.polyfit(xs, ys, 1)     # SP+diff ~ market_margin (user framing)
    bslope, bint = np.polyfit(ys, xs, 1)     # market_margin ~ SP+diff (attenuation-correct)
    scale_sp = path_a_scale / bslope
    hfa_sp = bint / bslope
    print(f"  n={len(xs)} games")
    print(f"  nominal (SP+diff ~ market): slope={fslope:.3f}, intercept={fint:+.2f}, "
          f"R^2={r * r:.3f}")
    print(f"    slope ~= 1 -> SP+diff is on ~the same NOMINAL point scale as a spread;")
    print(f"    intercept {fint:+.2f} ~ recovers market home-field (SP+diff carries none).")
    print(f"  calibration (market ~ SP+diff, attenuation-correct): slope={bslope:.3f}")
    print(f"    SP+ is noisier (R^2={r * r:.2f}) -> a weaker predictor needs a LARGER scale.")
    print(f"    => SP+ scale = {path_a_scale}/{bslope:.3f} = {scale_sp:.2f}, implied HFA = {hfa_sp:.2f}")
    print(f"    (final SP+ is leak-sharp; live SP+ noisier still -> true SP+ scale >= this.)")
    return scale_sp, hfa_sp


def main():
    ap = argparse.ArgumentParser(description="Leak-free scale calibration (spreads + prior-season SP+)")
    ap.add_argument("--refresh", action="store_true", help="force re-fetch of the backtest cache")
    args = ap.parse_args()

    print("#" * 76)
    print("# LEAK-FREE CALIBRATION — closing spreads (Path A) + prior-season SP+ (Path B)")
    print(f"# projector holds: WIN_PROB_POINTS_SCALE={WIN_PROB_POINTS_SCALE}, "
          f"HOME_FIELD_ADVANTAGE_PTS={HOME_FIELD_ADVANTAGE_PTS} (this tool changes NOTHING)")
    print("#" * 76)
    data = fetch_backtest_data(args.refresh)

    a_scale, a_train = path_a(data["lines"])
    b_scale, w_scale = path_b(data["games"], data["sp"])
    sp_scale, sp_hfa = units_bridge(data["lines"], data["sp"], a_scale)

    def inside(x):
        return w_scale <= x <= b_scale

    print("\n" + "#" * 76)
    print("# SUMMARY — the SP+ win-prob scale (report only; Zach decides)")
    print("#" * 76)
    print(f"  Path A closing-spread scale (SPREAD pts)   : {a_scale}  (train {a_train}, holds OOS)")
    print(f"  Path A -> SP+ units (attenuation-correct)  : {sp_scale:.1f}  (implied HFA {sp_hfa:.1f})")
    print(f"  Path B prior-season SP+ scale (UPPER bnd)  : {b_scale}")
    print(f"  within-season leaky fit (LOWER bnd)        : {w_scale} pooled / "
          f"{LEAKY_SINGLE_SEASON} single-season")
    print(f"  current projector constant                 : {WIN_PROB_POINTS_SCALE}")
    print(f"\n  BRACKET: {w_scale} <= true SP+ scale <= {b_scale}")
    print(f"    projector {WIN_PROB_POINTS_SCALE}          : "
          f"{'INSIDE' if inside(WIN_PROB_POINTS_SCALE) else 'OUTSIDE'}")
    print(f"    market-implied SP+ scale {sp_scale:.1f} : {'INSIDE' if inside(sp_scale) else 'OUTSIDE'}")
    print(f"    leaky {LEAKY_SINGLE_SEASON}              : "
          f"{'INSIDE' if inside(LEAKY_SINGLE_SEASON) else 'OUTSIDE (too steep)'}")


if __name__ == "__main__":
    main()
