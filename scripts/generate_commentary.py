#!/usr/bin/env python3
"""
generate_commentary.py — The one pundit (ARCHITECTURE §8 ADAPT, build order §10.8).

Job: narrative garnish only — LLMs NEVER touch scoring (§2). Read each group's
deterministic output and write one pundit's take on trends/hits/busts. Strip
WC's 1032-line roundtable to a SINGLE persona (persona TBD: Rome / Herbstreit /
Berman / SVP — §12). Calls OpenAI GPT via raw urllib (no SDK), key
OPENAI_API_KEY. Never blocks standings if GPT fails.

Status: STUB — no logic yet. Adapt from WC; persona to be chosen.
"""
