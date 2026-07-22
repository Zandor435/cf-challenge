#!/usr/bin/env python3
"""
scoring.py — Board 1: Standings, pure arithmetic (ARCHITECTURE §3, §10.2).

Job: from the shared cache, compute each pick's BANKED delta in the owner's
O/U direction (over: wins-line, under: line-wins) and the ENVELOPE — floor
(loses out) / ceiling (wins out) from games_remaining (read the actual 12/13
regular-season game count, not hardcoded — §1). Emit clinch/eliminate flags.
Sum picks -> owner total floor/ceiling. No model, no methodology risk; this
board is the credibility spine.

CACHE SEASON GUARD (§4, REQUIRED): load the cache ONLY via
utils.load_cache(season) — it raises SeasonMismatchError if the cache is tagged
for a different season than the one being scored. Never read cfbd_cache.json
directly; a stale-season cache would score a clean but entirely wrong board.
Resolve team names off the cache with utils.resolve_canonical() (exact, no
ambiguity guard) — NOT resolve_team(), which is for human-entered picks.

CONFERENCE-CHAMPIONSHIP RULE (§1, per group): CFBD returns conf-title games in
the seasonType=regular feed (tagged per-game conference_championship in the
neutral cache). Whether a title-game win settles into the season win total is
league config, not a constant — books differ. Read the group's flag with
utils.counts_conference_championship(config) and derive games_remaining/banked
wins off utils.count_scheduled_games(cache["games"], flag). Default False (§1):
title games don't count. Never hardcode 12/13 — derive from the real slate.

Status: STUB — no logic yet. Prior delta-model implementation preserved on
branch archive/old-spec-leagues (worth porting from).
"""
