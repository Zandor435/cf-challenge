#!/usr/bin/env python3
"""One selection sheet for every image currently under output/.

Not a per-batch contact sheet -- this walks output/ and renders whatever is
actually on disk into a single static HTML file you open by double-clicking.
The page reads nothing and writes nothing: selection state lives in
localStorage, and the handoff is a copied/downloaded list of relative paths.

    python scripts/build_selection_sheet.py [--width 460] [--quality 80]

Discovery, not configuration: category comes from where a file sits, and
setup/lead/pair/style/variant come from parsing the filename against the
conventions the generators already use (scene_batch.BATCH slugs,
generate_scenes.STYLE_SET, groups/panel/personas.json manager ids). A name that
will not parse is kept and shown under "unsorted" -- never dropped. A category
with no files on disk emits no section at all.

output/archive/** is folded into the category it mirrors and flagged, so an
archived take sits beside the live take that replaced it.
"""

import argparse
import base64
import io
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scene_batch import BATCH  # noqa: E402  -- pure data, no side effects

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
SHEET = OUTPUT / "selection_sheet.html"
PERSONAS_JSON = ROOT / "groups" / "panel" / "personas.json"
GENERATOR = ROOT / "scripts" / "generate_scenes.py"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# NEVER SWEPT, UNCONDITIONALLY. This page inlines every image it finds as
# base64 into a single file you open by double-clicking -- a file that can be
# mailed, synced or dropped in a shared folder. What it sweeps is therefore a
# DISTRIBUTION decision, not a display one, and the persona tree is where this
# repo keeps likeness material of real people: camera originals, un-approved
# renders, and the reference art generated from them.
#
# THE FILTER IS A SUBTREE, NOT A LIST, and that is the whole point. The first
# version of this guard enumerated `personas/family/` plus `.jpg`/`.jpeg` under
# `personas/`, and `personas/church/fat_joshb.png` walked around it the same
# day it was written -- a PNG, in a group the list did not name. Any rule that
# has to be extended per group or per extension is a rule that is already
# wrong for the next file added. Matching the path SEGMENT covers
# output/personas/**, output/archive/personas/**, output/_source/personas/**
# and every group that will ever exist under them, at every extension.
#
# This is not reachable by argument. --only narrows a sweep; it cannot widen
# one, and no flag turns this off.
EXCLUDED_SEGMENT = "personas"


def is_excluded(rel):
    """True if `rel` (relative to output/) lies anywhere under a personas/ dir."""
    parts = rel.parts if hasattr(rel, "parts") else Path(rel).parts
    return EXCLUDED_SEGMENT in parts

# Section order. A category discovered outside this list is appended just
# before "unsorted", which is always last.
CATEGORY_ORDER = ["personas", "banners", "posters", "scenes", "matchups",
                  "editorial", "objects", "halfcards", "recolors", "source",
                  "vault", "rejects", "duplicates", "unsorted"]

CATEGORY_BLURB = {
    "posters": "fight cards and matchup bills",
    "banners": "group banners by style",
    "scenes": "named compositions and tunnel walkouts",
    "objects": "trophy plates",
    "halfcards": "half-card plates",
    "recolors": "solo posters recolored to team accent",
    "personas": "the published portrait per manager -- feeds hero, page hero "
                "and avatar",
    "matchups": "two half-cards composited into one bill",
    "editorial": "column and editorial art. GROUP-AGNOSTIC, like objects -- "
                 "the SVP column runs in every group off one asset",
    "source": "raw camera input. never published, kept to re-treat from",
    "vault": "finished work with no slot yet, or still under review",
    "rejects": "disqualified in review -- kept, never deleted",
    "duplicates": "redundant copies, parked under their original path",
    "unsorted": "filename did not parse -- kept here rather than dropped",
}

# How each category buckets into headed groups. Empty tuple = one unheaded
# group for the whole section.
GROUP_KEYS = {
    "posters": ("setup", "lead"),
    "banners": ("style",),
    "scenes": ("setup", "lead"),
    "objects": ("lead",),
    "halfcards": ("lead", "setup"),
    "recolors": ("lead",),
    "personas": ("lead", "setup"),
    "matchups": ("lead",),
    "editorial": ("setup",),
    "source": ("setup",),
    "vault": ("setup",),
    "rejects": ("setup",),
    "duplicates": (),
    "unsorted": (),
}


# ---------------------------------------------------------------- vocabulary

def load_managers():
    try:
        data = json.loads(PERSONAS_JSON.read_text(encoding="utf-8"))
        return set(data.get("managers", {}))
    except Exception:
        return set()


def load_styles():
    """STYLE_SET keys, read out of the generator source rather than imported --
    importing generate_scenes pulls in dotenv and the billed image client."""
    try:
        src = GENERATOR.read_text(encoding="utf-8")
        block = re.search(r"^STYLE_SET\s*=\s*\{(.*?)^\}", src, re.S | re.M)
        if not block:
            return set()
        return set(re.findall(r'^\s*"([a-z0-9_-]+)":', block.group(1), re.M))
    except Exception:
        return set()


MANAGERS = load_managers()
STYLES = load_styles()
BATCH_SLUGS = sorted({e["slug"] for e in BATCH}, key=len, reverse=True)


# ------------------------------------------------------------------- parsing

VARIANT_RE = re.compile(r"^\d{2}$")


def split_variant(tokens):
    if tokens and VARIANT_RE.match(tokens[-1]):
        return tokens[:-1], tokens[-1]
    return tokens, None


def match_batch_slug(joined):
    """Longest known composition slug that this name starts with."""
    for slug in BATCH_SLUGS:
        if joined == slug:
            return slug, []
        if joined.startswith(slug + "_"):
            return slug, joined[len(slug) + 1:].split("_")
    return None, None


def parse(category, parts, stem):
    """-> dict(setup, lead, style, variant), or None if the name won't parse."""
    tokens = stem.split("_")

    if category in ("posters", "banners", "scenes", "halfcards", "matchups"):
        # The group prefix is read off the DIRECTORY, not hardcoded. This used
        # to demand tokens[0] == "panel", which was true only because panel was
        # the only group with art: the first file dropped in banners/church/
        # fell straight to "unsorted" with nothing wrong with it.
        if len(tokens) < 2 or tokens[0] != parts[1]:
            return None
        tokens = tokens[1:]

    if category == "posters":
        # panel_<fight|matchup>_<pair>_<nn>
        tokens, variant = split_variant(tokens)
        if not tokens or tokens[0] not in ("fight", "matchup"):
            return None
        pair = "_".join(tokens[1:])
        return dict(setup=tokens[0], lead=pair or None, style=None,
                    variant=variant)

    if category == "banners":
        # panel_<style>_<nn>
        tokens, variant = split_variant(tokens)
        if not tokens:
            return None
        return dict(setup=None, lead=None, style="_".join(tokens),
                    variant=variant)

    if category == "halfcards":
        # panel_half_<mid>_<facing>_<style>_<nn>
        tokens, variant = split_variant(tokens)
        if len(tokens) < 4 or tokens[0] != "half":
            return None
        return dict(setup=tokens[2], lead=tokens[1], style="_".join(tokens[3:]),
                    variant=variant)

    if category == "scenes":
        # panel_<setup>[_<lead>][_<style>]_<nn>
        tokens, variant = split_variant(tokens)
        style = None
        if tokens and tokens[-1] in STYLES:
            style, tokens = tokens[-1], tokens[:-1]
        if not tokens:
            return None
        slug, rest = match_batch_slug("_".join(tokens))
        if slug is not None:
            lead = "_".join(rest) if rest else None
        elif len(tokens) > 1 and tokens[-1] in MANAGERS:
            slug, lead = "_".join(tokens[:-1]), tokens[-1]
        else:
            slug, lead = "_".join(tokens), None
        return dict(setup=slug, lead=lead, style=style, variant=variant)

    if category == "matchups":
        # <group>_vs_<a>_<b>_<style>_<seam>, built by composite_halfcards.py.
        # Never had a rule here, so all 18 sat in "unsorted" from day one.
        if len(tokens) < 5 or tokens[0] != "vs":
            return None
        return dict(setup="vs", lead="_".join(tokens[1:-2]),
                    style="_".join(tokens[-2:]), variant=None)

    if category == "editorial":
        # <subject>_<treatment>_<nn>. No group token: this art is shared by
        # every group, so scoping it to one would be a lie about who owns it.
        tokens, variant = split_variant(tokens)
        if len(tokens) < 2:
            return None
        return dict(setup=tokens[0], lead=None, style="_".join(tokens[1:]),
                    variant=variant)

    if category == "objects":
        # trophy_<angle>_<nn>
        tokens, variant = split_variant(tokens)
        if len(tokens) < 2 or tokens[0] != "trophy":
            return None
        return dict(setup="trophy", lead="_".join(tokens[1:]), style=None,
                    variant=variant)

    if category == "recolors":
        # <mid>_<garment>_<provider>
        if len(tokens) < 3 or tokens[0] not in MANAGERS:
            return None
        return dict(setup="recolor", lead=tokens[0], style=tokens[1],
                    variant=None)

    if category == "personas":
        # <owner>_<persona>_<provider>_<nn>
        tokens, variant = split_variant(tokens)
        if len(tokens) < 3 or len(parts) < 2 or tokens[0] != parts[1].lower():
            return None
        return dict(setup="_".join(tokens[1:-1]), lead=tokens[0],
                    style=tokens[-1], variant=variant)

    return None


def classify(rel):
    """-> (category, archived, meta). Falls back to unsorted, never drops."""
    parts = list(rel.parts)
    archived = bool(parts) and parts[0] == "archive"
    if archived:
        parts = parts[1:]
    if len(parts) < 2:
        return "unsorted", archived, {}

    # Live recolors sit under personas/recolor/<mid>/ but the archived ones
    # under archive/recolor/<mid>/ -- drop the personas/ prefix so both reduce
    # to the same [recolor, <mid>, <file>] shape.
    if parts[0] == "personas" and parts[1] == "recolor":
        parts = parts[1:]

    top = parts[0]
    # _source/ and _vault/ are PARKING, not candidate art: raw camera input on
    # one side, finished work with no slot yet on the other. Neither follows a
    # filename convention and neither should be forced to -- they get a section
    # keyed on the group directory so they stay browsable, and "unsorted" goes
    # back to meaning "this is misfiled", which is the only way it is useful.
    if top in ("_source", "_vault", "duplicates", "portraits"):
        name = {"duplicates": "duplicates", "portraits": "rejects"}.get(
            top, top[1:])
        return name, archived, dict(setup=parts[1] if len(parts) > 2 else None,
                                    lead=None, style=None, variant=None)
    if top in ("posters", "banners", "scenes", "objects", "halfcards",
               "matchups", "editorial"):
        category = top
    elif top == "recolor":
        category = "recolors"
    elif top == "personas":
        category = "personas"
    else:
        return "unsorted", archived, {}

    meta = parse(category, parts, rel.stem)
    if meta is None:
        return "unsorted", archived, {}
    return category, archived, meta


# ---------------------------------------------------------------- thumbnails

def thumbnail(path, width, quality):
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w > width:
            im = im.resize((width, max(1, round(h * width / w))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=quality, method=5)
        return base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------- build

def collect(only=None):
    """Sweep output/ into tile records.

    `only` is a CONVENIENCE that narrows the sweep to one output/-relative
    prefix. It is applied AFTER the real-people guard and can only ever remove
    more, never add back -- passing only="personas/" returns nothing.
    """
    items = []
    excluded = 0
    for path in sorted(OUTPUT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            continue
        rel = path.relative_to(OUTPUT)
        # Applied HERE, in collect(), so every caller inherits it -- the sheet
        # builder, any future consumer, and a bare collect() in a REPL.
        if is_excluded(rel):
            excluded += 1
            continue
        if only and not rel.as_posix().startswith(only):
            continue
        category, archived, meta = classify(rel)
        items.append(dict(
            path=path,
            rel=rel.as_posix(),
            name=path.name,
            category=category,
            archived=archived,
            setup=meta.get("setup"),
            lead=meta.get("lead"),
            style=meta.get("style"),
            variant=meta.get("variant"),
        ))
    if excluded:
        # Say it out loud. A silent exclusion reads as "there was nothing
        # there", which is the one thing it must never be mistaken for.
        print(f"  excluded {excluded} file(s) under output/personas/ "
              f"(real-people guard; not overridable)")
    return items


def group_of(item, keys):
    return tuple(item.get(k) or "" for k in keys)


def group_label(keys, values):
    bits = [v for v in values if v]
    return " / ".join(bits) if bits else None


def tile_html(item):
    badge = '<span class="badge">archived</span>' if item["archived"] else ""
    chips = "".join(
        '<span class="chip">' + escape(v) + '</span>'
        for v in (item["style"], item["variant"]) if v)
    return (
        '<figure class="tile" data-path="' + escape(item["rel"]) + '"'
        ' data-cat="' + escape(item["category"]) + '"'
        ' data-style="' + escape(item["style"] or "") + '">'
        '<div class="frame">'
        '<img loading="lazy" src="data:image/webp;base64,' + item["b64"] + '"'
        ' alt="' + escape(item["name"]) + '">'
        + badge +
        '</div>'
        '<figcaption>'
        '<div class="markrow">'
        '<span class="mark">'
        '<button class="mk" data-set="keep" title="Keep (K)">K</button>'
        '<button class="mk" data-set="chuck" title="Chuck (X)">X</button>'
        '<button class="mk" data-set="" title="Clear (U)">&ndash;</button>'
        '</span>'
        '<span class="chips">' + chips + '</span>'
        '</div>'
        '<code>' + escape(item["name"]) + '</code>'
        '</figcaption>'
        '</figure>')


def section_html(cat, rows):
    keys = GROUP_KEYS.get(cat, ())
    rows.sort(key=lambda i: (group_of(i, keys), i["style"] or "",
                             i["archived"], i["variant"] or "", i["rel"]))
    blocks, current, buf = [], None, []

    def flush(values):
        if not buf:
            return
        label = group_label(keys, values) if keys else None
        head = ('<h3 class="group">' + escape(label) +
                '<span class="gcount">' + str(len(buf)) + '</span></h3>'
                if label else "")
        blocks.append(head + '<div class="grid">'
                      + "".join(tile_html(i) for i in buf) + '</div>')

    for item in rows:
        values = group_of(item, keys)
        if current is None or values != current:
            flush(current or ())
            current, buf = values, []
        buf.append(item)
    flush(current or ())

    return ('<section class="cat" id="cat-' + escape(cat) + '"'
            ' data-cat="' + escape(cat) + '">'
            '<h2>' + escape(cat) + '<span class="n">' + str(len(rows)) +
            '</span><span class="blurb">' +
            escape(CATEGORY_BLURB.get(cat, "")) + '</span></h2>'
            + "".join(blocks) + '</section>')


def ordered_categories(items):
    present = {i["category"] for i in items}
    cats = [c for c in CATEGORY_ORDER if c in present]
    extra = sorted(present - set(CATEGORY_ORDER))
    if extra:
        at = cats.index("unsorted") if "unsorted" in cats else len(cats)
        cats[at:at] = extra
    return cats


def build_html(items):
    cats = ordered_categories(items)
    sections = "".join(
        section_html(c, [i for i in items if i["category"] == c]) for c in cats)

    nav = "".join(
        '<a href="#cat-' + escape(c) + '">' + escape(c) + '</a>' for c in cats)
    cat_filters = "".join(
        '<label class="f"><input type="checkbox" class="fcat" value="'
        + escape(c) + '" checked>' + escape(c) + '</label>' for c in cats)
    styles = sorted({i["style"] for i in items if i["style"]})
    style_filters = "".join(
        '<label class="f"><input type="checkbox" class="fstyle" value="'
        + escape(s) + '" checked>' + escape(s) + '</label>' for s in styles)
    if not styles:
        style_filters = '<span class="muted">none</span>'

    return (TEMPLATE
            .replace("__TOTAL__", str(len(items)))
            .replace("__NAV__", nav)
            .replace("__CATFILTERS__", cat_filters)
            .replace("__STYLEFILTERS__", style_filters)
            .replace("__SECTIONS__", sections))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CF Challenge — selection sheet</title>
<style>
  :root{
    --bg:#0e1013; --panel:#171b21; --line:#262c35; --ink:#e8ecf1;
    --dim:#8b95a3; --keep:#4ade80; --chuck:#f87171; --accent:#f0b429;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
  code{font:12px/1.3 ui-monospace,"Cascadia Mono",Consolas,monospace}
  header{position:sticky;top:0;z-index:50;background:rgba(14,16,19,.97);
         border-bottom:1px solid var(--line);backdrop-filter:blur(6px)}
  .bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
       padding:10px 16px}
  .title{font-weight:700;letter-spacing:.14em;text-transform:uppercase;
         font-size:12px;color:var(--dim)}
  .count{font-size:13px;font-variant-numeric:tabular-nums;color:var(--dim);
         display:flex;gap:12px;align-items:baseline}
  .count b{font-size:19px;font-weight:700}
  .count .k b{color:var(--keep)}
  .count .x b{color:var(--chuck)}
  .count .u b{color:var(--ink)}
  select{background:var(--panel);color:var(--ink);border:1px solid var(--line);
         border-radius:6px;padding:5px 8px;font-size:12px}
  button{background:var(--panel);color:var(--ink);border:1px solid var(--line);
         border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}
  button:hover{border-color:var(--dim)}
  button.primary{background:var(--keep);color:#08130c;border-color:var(--keep);
                 font-weight:600}
  button.danger{background:var(--chuck);color:#1c0707;border-color:var(--chuck);
                font-weight:600}
  .filters{display:flex;gap:14px;flex-wrap:wrap;padding:0 16px 10px;
           border-top:1px solid var(--line);padding-top:9px}
  .frow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .flabel{font-size:10px;letter-spacing:.12em;text-transform:uppercase;
          color:var(--dim);min-width:52px}
  label.f{display:inline-flex;align-items:center;gap:4px;font-size:12px;
          color:var(--dim);cursor:pointer}
  label.f input{margin:0}
  nav{display:flex;gap:10px;flex-wrap:wrap;padding:0 16px 10px}
  nav a{color:var(--dim);font-size:12px;text-decoration:none;
        text-transform:uppercase;letter-spacing:.08em}
  nav a:hover{color:var(--accent)}
  main{padding:0 16px 120px}
  section.cat{margin:34px 0 0}
  h2{font-size:16px;text-transform:uppercase;letter-spacing:.14em;margin:0 0 4px;
     display:flex;align-items:baseline;gap:10px;
     border-bottom:1px solid var(--line);padding-bottom:8px}
  h2 .n{font-size:12px;color:var(--accent);font-variant-numeric:tabular-nums}
  h2 .blurb{font-size:11px;color:var(--dim);text-transform:none;
            letter-spacing:0;font-weight:400}
  h3.group{font-size:12px;color:var(--dim);letter-spacing:.1em;
           text-transform:uppercase;margin:20px 0 8px;display:flex;
           align-items:center;gap:8px}
  h3.group .gcount{color:#5a6472;font-size:11px}
  .grid{display:grid;gap:14px;
        grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}
  figure.tile{margin:0;background:var(--panel);border:1px solid var(--line);
              border-radius:8px;overflow:hidden;scroll-margin:200px 0 40px}
  .frame{padding:7px;cursor:pointer;position:relative;background:#10131800}
  .frame img{display:block;width:100%;height:auto;border-radius:3px;
             cursor:zoom-in;background:#0a0c0f;transition:opacity .12s,filter .12s}
  figure.tile[data-state="keep"]{border-color:var(--keep);
                                 box-shadow:0 0 0 1px var(--keep)}
  figure.tile[data-state="keep"] .frame{background:rgba(74,222,128,.14)}
  figure.tile[data-state="chuck"]{border-color:var(--chuck);
                                  box-shadow:0 0 0 1px var(--chuck)}
  figure.tile[data-state="chuck"] .frame{background:rgba(248,113,113,.09)}
  figure.tile[data-state="chuck"] img{opacity:.28;filter:grayscale(.75)}
  figure.tile.focused{outline:2px dashed var(--accent);outline-offset:3px}
  .badge{position:absolute;top:12px;left:12px;background:rgba(0,0,0,.72);
         color:var(--accent);font-size:9px;letter-spacing:.1em;
         text-transform:uppercase;padding:2px 6px;border-radius:3px}
  figcaption{padding:4px 9px 9px;display:flex;flex-direction:column;gap:5px}
  figcaption code{word-break:break-all}
  .markrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .mark{display:inline-flex;flex:none}
  button.mk{font:11px/1 ui-monospace,monospace;padding:4px 8px;border-radius:0;
            border:1px solid var(--line);background:#11151a;color:var(--dim)}
  button.mk:first-child{border-radius:5px 0 0 5px}
  button.mk:last-child{border-radius:0 5px 5px 0}
  button.mk + button.mk{border-left:0}
  button.mk.on[data-set="keep"]{background:var(--keep);color:#08130c;
                                border-color:var(--keep);font-weight:700}
  button.mk.on[data-set="chuck"]{background:var(--chuck);color:#1c0707;
                                 border-color:var(--chuck);font-weight:700}
  .chips{display:flex;gap:5px;flex-wrap:wrap}
  .chip{font:10px/1 ui-monospace,monospace;color:var(--dim);
        border:1px solid var(--line);border-radius:3px;padding:3px 5px}
  .muted{color:var(--dim);font-size:12px}
  .hidden{display:none !important}
  #warn{background:#4a1d1d;color:#ffd7d7;font-size:12px;padding:7px 16px;
        border-bottom:1px solid #6b2a2a}
  #lb{position:fixed;inset:0;z-index:100;background:rgba(8,9,11,.96);
      display:none;align-items:center;justify-content:center;flex-direction:column}
  #lb.on{display:flex}
  #lb img{max-width:94vw;max-height:82vh;object-fit:contain}
  #lbbar{display:flex;align-items:center;gap:14px;padding:12px}
  #lbname{font:12px ui-monospace,monospace;color:var(--dim)}
  #lb .nav{position:absolute;top:0;bottom:0;width:14vw;cursor:pointer;
           border:0;background:none;color:#5a6472;font-size:34px;opacity:.35}
  #lb .nav:hover{opacity:1}
  #lbprev{left:0} #lbnext{right:0}
  footer{padding:24px 16px;color:var(--dim);font-size:11px}
</style>
</head><body>

<header>
  <div id="warn" class="hidden">This browser is refusing localStorage on a
    local file, so selections will <b>not</b> survive a reload — finish in one
    sitting and download selection.json before you close the tab.</div>
  <div class="bar">
    <span class="title">CF Challenge · selection sheet</span>
    <span class="count">
      <span class="k"><b id="nkeep">0</b> kept</span>
      <span class="x"><b id="nchuck">0</b> chucked</span>
      <span class="u"><b id="nleft">__TOTAL__</b> unreviewed</span>
    </span>
    <button class="primary" id="copykeep">Copy KEEP list</button>
    <button class="danger" id="copychuck">Copy CHUCK list</button>
    <button id="dl">Download selection.json</button>
    <button id="clear">Clear all</button>
  </div>
  <div class="filters">
    <div class="frow"><span class="flabel">category</span>__CATFILTERS__</div>
    <div class="frow"><span class="flabel">style</span>__STYLEFILTERS__</div>
    <div class="frow"><span class="flabel">show</span>
      <select id="showfilter">
        <option value="all">everything</option>
        <option value="unreviewed">only unreviewed</option>
        <option value="keep">only kept</option>
        <option value="chuck">only chucked</option>
      </select>
      <span class="muted">click the frame to cycle unmarked → keep → chuck ·
        click the image to enlarge · keys: ←↑↓→ move, K keep, X chuck,
        U clear, Enter enlarge, Esc close</span>
    </div>
  </div>
  <nav>__NAV__</nav>
</header>

<main>__SECTIONS__</main>

<div id="lb">
  <button class="nav" id="lbprev">‹</button>
  <img id="lbimg" alt="">
  <div id="lbbar">
    <span id="lbname"></span>
    <button class="primary" id="lbkeep">Keep (K)</button>
    <button class="danger" id="lbchuck">Chuck (X)</button>
    <button id="lbclear">Clear (U)</button>
    <button id="lbclose">Close (Esc)</button>
  </div>
  <button class="nav" id="lbnext">›</button>
</div>

<footer>Static file. Nothing here writes to disk or calls out — selections live
in this browser's localStorage. Copy or download the list and hand it back.</footer>

<script>
(function(){
  var KEY = "cf-selection-v2", OLD = "cf-selection-v1";
  var tiles = Array.prototype.slice.call(document.querySelectorAll(".tile"));
  var TOTAL = tiles.length;
  var state = {}, canStore = true, focusIdx = -1;

  try {
    localStorage.setItem(KEY + "-probe", "1");
    localStorage.removeItem(KEY + "-probe");
    state = JSON.parse(localStorage.getItem(KEY) || "null") || migrate();
  } catch(e) { canStore = false; state = {}; }
  if (!canStore) document.getElementById("warn").classList.remove("hidden");

  // v1 stored a binary keep flag; anything it marked was a keep.
  function migrate(){
    var out = {};
    try {
      var old = JSON.parse(localStorage.getItem(OLD) || "{}");
      for (var k in old) if (old[k]) out[k] = "keep";
    } catch(e){}
    return out;
  }

  function save(){
    if (!canStore) return;
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch(e){}
  }

  function pathsFor(mark){
    return tiles.filter(function(t){ return state[t.dataset.path] === mark; })
                .map(function(t){ return "output/" + t.dataset.path; });
  }

  function paint(t){
    var s = state[t.dataset.path] || "";
    if (s) { t.dataset.state = s; } else { delete t.dataset.state; }
    t.querySelectorAll(".mk").forEach(function(b){
      b.classList.toggle("on", b.dataset.set === s && s !== "");
    });
  }

  function refresh(){
    var k = pathsFor("keep").length, x = pathsFor("chuck").length;
    document.getElementById("nkeep").textContent = k;
    document.getElementById("nchuck").textContent = x;
    document.getElementById("nleft").textContent = TOTAL - k - x;
    applyFilters();
  }

  function setMark(t, mark){
    if (mark) { state[t.dataset.path] = mark; } else { delete state[t.dataset.path]; }
    paint(t); save(); refresh();
    if (lb.classList.contains("on") && cur === t) paintLb();
  }

  // unmarked -> keep -> chuck -> unmarked
  function cycle(t){
    var s = state[t.dataset.path] || "";
    setMark(t, s === "" ? "keep" : (s === "keep" ? "chuck" : ""));
  }

  tiles.forEach(function(t, i){
    paint(t);
    t.querySelector(".frame").addEventListener("click", function(e){
      setFocus(i);
      if (e.target.tagName === "IMG") { openLb(t); return; }
      cycle(t);
    });
    t.querySelectorAll(".mk").forEach(function(b){
      b.addEventListener("click", function(){ setFocus(i); setMark(t, b.dataset.set); });
    });
  });

  // ---- focus ring ----------------------------------------------------
  function visible(){
    return tiles.filter(function(t){ return !t.classList.contains("hidden"); });
  }
  function setFocus(i){
    if (focusIdx >= 0 && tiles[focusIdx]) tiles[focusIdx].classList.remove("focused");
    focusIdx = i;
    if (focusIdx >= 0 && tiles[focusIdx]) tiles[focusIdx].classList.add("focused");
  }
  function focused(){ return focusIdx >= 0 ? tiles[focusIdx] : null; }
  function moveFocus(d){
    var vis = visible();
    if (!vis.length) return;
    var t = focused();
    var i = t ? vis.indexOf(t) : -1;
    var n = vis[i < 0 ? 0 : Math.min(vis.length - 1, Math.max(0, i + d))];
    setFocus(tiles.indexOf(n));
    n.scrollIntoView({block: "nearest"});
  }
  // Row-wise movement: nearest tile on the next distinct row, by x-position.
  function moveRow(dir){
    var vis = visible();
    if (!vis.length) return;
    var t = focused();
    if (!t) { setFocus(tiles.indexOf(vis[0])); vis[0].scrollIntoView({block:"nearest"}); return; }
    var r = t.getBoundingClientRect(), best = null, bestDx = Infinity, bestDy = Infinity;
    vis.forEach(function(o){
      if (o === t) return;
      var ro = o.getBoundingClientRect();
      var dy = ro.top - r.top;
      if (dir > 0 ? dy < 6 : dy > -6) return;          // must be a later/earlier row
      var ady = Math.abs(dy), adx = Math.abs(ro.left - r.left);
      if (ady < bestDy - 6 || (Math.abs(ady - bestDy) <= 6 && adx < bestDx)) {
        best = o; bestDy = ady; bestDx = adx;
      }
    });
    if (best) { setFocus(tiles.indexOf(best)); best.scrollIntoView({block: "nearest"}); }
  }

  // ---- filters -------------------------------------------------------
  function checkedValues(sel){
    var out = {};
    document.querySelectorAll(sel).forEach(function(c){ if (c.checked) out[c.value] = 1; });
    return out;
  }
  function applyFilters(){
    var cats = checkedValues(".fcat");
    var sty = checkedValues(".fstyle");
    var show = document.getElementById("showfilter").value;
    tiles.forEach(function(t){
      var s = t.dataset.style, mark = state[t.dataset.path] || "";
      var showOk = show === "all"
                || (show === "unreviewed" ? mark === "" : mark === show);
      var ok = !!cats[t.dataset.cat] && (s === "" || !!sty[s]) && showOk;
      t.classList.toggle("hidden", !ok);
    });
    document.querySelectorAll(".grid").forEach(function(g){
      var any = g.querySelector(".tile:not(.hidden)");
      g.classList.toggle("hidden", !any);
      var h = g.previousElementSibling;
      if (h && h.classList.contains("group")) h.classList.toggle("hidden", !any);
    });
    document.querySelectorAll("section.cat").forEach(function(s){
      s.classList.toggle("hidden", !s.querySelector(".tile:not(.hidden)"));
    });
    var t = focused();
    if (t && t.classList.contains("hidden")) setFocus(-1);
  }
  document.querySelectorAll(".fcat,.fstyle").forEach(function(c){
    c.addEventListener("change", applyFilters);
  });
  document.getElementById("showfilter").addEventListener("change", applyFilters);

  // ---- handoff -------------------------------------------------------
  function flash(btn, msg){
    var old = btn.textContent; btn.textContent = msg;
    setTimeout(function(){ btn.textContent = old; }, 1200);
  }
  function copy(btn, text){
    function fallback(){
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); flash(btn, "Copied"); }
      catch(e){ flash(btn, "Copy failed"); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function(){ flash(btn, "Copied"); }, fallback);
    } else { fallback(); }
  }
  document.getElementById("copykeep").addEventListener("click", function(){
    copy(this, pathsFor("keep").join("\n"));
  });
  document.getElementById("copychuck").addEventListener("click", function(){
    copy(this, pathsFor("chuck").join("\n"));
  });
  document.getElementById("dl").addEventListener("click", function(){
    var keep = pathsFor("keep"), chuck = pathsFor("chuck");
    var payload = {total: TOTAL, kept: keep.length, chucked: chuck.length,
                   unreviewed: TOTAL - keep.length - chuck.length,
                   keep: keep, chuck: chuck};
    var blob = new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "selection.json";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 2000);
  });
  document.getElementById("clear").addEventListener("click", function(){
    if (!confirm("Clear every mark — keeps and chucks both?")) return;
    state = {}; save(); tiles.forEach(paint); refresh();
  });

  // ---- lightbox ------------------------------------------------------
  var lb = document.getElementById("lb"), lbimg = document.getElementById("lbimg"),
      lbname = document.getElementById("lbname"), cur = null;
  function paintLb(){
    if (!cur) return;
    var s = state[cur.dataset.path] || "";
    lbname.textContent = cur.dataset.path + (s ? "   [" + s.toUpperCase() + "]" : "");
  }
  function openLb(t){
    cur = t;
    var thumb = t.querySelector("img").src;
    // prefer the original beside this file; fall back to the embedded thumb
    lbimg.onerror = function(){ lbimg.onerror = null; lbimg.src = thumb; };
    lbimg.src = t.dataset.path;
    paintLb();
    lb.classList.add("on");
  }
  function closeLb(){ lb.classList.remove("on"); cur = null; }
  function stepLb(d){
    if (!cur) return;
    var vis = visible();
    var i = vis.indexOf(cur);
    if (i < 0) return;
    var n = vis[(i + d + vis.length) % vis.length];
    if (n) { setFocus(tiles.indexOf(n)); openLb(n); }
  }
  document.getElementById("lbclose").addEventListener("click", closeLb);
  document.getElementById("lbprev").addEventListener("click", function(){ stepLb(-1); });
  document.getElementById("lbnext").addEventListener("click", function(){ stepLb(1); });
  document.getElementById("lbkeep").addEventListener("click", function(){ if (cur) setMark(cur, "keep"); });
  document.getElementById("lbchuck").addEventListener("click", function(){ if (cur) setMark(cur, "chuck"); });
  document.getElementById("lbclear").addEventListener("click", function(){ if (cur) setMark(cur, ""); });
  lb.addEventListener("click", function(e){ if (e.target === lb) closeLb(); });

  // ---- keyboard ------------------------------------------------------
  document.addEventListener("keydown", function(e){
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    var open = lb.classList.contains("on");
    var target = open ? cur : focused();
    var k = e.key;

    if (k === "Escape") { if (open) { closeLb(); e.preventDefault(); } return; }
    if (k === "ArrowLeft")  { open ? stepLb(-1) : moveFocus(-1); e.preventDefault(); return; }
    if (k === "ArrowRight") { open ? stepLb(1)  : moveFocus(1);  e.preventDefault(); return; }
    if (k === "ArrowUp")    { open ? stepLb(-1) : moveRow(-1);   e.preventDefault(); return; }
    if (k === "ArrowDown")  { open ? stepLb(1)  : moveRow(1);    e.preventDefault(); return; }
    if (k === "Enter") {
      if (!open && target) { openLb(target); e.preventDefault(); }
      return;
    }
    if (!target) return;
    if (k === "k" || k === "K") { setMark(target, "keep");  e.preventDefault(); }
    else if (k === "x" || k === "X") { setMark(target, "chuck"); e.preventDefault(); }
    else if (k === "u" || k === "U") { setMark(target, "");      e.preventDefault(); }
  });

  refresh();
})();
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=int, default=460,
                    help="embedded thumbnail width in px (default 460)")
    ap.add_argument("--quality", type=int, default=80,
                    help="WEBP quality (default 80)")
    ap.add_argument("--only", default=None, metavar="PREFIX",
                    help="sweep only paths under this output/-relative "
                         "prefix, e.g. scenes/church/. Narrows only: the "
                         "real-people guard applies either way and this "
                         "cannot reach past it.")
    ap.add_argument("--out", default=None, metavar="PATH",
                    help=f"write the sheet here instead of "
                         f"{SHEET.relative_to(ROOT).as_posix()}")
    args = ap.parse_args()

    if not OUTPUT.is_dir():
        print("ERROR: no output/ directory at " + str(OUTPUT), file=sys.stderr)
        return 1

    items = collect(args.only)
    if not items:
        where = f" under output/{args.only}" if args.only else " under output/"
        print(f"ERROR: no images found{where}.", file=sys.stderr)
        return 1

    def encode(item):
        item["b64"] = thumbnail(item["path"], args.width, args.quality)

    with ThreadPoolExecutor() as pool:
        list(pool.map(encode, items))

    sheet = Path(args.out) if args.out else SHEET
    if not sheet.is_absolute():
        sheet = ROOT / args.out
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text(build_html(items), encoding="utf-8")

    counts = {}
    for i in items:
        counts[i["category"]] = counts.get(i["category"], 0) + 1
    for cat in ordered_categories(items):
        arch = sum(1 for i in items
                   if i["category"] == cat and i["archived"])
        note = "  (" + str(arch) + " archived)" if arch else ""
        print(cat.ljust(10) + str(counts[cat]).rjust(4) + note)
    print("total".ljust(10) + str(len(items)).rjust(4))
    print("")
    print("output    " + str(sheet.relative_to(ROOT)).replace("\\", "/"))
    print("size      " + format(sheet.stat().st_size / 1e6, ".1f") + " MB")
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
    except Exception:
        sha = "unknown"
    print("commit    " + sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
