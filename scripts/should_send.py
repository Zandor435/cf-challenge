#!/usr/bin/env python3
"""
should_send.py — Email send gate (ARCHITECTURE §5, §8 CLONE, build order §10.7).

Job: decide whether a given group gets an email this run — honor the group's
email_enabled flag + recipient list in config.json, and enforce the
one-email-per-week cadence. At least one group gets email; maybe all.

Status: STUB — no logic yet. Port send logic from WC.
"""
