#!/usr/bin/env python3
"""Selection sheet for the tear-on-LEFT pass: every new cut beside the
published tear-on-right cut of the same portrait.

    python scripts/build_tear_side_sheet.py

WHY A SECOND SHEET. build_selection_sheet.py walks all of output/ and renders
whatever is on disk, grouped by category -- the right tool for picking a winner
out of a fan-out of N takes. This pass is not a fan-out. There is exactly one
candidate per portrait, and the only question worth putting in front of a
reviewer is "is the mirrored edge as good as the one it would sit opposite",
which is a PAIRING, not a grid. So the sheet is pairs.

WHAT IT SHOWS. Left card: the published docs/assets/profiles/<g>/<id>-ripped
.webp, tearing right, drawn in the portrait-LEFT composition it belongs to.
Right card: the staged output/profile_heroes_left/<g>/<id>-ripped-left.webp,
tearing left, drawn in the portrait-RIGHT composition it is for. Both sit on
the profile page's own paper colour with the editorial column mocked in beside
them, because a transparent tear judged on white is judged against a ground it
will never meet -- the whole point of the edge is where it breaks into copy.

WHO IS LISTED is derived from profile_order in the published personas.json, not
hand-listed. A sheet that could disagree with the page about who sits on which
side is worse than no sheet.

PUBLISHES NOTHING. Images are inlined as data URIs, the sheet is written to
output/ (gitignored), and no manifest or art slot is touched. Wiring a chosen
file into the page is a separate, deliberate step.
"""
import base64
import io as _io
import json
import sys
from html import escape
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
STAGED = ROOT / "output" / "profile_heroes_left"
SHEET = ROOT / "output" / "tear_side_selection.html"
THUMB_W = 420

PAPER = "#f4efe4"
INK = "#14110f"

# Mock copy for the editorial column. Deliberately flat and short: this sheet
# exists to judge an EDGE, and real prose beside it invites a reader to start
# editing the prose instead.
MOCK = ("The tear is supposed to open toward this column. Read the edge, not "
        "the words: does the ragged side break into the copy, or does it run "
        "off into the page margin where nothing meets it?")


def thumb_uri(path, width=THUMB_W):
    with Image.open(path) as im:
        im = im.convert("RGBA")
        if im.width > width:
            h = round(im.height * width / im.width)
            im = im.resize((width, h), Image.LANCZOS)
        buf = _io.BytesIO()
        im.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def right_aligned():
    """(group, manager_id, profile_order) for every manager the alternation
    puts portrait-RIGHT."""
    out = []
    for g in ("panel", "family", "church", "browns"):
        path = DOCS / "data" / g / "personas.json"
        per = json.loads(path.read_text(encoding="utf-8"))["managers"]
        for mid, rec in sorted(per.items(), key=lambda kv: kv[1]["profile_order"]):
            if rec["profile_order"] % 2 == 1:
                out.append((g, mid, rec["profile_order"]))
    return out


def pairs():
    """Match each staged left cut to the published right cut of the same file.

    Keyed by FILE STEM, not by manager: browns' todd has two rotating variants
    and each needs its own pair -- the same reason heroes.json is keyed by path.
    """
    rows = []
    for g, mid, order in right_aligned():
        found = []
        gdir = STAGED / g
        if gdir.is_dir():
            for f in sorted(gdir.glob("*-ripped-left.webp")):
                stem = f.name[: -len("-ripped-left.webp")]
                if stem == mid or stem.startswith(mid + "_"):
                    pub = DOCS / "assets" / "profiles" / g / (stem + "-ripped.webp")
                    found.append((stem, pub if pub.exists() else None, f))
        rows.append((g, mid, order, found))
    return rows


def why_none(group, mid):
    """A manager on the right with no new cut is not a gap -- name which of the
    three reasons it is, or the sheet reads as an unfinished batch."""
    if group == "family":
        return ("family's art is real photographs and period gag artifacts, not "
                "the AI coach-poster treatment, and is deliberately never torn "
                "on either side. There is nothing to mirror.")
    if (group, mid) == ("panel", "blaine"):
        return "hand-painted brush edge, not synthesised. (He is portrait-LEFT anyway.)"
    return ("no profile art on disk, so this profile renders DOSSIER -- a "
            "full-width composition with no portrait column, and therefore no "
            "side and no tear.")


def rel(path):
    return str(path.relative_to(ROOT)).replace("\\", "/")


def card_html(label, uri, side, note):
    if uri is None:
        return ('<div class="cut missing"><p class="lab">%s</p>'
                '<p class="none">%s</p></div>' % (label, escape(note)))
    art = '<div class="art"><img src="%s" alt=""></div>' % uri
    copy = '<div class="copy"><p>%s</p></div>' % escape(MOCK)
    inner = (art + copy) if side == "left" else (copy + art)
    return ('<div class="cut"><p class="lab">%s</p><div class="mock">%s</div></div>'
            % (label, inner))


def build():
    rows = pairs()
    made = sum(len(f) for _, _, _, f in rows)
    body = []
    for g, mid, order, found in rows:
        head = ('<h3>%s <span class="mid">%s</span>'
                '<span class="pos">persona position %d &middot; portrait RIGHT</span></h3>'
                % (escape(g), escape(mid), order + 1))
        if not found:
            body.append('<section class="mgr">%s<p class="skip">%s</p></section>'
                        % (head, escape(why_none(g, mid))))
            continue
        cards = []
        for stem, pub, new in found:
            left = card_html("PUBLISHED &mdash; tear on RIGHT (portrait left)",
                             thumb_uri(pub) if pub else None, "left",
                             "no published right cut for this file")
            right = card_html("NEW &mdash; tear on LEFT (portrait right)",
                              thumb_uri(new), "right", "")
            cards.append(
                '<div class="pair"><p class="stem">%s</p><div class="two">%s%s</div>'
                '<p class="paths"><code>%s</code><br><code>%s</code></p></div>'
                % (escape(stem), left, right,
                   escape(rel(pub)) if pub else "-", escape(rel(new))))
        body.append('<section class="mgr">%s%s</section>' % (head, "".join(cards)))

    html = TEMPLATE % {
        "paper": PAPER,
        "ink": INK,
        "made": made,
        "n_right": len(rows),
        "body": "".join(body),
    }
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    SHEET.write_text(html, encoding="utf-8", newline="\n")
    print("wrote %s (%d pairs across %d portrait-right managers)"
          % (rel(SHEET), made, len(rows)))
    return 0


TEMPLATE = """<!DOCTYPE html>
<meta charset="utf-8">
<title>Tear side &mdash; portrait-right managers</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; padding: 34px 30px 80px; background: #e7e1d4;
         color: %(ink)s; font: 15px/1.55 -apple-system, Segoe UI, Roboto, sans-serif; }
  h1 { font-size: 1.5rem; margin: 0 0 8px; letter-spacing: -.01em; }
  .sub { margin: 0 0 8px; color: #6f6355; max-width: 62em; }
  .count { margin: 18px 0 30px; font: 600 .78rem/1 ui-monospace, monospace;
           letter-spacing: .09em; text-transform: uppercase; color: #8a7f6d; }
  .mgr { margin: 0 0 40px; }
  h3 { font-size: 1.05rem; margin: 0 0 12px; text-transform: uppercase;
       letter-spacing: .1em; display: flex; gap: 10px; align-items: baseline; }
  h3 .mid { color: #b8452a; }
  h3 .pos { margin-left: auto; font-weight: 400; text-transform: none;
            letter-spacing: 0; color: #8a7f6d; font-size: .85rem; }
  .skip { margin: 0; padding: 14px 16px; background: #f0ebdf; color: #6f6355;
          border-left: 4px solid #c9c0ad; max-width: 62em; }
  .stem { margin: 0 0 8px; font: 600 .74rem/1 ui-monospace, monospace;
          letter-spacing: .08em; color: #8a7f6d; }
  .pair { margin: 0 0 26px; }
  .two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
  .cut { background: %(paper)s; padding: 14px; border: 1px solid #d6cdb9; }
  .lab { margin: 0 0 10px; font: 700 .7rem/1.3 ui-monospace, monospace;
         letter-spacing: .1em; color: #6f6355; }
  .mock { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
          gap: 14px; align-items: start; }
  .mock .art img { display: block; width: 100%%; height: auto; }
  .mock .copy p { margin: 0; font-size: .82rem; color: #4a4238; }
  .missing .none { margin: 0; color: #8a7f6d; font-size: .85rem; }
  .paths { margin: 8px 0 0; font-size: .72rem; color: #8a7f6d; }
  code { font-family: ui-monospace, monospace; }
  @media (max-width: 900px) { .two { grid-template-columns: 1fr; } }
</style>
<h1>Tear side &mdash; portrait-right managers</h1>
<p class="sub">Left card is what is published today: the tear on the RIGHT, drawn
in the portrait-left composition it belongs to. Right card is the new staged cut:
the tear on the LEFT, drawn in the portrait-right composition it is for. In both,
the tear should open toward the copy.</p>
<p class="sub">The left tear is the right tear <em>mirrored</em> &mdash; same seed,
same profile, same flecks, flipped. Only the alpha is flipped; the photograph is
never mirrored.</p>
<p class="sub"><strong>Nothing here is published.</strong> The new files are staged
under <code>output/profile_heroes_left/</code>, which is gitignored, and no manifest
or art slot points at them.</p>
<p class="count">%(made)d new cuts &middot; %(n_right)d portrait-right managers &middot; 0 API calls</p>
%(body)s
"""


if __name__ == "__main__":
    sys.exit(build())
