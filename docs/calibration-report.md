# Leak-free calibration of the SP+ win-probability scale

**Question.** Board 2 turns an SP+ rating differential into a per-game win
probability through `p = 1/(1+exp(-margin/scale))`, `margin = SP+diff + HFA`.
Two constants drive it: `WIN_PROB_POINTS_SCALE` (currently `11.0`) and
`HOME_FIELD_ADVANTAGE_PTS` (currently `2.5`). **Both are unvalidated.** `7.1`
came out of `calibrate.py` but is hindsight-leaked; `11.0` was inherited from the
wc-challenge (soccer) repo — a different sport, different units — and had never
been tested against anything.

**Why the obvious calibration is invalid.** CFBD's `/ratings/sp` returns the
**season-final** rating only (the `week` param is accepted but ignored). Fitting
the scale by predicting week-3 games with end-of-season ratings is hindsight
leakage: games look more decided than they were live, so the optimizer sharpens
(a too-small, too-steep scale) that would run overconfident on live in-season
SP+. That is the `7.1`. It is a diagnostic, not a recommendation.

**Outcome (2026-07):** the leak-free evidence was adopted — `WIN_PROB_POINTS_SCALE`
and `HOME_FIELD_ADVANTAGE_PTS` were **jointly** refit on the market bridge to
**13.5 / 4.0** (see *Decision*, below). Reproduce with
`python scripts/calibrate_spread.py`.

---

## Data

`/lines`, `/ratings/sp`, `/games` for **2021–2025** regular season, FBS-vs-FBS,
pulled from CFBD into the gitignored `data/backtest_cache/` (never the
season-guarded production cache). 15 API calls total.

Closing spread source: **Bovada**, which carries a closing line for **100%** of
FBS-vs-FBS games in every year (3,730 / 3,730; one 2024 game fell back). We use
the `spread` field (the closing/consensus line), home perspective, negative =
home favored. This is the single most important robustness fact here — no
selection on which games had a market.

---

## Path A — calibrate on closing spreads (primary; leak-free by construction)

A closing line is set **before kickoff** and cannot encode the outcome, so
fitting spread → P(win) is leak-free with no SP+ involved. We fit the projector's
exact logistic form to the market-implied home margin (`margin = -spread`, which
already contains the market's home-field edge, so a single `scale` parameter).

| fit set | n | fitted scale | Brier | LogLoss |
|---|---|---|---|---|
| **Full 2021–2025** | 3,730 | **8.85** | 0.1761 | 0.5229 |
| current proj `11.0` on same data | 3,730 | — | 0.1771 | 0.5271 |
| Train 2021–2023 | 2,216 | **9.1** | — | 0.5231 |
| **OOS 2024–2025 @ train scale 9.1** | 1,514 | — | 0.1768 | **0.5228** |
| (scale refit on 2024–2025 alone) | 1,514 | 8.55 | — | — |

The scale is **rock-stable out of sample**: a scale fit on 2021–2023 scores the
held-out 2024–2025 seasons with *no* degradation (OOS LogLoss 0.5228 ≈ train
0.5231), and refitting on the holdout alone barely moves it (8.55 vs 9.1).
Reliability is clean across all ten deciles — every bin's actual win rate sits
inside its 95% Wilson interval around the predicted rate except a single ~5-point
wobble mid-table (noise at n≈373/bin).

**Path A result: the leak-free points→probability scale for a per-game-vintage
predictor is ≈ 8.85–9.1 — in *spread* points.** (Converting to SP+ units is the
units bridge below; do **not** compare 8.85 to 11.0 directly.)

---

## Path B — prior-season SP+ (the bracket)

Predict season Y using **final season Y-1** SP+ (2022–2025 from 2021–2024
ratings). Leak-free — the ratings pre-date every game they score — but **stale**,
so it under-rates a predictor's live sharpness and biases the fitted scale
**flat** (too large). Fitting both `scale` and `hfa`:

| design | n | scale | hfa | Brier | LogLoss |
|---|---|---|---|---|---|
| **Prior-season (leak-free, stale → flat)** | 2,934 | **17.45** | 5.45 | 0.2078 | 0.6015 |
| current proj `11.0`/`2.5` on same data | 2,934 | — | — | 0.2141 | 0.6208 |
| within-season (final SP+, **leaky → steep**) | 3,730 | **7.95** | 3.05 | — | — |

The leaky within-season fit generalized across all five seasons is **7.95**
(consistent with the single-season `7.1` on record) — both hindsight-biased too
steep. These two biased estimators bound the truth from opposite sides:

> **BRACKET:  7.95  ≤  true SP+ scale  ≤  17.45**
> (within-season leaky = too steep = lower bound; prior-season stale = too flat = upper bound)

- **`11.0` is INSIDE the bracket** (≈32% up from the floor).
- **`7.1` is OUTSIDE the bracket** — below even the leaky lower bound; it is the
  too-steep artifact, confirmed.

---

## Units bridge — is an SP+ differential on the same scale as a point spread?

Path A calibrates points **in spread units**. Applying its scale to an SP+
differential assumes the two are on the same scale. Regressing them on the same
3,730 games (same-year final SP+ vs closing line):

- **Nominal (SP+diff ~ market margin): slope 1.057, intercept −2.70, R² 0.786.**
  The slope ≈ 1 says an SP+ differential is on **~the same nominal point scale as
  a spread** (a 1-point SP+ edge ≈ 1 market point). The −2.70 intercept ≈
  recovers the market's home-field advantage (SP+diff carries no HFA; the market
  margin does), a nice sanity check.
- The naive "fold the slope in" move (1.057 × 8.85 = **9.35**) is **wrong for
  calibration** — it ignores that SP+ is a *noisier* predictor than the market.
- **Attenuation-correct (market margin ~ SP+diff): slope 0.744.** A weaker
  predictor must be shrunk toward the sharp market (0.744 < 1) and therefore
  needs a **larger** scale to stay calibrated. This gives the SP+-appropriate
  scale = **8.85 / 0.744 ≈ 11.9, with implied HFA ≈ 3.9.** Because this uses
  *final* (leak-sharp) SP+, live/vintage SP+ is noisier still → the true SP+
  scale is **≥ ~11.9**, i.e. this is a lower-ish estimate.

So the market, translated into SP+ units the right way, points at **~12**, not 9.

---

## The joint market-bridge fit — the adopted method

Path A calibrates points in *spread* units; the projector runs on an *SP+*
differential. The **market bridge** ties them together and yields both constants
from one fit. In the projector's exact, intercept-free form
`p = σ((SP+diff + HFA·home)/scale)`, pick `(scale, HFA)` to best reproduce the
market's leak-free win probability `q = σ(-spread/scale_A)`, scored by
cross-entropy (the same proper scoring rule Path A and `calibrate.py` use). Home
is neutral-aware (from `/games`, joined on game id). Bootstrap (1,500 resamples):

| constant | fitted | 95% CI | adopted |
|---|---|---|---|
| `WIN_PROB_POINTS_SCALE` | **13.55** | [12.77, 14.45] | **13.5** |
| `HOME_FIELD_ADVANTAGE_PTS` | **3.95** | [3.65, 4.25] | **4.0** |

Why a proper scoring rule and not margin-space OLS: the projector emits
*probabilities* (fed to the Poisson-binomial and pool sim), so calibration lives
in probability space. A margin-space OLS bridge gives ~11.9/4.0; the
probability-space fit gives 13.5/4.0 because it correctly penalizes the
overconfidence that SP+'s noise induces. Both **exceed 11.0** and put it below the
CI. HFA 4.0 ≈ ~2.4 true home-field + ~1.6 a nominal-home offset (a small residual
edge to the designated home team even at neutral sites) that the intercept-free
form folds into the home term; genuinely neutral games get HFA=0.

**It is a lower bound on flatness.** The bridge uses *final* SP+ (leak-sharp);
live in-season SP+ is noisier, so its attenuation is worse and the true live scale
is **above** 13.5. Holding a value *below* the fitted CI (as 11.0 was) runs the
projector overconfident — the exact failure mode this exercise set out to avoid.

## The numbers, side by side

| number | value | status |
|---|---|---|
| **ADOPTED — joint market-bridge pair** | **13.5 / 4.0** | scale CI [12.8, 14.4], HFA CI [3.7, 4.3]; a lower bound |
| Path A — closing-spread fit (spread pts) | 8.85 (train 9.1, holds OOS) | gold-standard leak-free; *spread* units |
| Path B — prior-season SP+ fit | 17.45 | leak-free upper bound (stale → flat) |
| leaky within-season fit | 7.95 pooled / **7.1** single | biased lower bound — refuted |
| old projector constant | **11.0 / 2.5** | inherited, untested; below the CI — **retired** |

Bracket: **7.95 ≤ true SP+ scale ≤ 17.45.** Adopted 13.5 is inside; `7.1` is
outside (too steep); old `11.0` was inside the bracket but below the market-bridge
CI, i.e. too steep relative to the market.

---

## Decision (adopted 2026-07)

**Set `WIN_PROB_POINTS_SCALE = 13.5`, `HOME_FIELD_ADVANTAGE_PTS = 4.0`** — the
jointly-fitted market-bridge pair. `11.0` retired (below a lower bound →
overconfident); `7.1` refuted (leak-steep, outside the bracket).

**Board impact** (replay at `--as-of-week 6`; only the test fixture has real
picks yet — church/family/panel are pre-draft TODO placeholders and are
unchanged): the flatter scale + higher HFA de-sharpen big favorites and tighten
the race. Largest single-pick move: **Ohio State O10.5 win-prob 0.821 → 0.709
(−0.112)**; the leader's pool-win prob compresses (owner-3 0.400 → 0.368),
expected totals shrink toward the lines. Direction is exactly the intended
de-overconfidence.

## Supersession — the vintage archive (BUILD 2)

The market bridge is a **lower bound** only because CFBD gives us solely *final*
SP+. That gap closes permanently going forward: `fetch_results.py` now snapshots
the current SP+/FPI on every pass into `data/ratings_archive/<season>/` (committed,
append-only — see that folder's README). Once the archive holds enough live weeks,
`python scripts/calibrate.py --archive` fits the scale on the **vintage-correct**
rating for each game (the snapshot taken *before* that game's week) — a true
within-season holdout with **no** hindsight leakage and no market proxy. That fit
**supersedes** this report's pair from the **2027 offseason** onward (first full
2026 season archived). Until then the mode reports "insufficient — use the market
bridge." A full spine switch SP+ → Elo remains a separate, deliberate reopen.

*Market bridge run 2026-07 on 2021–2025 via `scripts/calibrate_spread.py`;
non-leaky successor via `scripts/calibrate.py --archive`.*
