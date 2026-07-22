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

**This report does not change any constant.** It reports leak-free evidence and
one recommendation; Zach decides. Reproduce with `python scripts/calibrate_spread.py`.

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

## The three numbers, side by side

| number | value | status |
|---|---|---|
| **Path A** — closing-spread fit (spread pts) | **8.85** (train 9.1, holds OOS) | gold-standard, leak-free; *spread* units |
| **Path A → SP+ units** (attenuation-correct) | **≈ 11.9**, HFA ≈ 3.9 | market translated to SP+ units; a lower estimate |
| **Path B** — prior-season SP+ fit | **17.45** | leak-free upper bound (stale → flat) |
| leaky within-season fit | 7.95 pooled / **7.1** single | biased lower bound (do not adopt) |
| current projector | **11.0** / HFA 2.5 | inherited, was untested — now bracketed |

---

## Recommendation (report only — Zach decides)

**Hold `11.0`, now for a real reason, not for lack of one.** Every leak-free line
of evidence is consistent with it: it sits inside the only bracket we can build
(7.95–17.45), and the market translated into SP+ units lands at ~11.9 — within a
point of it. The leaky `7.1` is **refuted**: it is outside the bracket, too steep,
and would run the projector overconfident on live data.

Two honest caveats on that "hold":

1. **It's defensible, not validated to a point.** The bracket is wide because the
   only two leak-free SP+ estimators are strongly biased in opposite directions.
   If Zach wants a *tighter* number, the market bridge (~11.9) is the best single
   point estimate, and **the evidence leans slightly UP from 11.0 toward ~12, not
   down.** Moving 11.0 → 12 is a small, evidence-aligned change; moving toward 7.1
   is not.
2. **HFA 2.5 is at the low edge** of every estimate here (market intercept ~2.7,
   attenuation-implied ~3.9, prior-season fit 5.45). A nudge to ~3 is mildly
   supported, but it's a weaker signal than the scale and the two interact — so
   if HFA moves, re-fit them together rather than eyeballing.

The only way to *narrow* the SP+ bracket (a true within-season holdout) is to
switch the ratings spine SP+ → **Elo**, the one CFBD rating with a real weekly
vintage. That is a deliberate reopen of the ratings decision (ARCHITECTURE §4/§12),
not a constant tweak, and is out of scope here.

*Run 2026-07 on 2021–2025 via `scripts/calibrate_spread.py`. Constants unchanged.*
