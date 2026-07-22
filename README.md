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

Each `config.json` carries `count_conference_championship`, the `managers`
roster (`{manager_id, display_name, email}` — `manager_id` is the stable,
never-displayed join key), and the draft rules `picks_per_manager` /
`min_distinct_conferences` (`null` = unenforced). **Season is not here** — it
lives once in top-level `season.json` (`{season, cfbd_default_season}`, both
ints), read by every script; the §6 guard asserts it matches the cache.

## Replacing the dummy data (real draft)

All three groups currently ship **engineered dummy picks** so the site renders a
full board and commentary has real state to narrate. Each `picks.json` is tagged
`"draft_status": "dummy"`, which makes the site show the amber **sample-data**
banner. Swapping in a real draft is one clean, per-group operation — **no code or
frontend change**:

For each group `groups/<group>/picks.json`:

1. **Overwrite** the `picks` array with the real drafted picks
   (`{manager, team, line, direction, conference}` — canonical team names only,
   EXACTLY 4 per manager across 4 distinct conferences).
2. **Flip** the top-level `"draft_status"` from `"dummy"` to `"final"`. This is
   the single switch that removes the sample-data banner.
3. **Adjust `config.json` only if the roster changed** — e.g. Family adding its
   two extra managers is a `managers` edit here plus their picks above, never a
   rebuild. `manager_id`s must match between the two files.
4. **Re-run** `python scripts/run_groups.py --group all`. This regenerates
   `docs/data/<group>/{standings,projection,timeline}.json` — those are **outputs**,
   never hand-edited; the dummy boards are overwritten in place.

**Keep untouched:** the engine (`scripts/`), the frontend (`docs/*.js|css|html`),
`season.json`, the shared cache (`data/cfbd_cache.json`), `teams_canonical.json`,
and the alias/ambiguous maps. **Optional:** delete a group's
`docs/data/<group>/timeline.json` if you want its snapshot history to start clean
from the real draft rather than carry the dummy-era weeks (it is append-only, so
old snapshots otherwise remain as history).

The name/conference/draft-rule/opposite-side gate
(`scripts/validate_team_names.py`) runs on the real picks exactly as it did on the
dummies, so a bad real draft (unknown team, wrong conference, same team on the
same side for two managers) fails the run loudly instead of scoring silently.

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
python scripts/validate_team_names.py         # fetch->score name + conference + draft-rule gate
python scripts/test_pick_rules.py             # draft rules: exactly 4 picks / 4 distinct conferences
python scripts/test_output_shape.py           # every emitted file vs docs/output-contract.md
python scripts/test_projector_correlation.py  # shared-draw pool odds (anti-correlation)
python scripts/selftest_10_1.py               # fetch/cache/season-guard deliverables
python scripts/calibrate.py                   # offseason: backtest SP+/FPI win-prob scaling
```

## Setup

1. **CFBD key:** free key from collegefootballdata.com → `.env` as `CFB_API_KEY`
   (or the `CFB_API_KEY` GitHub secret).
2. **Set the season:** edit top-level `season.json` (`{season, cfbd_default_season}`).
   **Fetch the cache:** `python scripts/fetch_results.py` (defaults to it).
3. **Draft:** fill each group's `picks.json` with canonical team names
   (`{manager, team, line, direction, conference}`); set `picks_per_manager` /
   `min_distinct_conferences` per group.
4. **Run:** `python scripts/run_groups.py --group all`.

The weekly workflow (`.github/workflows/update-data.yml`) is **disarmed** (cron
commented out); Zach re-arms it after a green manual `workflow_dispatch` run.

## Going live (re-arm checklist)

Ordered. Do these in sequence when the 2026 season is about to start — written
down so it isn't reconstructed from memory in August. Each step gates the next.

1. **Add the API-key secret.** Repo → Settings → Secrets → Actions: add
   `CFB_API_KEY` (a secret named `CFBD_API_KEY` also works — the workflow reads
   either). Without it the fetch writes nothing and the run degrades.
2. **Wait for CFBD 2026 data.** Don't flip until collegefootballdata.com actually
   serves 2026 games/lines + SP+ ratings. Flipping early scores an empty season.
3. **Flip `season.json` — both keys, together.** Set `season` **and**
   `cfbd_default_season` to `2026` in top-level `season.json`. These are the only
   season levers; **no year is hardcoded anywhere else** — the workflow fetch has
   no `--season` literal, so it follows `cfbd_default_season` automatically. Both
   must move as a pair: `season` is what groups score, `cfbd_default_season` is
   what the fetch pulls, and the §6 guard (`assert_season_matches_cache`) fails
   loudly if the scored season and the cache's season tag disagree.
4. **Dispatch manually.** Actions → *Weekly Data Update* → *Run workflow*
   (`workflow_dispatch`). Leave the cron commented for now.
5. **Verify a live fetch + a real archive snapshot.** In the run summary confirm
   **✅ Fetch OK** (not the degraded ⚠️ banner), and that the data commit contains
   a fresh `data/ratings_archive/2026/<YYYY-MM-DD>.json` snapshot with
   `"season": 2026` — proof the live 2026 fetch landed and the vintage archive is
   capturing it. Spot-check the live site shows Season 2026 with **no** replay
   banner.
6. **Uncomment the cron.** In `update-data.yml` re-enable the `schedule:` block
   (Sunday 11 PM ET / Monday 03:00 UTC). Commit. The season is now armed.
