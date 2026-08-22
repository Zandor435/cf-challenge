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
docs/data/<group_id>/{standings,projection,timeline}.json   ← the only write target
```

## Groups

Slugs are load-bearing (output path + URL); `display_name` is cosmetic. Roster
sizes vary per group and are set at the draft.

| Group | slug | Config |
|-------|------|--------|
| The Panel   | `panel`  | `groups/panel/config.json`  |
| Family League | `family` | `groups/family/config.json` |
| CEC (church) | `church` | `groups/church/config.json` |
| The Browns  | `browns` | `groups/browns/config.json` |

Each `config.json` carries `count_conference_championship`, the `managers`
roster (`{manager_id, display_name, email}` — `manager_id` is the stable,
never-displayed join key), and the draft rules `picks_per_manager` /
`min_distinct_conferences` — both required and always enforced (a missing key
fails the gate; there is no unenforced path). An optional
`conference_minimum_waivers` list exempts named managers from the conference
minimum only, and every applied waiver is printed on every run. **Season is not
here** — it lives once in top-level `season.json` (`{season,
cfbd_default_season}`, both ints), read by every script; the §6 guard asserts
it matches the cache.

## Replacing the dummy data (real draft)

Three groups (family, church, browns) still ship **engineered dummy picks** so
the site renders a full board and commentary has real state to narrate; panel's
real 2026 draft is entered (`"draft_status": "final"`). A dummy `picks.json` is
tagged `"draft_status": "dummy"`, which makes the site show the amber
**sample-data** banner. Swapping in a real draft is one clean, per-group
operation — **no code or frontend change**. `scripts/enter_draft.py` does steps
1–2 from a dictated paste block and runs the gate before writing anything;
by hand it is:

For each group `groups/<group>/picks.json`:

1. **Overwrite** the `picks` array with the real drafted picks
   (`{manager, team, line, direction, conference}` — canonical team names only,
   EXACTLY `picks_per_manager` per manager spanning at least
   `min_distinct_conferences` conferences, per the group's `config.json`).
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

## Tests

```bash
python -m pytest -v                           # THE suite: every scripts/test_*.py (CI runs exactly this)
python scripts/validate_team_names.py         # §9 gate on the committed picks (own CI step)
python scripts/sync_personas.py --check       # docs/ persona copy is current (own CI step)
python scripts/selftest_10_1.py               # live-cache health + bypass/season-guard paths (own CI step)
python scripts/calibrate.py                   # offseason only: backtest SP+/FPI win-prob scaling
```

Every `scripts/test_*.py` also runs standalone (`python scripts/test_x.py`)
with the same pass/fail result; `conftest.py` is what turns their check ledgers
into pytest failures. `selftest_10_1.py` is deliberately not a pytest module —
it reads the live cache, not the frozen contract fixture.

## Setup

1. **CFBD key:** free key from collegefootballdata.com → `.env` as `CFB_API_KEY`
   (or the `CFB_API_KEY` GitHub secret).
2. **Set the season:** edit top-level `season.json` (`{season, cfbd_default_season}`).
   **Fetch the cache:** `python scripts/fetch_results.py` (defaults to it).
3. **Draft:** fill each group's `picks.json` with canonical team names
   (`{manager, team, line, direction, conference}`); set `picks_per_manager` /
   `min_distinct_conferences` per group.
4. **Run:** `python scripts/run_groups.py --group all`.

The weekly workflow (`.github/workflows/update-data.yml`) is **armed**: an active
daily cron at `0 13 * * *` (13:00 UTC, ~9am ET). It is safe to leave running in
the off-season because the rule-7 week-window gate (`scripts/should_run.py`)
skips any day with no games *before* the dependency install or any API call, so
an off-cycle fire costs one checkout and nothing else. A manual
`workflow_dispatch` always bypasses that gate.

## Going live (re-arm checklist)

Ordered. Do these in sequence when the 2026 season is about to start — written
down so it isn't reconstructed from memory in August. Each step gates the next.

1. **Add the API-key secret.** Repo → Settings → Secrets → Actions: add
   `CFB_API_KEY` (a secret named `CFBD_API_KEY` also works — the workflow reads
   either). Without it the fetch writes nothing and the run degrades.
2. **Wait for CFBD 2026 data.** Don't flip until collegefootballdata.com actually
   serves 2026 games/lines + SP+ ratings. Flipping early scores an empty season.
3. **Flip `season.json` — both keys, together.** *(Done for 2026 — both keys
   read `2026`; left here as the procedure for the next season.)* Set `season`
   **and** `cfbd_default_season` to the new year in top-level `season.json`. These are the only
   season levers; **no year is hardcoded anywhere else** — the workflow fetch has
   no `--season` literal, so it follows `cfbd_default_season` automatically. Both
   must move as a pair: `season` is what groups score, `cfbd_default_season` is
   what the fetch pulls, and the §6 guard (`assert_season_matches_cache`) fails
   loudly if the scored season and the cache's season tag disagree.
4. **Dispatch manually.** Actions → *Weekly Data Update* → *Run workflow*
   (`workflow_dispatch`) — it bypasses the week-window gate, so it runs even
   before the first game week. Don't wait on the cron for this check.
5. **Verify a live fetch + a real archive snapshot.** In the run summary confirm
   **✅ Fetch OK** (not the degraded ⚠️ banner), and that the data commit contains
   a fresh `data/ratings_archive/2026/<YYYY-MM-DD>.json` snapshot with
   `"season": 2026` — proof the live 2026 fetch landed and the vintage archive is
   capturing it. Spot-check the live site shows Season 2026 with **no** replay
   banner.
6. **Confirm the cron is still live — before kickoff, not after.** There is
   nothing to re-enable by editing: the `schedule:` block is already armed at
   `0 13 * * *`. The risk is GitHub's inactivity rule — scheduled workflows are
   disabled on a repo with no pushes for 60 days, which an off-season repo hits
   easily. Check it directly: Actions → *Weekly Data Update* shows a disabled
   state with a re-enable prompt if it has been switched off. Re-enable it there.
   Any push resets the 60-day clock, so the mitigation is to push something
   before kickoff, not just to look. A `schedule`-triggered run in the log after
   the first game week then *confirms* it — that is confirmation, not the check;
   waiting on it means learning the cron was off by missing a slate. From there
   the week-window gate opens on its own — the season is armed.
