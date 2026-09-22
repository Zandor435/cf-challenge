# Pace scoring audit and migration — September 22, 2026

## What existed

| Concern | Audit finding |
| --- | --- |
| Original lines and selections | `groups/<group>/picks.json` stores manager, canonical team, `O`/`U`, conference, and the original line. Draft validation checks the frozen season win-total reference. |
| Projection | `projector.build_projection` already calculated actual wins plus expected remaining wins, then signed that difference against the line. No replacement model was needed. |
| Probabilities | Existing SP+ differential, home-field adjustment, logistic scale, and missing-rating fallback. Completed and remaining games come from `utils.team_state`. |
| Refresh | Existing automated data refreshes reproject the remaining schedule with the current ratings. Ratings archives, draft baselines, and old results remain intact. |
| History | Weekly timelines retain pick projections and title odds, with same-week replacement. Git history additionally retains previously published per-game projections. These are observations, not permission to reconstruct old probabilities from current ratings. |
| Ranking | Exact standings ranked banked delta, floor, and manager ID; projection output ranked title odds; homepage portfolios independently ranked projected total. |
| Old pace | A separate actual-versus-current-model diagnostic for completed games. This is not the new pick-relative pace and is no longer displayed. The legacy JSON field retains its original meaning. |
| Precision | Pick deltas were rounded before aggregation. Largest-remainder display adjustments made displayed picks sum by moving individual values. Both behaviors were replaced. |
| Classifications | Previously 35%/65%; now strictly below 40% / inclusive 40–60% / strictly above 60%. Classification does not score games. |

## Consumer migration

| Surface | Current behavior |
| --- | --- |
| Homepage / standings | One canonical pace rank and manager total, with four pick contributions. Each pick opens its own schedule. |
| Manager profiles | Same pace ranks and values, including preseason; team explanations share the homepage renderer. |
| Team drilldown | Record, original line, projected final wins, pick pace, and the full schedule. Completed matchup/probability text is struck through; W/L/T remains legible and pregame shading remains colored. |
| Scenario planner | The existing generated title routes are retained; there was no interactive result editor to migrate. Manager summaries lead with pace, while conditional title odds remain in expanded explanations. |
| Games That Matter | Existing shared-outcome simulations remain. Explanations also show exact conditional pace with one game's expectation replaced by its result, respecting both picked sides of a matchup. |
| Digest / editorial | Race totals, rank movement, leader changes, best/worst contributions, prompt guidance, and email standings use pace. Banked results and statuses remain explicit factual evidence. Archived columns are not rewritten. |

## Canonical contract

`pace.py` owns projection arithmetic, Over/Under sign, full-precision aggregation,
and ranking. Existing field names remain: `expected_final_wins`, `expected_delta`,
and `expected_total`. `scoring.py` joins these onto standings without overwriting
the old result-only fields. Higher unrounded pace wins; exact ties retain floor
descending, then manager ID ascending. Simulation title ties still split odds,
as before; this is separate from deterministic display ordering.

Every numerical display uses an independently rounded tenth, suppressing negative
zero. Rounded pick values may differ slightly from the rounded total; no pick is
adjusted to force an apparent sum. Remaining-game probabilities retain precision.
At season end an empty remaining schedule means projected wins equal actual wins.

`pregame-<season>.json` stores the latest published forecast strictly before
kickoff for each team/fixture. Later forecasts cannot overwrite completed-game
expectations. The one-time `backfill_pregame.py` migration recovers actual
published probabilities from Git only for unique exact schedule matches and
valid pre-kickoff timestamps, storing commit provenance. It never reruns ratings.
Unknown historical expectations remain null and use neutral shading.

## Failures and boundaries

- A pace refresh publishes standings and projection with the same generation timestamp. Standings embeds its own explanations so it can render coherently by itself.
- On calculation failure, retain the last matching season/roster generation and mark it stale; if none exists, scores and ranks are unavailable. Stale refreshes never become new timeline snapshots or new editorial packets.
- Optional title simulation/context failures do not block pace. Historical rounded values remain historical; no missing precision is fabricated.
- Missing ratings keep the established fallback and mark estimated probabilities. Invalid probabilities or completed games missing final scores reject the refresh rather than quietly count a game as zero or project a completed result.
- Explicit cancellations leave the eligible slate; postponements remain. Fixture IDs deduplicate provider corrections. Byes add nothing; completed ties add no wins and no remaining probability. Championship eligibility and replay rules are unchanged.
- The provider's existing normalized feed did not expose a reliable cancellation signal. The adapter now preserves explicit cancellation/status fields when supplied; a game merely marked incomplete cannot safely be inferred canceled.
- Generated artifacts use the committed cache. No live fetch, deployment, email send, model retuning, or historical-column regeneration is part of this migration.
