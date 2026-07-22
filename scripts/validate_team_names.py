#!/usr/bin/env python3
"""
validate_team_names.py — The fetch->score name gate (ARCHITECTURE §8 CLONE, §9).

Job: hard gate mapping every pick's team name to its CFBD-canonical name via
data/team_aliases.json before scoring. CFB naming is a bigger mess than WC
(Ole Miss/Mississippi, Miami FL vs OH, USC ambiguity, App State, ...), so this
is MORE load-bearing here. Keep it hard: exit 1 blocks the run so a mismatched
name can never silently mis-score a pick.

Status: STUB — no logic yet. Clone the WC pattern; expand aliases 5-10x.
"""
