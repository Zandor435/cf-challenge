#!/usr/bin/env python3
"""Render email/payload.json through template.html and send it via Resend.

Delivery only — all content is already resolved in payload.json (built by
build_email_payload.py). This script renders, sends to every recipient in
config.json as a single group email (replies go to everyone), and ONLY on a successful send
snapshots site/data/narrative_state.json -> email/last_sent_state.json so the
next email's win%/rank deltas are measured "since this email."

Recipients, from-address, and reply-to come from email/config.json; the subject
comes from payload.json (meta.subject). RESEND_API_KEY is read from the env.

Usage:
    RESEND_API_KEY=... python email/send_email.py
    python email/send_email.py --dry-run     # render + validate, do NOT send or snapshot
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE_DATA = os.path.join(ROOT, "site", "data")

from render import render_html  # noqa: E402  (local module, same dir)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def snapshot_state(narrative_path, out_path):
    """Copy the current narrative_state.json to last_sent_state.json for delta tracking."""
    if os.path.exists(narrative_path):
        shutil.copyfile(narrative_path, out_path)
        print(f"  snapshot: {narrative_path} -> {out_path}")
    else:
        print(f"  snapshot skipped: {narrative_path} not found", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Send the WC Challenge recap email via Resend")
    ap.add_argument("--payload", default=os.path.join(HERE, "payload.json"))
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--narrative", default=os.path.join(SITE_DATA, "narrative_state.json"))
    ap.add_argument("--state-out", default=os.path.join(HERE, "last_sent_state.json"))
    ap.add_argument("--date-out", default=os.path.join(HERE, "last_send_date.txt"))
    ap.add_argument("--dry-run", action="store_true",
                    help="render + validate but do not call Resend or snapshot state")
    ap.add_argument("--to", action="append", metavar="EMAIL",
                    help="override config.json recipients (repeatable); for test sends")
    args = ap.parse_args()

    if not os.path.exists(args.payload):
        print(f"ERROR: {args.payload} not found — run build_email_payload.py first.",
              file=sys.stderr)
        sys.exit(1)

    payload = load_json(args.payload)
    config = load_json(args.config)
    html = render_html(payload)

    subject = payload["meta"]["subject"]
    recipients = args.to or config.get("recipients") or []
    sender = config["from"]
    reply_to = config.get("reply_to")

    if not recipients:
        print("ERROR: no recipients in config.json.", file=sys.stderr)
        sys.exit(1)

    print(f"Subject: {subject}")
    print(f"To: {', '.join(recipients)}")
    print(f"Rendered HTML: {len(html)} bytes")

    if args.dry_run:
        print("Dry run — not sending, not snapshotting.")
        return

    # Local testing convenience: load RESEND_API_KEY from a repo-root .env if present.
    # In CI the secret is already in the env, so dotenv (if absent) is silently skipped.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except ImportError:
        pass

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("ERROR: RESEND_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    import resend
    resend.api_key = api_key

    params = {
        "from": sender,
        "to": recipients,        # one group email -> reply-all reaches everyone
        "subject": subject,
        "html": html,
    }
    if reply_to:
        params["reply_to"] = reply_to

    try:
        resp = resend.Emails.send(params)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: Resend send failed: {e}", file=sys.stderr)
        sys.exit(1)

    msg_id = resp.get("id") if isinstance(resp, dict) else getattr(resp, "id", None)
    print(f"Sent. Resend id: {msg_id}")

    # Only after a confirmed send do we advance the delta baseline — but a test
    # send (--to override) must NOT touch it, or it'd skew the real email's deltas.
    if args.to:
        print("  test send (--to) — not snapshotting the delta baseline.")
    else:
        snapshot_state(args.narrative, args.state_out)
        # Stamp the send date so should_send.py won't fire a second email today,
        # even if more matches finish later this UTC day.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(args.date_out, "w", encoding="utf-8") as f:
            f.write(today + "\n")
        print(f"  stamped send date: {today} -> {args.date_out}")


if __name__ == "__main__":
    main()
