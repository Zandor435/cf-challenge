#!/usr/bin/env python3
"""
validate_team_names.py — The fetch->score name gate (ARCHITECTURE §8 CLONE, §9).

Picks store CANONICAL team names only (§9), so this gate resolves every real
pick through utils.resolve_canonical (exact / normalized canonical match, no
ambiguity guard) — NOT resolve_team, which is the human-ENTRY path. A pick that
does not resolve to a canonical FBS team fails the run (exit 1) and names it.

The ambiguity guard resolve_team used to provide (bare "Miami"/"USC" raising) is
replaced by a STRONGER, storage-appropriate check: cross-check each pick's team
against its own `conference` field via teams_canonical.json. "Miami"/ACC is
correct; "Miami"/MAC (i.e. the person meant Miami (OH)) is caught, as is
"USC"/SEC vs "USC"/Big Ten — by the field that has to be filled in anyway. A
canonical string can be unambiguous yet still be the WRONG team; the conference
cross-check catches that where a bare-name ambiguity guard never could.

Also enforces the per-group draft rules when set (§5): picks_per_manager and
min_distinct_conferences. null = unenforced — the check is SKIPPED, never
assumed. Placeholder picks (todo=true / team "TODO") are skipped throughout.

Usage:
    python scripts/validate_team_names.py                # all groups
    python scripts/validate_team_names.py --group church
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (resolve_canonical, canonical_conference, UnknownTeamError,
                   normalize_team_name, get_all_group_ids, load_group_picks,
                   load_group_config)


def _is_placeholder(pick):
    return pick.get("todo") is True or str(pick.get("team", "")).strip().upper() == "TODO"


def _real_picks(picks):
    for pick in picks:
        if isinstance(pick, dict) and pick.get("_note"):
            continue
        if _is_placeholder(pick):
            continue
        yield pick


def validate_group(group_id):
    """Returns (checked, errors). Each error names the offending pick."""
    config = load_group_config(group_id)
    picks = load_group_picks(group_id).get("picks", [])
    errors, checked = [], 0

    def err(pick, problem, detail=""):
        errors.append({"group": group_id, "manager": pick.get("manager", "?"),
                       "team": pick.get("team"), "problem": problem, "detail": detail})

    for pick in _real_picks(picks):
        team = pick.get("team")
        # 1) canonical resolution (storage path, not the human ambiguity path)
        try:
            canonical = resolve_canonical(team)
        except UnknownTeamError as e:
            hint = f"did you mean: {', '.join(e.suggestions)}?" if e.suggestions else "no close match"
            err(pick, "not-canonical", hint)
            continue
        # 2) conference cross-check (the stronger, storage-appropriate guard)
        declared = pick.get("conference")
        actual = canonical_conference(canonical)
        if declared is None:
            err(pick, "missing-conference", "pick has no `conference` field to cross-check")
            continue
        if normalize_team_name(declared) != normalize_team_name(actual):
            err(pick, "conference-mismatch",
                f"'{canonical}' is in {actual!r}, but the pick says {declared!r} "
                f"— wrong team? (e.g. Miami vs Miami (OH))")
            continue
        checked += 1

    # 3) per-group draft rules — enforced only when configured (null = skip, §5/STEP 4)
    real = list(_real_picks(picks))
    ppm = config.get("picks_per_manager")
    mdc = config.get("min_distinct_conferences")
    if ppm is not None or mdc is not None:
        by_mgr = {}
        for pick in real:
            by_mgr.setdefault(pick.get("manager", "?"), []).append(pick)
        for mgr, mpicks in by_mgr.items():
            stub = {"manager": mgr, "team": "(roster)"}
            if ppm is not None and len(mpicks) != ppm:
                err(stub, "picks-per-manager",
                    f"has {len(mpicks)} picks, rule requires {ppm}")
            if mdc is not None:
                confs = {p.get("conference") for p in mpicks if p.get("conference")}
                if len(confs) < mdc:
                    err(stub, "min-distinct-conferences",
                        f"spans {len(confs)} conference(s) {sorted(confs)}, "
                        f"rule requires >= {mdc}")

    return checked, errors


def main():
    ap = argparse.ArgumentParser(description="Validate pick team names + conferences against canonical")
    ap.add_argument("--group", default="all")
    args = ap.parse_args()

    group_ids = get_all_group_ids() if args.group == "all" else [args.group]
    total_checked, all_errors = 0, []
    for gid in group_ids:
        checked, errors = validate_group(gid)
        total_checked += checked
        all_errors.extend(errors)

    if all_errors:
        print(f"NAME GATE FAILED — {len(all_errors)} bad pick(s):")
        for e in all_errors:
            print(f"  [{e['group']}] manager {e['manager']}: {e['problem'].upper()} "
                  f"team '{e['team']}'" + (f" — {e['detail']}" if e['detail'] else ""))
        sys.exit(1)

    print(f"Name gate OK: {total_checked} real pick(s) resolved + conference-checked "
          f"across {len(group_ids)} group(s).")


if __name__ == "__main__":
    main()
