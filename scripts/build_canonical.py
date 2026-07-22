#!/usr/bin/env python3
"""
build_canonical.py — Canonical team spine + alias rebuild (ARCHITECTURE §9, build §10.1 BUILD 1).

One /teams/fbs call -> data/teams_canonical.json (canonical school, conference,
CFBD id per FBS team). Then rebuild data/team_aliases.json so every alias target
is a VERIFIED canonical string, and report any target that does not exist in the
canonical file (the current map is suspected to have USC and Ole Miss inverted).

Bare "USC"/"Miami" are intentionally NOT in the alias map — utils.resolve_team
raises an explicit ambiguity error for them (§9). Run seasonally / one-time.

Usage:
    python scripts/build_canonical.py                  # season from season.json
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (DATA_DIR, CANONICAL_PATH, ALIASES_PATH, AMBIGUOUS_PATH,
                   normalize_team_name, save_json_atomic, load_json)
from cfbd_client import CFBDClient

# Curated variant -> intended canonical. Targets are validated against the live
# canonical set below and auto-snapped to the exact CFBD string; anything that
# can't be matched is reported, never silently written. Bare ambiguous tokens
# (USC, Miami) are deliberately absent — those raise in resolve_team (§9).
ALIAS_SEED = {
    # Ole Miss: CFBD canonical is "Ole Miss" (old map had this inverted).
    "Mississippi": "Ole Miss",
    "Ole' Miss": "Ole Miss",
    "Ol' Miss": "Ole Miss",
    # Southern Cal: canonical is "USC"; reach it via a disambiguated name only.
    "Southern California": "USC",
    "Southern Cal": "USC",
    "USC (Southern California)": "USC",
    # Miami disambiguation (bare "Miami" is ambiguous and raises). No identity
    # alias for "Miami (OH)" — it's canonical and resolves directly.
    "Miami (FL)": "Miami",
    "Miami FL": "Miami",
    "Miami Florida": "Miami",
    "Miami Ohio": "Miami (OH)",
    "Miami OH": "Miami (OH)",
    # Canonical != what a person types (surfaced by the BUILD 1 report).
    "Appalachian State": "App State",
    "Appalachian St.": "App State",
    "Louisiana Monroe": "UL Monroe",
    "ULM": "UL Monroe",
    "UMass": "Massachusetts",
    "Southern Mississippi": "Southern Miss",   # verified vs canonical, not memory
    "UL Lafayette": "Louisiana",
    "ULL": "Louisiana",
    "Louisiana Lafayette": "Louisiana",
    # High-confidence hand aliases (acronym canonical -> full name people type;
    # this direction can't be generated mechanically without an expansion table).
    "Connecticut": "UConn",
    "Sam Houston State": "Sam Houston",
    "North Carolina State": "NC State",
    "Middle Tennessee State": "Middle Tennessee",
    "MTSU": "Middle Tennessee",
    "Brigham Young": "BYU",
    # Common short forms.
    "Pitt": "Pittsburgh",
    "Cal": "California",
    "UNC": "North Carolina",
    # "St." -> "State" (normalization strips the period but not the abbreviation).
    "Ohio St.": "Ohio State",
    "Penn St.": "Penn State",
    "Michigan St.": "Michigan State",
    "Miss. State": "Mississippi State",
    "Mississippi St.": "Mississippi State",
    "Oklahoma St.": "Oklahoma State",
    "Oregon St.": "Oregon State",
    "Washington St.": "Washington State",
    "Iowa St.": "Iowa State",
    "Kansas St.": "Kansas State",
    "Fresno St.": "Fresno State",
    "San Diego St.": "San Diego State",
    "Boise St.": "Boise State",
    "Arizona St.": "Arizona State",
    "Florida St.": "Florida State",
}
# Identity self-maps (LSU->LSU, TCU->TCU, UCF->UCF, SMU->SMU, UNLV->UNLV,
# Miami (OH), NC State) are intentionally ABSENT: every one is already a
# canonical string, so resolve_team()/resolve_canonical() resolve it via the
# canonical path. The resolver needs no identity anchors.


def fetch_fbs_teams(client, season):
    print(f"  fetching /teams/fbs?year={season}")
    raw = client.get("/teams/fbs", {"year": season})
    teams = []
    for t in raw:
        school = t.get("school")
        if not school:
            continue
        teams.append({
            "id": t.get("id"),
            "school": school,
            "conference": t.get("conference"),
            "abbreviation": t.get("abbreviation"),
        })
    teams.sort(key=lambda x: x["school"])
    print(f"  -> {len(teams)} FBS teams")
    return teams


def snap_to_canonical(target, canonical_exact, canonical_by_key):
    """Return the exact canonical string for `target`, or None if it can't match."""
    if target in canonical_exact:
        return target
    return canonical_by_key.get(normalize_team_name(target))


def is_acronym_canonical(school):
    """True for all-caps acronym canonicals (BYU, LSU, TCU, UCF, ...). These
    can't be expanded to a full name mechanically (that needs an external
    dictionary = hand-listing), so we don't generate acronyms for them."""
    letters = re.sub(r"[^A-Za-z]", "", school)
    return bool(letters) and letters.isupper()


def make_acronyms(school):
    """Mechanical acronym candidates for a FULL-NAME canonical. Every form is
    >=2 letters: a bare 'U'+single-initial (UM, UT, UK, UB ...) is dropped —
    mechanical generation can't encode convention (UK is Kansas here but reads
    as Kentucky/Kansas; UB reads as Buffalo, not Baylor), and the fuzzy suggester
    handles single-letter cases more cleanly.

    Two conventions:
      - "<X> [Y] State" -> "<front-initials>SU" (the State-University form people
        actually type: Ohio State -> OSU, San Diego State -> SDSU) — never the
        bare "OS"/"SDS" nobody types. This is the *only* form for a State school.
      - Otherwise word initials plus a 'U'+initials form (University-of-X style:
        South Florida -> SF/USF, North Texas -> NT/UNT), each only when the plain
        initials are already >=2 letters (so single-word schools generate nothing).
    Returns a set of candidate acronym strings."""
    words = [re.sub(r"[^A-Za-z]", "", w) for w in school.split()]
    words = [w for w in words if w]
    if not words:
        return set()
    cands = set()
    if len(words) >= 2 and words[-1].lower() == "state":
        # "State" expands to "SU" (State University), NOT the bare "S". Front-word
        # initials + SU: OSU/MSU/ASU/FSU/KSU/BSU (all genuinely ambiguous), SDSU,
        # PSU. Nobody types "OS"/"MS", so we don't generate them.
        front = "".join(w[0] for w in words[:-1]).upper()
        cands.add(front + "SU")
        return cands
    inits = "".join(w[0] for w in words).upper()
    if len(inits) >= 2:                # single-word schools yield 1 init -> nothing
        cands.add(inits)               # word initials: SF, SM, MT, NC ...
        cands.add("U" + inits)         # U + initials: USF, USM, UNT ...
    return cands


def main():
    ap = argparse.ArgumentParser(description="Build canonical team spine + rebuild aliases")
    # No season literal: default is the single source (season.json).
    ap.add_argument("--season", type=int, default=None,
                    help="Season year for /teams/fbs (default: season.json cfbd_default_season)")
    args = ap.parse_args()
    season = args.season if args.season is not None else utils.get_cfbd_default_season()

    print("=" * 60)
    print(f"BUILD CANONICAL SPINE — season {season}")
    print("=" * 60)

    client = CFBDClient(utils.get_api_key())
    teams = fetch_fbs_teams(client, season)
    canonical_exact = {t["school"] for t in teams}
    canonical_by_key = {normalize_team_name(t["school"]): t["school"] for t in teams}

    # Fail loud on normalized-key collisions — the internal resolver
    # (resolve_canonical) relies on unique normalized keys; a collision would
    # silently make one canonical team unresolvable.
    key_groups = defaultdict(list)
    for t in teams:
        key_groups[normalize_team_name(t["school"])].append(t["school"])
    collisions = {k: v for k, v in key_groups.items() if len(v) > 1}
    if collisions:
        print("CANONICAL COLLISION — distinct schools share a normalized key:")
        for k, v in collisions.items():
            print(f"  '{k}': {v}")
        sys.exit(2)

    save_json_atomic(CANONICAL_PATH, {
        "season": season,
        "source": "CFBD /teams/fbs",
        "count": len(teams),
        "teams": teams,
    })

    # --- Audit the OLD alias map's targets against canonical -----------------
    old_audit = []  # (variant, target, ok, suggestion)
    if ALIASES_PATH.exists():
        old = load_json(ALIASES_PATH)
        for variant, target in old.items():
            if variant.startswith("_"):
                continue
            ok = target in canonical_exact
            suggestion = None if ok else canonical_by_key.get(normalize_team_name(target))
            old_audit.append((variant, target, ok, suggestion))
    old_orphans = [(v, t, s) for (v, t, ok, s) in old_audit if not ok]

    # --- Build the NEW alias map (validated targets only) --------------------
    new_aliases = {
        "_note": ('Variant -> canonical CFBD school. Targets verified against '
                  'teams_canonical.json. Hand-curated entries first, then '
                  'mechanically-generated acronym aliases. Bare "USC"/"Miami" and '
                  'collision acronyms (data/ambiguous.json) are absent: they raise '
                  'an ambiguity error. Resolution normalizes on lookup (lowercase, '
                  'strip punctuation/diacritics/whitespace).'),
    }
    seed_orphans = []
    for variant, intended in ALIAS_SEED.items():
        exact = snap_to_canonical(intended, canonical_exact, canonical_by_key)
        if exact is None:
            seed_orphans.append((variant, intended))
            continue
        # Don't let an alias key collide with a curated ambiguous bare token.
        if normalize_team_name(variant) in utils.AMBIGUOUS_BASE:
            continue
        new_aliases[variant] = exact

    # Fail loud: a seed target with no canonical match must NOT be silently
    # dropped (that shrinks the map with no error — the App State bug).
    if seed_orphans:
        print("\n" + "!" * 60)
        print(f"SEED VALIDATION FAILED — {len(seed_orphans)} ALIAS_SEED target(s) "
              f"not in teams_canonical.json:")
        for variant, intended in seed_orphans:
            print(f"  '{variant}' -> '{intended}'  (no such FBS team for season {season})")
        print("Refusing to write a silently-shrunken alias map. Fix ALIAS_SEED and re-run.")
        print("!" * 60)
        sys.exit(2)

    hand_alias_count = len(new_aliases) - 1  # minus _note

    # --- Mechanically generate acronym aliases, collision-checked ------------
    # A generated form is kept as an alias ONLY if it resolves to exactly ONE
    # canonical (across generated forms + canonicals + hand aliases). Forms that
    # map to 2+ canonicals go to the ambiguity guard (data/ambiguous.json), never
    # a silent wrong resolution. 'UM'/'USM'-style traps are found here, not listed.
    taken = defaultdict(set)  # normkey -> canonicals it already resolves to
    for school in canonical_exact:
        taken[normalize_team_name(school)].add(school)
    for variant, target in new_aliases.items():
        if not variant.startswith("_"):
            taken[normalize_team_name(variant)].add(target)

    gen = {}  # normkey -> {"display": acr, "schools": set()}
    for school in sorted(canonical_exact):
        if is_acronym_canonical(school):
            continue
        for acr in make_acronyms(school):
            k = normalize_team_name(acr)
            if k in utils.AMBIGUOUS_BASE:      # reserved (usc/miami)
                continue
            gen.setdefault(k, {"display": acr, "schools": set()})["schools"].add(school)

    generated_aliases = {}
    generated_ambiguous = {}  # normkey -> {"display": acr, "candidates": [...]}
    for k, info in gen.items():
        candidates = set(info["schools"]) | taken.get(k, set())
        if len(candidates) >= 2:
            generated_ambiguous[k] = {"display": info["display"],
                                      "candidates": sorted(candidates)}
        elif k not in taken:                   # unique AND not already resolvable
            generated_aliases[info["display"]] = next(iter(candidates))
        # else: already resolves to that one canonical -> redundant, skip

    for variant, target in generated_aliases.items():
        new_aliases[variant] = target

    save_json_atomic(ALIASES_PATH, new_aliases)
    save_json_atomic(AMBIGUOUS_PATH, {
        "_note": ("Mechanically-generated acronym collisions — tokens that map to "
                  "2+ FBS teams, routed to the resolver's ambiguity guard so they "
                  "never resolve silently. Merged with utils.AMBIGUOUS_BASE at "
                  "runtime. Keyed by normalized token -> candidate canonical strings."),
        **{k: v["candidates"] for k, v in sorted(generated_ambiguous.items())},
    })

    # --- Report --------------------------------------------------------------
    print("\n" + "-" * 60)
    print("ALIAS DISCREPANCY REPORT")
    print("-" * 60)
    print(f"OLD alias map — full audit ({len(old_audit)} entries):")
    print(f"  {'ALIAS':24s}    {'TARGET':22s}  STATUS")
    for variant, target, ok, suggestion in old_audit:
        status = "OK" if ok else f"ORPHAN (nearest: {suggestion or 'NONE'})"
        print(f"  {variant:24s} -> {target:22s}  {status}")

    if old_orphans:
        print(f"\nORPHANS ({len(old_orphans)}) — target not a real FBS team:")
        for variant, target, suggestion in old_orphans:
            fix = f"nearest canonical '{suggestion}'" if suggestion else "NO canonical match"
            print(f"  '{variant}' -> '{target}'  =>  {fix}")
    else:
        print("\nORPHANS: none — every OLD target existed in canonical.")

    # (seed_orphans would have exited non-zero above; reaching here means clean.)
    print("New alias map: all seed targets verified against canonical.")

    print("\n" + "-" * 60)
    print("MECHANICAL ACRONYM GENERATION")
    print("-" * 60)
    print(f"generated unique acronym aliases: {len(generated_aliases)}")
    print(f"collisions routed to ambiguity guard: {len(generated_ambiguous)}")
    for k, info in sorted(generated_ambiguous.items(), key=lambda x: x[1]["display"]):
        print(f"  {info['display']:6s} -> {', '.join(info['candidates'])}")

    print("\n" + "-" * 60)
    print("TOTALS")
    print("-" * 60)
    print(f"canonical teams:  {len(teams)}")
    print(f"aliases:          {len(new_aliases) - 1} "
          f"({hand_alias_count} hand + {len(generated_aliases)} generated)")
    print(f"ambiguity guard:  {len(utils.AMBIGUOUS_BASE)} curated (usc, miami) "
          f"+ {len(generated_ambiguous)} generated")
    print(f"CFBD calls:       {client.call_count}")


if __name__ == "__main__":
    main()
