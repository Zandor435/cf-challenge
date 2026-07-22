#!/usr/bin/env python3
"""
send_email.py — Weekly email dispatch (ARCHITECTURE §5, §8 CLONE, build order §10.7).

Job: send each enabled group's rendered email via Resend, from domain
mustardboy.xyz, to that group's recipient list. One email per week per group.
Gated by should_send.py.

Status: STUB — no logic yet. Port Resend wiring from WC.
"""
