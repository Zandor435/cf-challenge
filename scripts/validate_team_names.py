#!/usr/bin/env python3
"""
validate_team_names.py — The fetch->score name + draft-rule gate (ARCHITECTURE §8/§9, §5).

Picks store CANONICAL team names only (§9), so this gate resolves every real
pick through utils.resolve_canonical (exact / normalized canonical match, no
ambiguity guard) — NOT resolve_team, which is the human-ENTRY path. A pick that
does not resolve to a canonical FBS team fails the run (exit 1) and names it.

The ambiguity guard resolve_team used to provide (bare "Miami"/"USC" raising) is
replaced by a STRONGER, storage-appropriate check: cross-check each pick's team
against its own `conference` field via teams_canonical.json. "Miami"/ACC is
correct; "Miami"/MAC (i.e. the person meant Miami (OH)) is caught, as is
"USC"/SEC vs "USC"/Big Ten — by the field that has to be filled in anyway.

Draft rules (§5) are ALWAYS enforced per group — no unenforced path:
  - picks_per_manager: each manager has EXACTLY this many picks (no more, fewer).
  - min_distinct_conferences: those picks span at least this many conferences.
A manager with no real picks yet (all TODO placeholders) is SKIPPED, not failed,
so undrafted rosters pass. A config missing either rule key is itself a failure.

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
    out = []
    for pick in picks:
        if isinstance(pick, dict) and pick.get("_note"):
            continue
        if _is_placeholder(pick):
            continue
        out.append(pick)
    return out


def validate_group_data(group_id, config, picks):
    """Core gate on already-loaded (config, picks). Returns (checked, errors);
    each error names the group, the manager, and the violation."""
    errors, checked = [], 0

    def err(manager, team, problem, detail=""):
        errors.append({"group": group_id, "manager": manager, "team": team,
                       "problem": problem, "detail": detail})

    real = _real_picks(picks)

    # 1) name resolution + conference cross-check, per real pick
    for pick in real:
        team = pick.get("team")
        try:
            canonical = resolve_canonical(team)
        except UnknownTeamError as e:
            hint = f"did you mean: {', '.join(e.suggestions)}?" if e.suggestions else "no close match"
            err(pick.get("manager", "?"), team, "not-canonical", hint)
            continue
        declared = pick.get("conference")
        actual = canonical_conference(canonical)
        if declared is None:
            err(pick.get("manager", "?"), team, "missing-conference",
                "pick has no `conference` field to cross-check")
            continue
        if normalize_team_name(declared) != normalize_team_name(actual):
            err(pick.get("manager", "?"), team, "conference-mismatch",
                f"'{canonical}' is in {actual!r}, but the pick says {declared!r} "
                f"— wrong team? (e.g. Miami vs Miami (OH))")
            continue
        checked += 1

    # 2) draft rules — ALWAYS enforced (no null/unenforced path). Both keys are
    #    required; a missing one is a config failure, not a silent skip.
    ppm = config.get("picks_per_manager")
    mdc = config.get("min_distinct_conferences")
    if ppm is None or mdc is None:
        err("(config)", "(rules)", "rules-missing",
            f"picks_per_manager={ppm!r}, min_distinct_conferences={mdc!r}; both are "
            f"required — there is no unenforced path")
        return checked, errors

    # group by manager over REAL picks only -> managers with no real picks (all
    # TODO placeholders) never appear here, so undrafted rosters are skipped.
    by_mgr = {}
    for pick in real:
        by_mgr.setdefault(pick.get("manager", "?"), []).append(pick)
    for mgr, mpicks in by_mgr.items():
        if len(mpicks) != ppm:
            err(mgr, "(roster)", "picks-per-manager",
                f"has {len(mpicks)} pick(s), rule requires EXACTLY {ppm}")
        confs = {p.get("conference") for p in mpicks if p.get("conference")}
        if len(confs) < mdc:
            err(mgr, "(roster)", "min-distinct-conferences",
                f"{len(mpicks)} pick(s) span {len(confs)} conference(s) {sorted(confs)}, "
                f"rule requires >= {mdc} distinct")

    return checked, errors


def validate_group(group_id):
    """Load groups/<group_id>/ and validate it. Returns (checked, errors)."""
    config = load_group_config(group_id)
    picks = load_group_picks(group_id).get("picks", [])
    return validate_group_data(group_id, config, picks)


def main():
    ap = argparse.ArgumentParser(description="Validate pick names + conferences + draft rules")
    ap.add_argument("--group", default="all")
    args = ap.parse_args()

    group_ids = get_all_group_ids() if args.group == "all" else [args.group]
    total_checked, all_errors = 0, []
    for gid in group_ids:
        checked, errors = validate_group(gid)
        total_checked += checked
        all_errors.extend(errors)

    if all_errors:
        print(f"GATE FAILED — {len(all_errors)} violation(s):")
        for e in all_errors:
            print(f"  [{e['group']}] manager {e['manager']}: {e['problem'].upper()} "
                  f"'{e['team']}'" + (f" — {e['detail']}" if e['detail'] else ""))
        sys.exit(1)

    print(f"Gate OK: {total_checked} real pick(s) resolved + conference-checked + "
          f"draft-rule-checked across {len(group_ids)} group(s).")


if __name__ == "__main__":
    main()
