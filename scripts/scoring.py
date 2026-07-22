#!/usr/bin/env python3
"""
scoring.py — Board 1: Standings, pure arithmetic (ARCHITECTURE §3, §10.2).

Job: from the shared cache, compute each pick's BANKED delta in the owner's
O/U direction (over: wins-line, under: line-wins) and the ENVELOPE — floor
(loses out) / ceiling (wins out) from games_remaining (read the actual 12/13
regular-season game count, not hardcoded — §1). Emit clinch/eliminate flags.
Sum picks -> owner total floor/ceiling. No model, no methodology risk; this
board is the credibility spine.

Status: STUB — no logic yet. Prior delta-model implementation preserved on
branch archive/old-spec-leagues (worth porting from).
"""
