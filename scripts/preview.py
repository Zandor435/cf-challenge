#!/usr/bin/env python3
"""
preview.py — Render the weekly email to a local HTML file for browser testing.

The dev loop for the email. Iterate HERE, never by sending: every real send costs
a Resend credit, lands in four inboxes, and cannot be recalled. This renders the
same template through the same render.py the sender uses, so what you see is what
would go out.

Reads the REAL email/payload_<group>.json — build it first. There is deliberately
no --sample mode: the payload builder runs against committed data that is always
present in this repo, so a fabricated payload would only let the template drift
from the shape the real data has.

Writes email/preview_<group>.html (gitignored — a local dev artifact, never a
source of truth).

Usage:
    python scripts/build_email_payload.py --group panel
    python scripts/preview.py --group panel
    python scripts/preview.py --group panel --open
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import render_html  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EMAIL_DIR = ROOT / "email"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render the weekly email to a local HTML file.")
    ap.add_argument("--group", required=True)
    ap.add_argument("--payload", help="default: email/payload_<group>.json")
    ap.add_argument("--out", help="default: email/preview_<group>.html")
    ap.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the rendered file in the default browser")
    args = ap.parse_args(argv)

    payload_path = Path(args.payload) if args.payload else EMAIL_DIR / f"payload_{args.group}.json"
    if not payload_path.exists():
        print(f"ERROR: {payload_path} not found — run:\n"
              f"  python scripts/build_email_payload.py --group {args.group}", file=sys.stderr)
        return 1

    with open(payload_path, encoding="utf-8") as f:
        payload = json.load(f)

    html = render_html(payload)
    out = Path(args.out) if args.out else EMAIL_DIR / f"preview_{args.group}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    shown = [k for k in ("column", "board1", "board2", "best_worst", "rail") if payload.get(k)]
    print(f"{args.group}: week {payload['meta']['week']} -> {out}  ({len(html):,} bytes)")
    print(f"  sections rendered: {', '.join(shown)}")
    if args.open_browser:
        webbrowser.open(out.resolve().as_uri())
        print("  opened in browser")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
