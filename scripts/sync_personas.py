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

TONE, and what is left of it:
  tone is REQUIRED on every manager and every manager is `roast`. The registers
  -- `warm`, which withheld a fatal flaw, and `straight`, which withheld the
  flaw, the gag and the rival -- were retired on 2026-08-25 along with the
  page-side gate in docs/site.js. Nothing is nulled on the way out any more;
  what a persona authors is what the page publishes. The field is kept, and
  kept required, so the roster cannot drift back to a withholding register
  without someone restoring a real gate to go with it -- see VALID_TONES.

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
import copy
import json
import sys
from pathlib import Path

# The editorial-profile field contract lives beside this file rather than in
# it. See scripts/persona_schema.py for why -- in short, this module reconciles
# the roster and decides what leaves the repo; validating nine structured
# creative fields inline would have buried that.
try:
    from persona_schema import (  # noqa: F401  (run as a script from scripts/)
        PROFILE_SITE_FIELDS,
        PersonaSchemaError,
        validate_manager,
    )
except ImportError:  # imported as scripts.persona_schema by the test suite
    from scripts.persona_schema import (
        PROFILE_SITE_FIELDS,
        PersonaSchemaError,
        validate_manager,
    )

ROOT = Path(__file__).resolve().parent.parent
GROUPS_DIR = ROOT / "groups"
WEB_DATA_DIR = ROOT / "docs" / "data"
CANONICAL = ROOT / "data" / "teams_canonical.json"

# TONE, and what each register withholds. The value is the tuple of fields that
# THE ONLY REGISTER. `warm` (withheld a fatal flaw) and `straight` (withheld
# the flaw, the gag and the rival) were retired on 2026-08-25: family's three
# straight managers and church's five warm ones were flipped to roast and
# authored the blocks they had been withholding, which left the gate with no
# input and made it dead code.
#
# This stays a one-value whitelist rather than becoming a free string on
# purpose. Reintroducing a withholding register means editing this line, and
# whoever does that has to notice there is no longer any code behind it and
# restore a real gate -- in sync_personas.py AND in the page. A silently
# accepted `tone: "straight"` that renders a fatal flaw anyway is precisely
# the failure the registers existed to prevent, and it fails loudly here
# instead (playbook rule 4).
VALID_TONES = ("roast",)

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
) + PROFILE_SITE_FIELDS
# Deliberately NOT published: traits, silhouette_cue, silhouette_cues,
# _phase2_finding, and the editorial-profile creative brief (north_star,
# motifs, easter_eggs -- persona_schema.PRIVATE_FIELDS). Those are image-prompt
# inputs: internal art direction with no surface on the page. Keeping them out
# of docs/ is the whole reason this is a projection and not a file copy.
#
# PROFILE_SITE_FIELDS is appended rather than interleaved so the original ten
# keys keep their published order and the diff of an existing docs/ payload
# stays readable when a profile field is added to one manager.


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def team_colors():
    """{school: color} from the canonical spine."""
    doc = load_json(CANONICAL)
    return {t["school"]: t.get("color") for t in doc.get("teams", [])}


# The columnist block -- the column page's byline, not a manager. Only these
# keys leave the repo; anything else authored alongside them stays source, on
# the same rule that keeps traits and silhouette_cue out of docs/.
COLUMNIST_FIELDS = ("name", "role")


def columnist_payload(group_id, personas):
    """The `columnist` block, or None when the group does not declare one.

    NOT a manager, and that is the whole reason it is a separate function.
    build_payload reconciles `managers` against config.json's roster in both
    directions and exits 1 on an id with no roster slot -- which is right, and
    which the columnist would trip, because he writes about the group rather
    than playing in it. So he lives at the top level of personas.json and is
    projected here.

    ABSENT IS ORDINARY. Three of the four groups declare no columnist today and
    simply publish no block; the byline card then runs the name straight into
    the metadata, exactly as it did before this existed. A block that IS
    declared must carry a non-empty `name` -- a role line under no name is a
    subtitle for nobody -- and that fails loud (playbook rule 4).
    """
    src = personas.get("columnist")
    if src is None:
        return None
    if not isinstance(src, dict):
        sys.exit("FAIL [{g}]: `columnist` is {t}, not an object.".format(
            g=group_id, t=type(src).__name__))

    name = (src.get("name") or "").strip()
    if not name:
        sys.exit(
            "FAIL [{g}]: `columnist` declares no `name`. A role line under no "
            "name is a subtitle for nobody -- give it a name or remove the "
            "block.".format(g=group_id))

    out = {}
    for key in COLUMNIST_FIELDS:
        value = src.get(key)
        # Omitted, not nulled: the page tests presence, and an empty role would
        # render as a blank line between the name and the metadata.
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def build_payload(group_id, config, personas, colors):
    """Reconcile config <-> personas and project to the site shape.

    Fails loud on ANY mismatch. Returns the dict to publish.
    """
    columnist = columnist_payload(group_id, personas)

    cfg_ids = [m["manager_id"] for m in config.get("managers", [])]
    cfg_names = {m["manager_id"]: m.get("display_name") for m in config.get("managers", [])}
    per = personas.get("managers", {})

    # THE PERSONA FILE'S OWN ORDER, published as profile_order.
    #
    # WHY IT IS NOT THE ORDER OF THIS OUTPUT. The published record is keyed in
    # config.json order, and the page renders in STANDINGS order -- neither of
    # which is a position an editor controls. managers.js alternates the
    # sideline layout (portrait left / portrait right) off this number, and the
    # requirement it has to satisfy is that a manager's side never moves
    # because they won or lost a game. So the index is taken from the one list
    # that only ever changes when somebody edits it: the key order of
    # groups/<g>/personas.json.
    #
    # That makes JSON key order load-bearing in this one file. Python preserves
    # it on load and json.dumps preserves it on write, so the round trip is
    # safe -- but a tool that re-serialises personas.json alphabetically would
    # silently reshuffle which managers sit on which side, and would not fail
    # any test. Reorder that file only on purpose.
    persona_order = list(per.keys())

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
                "{v}. The withholding registers were retired and nothing gates on "
                "this any more -- a persona asking for one would be published in "
                "full instead. Restore a real gate before reintroducing one.".format(
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

        # Editorial-profile fields. Absent and null are always fine; present
        # and malformed fails the build and names the offender (playbook rule
        # 4). This runs BEFORE the projection so a bad layout key or a bad hex
        # is reported against the source file the author has to edit.
        try:
            validate_manager(group_id, mid, src)
        except PersonaSchemaError as exc:
            sys.exit(str(exc))

        # deepcopy, not a shared reference. Nothing mutates the nested dicts
        # today -- the tone strip that used to was removed with the register
        # gate -- but --check renders twice in one process, and a projection
        # that ever reaches through into the in-memory source is exactly how
        # the second render disagrees with the first.
        rec = copy.deepcopy({k: src.get(k, None) for k in SITE_FIELDS})

        rival = rec.get("rival")
        if rival is not None and rival not in cfg_ids:
            sys.exit(
                "FAIL [{g}/{m}]: rival {r!r} is not a manager_id in this group. The "
                "page links #{r} on itself -- an unknown id is a dead anchor.".format(
                    g=group_id, m=mid, r=rival)
            )

        # Position in groups/<g>/personas.json -- see persona_order above. Not
        # an authored field: it is derived from the source file's shape, so it
        # is set after the projection rather than listed in SITE_FIELDS.
        rec["profile_order"] = persona_order.index(mid)

        out[mid] = rec

    return {
        "_note": [
            "GENERATED -- do not edit. Written by scripts/sync_personas.py from",
            "groups/{g}/personas.json (source) + groups/{g}/config.json.".format(g=group_id),
            "Edit the source and re-run the script; --check runs in CI and fails on drift.",
            "Art-pipeline fields (traits, silhouette_cue/s) and the editorial-profile",
            "creative brief (north_star, motifs, easter_eggs) are deliberately NOT",
            "published -- they are image-prompt inputs, not page copy.",
            "tone is published as data and withholds nothing: the registers",
            "that nulled fields per tone were retired 2026-08-25 and every",
            "manager is `roast`. What a persona authors is what ships.",
            "profile_order is DERIVED, not authored: it is each manager's index",
            "in groups/{g}/personas.json, and managers.js alternates the sideline".format(g=group_id),
            "layout off it so a manager's side cannot move when their rank does.",
            "columnist is the COLUMN'S byline, not a manager -- static copy,",
            "published only when groups/{g}/personas.json declares it.".format(g=group_id),
        ],
        "$version": 1,
        "group_id": group_id,
        # Omitted entirely for a group that declares no columnist -- see
        # columnist_payload. Placed before `managers` because it describes the
        # file's one non-roster subject and reads better at the top; nothing
        # depends on key order here (unlike groups/<g>/personas.json, where it
        # is load-bearing -- see persona_order above).
        **({"columnist": columnist} if columnist else {}),
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
