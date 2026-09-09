#!/usr/bin/env python3
"""Render the email to email/preview.html for browser testing.

By default renders the real email/payload.json (build it first). With --sample it
fabricates a fully-populated, mid-tournament payload so you can preview every
section — hero, Rome hook, standings, the featured pundit, the CTA — without any
live data.

The --sample hero is a locally-generated placeholder (a solid-colour data-URI PNG)
so the hero section renders OFFLINE, before any image has been generated or the
site deployed. Avatar / CTA URLs still point at the configured site_base_url, so
the pundit thumbnail only loads once the site is deployed (a broken thumbnail
offline is expected).

Usage:
    python email/build_email_payload.py && python email/preview.py   # real data
    python email/preview.py --sample                                  # fake data
    python email/preview.py --open                                    # also open in browser
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))

from render import render_html  # noqa: E402


def load_config_base_url():
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return (json.load(f).get("site_base_url") or "").rstrip("/")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def sample_hero_data_uri():
    """A fake hero so the template's hero section renders offline. Tries a real
    placeholder PNG (diagonal team-colour gradient via PIL); if PIL is unavailable,
    falls back to a tiny inline SVG data URI so the section still shows."""
    w, h = 1200, 675
    try:
        from PIL import Image
        # Diagonal blend of two league colours over a near-black base — reads as a hero.
        base = (12, 14, 20)
        c1, c2 = (244, 196, 48), (47, 109, 255)  # Zach gold -> Gunner blue
        img = Image.new("RGB", (w, h), base)
        px = img.load()
        for y in range(h):
            for x in range(0, w, 4):  # step 4px — fast enough, invisible at hero scale
                t = (x / w + y / h) / 2
                r = int(base[0] + (c1[0] * (1 - t) + c2[0] * t) * 0.5)
                g = int(base[1] + (c1[1] * (1 - t) + c2[1] * t) * 0.5)
                b = int(base[2] + (c1[2] * (1 - t) + c2[2] * t) * 0.5)
                for dx in range(4):
                    if x + dx < w:
                        px[x + dx, y] = (min(r, 255), min(g, 255), min(b, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except ImportError:
        svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}'>"
               "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
               "<stop offset='0' stop-color='#f4c430'/><stop offset='1' stop-color='#2f6dff'/>"
               "</linearGradient></defs><rect width='100%' height='100%' fill='#0c0e14'/>"
               "<rect width='100%' height='100%' fill='url(#g)' opacity='0.5'/>"
               "<text x='50%' y='52%' fill='#0c0e14' font-family='Arial' font-size='64' "
               "font-weight='bold' text-anchor='middle'>HERO PLACEHOLDER</text></svg>")
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def sample_payload():
    """A realistic mid-tournament payload that hits every template branch."""
    base = load_config_base_url() or "https://zandor435.github.io/wc-challenge"

    def avatar(slug, v):
        f = f"{slug}.png" if v == 1 else f"{slug}_v{v}.png"
        return f"{base}/assets/portraits/pundits/{slug}/{f}"

    return {
        "meta": {
            "generated": "2026-06-23T05:00:00Z",
            "today": "2026-06-23",
            "day_number": 9,
            "day_label": "Day 9",
            "is_preseason": False,
            "subject": "WC Challenge Day 9: Mustard Boy's Brazil Carry Job Reaches Embarrassing New Heights",
            "site_base_url": base,
            "tournament": "FIFA World Cup 2026",
        },
        "hero_image_url": sample_hero_data_uri(),
        "site_url": base,
        "rome": {
            "headline": "Mustard Boy's Brazil Carry Job Reaches Embarrassing New Heights",
            "short": (
                "Back in the saddle, clones. Nine days in and the cream is separating from "
                "the curdled milk — and somebody in this pool drafted six teams just to ride "
                "one of them. You know who you are, Zach. Gunner's your leader, but a leader "
                "who needed France to bail him out twice this week is a leader on notice."
            ),
        },
        "standings": [
            {"rank": 1, "owner": "Gunner", "ring_name": "Bubba G", "color": "#2f6dff",
             "total_points": "34", "move_arrow": "—", "move_color": "#8b919c"},
            {"rank": 2, "owner": "Devin", "ring_name": "Ghost Pepper", "color": "#f0743a",
             "total_points": "31", "move_arrow": "▲", "move_color": "#28c060"},
            {"rank": 3, "owner": "Zach", "ring_name": "Mustard Boy", "color": "#f4c430",
             "total_points": "28", "move_arrow": "▼", "move_color": "#ec4444"},
            {"rank": 4, "owner": "Rafe", "ring_name": "The Noisemaker", "color": "#a855f7",
             "total_points": "22", "move_arrow": "▲", "move_color": "#28c060"},
            {"rank": 5, "owner": "Gayden", "ring_name": "The Backpass Assassin", "color": "#28c060",
             "total_points": "19", "move_arrow": "▼", "move_color": "#ec4444"},
        ],
        "featured_pundit": {
            "slug": "wynalda", "name": "Eric Wynalda", "color": "#e2231a",
            "avatar_url": avatar("wynalda", 4),
            "headline": "Bubba G Credits Genius Draft For France Doing All The Work",
            "subtitle": "'I built this,' says man whose other five teams have one win combined",
            "match": "Morocco vs Belgium",
        },
        "up_next": {
            "date_human": "Jun 24",
            "has_matches": True,
            "matches": [
                {"team1": "England", "team1_owner": "Gayden", "team1_color": "#28c060",
                 "team2": "Senegal", "team2_owner": "Gunner", "team2_color": "#2f6dff",
                 "group": "C", "time_et": "3 p.m. ET", "venue": "Atlanta"},
                {"team1": "USA", "team1_owner": "Devin", "team1_color": "#f0743a",
                 "team2": "Mexico", "team2_owner": "Rafe", "team2_color": "#a855f7",
                 "group": "D", "time_et": "9 p.m. ET", "venue": "Los Angeles"},
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser(description="Render the email to preview.html")
    ap.add_argument("--sample", action="store_true",
                    help="use fabricated mid-tournament data instead of payload.json")
    ap.add_argument("--payload", default=os.path.join(HERE, "payload.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "preview.html"))
    ap.add_argument("--open", action="store_true", help="open the result in a browser")
    args = ap.parse_args()

    if args.sample:
        payload = sample_payload()
    else:
        if not os.path.exists(args.payload):
            print(f"ERROR: {args.payload} not found. Build it first, or use --sample.",
                  file=sys.stderr)
            sys.exit(1)
        with open(args.payload, encoding="utf-8") as f:
            payload = json.load(f)

    html = render_html(payload)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {args.out} ({len(html)} bytes)")
    print(f"  subject: {payload['meta']['subject']}")

    if args.open:
        webbrowser.open("file://" + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
