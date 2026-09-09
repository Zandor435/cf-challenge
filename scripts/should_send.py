#!/usr/bin/env python3
"""
should_send.py — Email send gate (ARCHITECTURE §5, §8 CLONE, build order §10.7).

Job: decide whether a given group gets an email this run — honor the group's
email_enabled flag + recipient list, and enforce the one-email-per-week cadence.

ONE PLACE DECIDES. The caller asks once and keys every downstream step off the
answer. That is what stops a pipeline from doing paid work (or half a send) on a
run that was never going to email.

Five conditions, ALL required to send:

  1. Enabled   the group's email_enabled is true. This is the kill switch: false
               means never, regardless of everything below.
  2. Payload   email/payload_<group>.json exists — built upstream this run. No
               payload, nothing to render.
  3. Fresh     the payload's week is NEWER than the last week we emailed this
               group (email/state/<group>/last_send.json). This is what makes the
               cadence weekly without hardcoding a day: a second run on the same
               week is a no-op, and a new filed column is what opens the gate.
  4. Time      at/after 08:00 UTC — 4am ET, which is when the Sunday cron's first
               window fires. Late west-coast kicks finish around 08:00 UTC Sunday,
               so this is the earliest hour at which Saturday night is scored; a
               lower cutoff would email a board missing the night's results.
               MUST NOT be raised above 08: the workflow's Sunday cron fires at
               08:00, 09:00 and 12:00 UTC (the first two are 4am ET on either side
               of the Nov 1 DST change, the third a backstop for late-scored
               games), and a cutoff above 08 would make the 4am windows dead.
               Condition 3 is what makes three windows safe — the first one to
               pass sends, and the rest refuse the repeat week.
  5. Resolve   recipients resolve COMPLETELY (scripts/recipients.py). Checked here,
               before any rendering, so an incomplete roster fails the gate rather
               than surfacing as a partial send.

Prints one line and exits 0 to SEND, 1 to SKIP. A SKIP is a normal outcome, not an
error — most runs skip.

Usage:
    python scripts/should_send.py --group panel
    python scripts/should_send.py --group panel --now 2026-09-13T14:00:00Z
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recipients import RecipientsError, email_enabled, load_recipients  # noqa: E402

try:
    from utils import load_group_config
except ImportError:
    from scripts.utils import load_group_config

ROOT = Path(__file__).resolve().parent.parent
EMAIL_DIR = ROOT / "email"
# 08:00 UTC = 4am ET. Pinned to the workflow's earliest Sunday cron window;
# see condition 4 above before changing it.
SEND_HOUR_UTC = 8


def state_path(group_id: str) -> Path:
    """Per-group. A single shared state file would let one group's send suppress
    every other group's — the four-groups-one-marker trap."""
    return EMAIL_DIR / "state" / group_id / "last_send.json"


def load_state(group_id: str) -> dict:
    """First run has no state file; that is an ordinary state, not an error."""
    try:
        with open(state_path(group_id), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{state_path(group_id)} is corrupt ({e.msg}); refusing to guess") from None


def decide(group_id: str, now: datetime, payload_path: Path | None = None) -> tuple[bool, str]:
    """(should_send, reason). Pure — reads state and config, writes nothing."""
    cfg = load_group_config(group_id)

    if not email_enabled(cfg):
        return False, f"{group_id}: email_enabled is false"

    ppath = payload_path or EMAIL_DIR / f"payload_{group_id}.json"
    if not ppath.exists():
        return False, f"{group_id}: no payload built this run ({ppath.name})"
    try:
        with open(ppath, encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"{group_id}: payload is not valid JSON ({e.msg})"

    week = (payload.get("meta") or {}).get("week")
    if week is None:
        return False, f"{group_id}: payload carries no week"

    state = load_state(group_id)
    last_week = state.get("week")
    if last_week is not None and week <= last_week:
        return False, f"{group_id}: week {week} already emailed (last sent week {last_week})"

    now_utc = now.astimezone(timezone.utc)
    if now_utc.hour < SEND_HOUR_UTC:
        return False, (f"{group_id}: before {SEND_HOUR_UTC:02d}:00 UTC "
                       f"({now_utc:%H:%M} UTC) — Saturday night may not be scored yet")

    try:
        n = len(load_recipients(group_id, cfg))
    except RecipientsError as e:
        return False, f"{group_id}: recipients do not resolve — {e}"

    prior = f"last sent week {last_week}" if last_week is not None else "no prior send"
    return True, f"{group_id}: week {week} ready, {n} recipients ({prior})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--group", required=True)
    ap.add_argument("--payload")
    ap.add_argument("--now", help="override 'now' as ISO8601 UTC (testing)")
    args = ap.parse_args(argv)

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    ok, reason = decide(args.group, now, Path(args.payload) if args.payload else None)
    print(("send=true — " if ok else "send=false — ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
