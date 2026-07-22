# Football v2 Playbook — Lessons from the Soccer Build

**Purpose:** Carry the hard-won, incident-driven lessons out of the World Cup
(soccer) project so the football build doesn't relearn them — especially the
ways external APIs and the scheduled pipeline broke in production. This file is
self-contained and meant to be **copied into the football repo** at the start.
It does not document the soccer project; it tells the next build what to do.

Two halves:
1. **Resilience rules** — each is "here's what broke, here's the rule for v2."
2. **Concrete v2 action list** — prioritized, football-specific moves.

---

## Part 1 — Resilience rules (what broke → what to do)

### 1. Assume your primary data source will not exist when you need it
**What happened:** The paid provider (API-Football) never populated the live
season's fixtures. The whole pipeline was designed around a source that returned
nothing for the event we actually cared about.

**Rule for v2:** Treat the data source as **pluggable from day one**. Define a
single normalized internal schema (`fixture_id, date, week, stage, home, away,
home_score, away_score, …`) and put every provider behind a `--source` flag that
maps *into* that schema. Scoring/derivation code reads only the normalized schema
and never knows which provider produced it. Then a dead source is a one-flag swap,
not a rewrite. Validate the new source against a saved completed season *before*
the live one starts.

### 2. Network failures are normal; give every fetch retry + backoff
**What happened:** The free feed dropped connections for hours (SSL EOF, socket
reset, DNS timeout). A single failure under `bash -e` crashed the entire cron run.

**Rule for v2:** Every outbound GET retries with exponential backoff. Keep two
*distinct* loops because they're different failure modes:
- **Transient network** (SSL/URLError/socket/timeout): retry 3× at 5s/10s/20s.
- **Rate limit (429):** wait the full per-minute window (60s+) and retry.

On a *persistent* outage, **re-raise after the last attempt** — fail loud rather
than silently scoring stale data. (See rule 3 for what "loud" should and shouldn't
take down.)

### 3. One dead step must not take the whole pipeline dark
**What happened:** A 6h+ feed outage failed the fetch step, which (with no
`continue-on-error`) failed the job and skipped *every* downstream step — including
commentary and the site deploy. The narrative went stale even though perfectly
good scored data was already committed.

**Rule for v2:** Split steps into **needs-new-data** vs **reshapes-existing-data**.
- Make the fetch `continue-on-error: true` and record its real outcome.
- Steps that consume fresh data (validate, score, win-prob) gate on
  `steps.fetch.outcome == 'success'`.
- Steps that only reshape already-committed data (commentary, narrative, deploy)
  run **regardless**, so an outage degrades gracefully instead of freezing.
- Emit a visible `::warning::` on the bypass path so a green run that skipped
  scoring still reads as "feed down, ran in degraded mode."
- Build a **no-fetch escape-hatch workflow** (manual dispatch) that regenerates
  the derived/narrative layer from committed data, so you can ship template/prompt
  fixes while the feed is down.

### 4. An unmapped name silently deletes points — fail loud instead
**What happened (the "DR Congo bug"):** The feed spelled a team a way the alias
map didn't have. Scoring silently treated it as undrafted and dropped that team's
points. No error — just wrong numbers.

**Rule for v2:** Put a **name-validation gate between fetch and score**. Every
fetched team/player must resolve through the alias map to a canonical entity in
the roster, or the run **fails and names the offender**. A silent miss is far
worse than a loud stop. Maintain `aliases.json` as the single mapping edge and
convert at ingestion only.

### 5. A zero-result fetch must never clobber real data
**What happened:** Pre-kickoff and during outages the fetch returned 0 finished
matches. Without a guard, scoring would overwrite committed standings with zeros.

**Rule for v2:** Before scoring, count finished games; if 0, **skip and leave
existing output untouched**. Generally: *overwrite by default, but guard every
regeneration against empty/partial input.* Label each output file as
overwrite-vs-accumulate in its header so no one clobbers an accumulating file
(timelines, prediction logs, narrative state) with a regenerating one.

### 6. Track your free-tier budget before it cuts you off
**What happened:** Free tier was request-capped while the pipeline ran several
times a day.

**Rule for v2:** Count every request per run, keep a **cumulative daily tally**
that resets on the UTC date rollover, and warn loudly past a threshold. For
incremental/expensive endpoints (per-game event calls), only fetch entities you
haven't processed and persist a `processed` set so a scoreless game isn't
re-queried nightly.

### 7. The scheduler fires when there's no data — gate for it
**What happened:** An every-N-hours cron mostly fires on days with no games;
pre-season fires before any real data exists.

**Rule for v2:** Layer cheap gates so off-cycle runs cost nothing and can't
corrupt baselines:
- **Game-window gate:** skip the whole run unless today/yesterday (or the current
  week) actually has a game on the schedule. Manual dispatch always runs.
- **Kickoff/season date gate:** a self-removing date check that preserves the
  pre-season baseline until the season starts, then runs with no edit needed.
- **`--skip-if-empty`** on any step that accumulates.
- **`--skip-if-exists`** on anything paid (image/LLM generation) so each artifact
  is paid for at most once.

### 8. Platform/runtime migrations will hit you mid-season
**What happened:** GitHub forced JS actions onto a new Node runtime mid-tournament.

**Rule for v2:** Pin action versions, add the forward-compat shim env var when the
platform announces a migration, and don't depend on defaults that can change under
you during the live window.

### 9. Bot pushes don't trigger other workflows (anti-recursion)
**What happened:** Commits pushed with the default `GITHUB_TOKEN` do **not**
trigger `on: push` workflows, so the Pages deploy never fired on cron commits and
the site drifted stale between manual pushes.

**Rule for v2:** Deploy **from inside** the workflow that commits, gated on the
update job. Check out the branch HEAD (the commit you just pushed), not the event
SHA. Share a `concurrency` group between the cron deploy and any manual-push deploy
so they can't race the single environment.

### 10. Messy upstream strings: parse best-effort, never crash
**What happened:** Scorer blobs mixed straight/curly quotes and `"null"`
literals. Strict parsing would have thrown on real data.

**Rule for v2:** For enrichment data (scorers, stats), parse defensively, skip
tokens you can't read, and keep it `continue-on-error`. Enrichment quality should
never block the core standings commit.

### 11. Keep the README in lockstep with the live system
**What happened:** The README still described the *original* design (paid API as
primary, Netlify) long after the system migrated to a free feed + GitHub Pages.

**Rule for v2:** When you swap a source or host, update the top-of-README data-flow
diagram in the same PR. Stale architecture docs cost the next person (or you, in
six months) real debugging time.

---

## Part 2 — Concrete v2 (football) action list

Football specifics that change the design vs. soccer: a **fixed weekly schedule**
(not a 4-week tournament), **bye weeks**, a **long regular season + playoffs**,
**richer per-player stats** (the scoring surface is much bigger), and **more
mature free data sources** (ESPN's hidden JSON endpoints, nfl APIs, api-sports
American-Football). Prioritized:

**P0 — do before writing scoring logic**
1. **Normalized schema + `--source` abstraction first.** Lock the internal fixture
   schema, then write *two* source adapters from the start (e.g. ESPN + one
   backup) — proves the abstraction is real, not aspirational, and gives instant
   failover. Validate both against a **completed prior season** fixture set.
2. **Schema contract test in CI.** A fixed fixture set must produce byte-identical
   scoring output regardless of source adapter. This is the single check that
   catches source/engine drift the moment it happens.
3. **One engine, two entry points.** If there's an offline calibrator/projector and
   a live runner, they import the *same* engine module — never vendor a fork. The
   calibrator's tuned config *is* the live runner's input.

**P1 — pipeline resilience (port rules 2–9 directly)**
4. Two-loop retry/backoff on every fetch; re-raise on persistent failure.
5. `continue-on-error` fetch + needs-new-data vs reshapes-existing-data step split;
   `::warning::` on the degraded path.
6. No-fetch escape-hatch workflow for regenerating the derived/narrative layer.
7. Name/entity validation gate between fetch and score (teams *and* players —
   football's player scoring makes player-name drift a real risk).
8. Zero/partial-result clobber guard before any overwrite.
9. **Week-window gate** (replaces soccer's rest-day gate): skip unless the current
   week has games; handle **bye weeks** explicitly. Season date gate for the
   off-season. `--skip-if-empty` / `--skip-if-exists` everywhere.
10. Per-run + cumulative-daily API budget logging with a threshold warning.
11. Deploy from inside the committing workflow; pin action versions; add runtime
    shims proactively.

**P2 — data model & content**
12. **Static site renders JSON, computes nothing.** All logic in Python emitting
    JSON; the site only displays. Keep it portable across hosts (don't hard-couple
    to one PaaS — the soccer build migrated hosts mid-flight).
13. **One canonical data format** (pick JSON or CSV), convert only at ingestion.
    No parallel `x.csv` + `x.json` truths.
14. **Test fixtures in a gitignored folder behind a `--test` flag** — never commit
    fixtures into the files production reads (a 5am cron firing early will score
    fake data into the real baseline).
15. **Invest in the narrative-state builder before the prose layer.** Commentary is
    only as good as the structured state it reads (streaks, head-to-heads, notable
    events, themes). Football has far richer state to mine (player stats, rivalry
    weeks, playoff scenarios) — build that state first.
16. **Stateful content = one pinned voice; stateless one-offs = rotation.** A
    running column that builds on itself reads wrong if the persona changes between
    installments.

**P3 — process**
17. **Lock page layout and nav order before building pages; build mobile nav from
    the start.** Retrofitting structure is expensive.
18. **One thread per set of overlapping files; never two threads in one working
    tree.** Use separate git worktrees if parallelizing. Sync the repo before every
    build and name the "do not touch" files explicitly in each prompt.
19. **Definition of Done = committed, pushed, and confirmed on the remote** (and the
    deploy succeeded if the change is user-facing). Local-only work isn't done.
20. **Full pipeline smoke test before launch:** bypass the gates, trigger real API
    calls, confirm fetch → validate → score → derive → render end-to-end produces
    what the site expects. Don't discover a broken step the day real data arrives.

---

*Source project: World Cup 2026 fantasy soccer pipeline. Incidents referenced are
real production failures from that build (paid-API no-show, multi-hour feed
outages, the unmapped-team points bug, forced runtime migration, anti-recursion
deploy gap).*
