# Ratings vintage archive (BUILD 2) — committed, append-only

CFBD's `/ratings/sp` and `/ratings/fpi` return only the **season-final** rating
(the `week` parameter is accepted but ignored), so past *in-season* vintages are
unrecoverable. But every `fetch_results.py` pass already pulls the **current**
rating — so future vintages are free. This directory captures them.

## What writes here

`scripts/fetch_results.py` → `archive_ratings(cache)`, once per successful fetch,
**after** the shared cache is safely written. Best-effort: an archive hiccup
warns and never fails the pipeline.

## Layout

```
data/ratings_archive/<season>/<YYYY-MM-DD>.json
```

One file per **fetch date**. Each snapshot:

```json
{
  "date": "2026-09-15",          // fetch date (UTC) = the vintage date
  "week": 3,                      // CFBD-reported current week at fetch time
  "fetched_at": "2026-09-15T...", // full cache timestamp
  "season": 2026,
  "source": "CFBD",
  "sp_ratings":  { "<team>": { "rating": ..., "ranking": ..., ... }, ... },
  "fpi_ratings": { "<team>": { "fpi": ... }, ... },
  "lines": {}                     // populated only if a future fetch pulls lines
}
```

## Guarantees

- **Append-only, never overwrite.** If today's file already exists, the fetch
  **skips** it (a same-day re-run is a no-op; the first good snapshot of the day
  wins). A same-date file recording a *different* week is kept, with a warning.
- **Twice-weekly cadence → two files per week**, on distinct dates. Same CFBD
  week across two dates is expected (SP+ can refresh mid-week); these are distinct
  vintages and both are kept. No duplication, no clobbering.

## Why it's committed (not gitignored)

This archive **is** the future non-leaky calibration set. Once it holds enough
live weeks, `scripts/calibrate.py --archive` fits the SP+ win-prob scale on the
**vintage-correct** rating for each game (the snapshot taken *before* that game's
week) — a true within-season holdout with no hindsight leakage. From the **2027
offseason** onward this **supersedes** the market-bridge estimate in
`docs/calibration-report.md` (which is only a lower bound, because it uses final
SP+). Losing this archive would mean waiting another full season to rebuild it.
```
