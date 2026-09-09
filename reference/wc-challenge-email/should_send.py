#!/usr/bin/env python3
"""Decide whether the recap email should send on THIS pipeline run.

The pipeline now runs every few hours (cron: 0 */3 * * *), so the old "today is a
configured send date" gate is no longer sufficient on its own — it would fire on
every run of a send date. This adds three conditions on top of the send-date
schedule so exactly one email goes out per day, promptly after results land:

  1. Date:  today must be a configured send date (email/config.json send_dates).
  2. Time:  only at/after 05:00 UTC — the first scheduled run of the ET day, by
            which point the prior evening's finals are scored.
  3. Fresh: only if there are NEW scored results since the last email — compared
            by phase.matches_played in site/data/narrative_state.json vs the
            last-sent snapshot (email/last_sent_state.json).
  4. Once:  only if we have not already emailed today (email/last_send_date.txt,
            written by send_email.py on each successful send).

So if all of an evening's matches finish by midnight ET, the next run after 05:00
UTC catches the new results and emails promptly — but never twice in one UTC day,
even if more matches finish later the same day.

Prints a one-line reason and exits 0 to SEND, 1 to SKIP. Intended to be called
from the workflow's send gate:  `if python email/should_send.py; then ...`
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def matches_played(doc):
    """phase.matches_played from a narrative_state-shaped doc (0 if absent)."""
    try:
        return int(doc.get("phase", {}).get("matches_played", 0))
    except (AttributeError, TypeError, ValueError):
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--payload", default=os.path.join(HERE, "payload.json"))
    ap.add_argument("--narrative",
                    default=os.path.join(ROOT, "site", "data", "narrative_state.json"))
    ap.add_argument("--last-sent", default=os.path.join(HERE, "last_sent_state.json"))
    ap.add_argument("--last-send-date", default=os.path.join(HERE, "last_send_date.txt"))
    ap.add_argument("--now", help="override 'now' as ISO8601 UTC (testing)")
    args = ap.parse_args()

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")

    def skip(reason):
        print(f"send=false — {reason}")
        sys.exit(1)

    # 1. Configured send date?
    cfg = load_json(args.config)
    if today not in cfg.get("send_dates", []):
        skip(f"{today} is not a configured send date")

    # Payload must exist (built upstream) or there is nothing to render.
    if not os.path.exists(args.payload):
        skip("no email/payload.json built this run")

    # 2. At/after 05:00 UTC?
    if now.astimezone(timezone.utc).hour < 5:
        skip(f"before 05:00 UTC ({now.astimezone(timezone.utc):%H:%M} UTC)")

    # 4. Already emailed today?
    if os.path.exists(args.last_send_date):
        with open(args.last_send_date, encoding="utf-8") as f:
            if f.read().strip() == today:
                skip(f"already emailed today ({today})")

    # 3. New scored results since the last email?
    try:
        current = matches_played(load_json(args.narrative))
    except FileNotFoundError:
        skip("narrative_state.json not found — nothing scored yet")
    try:
        last = matches_played(load_json(args.last_sent))
    except FileNotFoundError:
        last = 0
    if current <= last:
        skip(f"no new scored results since last email "
             f"(matches_played {current} <= {last})")

    print(f"send=true — {today}: {current - last} new scored match(es) "
          f"since last email ({current} vs {last}); first eligible run after 05:00 UTC")
    sys.exit(0)


if __name__ == "__main__":
    main()
