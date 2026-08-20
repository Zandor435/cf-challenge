#!/usr/bin/env python3
"""
sync_personas.py -- publish group persona prose to the Pages web root.

WHY THIS EXISTS (the point of truth for the decision):
  groups/<group>/personas.json is SOURCE. It sits beside the config and picks
  it describes, and it carries art-pipeline inputs (traits, silhouette_cue/s)
  that must never reach a browser. But GitHub Pages serves ONLY docs/ -- a
  fetch of ../groups/panel/personas.json from docs/managers.html is a 404 on
  the live site. So the page cannot read the source file directly.

  Three options were on the table: (a) move personas.json into docs/ and let
  groups/ point at it, (b) have the site fetch a raw.githubusercontent URL,
  (c) publish a derived copy into the web root and let CI fail on drift.

  (a) puts hand-authored source in the build-output tree and drags the private
  art-direction fields onto the public site. (b) adds a cross-origin runtime
  dependency on github.com for a file we already ship, and breaks any local
  `python -m http.server` preview of docs/. (c) is what this repo already does
  for the other shared, non-contract site file -- build_canonical.py's
  "publish the spine to the site" step writes docs/data/teams_canonical.json
  exactly this way -- so (c) it is: ONE existing pattern instead of a new one.

  The copy is derived, never hand-edited, and --check (wired into
  .github/workflows/tests.yml) regenerates it in memory and diffs. A stale
  docs/ copy therefore fails CI loudly instead of drifting silently, which is
  the only thing that makes a copy safe.

READS   groups/<group>/config.json      -- the manager roster (join key)
        groups/<group>/personas.json    -- the prose, extended from personas.md
        data/teams_canonical.json       -- team colors, to prove that `color`
                                           is a cache of that lookup and not a
                                           second source of truth
WRITES  docs/data/<group>/personas.json -- OVERWRITE (fully regenerated every
                                           run; never accumulates)

Groups with no personas.json are skipped, not failed. The page 404s cleanly on
the fetch and every manager still gets a real card off standings/projection, so
a league can launch with no prose at all and look finished. Empty is a normal
state here, not a broken one.

TONE, and why it is stripped here as well as gated in the page:
  tone is REQUIRED on every manager and has three registers -- see TONE_POLICY
  below for what each one withholds. Whatever a register withholds is nulled in
  the payload before it ever leaves the repo, and docs/managers.html gates on
  tone independently. Two gates, because the strict register is somebody's
  parents and a single CSS or JS regression must not be able to print a "fatal
  flaw" under someone's father's name.

Playbook compliance (CLAUDE.md):
  - rule 4: an unmapped manager_id -- in EITHER direction -- fails loud and
    names the offender. Silently dropping a manager is how a persona goes
    missing from the site with a green build.
  - rule 5: overwrite-by-default, but every write is a full regeneration from
    committed source; there is nothing here to clobber.
  - rule 7: no network, no paid API. Safe to run on every push.

Usage:
    python scripts/sync_personas.py             # write every group
    python scripts/sync_personas.py --group panel
    python scripts/sync_personas.py --check     # CI: fail if docs/ is stale
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GROUPS_DIR = ROOT / "groups"
WEB_DATA_DIR = ROOT / "docs" / "data"
CANONICAL = ROOT / "data" / "teams_canonical.json"

# TONE, and what each register withholds. The value is the tuple of fields that
# are nulled before publishing.
#
#   roast     nothing withheld. Panel and Browns: rooms where everyone has
#             agreed, over decades, to be a target.
#   warm      a fatal flaw is withheld, a running gag and a rival are not.
#             CEC is the case this exists for: it is warm-register -- jokes aim
#             at picks, not people -- yet every manager has an affectionate
#             gag authored and there is one real rivalry in the group. Under a
#             two-value gate that content had to be either published with a
#             "fatal flaw" beside it or thrown away, and neither is right.
#   straight  everything personal withheld. Family's John, Rachel and Vic --
#             somebody's parents, somebody's uncle. The strict register stays
#             strict; `warm` exists so that nothing needs to be loosened here
#             to make another group render.
TONE_POLICY = {
    "roast":    (),
    "warm":     ("fatal_flaw",),
    "straight": ("fatal_flaw", "running_gag", "rival"),
}
VALID_TONES = tuple(TONE_POLICY)

# The site contract. Every one of these keys is present on every published
# manager -- null when unauthored, NEVER absent and NEVER a "TODO" string, so
# the page tests one thing (falsy) instead of three.
SITE_FIELDS = (
    "tone",
    "display_name",
    "epithet",
    "tagline",
    "backstory",
    "running_gag",
    "draft_tendency",
    "fatal_flaw",
    "rival",
    "color",
)
# Deliberately NOT published: traits, silhouette_cue, silhouette_cues,
# _phase2_finding. Those are Gemini prompt inputs -- internal art direction
# with no surface on the page. Keeping them out of docs/ is the whole reason
# this is a projection and not a file copy.


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def team_colors():
    """{school: color} from the canonical spine."""
    doc = load_json(CANONICAL)
    return {t["school"]: t.get("color") for t in doc.get("teams", [])}


def build_payload(group_id, config, personas, colors):
    """Reconcile config <-> personas and project to the site shape.

    Fails loud on ANY mismatch. Returns the dict to publish.
    """
    cfg_ids = [m["manager_id"] for m in config.get("managers", [])]
    cfg_names = {m["manager_id"]: m.get("display_name") for m in config.get("managers", [])}
    per = personas.get("managers", {})

    missing = [i for i in cfg_ids if i not in per]
    extra = [i for i in per if i not in cfg_ids]
    if missing:
        sys.exit(
            "FAIL [{g}]: manager(s) in config.json with NO persona entry: {ids}. "
            "Add them to groups/{g}/personas.json (tone is required) or remove "
            "them from the config.".format(g=group_id, ids=", ".join(sorted(missing)))
        )
    if extra:
        sys.exit(
            "FAIL [{g}]: persona entr(ies) for manager_id(s) NOT in config.json: {ids}. "
            "A persona with no roster slot never renders -- fix the id, or add the "
            "manager to groups/{g}/config.json.".format(g=group_id, ids=", ".join(sorted(extra)))
        )

    out = {}
    for mid in cfg_ids:
        src = per[mid]

        tone = src.get("tone")
        if tone not in VALID_TONES:
            sys.exit(
                "FAIL [{g}/{m}]: tone is {t!r}; it is REQUIRED and must be one of "
                "{v}. tone decides whether this person gets a 'fatal flaw' printed "
                "under their name -- there is no safe default.".format(
                    g=group_id, m=mid, t=tone, v=VALID_TONES)
            )

        name = src.get("display_name")
        if name != cfg_names[mid]:
            sys.exit(
                "FAIL [{g}/{m}]: display_name disagrees -- config.json says {c!r}, "
                "personas.json says {p!r}. One person, one name.".format(
                    g=group_id, m=mid, c=cfg_names[mid], p=name)
            )

        # `color` is a cache of the canonical team color, not a second truth.
        team = src.get("team")
        color = src.get("color")
        if team and team in colors and color and color.lower() != (colors[team] or "").lower():
            sys.exit(
                "FAIL [{g}/{m}]: color {c} does not match teams_canonical.json for "
                "{t!r} ({k}). personas.json caches that lookup; it must not diverge "
                "from it.".format(g=group_id, m=mid, c=color, t=team, k=colors[team])
            )

        rec = {k: src.get(k, None) for k in SITE_FIELDS}
        for k in TONE_POLICY[tone]:
            rec[k] = None

        rival = rec.get("rival")
        if rival is not None and rival not in cfg_ids:
            sys.exit(
                "FAIL [{g}/{m}]: rival {r!r} is not a manager_id in this group. The "
                "page links #{r} on itself -- an unknown id is a dead anchor.".format(
                    g=group_id, m=mid, r=rival)
            )

        out[mid] = rec

    return {
        "_note": [
            "GENERATED -- do not edit. Written by scripts/sync_personas.py from",
            "groups/{g}/personas.json (source) + groups/{g}/config.json.".format(g=group_id),
            "Edit the source and re-run the script; --check runs in CI and fails on drift.",
            "Art-pipeline fields (traits, silhouette_cue/s) are deliberately NOT published.",
            "tone withholds fields per register: straight nulls fatal_flaw,",
            "running_gag and rival; warm nulls fatal_flaw; roast nulls nothing.",
        ],
        "$version": 1,
        "group_id": group_id,
        "managers": out,
    }


def render(payload):
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


def groups_with_personas(only=None):
    for d in sorted(GROUPS_DIR.iterdir()):
        if only and d.name != only:
            continue
        if (d / "personas.json").exists() and (d / "config.json").exists():
            yield d.name


def main():
    ap = argparse.ArgumentParser(description="Publish group persona prose into docs/.")
    ap.add_argument("--group", help="one group id (default: every group with a personas.json)")
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the docs/ copy is missing or stale")
    args = ap.parse_args()

    colors = team_colors()
    names = list(groups_with_personas(args.group))
    if args.group and not names:
        sys.exit("FAIL: group {g!r} has no groups/{g}/personas.json".format(g=args.group))
    if not names:
        print("no group declares personas.json -- nothing to sync")
        return 0

    stale = []
    for gid in names:
        cfg = load_json(GROUPS_DIR / gid / "config.json")
        per = load_json(GROUPS_DIR / gid / "personas.json")
        text = render(build_payload(gid, cfg, per, colors))
        dest = WEB_DATA_DIR / gid / "personas.json"

        if args.check:
            have = dest.read_text(encoding="utf-8") if dest.exists() else None
            if have != text:
                state = "missing" if have is None else "stale"
                stale.append(gid)
                print("DRIFT [{g}]: {p} is {s}".format(
                    g=gid, p=dest.relative_to(ROOT).as_posix(), s=state))
            else:
                print("ok    [{g}]: {p} matches source".format(
                    g=gid, p=dest.relative_to(ROOT).as_posix()))
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        print("wrote [{g}]: {p} ({n} managers, {b} bytes)".format(
            g=gid, p=dest.relative_to(ROOT).as_posix(),
            n=len(json.loads(text)["managers"]), b=len(text)))

    if stale:
        sys.exit(
            "\nFAIL: the published persona copy under docs/ does not match "
            "groups/<group>/personas.json.\nRun `python scripts/sync_personas.py` "
            "and commit the result."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
