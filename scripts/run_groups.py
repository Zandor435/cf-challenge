#!/usr/bin/env python3
"""
run_groups.py — Multi-tenant pipeline loop (ARCHITECTURE §5, build order §10.4).

Job: orchestrate one run across all groups. Fetch once into the shared
data/cfbd_cache.json, then LOOP over groups/*/ — validate names, score
(Board 1) and project (Board 2) each group off the one shared cache, writing
each group's standings + projection JSON to groups/<id>/output/. Four groups
cost the same CFBD calls as one (same games, different picks). Everything
keyed by group_id.

Status: STUB — no logic yet.
"""
