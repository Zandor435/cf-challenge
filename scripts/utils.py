#!/usr/bin/env python3
"""
utils.py — Shared helpers (ARCHITECTURE §5, §8).

Job: config-driven I/O plumbing reused across the pipeline — load group
config.json / picks.json, read the shared data/cfbd_cache.json, JSON save
helpers, group_id path resolution (groups/<id>/output/), and team-name
normalization used by the validate gate. Keep everything config-driven,
nothing hardcoded (§8 crown jewel).

Status: STUB — no logic yet.
"""
