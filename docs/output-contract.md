# Output Contract — the engine's write surface (LOCKED)

Every downstream consumer (site, email, pundit) reads **only** what this file
defines. The engine writes exactly three files per group to the single write
target `site/data/<group_id>/`:

| file | board | overwrite vs accumulate |
|------|-------|-------------------------|
| `standings.json`  | Board 1 — exact arithmetic | **overwrite** (regenerated each run) |
| `projection.json` | Board 2 — labeled projection | **overwrite** (regenerated each run) |
| `timeline.json`   | history | **append-only** (one snapshot per scored week; idempotent) |

`scripts/test_output_shape.py` validates every emitted file against this
contract; it is part of the suite. Consumers may rely on every key below being
present. Producers may add keys, but never remove or rename one here without
updating this file and the shape test **in the same commit**.

Conventions: deltas are signed floats in the pick's O/U direction
(`O`: `wins - line`; `U`: `line - wins`). Probabilities are floats in `[0,1]`.
`as_of_week` in `meta` mirrors the `--as-of-week N` flag and is `null` on a live
run (real current week). All timestamps are ISO-8601 UTC.

---

## `meta` block (shared by standings.json + projection.json)

```json
{
  "group_id": "panel",
  "season": 2025,
  "as_of_week": 6,
  "generated_at": "2026-07-22T04:15:00+00:00",
  "cache_fetched_at": "2026-07-22T03:34:33+00:00"
}
```
`projection.json`'s `meta` carries two extra keys: `"ratings_source": "SP+"` and
`"ratings_asof"` (ISO stamp of when the SP+ ratings used were pulled — the
cache's `fetched_at`; SP+ is a single snapshot, not weekly history).

---

## `standings.json` — Board 1 (always exact, pure arithmetic)

```json
{
  "meta": { "...": "meta block above" },
  "managers": [
    {
      "manager_id": "zach",
      "display_name": "Zach",
      "banked_total": 6.5,
      "floor": -1.5,
      "ceiling": 14.5,
      "rank": 1,
      "picks": [
        {
          "team": "Ohio State",
          "conference": "Big Ten",
          "line": 10.5,
          "direction": "O",
          "banked_wins": 8,
          "banked_losses": 0,
          "games_remaining": 4,
          "banked_delta": -2.5,
          "floor": -2.5,
          "ceiling": 1.5,
          "status": "LIVE"
        }
      ]
    }
  ]
}
```

Arithmetic (all exact; reproducible by hand):
- `banked_delta` = O: `banked_wins - line`; U: `line - banked_wins`.
- `floor`  = worst-case final delta (O: lose out → `banked_wins - line`;
  U: win out → `line - (banked_wins + games_remaining)`).
- `ceiling` = best-case final delta (O: win out → `(banked_wins + games_remaining) - line`;
  U: lose out → `line - banked_wins`).
- `status` = `CLINCHED` if `floor > 0`; `DEAD` if `ceiling < 0`; else `LIVE`.
- manager `banked_total` / `floor` / `ceiling` = sums of the picks' fields.
- `rank` = 1-based over managers by `banked_total` desc, ties broken by `floor`
  desc then `manager_id` (deterministic; distinct ranks).

Invariant (checked by the shape test and VERIFY step 3): when a pick's
`games_remaining == 0`, `floor == ceiling == banked_delta`.

---

## `projection.json` — Board 2 (labeled projection, ratings-driven)

```json
{
  "meta": { "...": "meta block", "ratings_source": "SP+", "ratings_asof": "2026-07-22T03:34:33+00:00" },
  "managers": [
    {
      "manager_id": "zach",
      "display_name": "Zach",
      "expected_total": 9.3,
      "p05": 1.5,
      "p50": 9.5,
      "p95": 16.5,
      "p_win_pool": 0.42,
      "picks": [
        {
          "team": "Ohio State",
          "conference": "Big Ten",
          "line": 10.5,
          "direction": "O",
          "p_beat_line": 0.71,
          "expected_delta": 0.8,
          "expected_final_wins": 11.3,
          "win_distribution": [
            { "wins": 8,  "prob": 0.01 },
            { "wins": 9,  "prob": 0.08 },
            { "wins": 10, "prob": 0.22 },
            { "wins": 11, "prob": 0.34 },
            { "wins": 12, "prob": 0.35 }
          ]
        }
      ]
    }
  ]
}
```

- Per-game win prob = logistic of the SP+ rating differential + home field
  (constants exposed in `projector.py`).
- `win_distribution` = exact Poisson-binomial over the pick's remaining games
  (`np.convolve` of `[1-p_i, p_i]`), indexed by **final** win total from
  `banked_wins` to `banked_wins + games_remaining`; `prob` sums to 1.
- `expected_final_wins` = `banked_wins + Σ p_i`;
  `expected_delta` = O: `expected_final_wins - line`; U: `line - expected_final_wins`.
- `p_beat_line` = O: `P(final_wins > line)`; U: `P(final_wins < line)`
  (lines are half-integers, so no push).
- `expected_total` = Σ picks' `expected_delta` (exact).
- `p05/p50/p95` = percentiles of the manager's projected **total delta**, and
  `p_win_pool` = P(this manager has the group's highest total) — **both from the
  shared-per-team-draw Monte Carlo** (see below), so managers on opposite sides
  of the same team are correctly anti-correlated.

Invariant: when `games_remaining == 0`, `expected_delta == banked_delta`
exactly and `p_beat_line ∈ {0.0, 1.0}`.

### Pool odds MUST use shared per-team draws (ARCHITECTURE §3)
Each Monte-Carlo trial draws every team's remaining season **once**, then scores
every manager in the group off that same draw. Two managers holding opposite
sides of one team get anti-correlated totals; independent draws mis-state
`p_win_pool` by 5–7 points. `test_projector_correlation.py` asserts the
negative correlation.

---

## `timeline.json` — append-only history

```json
{
  "group_id": "panel",
  "season": 2025,
  "snapshots": [
    {
      "as_of_week": 6,
      "generated_at": "2026-07-22T04:15:00+00:00",
      "managers": [
        {
          "manager_id": "zach",
          "p_win_pool": 0.42,
          "picks": [
            {
              "team": "Ohio State",
              "banked_delta": -2.5,
              "floor": -2.5,
              "ceiling": 1.5,
              "expected_delta": 0.8,
              "p_beat_line": 0.71
            }
          ]
        }
      ]
    }
  ]
}
```

- One snapshot **per scored week — latest run wins — NOT one per pipeline run.**
  `as_of_week` here is the **effective** scored week: the `--as-of-week N` value,
  or the cache's real current week on a live run (always concrete, never null —
  it is the idempotency key).
- **Append-only / idempotent, keyed by week:** re-running the same effective week
  *replaces* that week's snapshot in place (never duplicates it, never rewrites
  earlier weeks). `snapshots` stays sorted ascending by `as_of_week`.
- **Why latest-run-wins (intended behavior):** the cadence is twice-weekly (a
  Saturday-night heavy pass + one midweek pass, ARCHITECTURE §10.6). Both pulls
  land in the **same** CFB week, so the midweek run **overwrites that week's
  Saturday entry** — the timeline holds the most recent read of each week, not a
  row per pull. A pick's line never moves (§1), and banked results only ever
  firm up within a week, so the latest snapshot is always the most correct one.
  Consumers that want intra-week deltas should diff across weeks, not runs.
