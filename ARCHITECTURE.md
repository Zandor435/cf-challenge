# CF Challenge — Architecture & Handoff

> **What this is:** the portable handoff doc for building the College Football (CF) Challenge — a live, multi-group, win-totals fantasy scoring engine. It was written in the WC Challenge project (which has the full context) to be carried into the CF project (which starts with an empty memory space). It carries every locked decision so nothing has to be re-derived. Read this top to bottom before scaffolding anything.
>
> **Lineage:** CF reuses the *architecture* of WC Challenge (the completed 2026 World Cup fantasy league, repo `github.com/Zandor435/wc-challenge`, local `c:\Users\zacha\Claude Code\wc-challenge`). WC is finished — its live pipeline was retired, Devin won. CF is a fresh repo on the **work PC**. WC is reused via GitHub as reference, not copied machine-to-machine.

---

## 1. The League Model

- **Format:** season-long win-totals pool. NOT a bracket, NOT a tournament. Runs the full ~16–17 week CFB regular season.
- **Each manager drafts EXACTLY 4 college football teams, from 4 distinct conferences** (settled format, LOCKED — the only draft rule; draft help is out of scope, Zach supplies the draft). Enforced per group by `validate_team_names.py` via `picks_per_manager: 4` / `min_distinct_conferences: 4` — always on, no unenforced path.
- **Each pick is an over/under bet against that team's preseason Vegas win total.** Example: Penn State over 10.5 → they must win 11+ to be positive. Win 11 → +0.5. Win 3 → the under would have been +7.5.
- **Score per pick = signed delta in the owner's chosen direction:**
  - Over: `actual_wins − line`
  - Under: `line − actual_wins`
- **Owner's total = sum of their picks' deltas.** Highest aggregate wins.
- **Multiple groups:** 3–4 independent friend groups, each its own set of owners/picks. **One codebase serves all** (see §5).

### Scoring boundary (LOCKED — corrected 2026-07 against real 2025 CFBD data)
- **Regular season only.** Bowls and the playoff are excluded — none of those wins count toward the line.
- **The 13th game is the conference-championship game, and CFBD tags it `seasonType=regular` — not postseason.** Verified against the full 2025 slate: the only FBS teams sitting at 13 scheduled games are the **18 conference-title-game participants** (9 conferences × 2). CFBD returns those title games *inside* the `seasonType=regular` feed, so a `seasonType` filter alone does **not** exclude them. They are identified by CFBD's free-text **`notes` field** (`"<Conference> Championship"`) **combined with both teams being FBS** (`homeClassification`/`awayClassification == "fbs"`) — the FBS check is load-bearing, because FCS/D-II/D-III playoff brackets and SWAC/SIAC titles share the `notes` wording but always involve a non-FBS team. **Do not** identify them by `neutral_site` (4 of 9 are on-campus) or a week-number cap (the week shifts year to year).
- **The Hawaii exemption did not apply in 2025.** Prior drafts framed the 13th game as the Hawaii exemption (a team traveling to Hawai'i earning an extra regular-season game). The data refutes it: **Hawai'i played 12**, and all **7 teams that played at Hawai'i sat at 12** — zero teams banked a 13th game that way. If it ever recurs it's just another regular-season game the line already prices in; it is *not* the source of the 13th games we see.
- **Whether conference-championship wins count is per-group config, not a code constant.** Books differ on whether title games settle into season win totals, and Zach plays in multiple leagues against different books. The fetch layer stays **neutral** — the shared cache tags every game (`conference_championship`) and excludes nothing. Each `groups/*/config.json` carries **`count_conference_championship`** (default **false** = title games excluded). `scoring.py` and `projector.py` read it per group off the one shared cache (see §5).
- **Edge case for the engine:** read `games_remaining` off each team's *actual scheduled regular-season game count* (12 or 13, after applying the per-group flag), **never a hardcoded 12**. With the flag off every FBS team is at 12; with it on the 18 title-game participants are at 13.

### The line never moves (LOCKED)
The win-total **line is frozen at draft** — it's the bet, the fixed yardstick. It is static config, entered once, never updated. Nothing in the live pipeline scrapes or refreshes lines. This is what makes the fetch layer robust: the pipeline only ever needs to **tally wins per team**, the single most reliable datum any CFB API returns.

---

## 2. Core Design Principle (inherited from WC, non-negotiable)

**LLMs never touch scoring. Python does all scoring deterministically. LLMs handle narrative only.**

This is the credibility spine. The moment a model assigns win probabilities or "reseeds" a team, scoring becomes non-reproducible and the first losing manager blames "the AI." Every number a manager sees must be reproducible arithmetic. The pundit narrates trends; it never sets them.

---

## 3. The Scoring Engine — Two Boards

The projection problem splits into two layers. Only one is fuzzy, and even that one is deterministic (ratings-driven, not model-driven). Present them as **two visually separate boards**.

### Board 1 — Standings (always exact, pure arithmetic)
- **Banked delta:** from wins tallied so far, in the owner's O/U direction.
- **Envelope:** floor (team loses out) and ceiling (team wins out), from `games_remaining`. Converts to a floor/ceiling on each pick's delta and each owner's total. This narrows to the truth automatically every week.
- **Clinch / eliminate flags:** "this pick can no longer hit the over," etc.
- This is the WC "Max Points Remaining" tile, reborn. No model, no methodology risk. This board is your credibility.

### Board 2 — Projected Finish (labeled projection, ratings-driven)
- Each remaining game gets a **win probability from a power-rating differential + home field** (see §4 for source).
- Sum per-game win probabilities → **expected remaining wins** → projected final total → projected delta.
- Because each remaining game is an independent trial with its own probability, the number of additional wins follows a **Poisson-binomial distribution** → exact P(every possible final win count) → exact **P(this pick beats its line)**. Computed analytically by convolving `[1-p_i, p_i]` over a team's remaining games (`np.convolve`, n ≤ 13) — no Monte Carlo needed for a single pick or a single manager's own total (their picks are on distinct teams, so independent).
- **P(win the pool) MUST use shared per-team draws (LOCKED).** Pool odds are a *joint* question across managers, and managers hold **opposite sides of the same team** — so their totals are **anti-correlated**, not independent. Each Monte-Carlo trial therefore draws every team's remaining season **once** and scores *every* manager off that same draw. Convolving managers' totals as if independent (or drawing each manager's teams separately) mis-states P(win pool) by **5–7 points**. `projector.py`'s `simulate_totals()` implements the shared draw; `test_projector_correlation.py` asserts two managers on opposite sides of one team come out negatively correlated. The exposed tuning knobs (home-field points, SP+→prob scale) are named module constants, not inline magic numbers.
- This is the "who's trending / who's better than we thought" read. It updates automatically each week as ratings refresh — that IS the auto-reseeding (see §6). Clearly label it a projection.

### Why two boards protect the launch
This is Zach's first *live* (not end-of-season) scoring. The exact board is never wrong; the projection is a labeled best-guess inside a converging exact range. You're never exposed to "the engine cheated me."

---

## 4. Data Layer — CFBD (LOCKED via Perplexity deep research)

**Single vendor: CollegeFootballData (CFBD), `api.collegefootballdata.com`.** Free tier covers everything CF needs:
- **Game results + full schedules** (the wins tally — non-negotiable core).
- **Power ratings:** SP+, FPI, Elo, and a native win-probability metric.
- **Stats/standings/betting lines** (website flavor; nice-to-have).
- Free key (or Patreon key). **REST/JSON.** Official Python client `cfbd-python` exists.

**Ratings spine (LOCKED): SP+.** Have CC test whether CFBD's **built-in win-probability endpoint** is usable off-the-shelf for the projection — if yes, less math to own; if it's pregame-only/stale, compute per-game probabilities from SP+ ourselves. Either path is deterministic.
- **SP+ caveat:** partly preseason-weighted through ~September, then fully results-based. So early-season reseeding is *sluggish then sharp* — a known property, not a bug. It's a reason to show the exact banked/envelope numbers prominently (they're instant, no lag). If the projection feels too slow mid-season, **FPI** is more reactive — have CC expose both so we can A/B which *feels* right. Tuning knob, not a structural decision.

### THE constraint: 1,000 API calls / month (free tier)
This reshapes the pipeline vs. WC's "poll often" instinct. **Architecture: fetch broad, cache local, compute off cache.**
- One weekly pass pulls the full results slate + current SP+ in a handful of calls → writes `data/cfbd_cache.json`.
- **Every group scores against the cache**, not fresh API hits per team per group.
- **Four groups cost the same calls as one** — groups are different *picks* over the *same* games. This is the core payoff of one-codebase multi-tenancy.
- Twice-weekly cadence lands around ~30–50 calls/month. Enormous margin under 1,000.

### Reliability notes for CC
- CFBD v2 warns of **breaking changes** and **Cloudflare burst-blocking**. Port WC's fetch-hardening (backoff/retry) and especially the **commentary-bypass pattern** (run off last-cached data when a fetch fails) — now **mandatory**. Cache-first design makes an outage a non-event: score off the last good JSON.
- **ESPN unofficial endpoint** (`site.api.espn.com/.../college-football/scoreboard`) = documented *fallback* for live scores only if CFBD lags. Never primary; undocumented, breaks without notice.
- **Win totals stay manual.** CFBD carries betting lines, but season-long win totals via API are unreliable and the line is frozen config anyway. A "market vs. your pick" display tile is *possible* via CFBD lines but strictly cosmetic — parked.

---

## 5. Multi-Tenancy — One Codebase, N Groups (design in from day one)

Four forks = every bug fixed four times. Zach already felt shipping-discipline pain at 1×. **One repo, parameterized by `group_id`.** This is a clean loop if built now, an ugly retrofit if bolted on later.

```
groups/
  panel/                # slug is load-bearing: it is the output path + URL
    config.json         # group_id (slug), display_name, managers
                        #   [{manager_id, display_name, email}], count_conference_championship,
                        #   picks_per_manager: 4 / min_distinct_conferences: 4 (LOCKED, always enforced)
                        #   NOTE: no `season` here — season is single-source in /season.json
    picks.json          # each manager's 3–4 canonical picks: {manager, team, line, direction (O/U), conference}
  family/  ...
  church/  ...
season.json             # SINGLE SOURCE: {season, cfbd_default_season} (ints). §6 guard = one comparison vs cache.
data/
  cfbd_cache.json       # SHARED — one weekly pull, every group reads it
site/
  data/<group_id>/      # THE ONLY engine write target: standings.json, projection.json, timeline.json
```

**Output write target (LOCKED):** the engine writes the three contract files
(`standings.json`, `projection.json`, `timeline.json`) **only** to
`site/data/<group_id>/`. The old `groups/*/output/` directory is removed — there
is one write surface, defined by `docs/output-contract.md`. `manager_id` is a
stable slug, never displayed, and is the join key across every output file.

- Pipeline **loops over groups**; scoring runs N times over the one shared cache.
- **`count_conference_championship` (per-group league rule, default `false`).** Do conference-championship wins settle into a team's season win total? Sportsbooks differ, and Zach's groups may bet against different books, so this is **league config, not a code constant**. The shared cache is neutral — it tags conf-title games (`conference_championship`) but excludes nothing (§1); this flag is where each group decides. `scoring.py`/`projector.py` read it via `utils.counts_conference_championship(config)` and derive schedules with `utils.count_scheduled_games(cache["games"], flag)`. Four groups can hold four different answers over the same one cache — the payoff of shared-cache multi-tenancy.
- Frontend = **one site**: a landing page fanning out to per-group pages. Everything keyed by `group_id`.
- Flat JSON per group holds fine at this volume (4 groups × ~4 managers × 3–4 picks). **Defer the database** — the lean-architecture call stands.
- **Email:** keep Resend, one email/week, domain `mustardboy.xyz`. Per-group `email_enabled` flag + recipient list in each `config.json`. At least one group gets email; maybe all.

---

## 6. Auto-Reseeding — Already Built In (do not build a manual reseed)

Zach asked about mid-season reseeding (a team that looked like an expected win now looks like a loss). **The engine already does this automatically — do not build a separate manual reseed.** Three distinct objects, don't blur them:

1. **The line** — frozen at draft, never moves.
2. **The live projection** — reseeds *itself* weekly because SP+ refreshes weekly. A team that keeps losing sees its SP+ drop → remaining-game win probs drop → projected total falls. No one touches anything. This IS the reseeding.
3. **The preseason baseline** (§7) — frozen at Week 0 *on purpose*, as the "what we thought at the start" comparison line. Never reseed this; the gap between it and the live projection is the story.

**A manual override reintroduces the exact "AI/Zach fudged my odds" problem the deterministic design exists to kill. Don't build it.** Trust the ratings or improve the ratings source — never hand-edit outcomes.

### The one place this silently breaks
⚠️ **The weekly pull MUST refresh SP+ ratings, not just scores.** Scores alone → exact banked/envelope but a *frozen* projection that never reseeds. Make "weekly pull refreshes ratings, not just scores" an explicit line-item in CC's build. This is the single failure mode.

**Constraint check:** neither results nor ratings is expensive — both are a handful of CFBD calls, cached. The only "constraint" is conceptual (pull ratings too), not budget or availability.

---

## 7. The One-Time Preseason Baseline Pass (Zach + Claude, after CC pulls data)

A one-time manual seeding of preseason expectation — a *frozen snapshot* of "what we expected at draft," used as a **comparison line** for drift, NOT as the scoring spine.

**Sequence (order matters):**
1. Zach enters teams + win-total lines (draft config).
2. CC pulls each team's schedule + **preseason SP+** from CFBD.
3. *Then* Claude + Zach do the one-time pass: game by game, **anchored to the SP+ rating gaps** (favored by a lot → expected win; big underdog → expected loss; inside a threshold → tossup), summing to an "expected wins" baseline per team.
4. Baseline freezes into config as the draft-day expectation line.

Doing step 3 *before* CC pulls SP+ means guessing from a model's memory (which gets current CFB teams wrong). Anchoring to pulled ratings makes it grounded and defensible — built the same way the live engine works, just frozen at Week 0. Where the implied expected-win total diverges from the Vegas line is itself interesting (market vs. SP+ disagree).

---

## 8. Reuse Audit (grounded in the real WC repo inventory)

### CLONE AS-IS (proven pattern, minimal edit)
- **`scoring.py`'s config-driven architecture** — the crown jewel. All inputs JSON/CSV, nothing hardcoded, writes JSON to `site/data/`. This is exactly what makes multi-tenant a clean loop. Keep the skeleton, swap the math.
- **`validate_team_names.py`** — the fetch→score guard. More load-bearing for CF (see §9).
- **`CLAUDE.md`** build principles + "definition of done = committed AND pushed." (Note: WC's CLAUDE.md prescribes a CI consistency test and a live-pipeline workflow that **never existed on disk** — aspirational. For CF, make them real or cut them.)
- **Email machinery:** `send_email.py`, `build_email_payload.py`, `should_send.py`, `render.py`; Resend + `mustardboy.xyz`. Recipient structure changes to per-group config; send logic ports.
- **Fetch-hardening patterns:** backoff/retry, alias-mapping, and the **commentary-bypass** (run off last-cached data on fetch failure). Now mandatory.

### ADAPT (skeleton survives, content rewritten)
- **`scoring.py` math** → win-total deltas. Keep config-driven I/O; rewrite the compute.
- **`generate_commentary.py`** (1032 lines) → strip to **one pundit**. OpenAI-via-urllib call structure reuses (note: WC commentary runs on **OpenAI GPT**, key `OPENAI_API_KEY`, called via raw urllib — no SDK). `rome_column_template.md` + `pundit_roundtable.md` → one persona template. **Persona TBD** — candidates: Jim Rome, Kirk Herbstreit, Chris Berman, Scott Van Pelt. Zach will research/experiment.
- **Analytics frontend** → keep the **Geckoboard tiled-grid design language**; rebuild the tiles (delta board, per-pick O/U tracker, envelope, projected-finish, hits/busts).
- **`team_aliases.json`** (23 entries for WC) → **balloons** for CFB. Same mechanism, 5–10× the entries (see §9).

### REBUILD
- **`fetch_results.py`** (624 lines, WC feeds) → **CFBD client**. Simpler and more robust: results + SP+ in a handful of calls.
- **The pipeline workflow** — WC's live cron was *retired and doesn't exist to port*. CF's is a fresh multi-tenant build (cleaner anyway).

### DROP ENTIRELY
- **`sim/`** (616 lines) + **`win_probability.py`** (213) — bracket Monte Carlo. Replaced by the Poisson-binomial projector (fresh, smaller).
- **`pool/`** — archived WC calibrator, the "parallel truths" antipattern. Do not touch or reuse.
- Knockout / tier / upset-bonus logic in scoring.
- **~9 of 10 image generators** (`generate_portraits.py`, `generate_wwe_portraits.py`, `generate_editorial_illustrations.py`, `generate_clash_banners.py`, avatars, etc.). Almost all WC trash-talk flavor. CF is "more engine than trash talk." Maybe keep *one* hero-banner-per-group pattern later; the rest drop.
- WC-specific pipeline scripts: `resolve_knockout_schedule.py`, `player_goals.py`, `generate_predictions.py`, `build_narrative_state.py`.

### NEW BUILDS
- Win-totals config per pick: `{team, line, direction (O/U), conference}`.
- `groups/` multi-tenant scaffold (§5).
- CFBD data layer + cache (§4).
- Poisson-binomial projector (§3, Board 2).
- **`requirements.txt`** (WC has none at root): `cfbd` (or `requests`), `numpy`, `resend`, `python-dotenv`, plus OpenAI-via-urllib.

---

## 9. CFB Naming Is a Bigger Mess Than WC (raise `team_aliases` priority)

"Ole Miss"/"Mississippi", Miami (FL) vs Miami (OH), "App State", "USC" (Southern Cal vs South Carolina), etc. `team_aliases.json` + the `validate_team_names.py` gate aren't just reusable — they're **more load-bearing** here. Budget real time for the alias map, and keep the validate gate hard (exit 1 blocks the run) so a mismatched name can never silently mis-score a pick.

---

## 10. Build Order (engine first, flavor last — matches "more engine than trash talk")

1. **CFBD fetch + cache** + `requirements.txt`. (Have CC confirm current CFBD free-tier limits/endpoints and whether the native win-probability endpoint is usable.)
2. **Win-totals config + scoring math** (banked deltas + envelope). Board 1.
3. **Poisson-binomial projector** (Board 2) + the one-time SP+-anchored preseason baseline pass (§7).
4. **Multi-tenant `groups/` loop.**
5. **Frontend** — two boards, tiled grid. **Freshness stamp (LOCKED):** every board renders "data as of `<fetched_at>`, week N" from the cache, so a stale board is self-evidently stale to the owners reading it — not just to CI. Cache staleness is caught three ways: the fetch age-ceiling (fail loud > 10 days, §4), the season guard (fail loud on mismatch, §4), and this visible stamp.
6. **Weekly cron workflow** (fresh; twice-weekly — Sat-night heavy pass + one midweek).
7. **Email** — one/week, per-group.
8. **One pundit** — garnish.

---

## 11. Operating Model (how Zach runs the stack)

- **Claude Desktop (this thread / CF thread):** strategy, architecture, decisions, prompt-writing for CC, quality control. Holds the project map. Does NOT do bulk computation or live research.
- **Claude Code (CC):** all execution — parsing, scoring, simulations, structured data, and Perplexity research *via CC*. Runs in the VS Code extension chat window on Windows.
- **Perplexity (via CC):** research. Already used for the CFBD API decision.
- **GPT / OpenAI:** commentary generation (the one pundit).
- **Workflow rules that carry over:** read-only diagnostic prompts before authorizing fixes; batch piecemeal changes into one CC prompt; one CC thread per set of overlapping files; `git worktree` to isolate parallel threads; "definition of done" = committed AND pushed; sync repo before starting new threads.

### Deploy target — decide deliberately
WC ended up **Netlify-primary** (`netlify.toml`) with GitHub Pages as stopgap. For CF, pick deliberately rather than inherit. (Multi-group landing-page structure may favor one over the other — evaluate when the frontend is scaffolded.)

---

## 12. Open Items (not blocking scaffold)
- **Pundit persona** — Zach researching (Rome / Herbstreit / Berman / SVP).
- **SP+ win-prob scale — DECIDED 2026-07. `WIN_PROB_POINTS_SCALE=13.5`, `HOME_FIELD_ADVANTAGE_PTS=4.0`, JOINTLY fitted on the leak-free market bridge.** Both constants come from one fit — never mix a scale from one method with an HFA from another.
  - **Method (`scripts/calibrate_spread.py`, report `docs/calibration-report.md`, 2021–2025).** A closing spread is set before kickoff and can't encode the result, so calibrating spread→P(win) is leak-free (Path A: spread scale 8.85, stable OOS). The **market bridge** then jointly picks the projector's `(scale, HFA)` — in its exact intercept-free form `p = σ((SP+diff + HFA·home)/scale)` — to best reproduce the market's leak-free win probability, scored by cross-entropy. Fitted pair **13.55 / 3.95** (95% CI scale **[12.8, 14.4]**, HFA **[3.7, 4.3]**), adopted at 1-decimal `13.5 / 4.0`. HFA 4.0 ≈ ~2.4 true home-field + ~1.6 a nominal-home offset the intercept-free form folds into the home term.
  - **It is a LOWER bound on flatness.** The bridge uses season-FINAL SP+ (the `week` param is ignored: Indiana 2024 SP+ = 20.1 at no-week/wk3/wk10; 2025 cache==live==wk4), which is *sharper* than the live in-season SP+ the projector actually runs on. Live SP+ is noisier → worse attenuation → the true live scale is **above** 13.5. So 13.5 is the floor; if anything the live projector should be flatter still.
  - **`11.0` RETIRED.** It was inherited from the wc-challenge repo (different sport/units), never tested here, and sat **below** the fitted CI — i.e. it ran the projector overconfident, below a lower bound. That is the failure mode we set out to avoid.
  - **`7.1` REFUTED.** It was `calibrate.py`'s in-season-SP+ fit, hindsight-leaked too steep; it sits outside the leak-free bracket **7.95 ≤ true scale ≤ 17.45** (below even the leaky lower bound). Do not adopt it.
  - **Re-fit each offseason**, and permanently once the SP+ vintage archive (§ below / BUILD 2) holds enough live weeks — that supersedes the bridge with a true within-season holdout. The only other vintage-correct in-season CFBD rating is **Elo** (`/ratings/elo?year=&week=&seasonType=`, week-varying); a full spine switch SP+→Elo remains a separate, deliberate reopen, not a constant tweak. FPI A/B has the same in-season leakage limitation and is shelved.
- **Deploy target** — Netlify vs Pages, decide at frontend stage.
- **CFBD native win-probability endpoint — DECIDED (2026-07, during §10.1 research). Board 2 computes per-game win probability from the SP+ rating differential + home field; the native endpoint is rejected. Do NOT reopen in §10.3.** Reasoning: `/metrics/wp/pregame` is **spread-gated** — it returns a probability only for games with a *posted betting line*, so it cannot price games weeks out, and Board 2 needs *every remaining game from Week 1*. `/metrics/wp` is **in-game play-by-play only** (games already started). Neither yields a forward, full-season projection of unplayed games. The SP+-differential path is deterministic and defined for every scheduled game, which is what the Poisson-binomial projector requires.
- **Cross-machine ding** — set a CC `Stop` hook in user-level settings on each Windows PC for an audible/toast alert on completion (VS-Code-extension audio can be flaky; use SAPI speak or a toast, and test it fires).
