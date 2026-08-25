#!/usr/bin/env python3
"""What art is missing, per group x manager x slot. Read-only, no network.

    python scripts/art_gaps.py [--group browns] [--json]

WHY THIS EXISTS. The site consumes art through four slots, and three of them
are the SAME source picture at different crops:

    one portrait per manager -> profile_hero      (as-is, long edge 1200)
                             -> profile_page_hero (torn cut, build_profile_heroes.py)
                             -> manager_avatar    (square face crop)
    one banner per group     -> hero_banner

So "what do I still need to generate" is not a list anyone should maintain by
hand -- it is derivable. Rosters come from groups/<g>/config.json, sources from
output/personas/<group>/ and output/banners/<group>/, and published derivatives
from docs/. A manager added to config.json shows up here as gaps on the next
run, with no edit to this file.

STATUS VOCABULARY, narrowest to widest:
    LIVE     published under docs/ AND declared in art_slots.json AND some page
             script actually resolves the slot -- visible on the site
    STAGED   published and declared, but NO page reads this slot yet. The art is
             done; the wiring is not. Reporting these as LIVE would be a lying
             green, which is the one thing this report must never produce.
    BUILT    published under docs/ but NOT declared -- a file nobody renders
    READY    source art exists in output/, derivative not built yet -- no
             generation needed, just a crop/convert
    GAP      no source art at all -- this one needs pixels

Only GAP costs an image-generation call. LIVE/BUILT/READY are bookkeeping.
Pair with --skip-if-exists on the generators (CLAUDE.md rule 7) so nothing is
paid for twice.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
DOCS = ROOT / "docs"
SLOTS = DOCS / "assets" / "art_slots.json"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
PER_MANAGER = ("profile_hero", "profile_page_hero", "profile_page_hero_left",
               "manager_avatar")
PER_GROUP = ("hero_banner", "svp_column_art", "editorial_hero")

# Which slots a page script actually calls resolveArt() for. Derived from the
# site source rather than hardcoded, so wiring a slot up flips its status here
# with no edit to this file -- and forgetting to wire one can never read LIVE.
#
# *.html AS WELL AS *.js, and that omission cost this report its accuracy. The
# SVP byline was wired at 9c8299a in an inline <script> in svp.html:183, which
# is where a one-page behaviour belongs; scanning only the .js files meant the
# call was invisible here and all four groups' svp_column_art kept reading
# STAGED -- "art done, but no page reads the slot yet" -- for a slot the page
# reads on every load. A derived status that cannot see half the site source
# is hardcoded with extra steps.
def _consumed_slots():
    found = set()
    for f in sorted([*(ROOT / "docs").glob("*.js"),
                     *(ROOT / "docs").glob("*.html")]):
        src = f.read_text(encoding="utf-8", errors="ignore")
        for slot in PER_MANAGER + PER_GROUP:
            if f"'{slot}'" in src or f'"{slot}"' in src:
                found.add(slot)
    return found


CONSUMED = _consumed_slots()

# Group-agnostic art: one asset, every group. The SVP column runs in all four,
# so its source lives in output/editorial/ rather than under any group.
EDITORIAL = ROOT / "output" / "editorial"
SHARED_SVP = EDITORIAL.is_dir() and any(
    p.stem.startswith("svp_") for p in EDITORIAL.iterdir()
    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})


def gemini_ref_ceiling():
    """Character-reference slots on the default image model.

    Read from gemini_image rather than restated, so a model change moves this
    report with it instead of leaving a stale number behind.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gemini_image
        return gemini_image.ref_limit(gemini_image.DEFAULT_MODEL)
    except Exception:
        return 5


def groups():
    """-> {group_id: [manager_id, ...]}, straight from the configs."""
    out = {}
    for cfg in sorted((ROOT / "groups").glob("*/config.json")):
        d = json.loads(cfg.read_text(encoding="utf-8"))
        out[d["group_id"]] = [m["manager_id"] for m in d.get("managers", [])]
    return out


def declared(slots, group, slot):
    """Does art_slots.json actually declare a candidate for this slot?"""
    spec = slots.get("groups", {}).get(group, {}).get(slot)
    if not isinstance(spec, dict):
        return False
    if spec.get("candidates"):
        return True
    return bool(spec.get("by_id"))


def sources(group, mid):
    """Portrait sources on disk for one manager, newest convention first."""
    d = OUT / "personas" / group
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXT
                  and p.stem.startswith(f"{group}_{mid}_"))


def published(group, mid, slot):
    """Derivative files on disk under docs/ for this slot."""
    if slot == "profile_hero":
        base = DOCS / "assets" / "profiles" / group
        return sorted(base.glob(f"{mid}.webp")) + sorted(base.glob(f"{mid}_*.webp")) \
            if base.is_dir() else []
    if slot == "profile_page_hero":
        base = DOCS / "assets" / "profiles" / group
        if not base.is_dir():
            return []
        # family points this slot at the plain file on purpose: its art is
        # real photographs, which get no painted tear. See
        # build_profile_heroes.py.
        hits = sorted(base.glob(f"{mid}-ripped.webp")) + \
            sorted(base.glob(f"{mid}_*-ripped.webp"))
        return hits or (sorted(base.glob(f"{mid}.webp")) if group == "family" else [])
    if slot == "profile_page_hero_left":
        base = DOCS / "assets" / "profiles" / group
        if not base.is_dir():
            return []
        return sorted(base.glob(f"{mid}-ripped-left.webp")) +             sorted(base.glob(f"{mid}_*-ripped-left.webp"))
    if slot == "manager_avatar":
        # TWO layouts, and the group-scoped one is checked FIRST.
        # docs/img/avatars/<group>/ is where every group except panel writes,
        # because the flat docs/img/avatars/ cannot hold `zach` four times --
        # he is on all four rosters with different art on each. panel stays
        # flat: art_slots.json is documented as safe to 404, and moving
        # panel's files would break the fallback that contract promises.
        scoped = DOCS / "img" / "avatars" / group
        if scoped.is_dir():
            hit = sorted(scoped.glob(f"{mid}_56.webp"))
            if hit:
                return hit
        flat = DOCS / "img" / "avatars"
        return sorted(flat.glob(f"{mid}_56.webp")) if flat.is_dir() else []
    return []


def portrait_right(group, mid):
    """Does the alternation place this manager's section portrait-RIGHT?

    Odd profile_order in the PUBLISHED personas.json -- the same number
    managers.js alternates on, read from the same file the page reads, so this
    report cannot disagree with the page about who is on which side.
    """
    f = DOCS / "data" / group / "personas.json"
    if not f.is_file():
        return False
    try:
        rec = json.loads(f.read_text(encoding="utf-8"))["managers"].get(mid) or {}
    except (ValueError, KeyError):
        return False
    order = rec.get("profile_order")
    return isinstance(order, int) and order % 2 == 1


def status(group, mid, slot, slots):
    # THE ONLY SLOT THAT IS NOT OWED TO EVERYONE, and it is owed to six of the
    # twenty-four. A left-edge cut exists to make the tear open toward the copy
    # on a portrait-RIGHT section. Two separate things make it inapplicable,
    # and both are "-" rather than GAP, or this report would invent a backlog
    # of eighteen files nobody wants and bury the ones that matter:
    #
    #   the section is portrait-LEFT   -- the right-edge cut is already correct
    #   there is no tear to mirror     -- family's art is real photographs and
    #                                     is never torn on either edge, and a
    #                                     manager with no art at all renders
    #                                     DOSSIER, which has no portrait column
    #                                     and therefore no side
    #
    # The second test is for a `-ripped` file specifically, not for "some
    # published page_hero": family's page_hero slot resolves to the PLAIN
    # <id>.webp on purpose, so anything looser reads family as four managers
    # one command away from a cut that must never be made.
    if slot == "profile_page_hero_left":
        if not portrait_right(group, mid):
            return "-"
        base = DOCS / "assets" / "profiles" / group
        torn = (sorted(base.glob(f"{mid}-ripped.webp"))
                + sorted(base.glob(f"{mid}_*-ripped.webp"))) if base.is_dir() else []
        if not torn:
            return "-"
    pub, src = published(group, mid, slot), sources(group, mid)
    if slot == "manager_avatar" and pub and not declared(slots, group, slot):
        pub = []                      # not this group's file -- see published()
    if pub:
        if not declared(slots, group, slot):
            return "BUILT"
        return "LIVE" if slot in CONSUMED else "STAGED"
    if slot == "profile_page_hero_left":
        # Reached only when the manager is portrait-RIGHT and a torn cut
        # exists, so there is always something to mirror: READY, never GAP.
        # --side left is a free, deterministic flip of the file they have.
        return "READY"
    return "READY" if src else "GAP"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", help="limit to one group")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    a = ap.parse_args()

    slots = json.loads(SLOTS.read_text(encoding="utf-8")) if SLOTS.is_file() else {}
    g = groups()
    if a.group:
        if a.group not in g:
            sys.exit(f"ERROR: no group {a.group!r}. Known: {', '.join(sorted(g))}")
        g = {a.group: g[a.group]}

    report, gaps, ready = {}, 0, 0
    for grp, mids in sorted(g.items()):
        rows = {}
        for mid in mids:
            rows[mid] = {s: status(grp, mid, s, slots) for s in PER_MANAGER}
        # group-wide slots
        bdir = OUT / "banners" / grp
        has_banner = bdir.is_dir() and any(
            p.suffix.lower() in IMAGE_EXT for p in bdir.iterdir())
        live_banner = (DOCS / "assets" / "banners" / f"{grp}.webp").is_file()
        if live_banner and declared(slots, grp, "hero_banner"):
            hb = "LIVE" if "hero_banner" in CONSUMED else "STAGED"
        elif live_banner:
            hb = "BUILT"
        else:
            hb = "READY" if has_banner else "GAP"
        gw = {"hero_banner": hb}
        # svp_column_art is SHARED: the column runs in every group off one
        # asset in output/editorial/, so its source is group-agnostic and the
        # only per-group question is whether that group declares the slot.
        for s in PER_GROUP[1:]:
            if not declared(slots, grp, s):
                gw[s] = "READY" if (s == "svp_column_art" and SHARED_SVP) else "GAP"
            else:
                gw[s] = "LIVE" if s in CONSUMED else "STAGED"
        report[grp] = {"managers": rows, "group": gw}
        for r in rows.values():
            gaps += sum(v == "GAP" for v in r.values())
            ready += sum(v == "READY" for v in r.values())
        gaps += sum(v == "GAP" for v in gw.values())
        ready += sum(v == "READY" for v in gw.values())

    if a.json:
        print(json.dumps(report, indent=1))
        return

    for grp, d in report.items():
        print(f"\n=== {grp} " + "=" * (66 - len(grp)))
        print(f"  {'manager':<12} {'profile_hero':<14} {'page_hero':<14} "
              f"{'page_hero_L':<13} avatar")
        for mid, r in d["managers"].items():
            print(f"  {mid:<12} {r['profile_hero']:<14} "
                  f"{r['profile_page_hero']:<14} "
                  f"{r['profile_page_hero_left']:<13} {r['manager_avatar']}")
        print("  " + "-" * 60)
        for s, v in d["group"].items():
            print(f"  {'(group)':<12} {s:<28} {v}")

    print("\n" + "=" * 70)
    # A group whose roster outgrew the character-reference ceiling cannot have
    # an all-hands banner from ONE render, so a `LIVE` banner there is
    # necessarily missing somebody. This is structural, not a guess: the
    # ceiling is a property of the model. family hit it at 8 and was built as
    # two composited comic panels; browns crossed it the moment it went to 7.
    ceiling = gemini_ref_ceiling()
    over = [(g, len(v["managers"])) for g, v in report.items()
            if len(v["managers"]) > ceiling]
    for g, n in over:
        live = report[g]["group"].get("hero_banner") in ("LIVE", "STAGED")
        print(f"\nNOTE: {g} has {n} managers, over the {ceiling}-reference "
              f"ceiling for one render.")
        if live:
            print(f"      Its published banner therefore CANNOT show all {n} "
                  f"-- it is stale by")
            print(f"      construction. Rebuild it as composited panels, the "
                  f"way family's was.")

    staged = sum(v == "STAGED" for d in report.values()
                 for r in (list(d["managers"].values()) + [d["group"]])
                 for v in r.values())
    if staged:
        print(f"STAGED{staged:>4}   art done, but no page reads the slot yet")
    print(f"GAP   {gaps:>3}   needs generation")
    print(f"READY {ready:>3}   source exists, needs only a crop/convert/declare")
    # Shared ids across rosters used to be a live hazard: manager_avatar
    # resolved to the flat img/avatars/{id} for every group, and `zach` is on
    # all four. Now every group except panel is scoped to img/avatars/<group>/,
    # so this is reported as a fact to keep an eye on rather than a warning.
    shared = sorted(m for m in set().union(*(set(v["managers"])
                                             for v in report.values()))
                    if sum(m in v["managers"] for v in report.values()) > 1)
    if shared and len(report) > 1:
        print(f"\nNOTE: {', '.join(shared)} appear(s) on more than one roster.")
        print("      Avatars are group-scoped (img/avatars/<group>/<id>), so "
              "the art may")
        print("      differ per group. panel alone stays flat and cannot "
              "collide with them.")


if __name__ == "__main__":
    main()
