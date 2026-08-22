#!/usr/bin/env python3
"""
build_team_marks.py -- the render-ready team-identity map the profile page reads.

WHY THIS EXISTS RATHER THAN "just fetch teams_canonical.json"
-------------------------------------------------------------
docs/data/teams_canonical.json is already published (build_canonical.py puts it
there) and it does contain the colors. But it is 178 KB of season records,
alt colors and sixteen logo URLs per team, and the profile page needs exactly
three fields for 137 teams. Shipping the spine to a browser to read 3 of its
~25 keys is 170 KB of waste on the one page that renders every pick every
manager holds.

More to the point, the repo's rule is that Python computes and JS renders. The
"which colour, which abbreviation, is there a logo on disk" decision is a
computation. Doing it in the page means the page carries a copy of the
primary-vs-alternate colour rule, and that rule has exactly one correct answer
that must not exist in two places.

WRITES  docs/data/team_marks.json -- OVERWRITE, fully regenerated every run.
        Shared by every group (team identity is not group-scoped), which is why
        it sits at docs/data/ rather than docs/data/<group>/.

THE COLOUR RULE, stated once, here: `color` (primary) ALWAYS, `alt_color`
NEVER. Alt colors in the canonical spine are overwhelmingly #ffffff or #000000
-- they are the secondary in a helmet pairing, not a brand colour -- and a
white chip on ivory paper is an invisible chip. A team whose primary is missing
or unparseable gets null and the page falls back to its own ink, which is the
same tier a team with no canonical record at all lands on.

LOGOS ARE DELIBERATELY LOCAL-ONLY. teams_canonical.json carries
cdn.collegefootballdata.com URLs; this script never emits them. A static site
that hot-links 137 third-party images has a runtime dependency on someone
else's CDN for its core content, and the profile system's stack constraint is
self-hosted assets only. `logo` is therefore the path to a file that exists
under docs/assets/logos/, or null. Today that directory is empty and every
team resolves to null, which is the normal state, not a broken one -- the page
renders a team-coloured monogram chip instead and looks finished. Populate the
directory later and the same key starts answering with no page change.

Playbook compliance (CLAUDE.md):
  - rule 5: overwrite-by-default, but a run that resolves ZERO teams refuses to
    write rather than clobbering a good map with an empty one.
  - rule 7: no network, no paid API. Safe to run on every push.
  - rule 13: one canonical format; this converts at ingestion only.

Usage:
    python scripts/build_team_marks.py
    python scripts/build_team_marks.py --check    # CI: fail if docs/ is stale
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "data" / "teams_canonical.json"
DEST = ROOT / "docs" / "data" / "team_marks.json"
LOGO_DIR = ROOT / "docs" / "assets" / "logos"
LOGO_REL = "assets/logos"

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Filesystem-safe id for a school name -> the logo filename we would look for.
# "Texas A&M" -> "texas-am", "Ole Miss" -> "ole-miss". Kept here (not in the
# page) so the naming rule for a dropped-in logo file has one definition.
def slug(school):
    s = school.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def monogram(school, abbreviation):
    """The 1-3 character mark drawn when no logo file exists.

    Prefers the canonical abbreviation because it is the one people read on a
    scoreboard. Falls back to initials, then to the first two letters, so this
    never returns empty -- an empty chip is a hole in the table.
    """
    # Punctuation is stripped BEFORE truncating, not after: the canonical
    # abbreviation for Texas A&M is "TA&M", and a naive [:3] yields "TA&" --
    # a chip reading "TA ampersand". Strip first and it yields "TAM".
    if abbreviation:
        clean = re.sub(r"[^A-Za-z0-9]+", "", abbreviation)
        if clean:
            return clean[:3].upper()
    words = [w for w in re.split(r"[^A-Za-z0-9]+", school) if w]
    if len(words) >= 2:
        return "".join(w[0] for w in words[:3]).upper()
    return school[:2].upper()


def ink_for(hex_color):
    """Black or white — whichever is readable ON `hex_color`.

    THE BUG THIS FIXES: the monogram chip was drawing white type on the team's
    primary colour unconditionally. That is right for Oklahoma State's #fe5c00
    and catastrophic for Wake Forest's #ceb888, where white lands at about
    1.9:1 and the chip reads as an empty gold square. Roughly a fifth of FBS
    primaries are light enough to have the same problem.

    Computed here rather than in CSS because it is a decision, not a style:
    the page should be told what colour the type is, not work it out. Uses the
    WCAG relative-luminance formula and picks whichever of black/white gives
    the better ratio, which is exactly the 0.179 luminance threshold.
    """
    if not hex_color:
        return "#ffffff"
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return "#ffffff"
    chan = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    lum = 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]
    return "#14110f" if lum > 0.179 else "#ffffff"


def build():
    doc = json.loads(CANONICAL.read_text(encoding="utf-8"))
    teams = doc.get("teams", [])
    out = {}
    no_color = []
    for t in teams:
        school = t.get("school")
        if not school:
            continue
        color = t.get("color")
        # Primary only. See the module docstring -- alt_color is a helmet
        # secondary and is almost always white or black.
        if not (isinstance(color, str) and HEX_RE.match(color)):
            color = None
            no_color.append(school)
        logo_file = LOGO_DIR / "{s}.webp".format(s=slug(school))
        out[school] = {
            "abbr": monogram(school, t.get("abbreviation")),
            "color": color,
            "ink": ink_for(color),
            "logo": "{d}/{s}.webp".format(d=LOGO_REL, s=slug(school)) if logo_file.exists() else None,
        }
    return out, no_color


def render(marks):
    payload = {
        "_note": [
            "GENERATED by scripts/build_team_marks.py -- do not edit.",
            "school -> { abbr, color, ink, logo }. The render-ready team identity",
            "the profile picks table reads. `color` is teams_canonical.json's",
            "PRIMARY color, never alt_color (alt is a helmet secondary -- nearly",
            "always white or black, and invisible on ivory paper).",
            "`ink` is black or white, whichever is READABLE on `color` -- the page",
            "is told what colour the monogram type is rather than working it out.",
            "`logo` is a docs-relative path to a LOCAL file or null; third-party",
            "CDN logo URLs are deliberately never emitted. null is the normal",
            "state and renders a team-coloured monogram chip instead.",
        ],
        "$version": 1,
        "count": len(marks),
        "teams": dict(sorted(marks.items())),
    }
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Build the render-ready team identity map.")
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the docs/ copy is missing or stale")
    args = ap.parse_args()

    marks, no_color = build()

    # Rule 5: a zero-result build must never clobber a good map.
    if not marks:
        sys.exit("FAIL: resolved 0 teams from {p} -- refusing to overwrite {d}. "
                 "The canonical spine is missing or malformed.".format(
                     p=CANONICAL.relative_to(ROOT).as_posix(),
                     d=DEST.relative_to(ROOT).as_posix()))

    text = render(marks)

    if args.check:
        have = DEST.read_text(encoding="utf-8") if DEST.exists() else None
        if have != text:
            state = "missing" if have is None else "stale"
            sys.exit("DRIFT: {d} is {s}. Run `python scripts/build_team_marks.py` "
                     "and commit the result.".format(
                         d=DEST.relative_to(ROOT).as_posix(), s=state))
        print("ok: {d} matches source ({n} teams)".format(
            d=DEST.relative_to(ROOT).as_posix(), n=len(marks)))
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(text, encoding="utf-8")
    have_logo = sum(1 for m in marks.values() if m["logo"])
    print("wrote {d}: {n} teams, {l} with a local logo, {c} with no primary colour".format(
        d=DEST.relative_to(ROOT).as_posix(), n=len(marks), l=have_logo, c=len(no_color)))
    if no_color:
        print("  no primary colour (monogram falls back to ink): {s}".format(
            s=", ".join(sorted(no_color)[:8]) + ("..." if len(no_color) > 8 else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
