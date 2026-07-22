# CFB Fantasy Platform

Multi-group college football fantasy platform: season-long win-totals pool with
two scoring boards (exact standings + a labeled SP+ projection), automated
weekly updates via GitHub Actions. See `ARCHITECTURE.md` for the full design and
`docs/output-contract.md` for the locked output schemas.

## Architecture

One codebase, N groups. The data fetch is shared (same CFB games + SP+ ratings,
one cache); scoring/projection run **per group** off that one cache, keyed by
`group_id`. Two boards (ARCHITECTURE §3):

- **Board 1 — standings** (`scoring.py`): pure, reproducible-by-hand arithmetic —
  banked delta in each pick's O/U direction + a floor/ceiling envelope + a
  CLINCHED/DEAD/LIVE status. The credibility spine.
- **Board 2 — projection** (`projector.py`): per-game win probability from the
  SP+ differential + home field → exact Poisson-binomial per pick → shared-draw
  Monte-Carlo pool odds. Clearly labeled a projection.

```
fetch_results.py (shared: results + SP+ -> data/cfbd_cache.json)
        │
run_groups.py  ──loop groups──▶  validate ─▶ score ─▶ project ─▶ timeline
        │                          (§9)     (Board 1)  (Board 2)  (append-only)
        ▼
site/data/<group_id>/{standings,projection,timeline}.json   ← the only write target
```

## Groups

Slugs are load-bearing (output path + URL); `display_name` is cosmetic. Roster
sizes vary per group and are set at the draft.

| Group | slug | Config |
|-------|------|--------|
| The Panel   | `panel`  | `groups/panel/config.json`  |
| Family League | `family` | `groups/family/config.json` |
| Church League | `church` | `groups/church/config.json` |

Each `config.json` carries `season`, `count_conference_championship`, the
`managers` roster (`{manager_id, display_name, email}` — `manager_id` is the
stable, never-displayed join key), and the draft rules `picks_per_manager` /
`min_distinct_conferences` (`null` = unenforced).

## Running the engine

```bash
# whole pipeline for every group, off the shared cache (live current week)
python scripts/run_groups.py --group all

# replay a past week (2025 completed-season fixture) — this is how a finished
# season becomes a live-season test: games after week N are treated as unplayed
python scripts/run_groups.py --group all --as-of-week 6

# the realistic pre-draft fixture (data/test_picks.json) as a synthetic group
python scripts/run_groups.py --test --as-of-week 6

# individual boards (both accept --group / --test / --as-of-week)
python scripts/scoring.py   --test --as-of-week 6
python scripts/projector.py --test --as-of-week 6

# fetch first, then reshape (the workflow keeps fetch as its own gated step)
python scripts/run_groups.py --group all --fetch
```

## Tests (all part of the suite)

```bash
python scripts/test_resolver.py               # name resolver + ambiguity guard (§9)
python scripts/test_cache_access.py           # AST guards: cache I/O + raw banked-index ownership
python scripts/validate_team_names.py         # fetch->score name + conference gate
python scripts/test_output_shape.py           # every emitted file vs docs/output-contract.md
python scripts/test_projector_correlation.py  # shared-draw pool odds (anti-correlation)
python scripts/selftest_10_1.py --season 2025 # fetch/cache/season-guard deliverables
```

## Setup

1. **CFBD key:** free key from collegefootballdata.com → `.env` as `CFB_API_KEY`
   (or the `CFB_API_KEY` GitHub secret).
2. **Fetch the cache:** `python scripts/fetch_results.py --season 2025`
   (flip to 2026, and the group configs' `season`, once that season is live).
3. **Draft:** fill each group's `picks.json` with canonical team names
   (`{manager, team, line, direction, conference}`); set `picks_per_manager` /
   `min_distinct_conferences` per group.
4. **Run:** `python scripts/run_groups.py --group all`.

The weekly workflow (`.github/workflows/update-data.yml`) is **disarmed** (cron
commented out); Zach re-arms it after a green manual `workflow_dispatch` run.
