#!/usr/bin/env python3
"""
send_email.py — Weekly email dispatch (ARCHITECTURE §5, §8 CLONE, build order §10.7).

Job: send one group's rendered email via Resend, from mustardboy.xyz, to that
group's recipient list. One email per week per group. Gated by should_send.py.

DELIVERY ONLY. Every value is already resolved in email/payload_<group>.json; this
renders it, sends it, and — only on a confirmed send — stamps the state that makes
the next run a no-op. It computes nothing about the pool.

Recipients come from scripts/recipients.py (the gitignored overlay, or the
RECIPIENTS_JSON secret in CI), never from the payload and never from the public
group config. The list is resolved BEFORE rendering so a roster problem fails
before any work is done.

One group email, not N personalised ones: every address goes in a single `to`, so
a reply-all reaches the whole group — the point of a pool newsletter.

STATE (email/state/<group>/last_send.json) is written ONLY after Resend confirms.
It is per-group: a single shared marker would let panel's send suppress family's.

    --dry-run   render + validate, send nothing, write nothing
    --to        send to an override address instead of the roster; skips BOTH the
                state write and the date stamp, because a test send must not make
                the real weekly send think it already happened

Usage:
    python scripts/send_email.py --group panel --dry-run
    RESEND_API_KEY=... python scripts/send_email.py --group panel
    RESEND_API_KEY=... python scripts/send_email.py --group panel --to me@example.com
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recipients import RecipientsError, email_enabled, load_recipients  # noqa: E402
from render import render_html  # noqa: E402

try:
    from utils import load_group_config
except ImportError:
    from scripts.utils import load_group_config

ROOT = Path(__file__).resolve().parent.parent
EMAIL_DIR = ROOT / "email"


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def stamp_state(group_id: str, week: int, subject: str, count: int, msg_id: str | None):
    """Record the confirmed send. Per-group path; written atomically."""
    path = EMAIL_DIR / "state" / group_id / "last_send.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "$note": ("Written ONLY after Resend confirms a send to the real roster. "
                  "should_send.py compares `week` against the payload's week to keep "
                  "the cadence at one email per group per week. A --to test send "
                  "deliberately does NOT touch this file. Per-group on purpose: a "
                  "shared marker would let one group's send suppress the others. "
                  "Absent on first run, which is an ordinary state."),
        "group_id": group_id,
        "week": week,
        "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "subject": subject,
        "recipient_count": count,   # a COUNT, never the addresses — this file is committed
        "resend_id": msg_id,
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--group", required=True)
    ap.add_argument("--payload", help="default: email/payload_<group>.json")
    ap.add_argument("--config", default=str(EMAIL_DIR / "config.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="render + validate; do NOT call Resend, do NOT write state")
    ap.add_argument("--to", action="append", metavar="EMAIL",
                    help="override the roster (repeatable); skips the state write")
    args = ap.parse_args(argv)

    ppath = Path(args.payload) if args.payload else EMAIL_DIR / f"payload_{args.group}.json"
    if not ppath.exists():
        print(f"ERROR: {ppath} not found — run build_email_payload.py first.", file=sys.stderr)
        return 1

    payload = load_json(ppath)
    ecfg = load_json(Path(args.config))
    gcfg = load_group_config(args.group)

    # The kill switch applies to a real send. An override send is an explicit
    # human act against a named address, so it is allowed while a group is dark.
    if not email_enabled(gcfg) and not args.to and not args.dry_run:
        print(f"ERROR: {args.group} has email_enabled: false — refusing to send.", file=sys.stderr)
        return 1

    # Resolve BEFORE rendering: a roster problem should fail before any work.
    if args.to:
        recipients = args.to
        source = "--to override"
    else:
        try:
            recipients = load_recipients(args.group, gcfg)
        except RecipientsError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        source = "roster"

    subject = payload["meta"]["subject"]
    week = payload["meta"]["week"]
    sender = ecfg["from"]
    html = render_html(payload)

    print(f"Group:   {args.group} (week {week})")
    print(f"Subject: {subject}")
    print(f"From:    {sender}")
    print(f"To:      {len(recipients)} recipient(s) via {source}")
    print(f"HTML:    {len(html):,} bytes")

    if args.dry_run:
        print("Dry run — nothing sent, no state written.")
        return 0

    # Local convenience: a repo-root .env supplies the key for a dev send. In CI the
    # secret is already in the environment, so a missing dotenv is silently fine.
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("ERROR: RESEND_API_KEY not set.", file=sys.stderr)
        return 1

    import resend
    resend.api_key = api_key

    params = {"from": sender, "to": recipients, "subject": subject, "html": html}
    # reply_to is the same roster, so a plain Reply reaches the group rather than
    # a noreply sender. Skipped on an override send — a test must not fan out.
    if not args.to:
        params["reply_to"] = recipients

    try:
        resp = resend.Emails.send(params)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: Resend send failed: {e}", file=sys.stderr)
        return 1

    msg_id = resp.get("id") if isinstance(resp, dict) else getattr(resp, "id", None)
    print(f"Sent. Resend id: {msg_id}")

    if args.to:
        print("  --to override — state NOT written (the real weekly send still owes this week).")
    else:
        path = stamp_state(args.group, week, subject, len(recipients), msg_id)
        print(f"  state: week {week} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
