# CF Challenge — build principles

College Football win-totals fantasy scoring engine. Season-long over/under pool,
multi-group, one codebase. **`ARCHITECTURE.md` (repo root) is the canonical spec.**
This file is how we build.

## Core principles (non-negotiable)

- **Config-driven, nothing hardcoded.** Every input lives in JSON — group
  `config.json` / `picks.json`, `data/cfbd_cache.json`, `data/team_aliases.json`.
  Scripts read config and write JSON; no team names, lines, or owners baked into
  code. This is what makes multi-tenancy a clean loop, not four forks (§5, §8).
- **LLMs never touch scoring.** Python does all scoring deterministically; LLMs
  handle narrative only. Every number a manager sees must be reproducible
  arithmetic. The pundit narrates trends; it never sets them (§2).
- **Two boards, never blurred.** Board 1 (standings) is exact arithmetic —
  banked deltas + floor/ceiling envelope. Board 2 (projected finish) is a
  clearly LABELED, ratings-driven projection. A projection never leaks into the
  exact board (§3).
- **The line is frozen at draft.** Static config, entered once — never scraped or
  refreshed. The live pipeline only ever tallies wins (§1, §4).

## Schema (locked)

- Pick record: `{ manager, team, line, direction, conference }`.
  `direction` is `"O"` (over) or `"U"` (under) — canonical **everywhere**. No
  `side`, no `over`/`under` strings.
- `group_id` inside each `groups/<id>/config.json` MUST equal its directory name.

## Data & cost discipline

- Single vendor: CollegeFootballData (CFBD), free tier — **1,000 API calls/month**.
  Fetch broad, cache local to `data/cfbd_cache.json`, compute off the cache. Every
  group scores the same shared cache (§4).
- The weekly pull MUST refresh **SP+ ratings, not just scores** — that refresh IS
  the projection's auto-reseed, and skipping it is the one silent failure mode (§6).
- Fetch failures are non-events: score off the last good cache (commentary-bypass).

## Workflow

- **Read-only diagnostics before fixes.** Investigate and confirm the cause with
  read-only commands before authorizing any change (§11).
- **Deterministic first.** Build the exact board before the projection; the pundit
  is garnish and comes last (build order §10).
- **Definition of done = committed AND pushed.** Not "works on disk" — pushed.
