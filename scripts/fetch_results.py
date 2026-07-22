#!/usr/bin/env python3
"""
fetch_results.py — CFBD data layer (ARCHITECTURE §4, build order §10.1).

Job: pull the full regular-season results slate + current SP+ ratings from
CollegeFootballData (api.collegefootballdata.com) in a handful of calls and
write the SHARED data/cfbd_cache.json. MUST refresh SP+ ratings, not just
scores (§6 — the single silent failure mode). Fetch-hardening: backoff/retry
and commentary-bypass (run off the last good cache on failure). Auth via
CFB_API_KEY. Also: confirm whether CFBD's native win-probability endpoint is
usable off-the-shelf vs computing from SP+ (§4, §12).

Status: STUB — no logic yet. Rebuild as CFBD client (§8 REBUILD). Prior
implementation preserved on branch archive/old-spec-leagues.
"""
