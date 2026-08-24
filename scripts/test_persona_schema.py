#!/usr/bin/env python3
"""
test_persona_schema.py -- the editorial-profile field contract, both directions.

TWO PROPERTIES, and they pull against each other, which is why both are tested:

  1. EVERY new field is OPTIONAL. 2 of the 24 managers across the four leagues
     have no persona content whatsoever and 12 more have only the four original
     prose fields, so a record carrying none of these fields must validate
     clean and the page must compose around every absence. A schema that
     demands the new fields would take the whole site down for the majority of
     its roster.

  2. A field that is PRESENT and MALFORMED must FAIL LOUD and name the
     offender. Unknown layout key, bad hex, typo'd sub-key, a module block
     decorating a flat field that does not exist, half a footer. The
     alternative -- ignore what you cannot parse -- renders as a block quietly
     missing from an otherwise finished-looking page, which is the creative-data
     equivalent of the DR Congo bug (playbook rule 4).

AND THE TONE GATE, which is the one rule here that is not cosmetic. The three
registers each withhold a set of flat fields; `modules` only ever decorates a
flat field (label/headline/art -- never the body prose). So a withheld flat
field MUST take its module block with it. If it does not, a straight-register
manager -- somebody's father, on a page his family reads -- renders a "Fatal
Flaw" label and headline over an empty body. test_tone_gate_strips_modules is
the check that this cannot happen, and it asserts against the real published
payload, not a synthetic one.

Runs both ways: pytest collects one test per section and conftest.py raises on
any check() recorded as FAIL; `python scripts/test_persona_schema.py` prints
the transcript and exits nonzero on any failure.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from persona_schema import (  # noqa: E402
    DEFAULT_LAYOUT,
    LAYOUTS,
    MODULE_KEYS,
    PRIVATE_FIELDS,
    PROFILE_SITE_FIELDS,
    PersonaSchemaError,
    strip_modules_for_tone,
    validate_manager,
)

ROOT = Path(__file__).resolve().parent.parent
GROUPS_DIR = ROOT / "groups"
WEB_DATA_DIR = ROOT / "docs" / "data"

# Mirrors sync_personas.TONE_POLICY. Duplicated deliberately: if the policy
# there is ever loosened, this test should fail rather than silently agree
# with the new value. The strict register is somebody's parents; a test that
# imports the thing it is guarding cannot guard it.
EXPECTED_TONE_POLICY = {
    "roast": (),
    "warm": ("fatal_flaw",),
    "straight": ("fatal_flaw", "running_gag", "rival"),
}

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# These RETURN the verdict rather than recording it, so the check() call stays
# in the test body. scripts/test_suite_collection.py walks the AST for a
# literal check()/assert inside each test_* function and fails a function that
# has neither -- a helper that records on the caller's behalf reads as a test
# that asserts nothing. That rule is right, so the helpers bend to it.
def rejects(record, mid="tester"):
    """(ok, detail) for: validate_manager rejects this record AND names the offender."""
    try:
        validate_manager("schematest", mid, record)
    except PersonaSchemaError as exc:
        msg = str(exc)
        if mid not in msg or "schematest" not in msg:
            return False, "rejected, but the message names neither group nor manager"
        return True, msg.split(":", 1)[1].strip()[:70]
    return False, "ACCEPTED a malformed record"


def accepts(record, mid="tester"):
    """(ok, detail) for: validate_manager accepts this legal record."""
    try:
        validate_manager("schematest", mid, record)
    except PersonaSchemaError as exc:
        return False, f"rejected a legal record: {exc}"
    return True, ""


# ---------------------------------------------------------------------------
def test_every_new_field_is_optional():
    """The empty record, and the four-prose-field record, both validate."""
    check("empty record validates (a manager with no persona at all)", *accepts({}))

    check("null-everywhere record validates", *accepts({
        "display_name": "Josh", "tone": "roast",
        "epithet": None, "tagline": None, "backstory": None,
    }))

    # The shape 12 of the 24 managers actually have today.
    check("legacy four-prose-field record validates unchanged", *accepts({
        "display_name": "David", "tone": "warm",
        "epithet": "The Spider",
        "backstory": "Richmond man.",
        "running_gag": "Richmond and Georgia in the same heart.",
        "draft_tendency": "Picks for the story, not the spread.",
    }))

    # And a maximal record.
    check("maximal record validates", *accepts({
        "display_name": "Blaine", "tone": "roast",
        "draft_tendency": "Contrarian for the bit.",
        "fatal_flaw": "Special teamers.",
        "running_gag": "The in-laws.",
        "archetype": "The Agitator",
        "thesis": "Sarcasm, sourdough, and a Longhorn in-law situation.",
        "layout": "sideline",
        "theme": {"accent": "#fe5c00", "accent_secondary": "#111", "paper": None, "ink": None},
        "dossier": {"role": "Agitator", "nicknames": ["The Agitator", "Frosted Tips"],
                    "known_for": "Starting arguments.", "hometown": "Somewhere loud",
                    "college": "Boise State", "drafted": "2019", "status": "Underrated"},
        "modules": {
            "draft_tendency": {"label": "Draft tendency", "headline": "Contrarian for the Bit", "art": None},
            "fatal_flaw": {"label": "Fatal flaw", "headline": "Special Teams PTSD", "art": None},
            "running_gag": {"label": "Running gag", "headline": "The In-Laws", "art": None},
        },
        "pull_quote": {"text": "Sarcasm is my second language.", "attribution": "Blaine"},
        "footer": {"left": "AGITATES BY DAY.", "right": "OVERTHINKS BY NIGHT."},
        "assets": {"hero": "assets/profiles/panel/blaine-ripped.webp",
                   "nameplate": None, "signature": None, "badge": None, "spots": []},
        "north_star": "A vintage game program that got left in a coffee shop.",
        "motifs": ["coffee", "longhorns"],
        "easter_eggs": ["frosted tips", "ear piercing", "sourdough"],
    }))


def test_malformed_fields_fail_loud():
    base = {"display_name": "T", "tone": "roast", "draft_tendency": "x"}

    check("layout rejects wrong case (would silently become the default)",
          *rejects({**base, "layout": "SIDELINE"}))
    check("layout rejects a near-miss typo", *rejects({**base, "layout": "sidelines"}))
    check("layout rejects an unknown variant", *rejects({**base, "layout": "hero"}))
    check(f"layout null is legal (means {DEFAULT_LAYOUT!r})",
          *accepts({**base, "layout": None}))
    for lay in LAYOUTS:
        check(f"layout {lay!r} accepted", *accepts({**base, "layout": lay}))

    check("theme rejects hex with no #", *rejects({**base, "theme": {"accent": "fe5c00"}}))
    check("theme rejects non-hex digits", *rejects({**base, "theme": {"accent": "#gg0000"}}))
    check("theme rejects a CSS colour name", *rejects({**base, "theme": {"accent": "orange"}}))
    check("theme rejects an unknown key", *rejects({**base, "theme": {"accent2": "#fff"}}))
    check("theme rejects a list", *rejects({**base, "theme": ["#fff"]}))
    check("theme accepts #rgb shorthand", *accepts({**base, "theme": {"accent": "#fff"}}))
    check("theme accepts uppercase #rrggbb", *accepts({**base, "theme": {"accent": "#FE5C00"}}))

    check("dossier rejects `nickname` singular (the typo that drops the block)",
          *rejects({**base, "dossier": {"nickname": ["a"]}}))
    check("dossier.nicknames rejects a bare string",
          *rejects({**base, "dossier": {"nicknames": "The Agitator"}}))
    check("dossier.nicknames rejects an empty entry",
          *rejects({**base, "dossier": {"nicknames": ["ok", ""]}}))

    check("modules rejects a typo'd sub-key",
          *rejects({**base, "modules": {"draft_tendency": {"headlines": "x"}}}))
    check("modules rejects an unknown module key",
          *rejects({**base, "modules": {"fatal_flow": {"headline": "x"}}}))
    check("modules rejects a block whose flat field is empty (headline, no body)",
          *rejects({**base, "modules": {"fatal_flaw": {"headline": "Special Teams PTSD"}}}))
    check("modules rejects a non-string headline",
          *rejects({**base, "modules": {"draft_tendency": {"headline": 7}}}))

    check("footer rejects one half of the two-part band",
          *rejects({**base, "footer": {"left": "AGITATES BY DAY."}}))
    check("footer rejects an empty half",
          *rejects({**base, "footer": {"left": "a", "right": ""}}))
    check("footer accepts both halves",
          *accepts({**base, "footer": {"left": "a", "right": "b"}}))

    check("pull_quote rejects an attribution with no text",
          *rejects({**base, "pull_quote": {"attribution": "Blaine"}}))
    check("assets.spots rejects a bare string",
          *rejects({**base, "assets": {"spots": "one.webp"}}))
    check("archetype rejects an empty string", *rejects({**base, "archetype": ""}))
    check("motifs rejects a bare string", *rejects({**base, "motifs": "coffee"}))


def test_tone_gate_strips_modules():
    """A withheld flat field takes its module block with it."""
    for tone, withheld in EXPECTED_TONE_POLICY.items():
        rec = {
            "tone": tone,
            "draft_tendency": "x", "fatal_flaw": "y", "running_gag": "z", "rival": "someone",
            "modules": {k: {"label": k, "headline": "H"} for k in MODULE_KEYS},
        }
        for k in withheld:
            rec[k] = None
        strip_modules_for_tone(rec, withheld)
        mods = rec.get("modules") or {}
        leaked = [k for k in withheld if mods.get(k)]
        check(f"tone {tone!r}: withheld modules stripped ({', '.join(withheld) or 'none'})",
              not leaked, f"LEAKED {leaked}" if leaked else "")
        # And the register must NOT strip what it does not withhold.
        kept = [k for k in MODULE_KEYS if k not in withheld]
        lost = [k for k in kept if not mods.get(k)]
        check(f"tone {tone!r}: permitted modules survive", not lost,
              f"wrongly stripped {lost}" if lost else "")

    # An all-null modules dict collapses to None so the page tests one thing.
    rec = {"tone": "straight", "modules": {"fatal_flaw": {"headline": "H"}}}
    strip_modules_for_tone(rec, ("fatal_flaw", "running_gag", "rival"))
    check("all-null modules collapses to None", rec["modules"] is None,
          f"got {rec['modules']!r}")

    # No modules at all is not an error.
    rec = {"tone": "roast"}
    strip_modules_for_tone(rec, ())
    check("absent modules is a no-op", rec.get("modules") is None)


def test_private_fields_never_published():
    """The creative brief stays in the repo. This asserts on the real payload."""
    for f in PRIVATE_FIELDS:
        check(f"{f!r} is not in the published field list",
              f not in PROFILE_SITE_FIELDS)

    published = sorted(WEB_DATA_DIR.glob("*/personas.json"))
    check("published persona payloads exist to audit", bool(published),
          f"{len(published)} file(s)")
    banned = set(PRIVATE_FIELDS) | {"traits", "silhouette_cue", "silhouette_cues"}
    for path in published:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for mid, rec in doc.get("managers", {}).items():
            leaked = sorted(banned & set(rec))
            check(f"{path.parent.name}/{mid}: no art-direction fields published",
                  not leaked, f"LEAKED {leaked}" if leaked else "")


def test_every_source_persona_validates():
    """Every manager in every group, against the real source files."""
    seen = 0
    for src_path in sorted(GROUPS_DIR.glob("*/personas.json")):
        gid = src_path.parent.name
        doc = json.loads(src_path.read_text(encoding="utf-8"))
        for mid, rec in doc.get("managers", {}).items():
            seen += 1
            try:
                validate_manager(gid, mid, rec)
                ok, detail = True, ""
            except PersonaSchemaError as exc:
                ok, detail = False, str(exc)
            check(f"{gid}/{mid} source validates", ok, detail)
    check("every group's personas were audited", seen >= 20, f"{seen} managers")


def test_every_profile_is_sideline():
    """ONE VARIANT ACROSS ALL FOUR LEAGUES, and the alternation that replaced it.

    The scroll's rhythm used to come from switching layout variants per person
    (panel alternated sideline/program, church's david took headliner). It now
    comes from which SIDE the sideline portrait sits on, which means the
    variant has to be uniform or the alternation reads as noise on top of
    noise. DEFAULT_LAYOUT is already 'sideline', so an ABSENT key is correct
    and is not what this guards -- a re-declared 'program' or 'headliner' is.

    The second half is the one that would actually break silently: the side is
    each manager's INDEX in groups/<g>/personas.json, so two managers sharing
    an index, or an index the published file disagrees with, would put two
    profiles on the same side with nothing to show for it.
    """
    seen = 0
    for src_path in sorted(GROUPS_DIR.glob("*/personas.json")):
        gid = src_path.parent.name
        doc = json.loads(src_path.read_text(encoding="utf-8"))
        mgrs = doc.get("managers", {})
        for mid, rec in mgrs.items():
            seen += 1
            got = rec.get("layout")
            check(f"{gid}/{mid} is sideline (or unset, which means sideline)",
                  got in (None, "sideline"), f"layout is {got!r}")

        # profile_order must be the source file's key order, exactly, and the
        # published copy must agree with it. sync_personas.py derives it, so a
        # disagreement here means the docs/ copy is stale in a way --check
        # would catch too -- but this names the field, which --check does not.
        pub_path = ROOT / "docs" / "data" / gid / "personas.json"
        if not pub_path.exists():
            continue
        pub = json.loads(pub_path.read_text(encoding="utf-8")).get("managers", {})
        expect = {mid: i for i, mid in enumerate(mgrs)}
        got = {mid: rec.get("profile_order") for mid, rec in pub.items()}
        check(f"{gid}: published profile_order matches the persona file's order",
              got == expect, f"expected {expect}, got {got}")
    check("every group's layouts were audited", seen >= 20, f"{seen} managers")


def main():
    test_every_new_field_is_optional()
    test_malformed_fields_fail_loud()
    test_tone_gate_strips_modules()
    test_private_fields_never_published()
    test_every_source_persona_validates()
    test_every_profile_is_sideline()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
