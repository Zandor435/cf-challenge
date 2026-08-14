#!/usr/bin/env python3
"""
generate_commentary.py — The one pundit (ARCHITECTURE §8 ADAPT, build order §10.8).

Job: narrative garnish only — LLMs NEVER touch scoring (§2). Read each group's
deterministic output and write one pundit's take on trends/hits/busts. Every
number the column prints is precomputed upstream; this script never derives one.

Persona: DECIDED — a Scott Van Pelt parody, a SINGLE pinned voice across all
three groups (ARCHITECTURE §12, landed 2026-07-27). Rome / Herbstreit / Berman
were dropped; do not reopen. The voice lives in templates/svp_persona.md and is
passed as the system prompt — this script composes, it does not characterize.

Calls OpenAI GPT via raw urllib (no SDK), key OPENAI_API_KEY. Fail-soft is
mandatory: any failure logs to stderr and exits 0, so commentary can never
block standings or the rest of the pipeline.

Status: STUB — no logic yet.
"""
