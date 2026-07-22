#!/usr/bin/env python3
"""
projector.py — Board 2: Projected Finish, ratings-driven (ARCHITECTURE §3, §10.3).

Job: give each remaining game a win probability from the SP+ power-rating
differential + home field. Treat remaining games as independent trials ->
Poisson-binomial distribution over additional wins -> exact P(this pick beats
its line). Convolve a manager's picks -> projected-total distribution ->
P(win the pool). Deterministic, but clearly LABELED a projection. This is the
auto-reseeding surface: it moves only because SP+ refreshes weekly (§6).

Replaces WC's dropped sim/ + win_probability.py bracket Monte Carlo (§8 DROP).

CACHE SEASON GUARD (§4, REQUIRED): load the cache ONLY via
utils.load_cache(season) — it raises SeasonMismatchError if the cache is tagged
for a different season than the one being projected. Never read cfbd_cache.json
directly; a stale-season cache would project a clean but entirely wrong board.
Resolve team names off the cache with utils.resolve_canonical() (exact, no
ambiguity guard) — NOT resolve_team(), which is for human-entered picks.

CONFERENCE-CHAMPIONSHIP RULE (§1, per group): the set of remaining games depends
on whether conf-title games count for this league. Read the group's flag with
utils.counts_conference_championship(config) and build the remaining-game slate
off utils.count_scheduled_games(cache["games"], flag) — same shared cache, same
per-game conference_championship tag scoring.py uses. Default False (§1).

Status: STUB — no logic yet.
"""
