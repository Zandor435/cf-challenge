# Output Contract — the engine's write surface (LOCKED)

Every downstream consumer (site, email, pundit) reads **only** what this file
defines. The engine writes exactly four files per group to the single write
target `docs/data/<group_id>/` (GitHub Pages serves from `docs/` on main):

| file | board | overwrite vs accumulate |
|------|-------|-------------------------|
| `standings.json`  | Board 1 — exact arithmetic | **overwrite** (regenerated each run) |
| `projection.json` | Board 2 — labeled projection | **overwrite** (regenerated each run) |
| `timeline.json`   | history | **append-only** (one snapshot per scored week; idempotent) |
| `analytics.json`  | Board 3 — reshape of the three above | **overwrite** (regenerated each run) |

`scripts/test_output_shape.py` validates every emitted file against this
contract; it is part of the suite. Consumers may rely on every key below being
present. Producers may add keys, but never remove or rename one here without
updating this file and the shape test **in the same commit**.

### Auxiliary publishers

The four boards above are what the **engine loop** (`run_groups.py`) writes, and
that set is closed. Three other scripts publish into the same directory on their
own cadence — they are not boards, nothing scores off them, and a group may be
missing any of them entirely. They are listed here because the sentence above
used to read as "these four files and nothing else", which was already untrue:

| file | producer | overwrite vs accumulate | absent when |
|------|----------|-------------------------|-------------|
| `personas.json` | `sync_personas.py` | **overwrite** | no personas authored for the group |
| `banners.json`  | `build_banners.py` | **overwrite** | no banner set built for the group |
| `columns/week_<N>.json` | `generate_commentary.py` | **accumulate** | that week has not been filed |
| `columns/index.json`    | `generate_commentary.py` | **derived** (rebuilt in full) | no column filed yet — the normal state before Week 0 |
| `columns/rail.json`     | `build_rail.py` (from the week packet) | **overwrite**, and REMOVED when the packet supports no card | the group is not in `build_rail.RAIL_GROUPS`, or the packet carries neither block |

Every consumer of these must treat a 404 as an ordinary state and render its own
empty state, exactly as `svp.html` does for a group with no column.

#### `columns/` — the published column archive

**This replaced a single `column.json` that held the newest column only and was
overwritten every week.** The `.md` sources under `groups/<group>/output/`
accumulated, but only the latest was ever reachable from the site, so filing
week 1 erased week 0 from the web. The archive accumulates instead.

```
docs/data/<group>/columns/week_<N>.json   one filed column   ACCUMULATE
docs/data/<group>/columns/index.json      the manifest       DERIVED
docs/data/<group>/columns/rail.json       the page's rail    DERIVED
```

##### `rail.json` — the column page's data rail

Two blocks, both copied verbatim out of the week packet by
`scripts/build_rail.py`, which computes nothing and re-selects nothing. The
packet's own selection (`collisions[0]`, and the coda rule's
`worst_pick_on_the_board`) is the selection, so the rail and the prose are
always about the same picks.

```json
{
  "group_id": "panel", "week": 0, "generated_at": "...",
  "collision": {
    "team": "Texas", "line": 9.5,
    "picks": [ { "manager": "Blaine", "direction": "U", "p_beat_line": "86%" },
               { "manager": "Chris",  "direction": "O", "p_beat_line": "14%" } ]
  },
  "featured_pick": {
    "card_title": "Oregon outlook",
    "manager": "Jonathan", "team": "Oregon", "direction": "O", "line": 10.5,
    "expected_final_wins": 9.456, "expected_delta": -1.044
  }
}
```

`featured_pick.card_title` is the **literal card heading**, written by
whichever builder selected the pick and printed verbatim. It exists because the
two builders answer *different selection questions* with the same fields:
preseason's `worst_pick_on_the_board` is "the pick the model likes least"
(lowest `market_gap`), while in season it is the bad-beat coda, "the pick that
died ugliest". Both are correct; titling both `<team> outlook` meant the card
changed subject at week 1 with nothing on the page saying so. Preseason emits
`"<team> outlook"` — the exact wording the page shipped before this field —
and `build_week_packet.py` emits `"<team> bad beat"`, the column's own name for
that coda. Consumers **must not** infer the title from `week`, a preseason
flag, or anything else; if the field is absent (a `rail.json` written before it
existed) fall back to `"<team> outlook"` and still render the card.

`p_beat_line` is a **rendered percent string**, not a probability. The site
renders JSON and computes nothing, so the rounding happens in Python; the key
keeps the packet's name for provenance and the value is what the card prints.
`line`, `expected_final_wins` and `expected_delta` are the packet's own
decimals, unchanged (`expected_final_wins` / `expected_delta` are the packet's
`implied_expected_wins` / `market_gap` — the same two numbers `projection.json`
carries at one fewer decimal place).

Either key may be **absent**, and absence is ordinary: no collision this week,
or no featured pick. Consumers drop that card. A packet supporting neither
removes the file outright rather than leaving a stale card standing, so a 404
here is the normal state for most groups and every consumer must render
without it.

Both builders produce `collisions` and `worst_pick_on_the_board`:
`preseason_baseline.py --week0-packet` for Week 0, and `build_week_packet.py`
for every packed real week thereafter. (This paragraph previously said the
in-season counterpart did not exist and the first real week cleared the rail —
that stopped being true when the in-season blocks landed.)

##### `week_<N>.json` — one filed column

```json
{
  "meta": {
    "group_id": "family",
    "season": 2026,
    "week": 0,
    "preseason": true,
    "generated_at": "2026-08-23T16:04:11+00:00",
    "model": "gpt-4o",
    "source": "groups/family/output/column_week_0.md"
  },
  "column": {
    "paragraphs": ["...", "..."],
    "word_count": 412
  }
}
```

- Unchanged in shape from the `column.json` it replaced — only its path and its
  lifetime changed. **Write-once per week:** filing week N opens `week_N.json`
  and no other, so a publish cannot mutate an already-filed column. Re-filing
  the same week after a prompt fix rewrites that one file, which is what a
  prompt fix is for.
- `groups/<group>/output/column_week_<N>.md` remains the **source of record** —
  what `column_memory.json` names, what next week's prompt reads back for
  callbacks, and what a human reviews. `meta.source` names it.
- It is also the **durable record of when a column was filed**. The `.md`
  carries no title, byline, date or week inside it (week is in its filename and
  nothing else is), so this file — never the source — is what the index is
  built from.
- `paragraphs` is the column **pre-split in Python**, blank-line separated with
  blanks dropped. The page renders one `<p>` per entry and splits nothing
  itself; `word_count` is likewise computed at write time. Same rule as
  `analytics.json`: if the page needs a number, it gets a key.
- `meta.preseason` is the packet's own flag, not a second opinion about what
  week 0 means. The page labels a preseason column "Preseason" rather than
  "Week 00", matching `standings.json`'s null `as_of_week`. **Week 0 is an
  ordinary entry in the archive, not a special case.**

##### `index.json` — the manifest

```json
{
  "$note": ["..."],
  "$version": 1,
  "group_id": "panel",
  "count": 2,
  "columns": [
    {"week": 1, "preseason": false, "generated_at": "...", "word_count": 344, "file": "week_1.json"},
    {"week": 0, "preseason": true,  "generated_at": "...", "word_count": 332, "file": "week_0.json"}
  ]
}
```

- **Derived**, and derived from the published `week_<N>.json` files beside it —
  never from the `.md` sources, which carry no date and would restamp every
  historical column with today. Regenerable with no network:
  `python scripts/generate_commentary.py --group <g> --reindex`.
- **Newest first.** `columns[0]` is the current column; the order is the page's
  reading order and is not re-sorted in JS. Sorted by `week` as an integer, so
  week 10 precedes week 2.
- **Carries no prose and no timestamp of its own.** A reader opening week N
  fetches the file that entry's `file` key names. Nothing here is a clock
  reading, so rebuilding with nothing new is byte-identical and a republish
  produces no git churn.
- A week file that will not parse is skipped with a `::warning::` rather than
  taking down the index for every other week (playbook rule 10).

Conventions: deltas are signed floats in the pick's O/U direction
(`O`: `wins - line`; `U`: `line - wins`). Probabilities are floats in `[0,1]`.
`as_of_week` in `meta` mirrors the `--as-of-week N` flag and is `null` on a live
run (real current week). All timestamps are ISO-8601 UTC.

---

## `meta` block (shared by standings.json + projection.json + analytics.json)

```json
{
  "group_id": "panel",
  "season": 2025,
  "as_of_week": 6,
  "draft_status": "dummy",
  "generated_at": "2026-07-22T04:15:00+00:00",
  "cache_fetched_at": "2026-07-22T03:34:33+00:00"
}
```
`draft_status` (standings.json only) mirrors the group's `picks.json` top-level
field: `"dummy"` for engineered sample data — the site shows the amber
**sample-data** banner — or `"final"` once the real draft is entered (`null` for
the demo fixture and when the key is absent; only the literal `"dummy"` triggers
the banner). `projection.json`'s `meta` carries two extra keys instead:
`"ratings_source": "SP+"` and
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

### Head-to-head games are ONE draw, not two (ARCHITECTURE §3)
The rule above shares a draw per **team**. That is not sufficient on its own:
when two *picked* teams play **each other**, the same physical game sits on both
teams' remaining slates, and drawing each team's schedule independently lets a
trial contain both teams winning it — an outcome that does not exist. It is not
a corner case; at week 6 the four groups carry 5 / 10 / 17 / 28 such games.

So the sim draws **one uniform per game**, not per `(team, game)`:

- Games are paired by `(week, {team_a, team_b})` — identical from either side,
  and week-qualified because a conference-championship rematch is a real second
  game between the same two teams.
- The **reference side** is the home team; at a neutral site, the
  alphabetically-first canonical name. The reference wins iff `u < p_reference`;
  the other side wins iff the reference lost. Exactly one side wins, always.
- The two sides' win probabilities are **already exact complements** — home-field
  is applied antisymmetrically (`+HFA` / `−HFA`, `0` at a neutral site) and the
  logistic satisfies `logistic(−x) = 1 − logistic(x)` — so nothing is
  reconciled. `p_A + p_B == 1` is *asserted*, not assumed; a violation means the
  win-probability model changed and the coupling would be arbitrating a
  contradiction.
- A slate **asymmetry** (A lists B in week N, B does not list A) fails loud and
  names both teams. The two teams disagreeing about the slate is exactly what
  `utils.team_state` is supposed to make impossible.

**Coupling changes the joint distribution only.** Every per-pick field
(`win_distribution`, `p_beat_line`, `expected_delta`, `expected_final_wins`,
`expected_total`) is a marginal computed from that pick's own Poisson-binomial
and is **arithmetically unchanged** — verified as a zero-diff across all four
groups. Only `p05/p50/p95` and `p_win_pool` move, and in practice by less than
half a percentage point; no group's ordering changed.

`test_projector_h2h.py` asserts coherence (no trial has both sides winning, or
both losing), complementarity, marginal preservation, the untouched unpaired
path, and the loud failure on asymmetry.

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

---

## `analytics.json` — Board 3 (reshape; the analytics page is a pure renderer)

The analytics page **computes nothing**. Every number it shows is computed in
`scripts/analytics.py` and shipped as a key — including ratios and sort order.
If the page needs a number, it gets a key. This is the same rule the rest of the
site follows, stated once more because this file is where it is easiest to break.

`analytics.json` re-derives nothing another board owns: `p_win_pool` is plumbed
straight from `projection.json`, ranks/floors/ceilings from `standings.json`,
week-over-week movement from `timeline.json`. It reads no cache and runs no
model, so it cannot disagree with the boards next to it.

### Board separation is part of the contract

Every module carries an explicit `board` field:

| value | meaning | renderer obligation |
|-------|---------|---------------------|
| `"exact"` | derived from banked results and the floor/ceiling envelope; reproducible by hand off `standings.json` | none — present as fact |
| `"projection"` | derived from the Poisson-binomial / shared-draw model | **must** carry a projection label |

No module ships without a `board`, and no module mixes both kinds of number
inside itself without per-field marking. Today exactly one module
(`championship_odds`) is `"projection"`; every other module is `"exact"`.
`scripts/test_output_shape.py` asserts both the presence and the validity of
every `board` field.

### Top level

```json
{
  "meta": { "...": "meta block (no draft_status, no ratings keys)" },
  "race":               { "board": "exact",      "...": "" },
  "championship_odds":  { "board": "projection", "...": "" },
  "best_worst":         { "board": "exact",      "...": "" },
  "paths":              { "board": "exact",      "...": "" },
  "portfolio":          { "board": "exact",      "...": "" },
  "leverage": null
}
```

`leverage` ("games that matter") is **reserved and pinned to `null`**. It is the
only genuinely new arithmetic on this page and it lands in its own commit against
this contract. The key exists now so the shape is stable: the renderer branches
on `leverage === null`, never on whether the key is present.

### `race` — board: exact

```json
{
  "board": "exact",
  "prior_week": 6,
  "leader_id": "blaine",
  "managers": [
    {
      "manager_id": "blaine", "display_name": "Blaine",
      "rank": 1, "banked_total": 9.0, "floor": 9.0, "ceiling": 9.0,
      "gap_to_leader": 0.0, "ceiling_remaining": 0.0, "week_move": 3
    }
  ]
}
```
- Ordered by `rank` (standings order).
- `gap_to_leader` = leader's `banked_total` minus mine. Always `>= 0`; `0` for
  the leader.
- `ceiling_remaining` = `ceiling` minus `banked_total` — the points still
  physically on the table. `0.0` once every pick is settled.
- `week_move` = **signed places gained** (`prior_rank - rank`): `+2` climbed two
  spots, `-1` slipped one, `0` held. `null` — never `0` — when there is no prior
  snapshot or the manager did not appear in it.
- `prior_week` = the `as_of_week` of the snapshot `week_move` was measured
  against, or `null`. That comparison snapshot is the most recent one **strictly
  before** the effective week being scored: `run_groups.py` appends the current
  week's snapshot before analytics runs, so "the latest snapshot" would be this
  week and every move would read `0`.

  **`null` has three distinct causes, and they are not interchangeable.**
  `analytics.select_prior` returns a reason with every refusal so the run log
  can say which one it was; the emitted JSON carries only the `null`, so a
  consumer cannot tell them apart and must not try:

  | cause | reason token | ordinary or fault |
  |-------|--------------|-------------------|
  | **No prior snapshot.** Either the group has no `timeline.json` at all, or it has one but holds no snapshot strictly before the effective week (this is the season's first scored week). | `no_timeline` / `no_earlier_week` | ordinary |
  | **Unresolvable week.** Neither `--as-of-week` nor the cache supplies an effective week, so there is no "now" and therefore no "before". Refusing is deliberate: treating `None` as a wildcard made every snapshot eligible and `max()` handed back the highest week on file — for the 2026 preseason, a week-16 snapshot left by the 2025 replay. | `week_unresolved` | ordinary **in preseason**, a real fault mid-season — `any_games_banked()` on the standings is the discriminator, and the fault case logs a `::warning::` |
  | **Declared-season mismatch.** `timeline.json` declares a `season` and it is not the one being scored. The file is append-only across seasons and its snapshots are keyed by week **alone**, so last season's week 6 sits in the same list as this season's; without this check a plain strictly-before test would reach back across the rollover once this season passes that week. A timeline that declares **no** season does not trip this — the week test then stands alone, because inventing a season here would be a guarantee the data does not make. | `season_mismatch` | ordinary after a rollover, and logs a `::warning::` naming both seasons |

  The rule underneath all three is the same one that rejects a confident `0`:
  an honest `null` beats a confidently wrong number. `championship_odds`
  carries its own `prior_week` under the same rules (plus its own two extra
  null cases for `week_move` — see below).
- Prior ranks are recomputed from that snapshot's per-pick `banked_delta` /
  `floor` using **exactly** `standings.json`'s ordering (`banked_total` desc,
  `floor` desc, `manager_id` asc) — the snapshot carries no `rank` of its own.

### `championship_odds` — board: **projection**

```json
{
  "board": "projection",
  "prior_week": 6,
  "available": true,
  "managers": [
    { "manager_id": "blaine", "display_name": "Blaine",
      "p_win_pool": 1.0, "week_move": 0.267733 }
  ]
}
```
- **Sorted by `p_win_pool` desc** (nulls last, then `manager_id`) — its own
  ranking, not standings order. Sorting in JS would be computation.
- `p_win_pool` is **plumbed verbatim** from `projection.json`, never recomputed.
  The pool sim uses shared per-team draws (ARCHITECTURE §3); recomputing it off
  the per-pick distributions would drop that anti-correlation and print a
  different, wronger number under the same label.
- `week_move` here is a **change in probability** (`p_win_pool` minus the prior
  snapshot's), signed, 6 decimals — *not* a change in places. `race` owns places.
  `null` when the projector degraded, when there is no prior snapshot, or when
  either side's `p_win_pool` is missing.
- `available` is `false` when the projector degraded this run: every
  `p_win_pool` is `null`, and the page should say the projection is unavailable
  rather than render a column of blanks.

### `best_worst` — board: exact

```json
{
  "board": "exact",
  "steal": [ { "manager_id": "blaine", "display_name": "Blaine",
               "team": "Ohio State", "conference": "Big Ten",
               "line": 9.5, "direction": "O", "delta": 2.5 } ],
  "bust":  [ { "...": "same shape" } ],
  "managers": [
    { "manager_id": "blaine", "display_name": "Blaine",
      "mvp": [ { "...": "same shape" } ],
      "anchor": [ { "...": "same shape" } ] }
  ]
}
```
- `steal` = the group-wide largest **positive** `banked_delta`; `bust` = the
  largest **negative**. Sign-gated: a group where nobody is above their line has
  no steal and emits `[]`.
- `mvp` / `anchor` = that manager's own best and worst pick. **Relative, not
  sign-gated** — a manager's best pick is still their best pick when it is under
  water. A one-pick manager is their own mvp *and* anchor.
- **Ties emit every tied entry.** All four fields are arrays, always, even for a
  single winner. The renderer must handle `length > 1` and `length == 0`.
- `delta` is the pick's `banked_delta` from `standings.json`, unchanged.

### `paths` — board: exact

```json
{
  "board": "exact",
  "leader_id": "blaine",
  "managers": [
    {
      "manager_id": "chris", "display_name": "Chris",
      "state": "eliminated",
      "comparison": {
        "basis": "ceiling_vs_highest_floor",
        "my_field": "ceiling", "my_value": -8.0,
        "operator": "<",
        "other_manager_id": "blaine", "other_display_name": "Blaine",
        "other_field": "floor", "other_value": 9.0
      }
    }
  ]
}
```
Derived **entirely** from the floor/ceiling envelope already in
`standings.json` — no new math, nothing the reader cannot check by eye.
Evaluated in this order, and **the order is part of the contract**:

| # | `state` | rule | `basis` |
|---|---------|------|---------|
| 1 | `eliminated` | my `ceiling` < some manager's `floor` (binding = the highest such floor) | `ceiling_vs_highest_floor` |
| 2 | `clinched` | my `floor` > every other manager's `ceiling` | `floor_vs_highest_ceiling` |
| 3 | `controls_destiny` | my `ceiling` > every other `floor` **and** I currently lead | `ceiling_vs_highest_floor` |
| 4 | `needs_help` | otherwise — my `ceiling` can still reach the leader's `floor`, but I cannot get there alone | `ceiling_vs_leader_floor` |

- `eliminated` is tested **first** because it is the only strictly-dominating
  fact: if someone's guaranteed minimum already exceeds my maximum, no later rule
  should be able to paint that as alive.
- `needs_help` is the closed-set fallback and reports `>=` where the four literal
  rules use `>`. That `>=` is what keeps the state set closed: at
  `ceiling == leader_floor` I am not eliminated (a tie is still reachable) but I
  cannot win outright alone. Without it, a group tied at zero (pre-draft, dummy
  picks, a group with no scored weeks) would fall through all four rules into no
  state at all.
- A sole manager is `clinched` with `basis: "sole_manager"` and every `other_*`
  field `null` — vacuously true, said explicitly rather than compared against
  nobody.
- **Week 1 puts everyone in `controls_destiny` / `needs_help`. That is correct**
  and the page says so; it is not special-cased.
- `comparison` is emitted as **operands, not prose**, so the renderer shows the
  reasoning ("your ceiling 4.5 is below Blaine's floor 9.0") rather than an
  unexplained label.

### `portfolio` — board: exact

```json
{
  "board": "exact",
  "managers": [
    {
      "manager_id": "blaine", "display_name": "Blaine",
      "banked_total": 9.0, "absolute_total": 9.0,
      "picks": [
        {
          "team": "Ohio State", "conference": "Big Ten",
          "line": 9.5, "direction": "O",
          "banked_delta": 2.5, "floor": 2.5, "ceiling": 2.5,
          "status": "CLINCHED", "games_remaining": 0,
          "share_of_delta": 0.277778
        }
      ]
    }
  ]
}
```
- Every pick the manager holds, in `standings.json` order; the per-pick fields
  are the Board-1 values unchanged.
- `absolute_total` = the sum of `|banked_delta|` over the manager's picks — the
  total swing their portfolio has actually produced, regardless of direction.
- `share_of_delta` = `|banked_delta| / absolute_total` — the **concentration
  number**: how much of everything that has happened to this manager is this one
  team. Absolute on both sides on purpose, so a `-6.0` anchor is 60% of a
  portfolio that also holds `+4.0`, and a manager's shares sum to `1.0`. A signed
  numerator over a signed total would blow up near a net-zero manager and print
  shares above 100%.
- `share_of_delta` is **`null`** — never `0`, never a divide error — when
  `absolute_total == 0`. That is exactly the pre-draft / dummy / nothing-played
  case, where the honest answer is "unknown", not "0% of nothing".

### `meta`

The shared meta block (`group_id`, `season`, `as_of_week`, `generated_at`,
`cache_fetched_at`) with **no** extra keys — no `draft_status` (that stays on
`standings.json`, the board the banner reads) and no `ratings_*` (analytics runs
no model of its own, so the model's provenance stays on `projection.json`).
The §6 season single-source guard applies: if `season.json` disagrees with the
cache's season tag, the run fails loud and no file is emitted.

### Degrade behavior

Board 3 is a pure reshape, so a failure here means a bug in the reshaping, never
bad data. Like the projector it logs a `::warning::` and continues —
`standings.json` still ships and the run stays green-but-degraded rather than
dark (CLAUDE.md rule 3). A group with `draft_status: "dummy"`, no scored weeks,
or no timeline at all emits every module with honest nulls rather than crashing.
