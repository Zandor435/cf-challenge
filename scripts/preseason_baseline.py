#!/usr/bin/env python3
"""
preseason_baseline.py — One-time SP+-anchored preseason baseline (ARCHITECTURE §7, §10.3).

Job: after CC pulls each team's schedule + PRESEASON SP+, run the one-time
manual pass (Claude + Zach) that walks each team's schedule game-by-game,
anchored to SP+ rating gaps (big favorite -> expected win; big underdog ->
loss; inside threshold -> tossup), summing to an "expected wins" baseline per
team. Freeze that into config as the draft-day expectation line. NEVER reseed
this (§6) — the gap between it and the live projection is the story.

Status: STUB — no logic yet. Runs once, post-data-pull, pre-season.
"""
