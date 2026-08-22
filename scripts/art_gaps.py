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
    LIVE     published under docs/ AND declared in art_slots.json -- on the site
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
PER_MANAGER = ("profile_hero", "profile_page_hero", "manager_avatar")
PER_GROUP = ("hero_banner", "svp_column_art", "editorial_hero")

# Group-agnostic art: one asset, every group. The SVP column runs in all four,
# so its source lives in output/editorial/ rather than under any group.
EDITORIAL = ROOT / "output" / "editorial"
SHARED_SVP = EDITORIAL.is_dir() and any(
    p.stem.startswith("svp_") for p in EDITORIAL.iterdir()
    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})


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
        # family points this slot at the plain file on purpose (tone gate).
        hits = sorted(base.glob(f"{mid}-ripped.webp")) + \
            sorted(base.glob(f"{mid}_*-ripped.webp"))
        return hits or (sorted(base.glob(f"{mid}.webp")) if group == "family" else [])
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


def status(group, mid, slot, slots):
    pub, src = published(group, mid, slot), sources(group, mid)
    if slot == "manager_avatar" and pub and not declared(slots, group, slot):
        pub = []                      # not this group's file -- see published()
    if pub:
        return "LIVE" if declared(slots, group, slot) else "BUILT"
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
        gw = {"hero_banner": ("LIVE" if live_banner and declared(slots, grp, "hero_banner")
                              else "BUILT" if live_banner
                              else "READY" if has_banner else "GAP")}
        # svp_column_art is SHARED: the column runs in every group off one
        # asset in output/editorial/, so its source is group-agnostic and the
        # only per-group question is whether that group declares the slot.
        gw["svp_column_art"] = ("LIVE" if declared(slots, grp, "svp_column_art")
                                else "READY" if SHARED_SVP else "GAP")
        for s in PER_GROUP[2:]:
            gw[s] = "LIVE" if declared(slots, grp, s) else "GAP"
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
        print(f"  {'manager':<12} {'profile_hero':<14} {'page_hero':<14} avatar")
        for mid, r in d["managers"].items():
            print(f"  {mid:<12} {r['profile_hero']:<14} "
                  f"{r['profile_page_hero']:<14} {r['manager_avatar']}")
        print("  " + "-" * 60)
        for s, v in d["group"].items():
            print(f"  {'(group)':<12} {s:<28} {v}")

    print("\n" + "=" * 70)
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
