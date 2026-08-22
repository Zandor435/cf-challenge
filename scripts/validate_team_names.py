#!/usr/bin/env python3
"""
validate_team_names.py — The fetch->score name + draft-rule gate (ARCHITECTURE §8/§9, §5).

Picks store CANONICAL team names only (§9), so this gate resolves every real
pick through utils.resolve_canonical (exact / normalized canonical match, no
ambiguity guard) — NOT resolve_team, which is the human-ENTRY path. A pick that
does not resolve to a canonical FBS team fails the run (exit 1) and names it.

The ambiguity guard resolve_team used to provide (bare "Miami"/"USC" raising) is
replaced by a STRONGER, storage-appropriate check: cross-check each pick's team
against its own `conference` field. "Miami"/ACC is correct; "Miami"/MAC (i.e. the
person meant Miami (OH)) is caught, as is "USC"/SEC vs "USC"/Big Ten — by the
field that has to be filled in anyway.

REFERENCE AUTHORITY (locked): the conference cross-check, and the new line check
below, both read data/team_win_totals_2026.json — the frozen draft-day reference
built by build_team_reference.py, keyed by canonical team name. It is the SOLE
authority on `conference` and `win_total` for the 2026 season.
teams_canonical.json is deliberately NOT consulted for either: its conference
field is a 2025-vintage snapshot that realignment has moved past (Boise State is
Mountain West there, Pac-12 in 2026), so trusting it would fail correct picks and
corrupt the 4-distinct-conference count. It stays authoritative for team IDENTITY
only — resolve_canonical still runs against it. Per real pick, three checks:
  - the team resolves to a canonical FBS team AND has a reference entry,
  - the pick's `conference` equals the reference's conference,
  - the pick's `line` equals the reference's `win_total` — a pick scored against
    a line the league never agreed to is a silently wrong result, the same bug
    class as a mis-resolved name.
  - the pick's `direction` is exactly "O" or "U" — the engine tests
    `direction == "O"` and scores everything else as an under, so a stray "o"
    inverts the pick silently, with no error anywhere.
Every failure names the group, the manager, the team, and which field disagreed.

Draft rules (§5) are ALWAYS enforced per group — no unenforced path:
  - picks_per_manager: each manager has EXACTLY this many picks (no more, fewer).
  - min_distinct_conferences: those picks span at least this many conferences.
    A manager named in the group config's OPTIONAL `conference_minimum_waivers`
    is exempt from THIS rule only (every other rule still applies to them, and
    every un-waived manager still hard-fails). The list is opt-in per manager:
    absent, empty, or not naming a manager all mean the gate applies normally,
    so a config that says nothing waives nobody. Each applied waiver PRINTS a
    "WAIVED:" line on every run — a waiver that scores a roster differently from
    the written rule has to be visible in the output that scored it, not just in
    the config file, or the next person reads a passing gate as rule-compliant.
  - team-sharing: two managers may hold the same team only on OPPOSITE sides.
    Same team + same side across two managers scores BOTH as "winning" the same
    bet — the worst bug class in the project — so it is exit 1, naming both
    managers, the team, and the side. A single manager holding the same team
    twice (any side) is likewise a failure. The side buckets are keyed on the
    NORMALIZED direction, so a case difference cannot split one real side into
    two buckets and slip past the check.
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
from utils import (resolve_canonical, UnknownTeamError, normalize_team_name,
                   get_all_group_ids, load_group_picks, load_group_config)

# The frozen draft-day reference (§1 "the line never moves"), built by
# build_team_reference.py. Sole authority on conference + win_total (see above).
WIN_TOTALS_PATH = utils.DATA_DIR / "team_win_totals_2026.json"

_SIDE_LABEL = {"O": "over", "U": "under"}

_WIN_TOTALS = None


def load_win_totals():
    """{normalized canonical team -> {display_name, conference, win_total}}.
    Keyed on the normalized name so it lines up with resolve_canonical's output
    regardless of punctuation/diacritics ("San José State")."""
    global _WIN_TOTALS
    if _WIN_TOTALS is None:
        raw = utils.load_json(WIN_TOTALS_PATH)   # missing file exits 1 — no silent pass
        _WIN_TOTALS = {normalize_team_name(k): v
                       for k, v in raw.get("teams", {}).items()}
    return _WIN_TOTALS


def _as_number(value):
    """float(value) or None — a line stored as a string still compares, a
    non-numeric one is reported rather than crashing the gate."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side_label(direction):
    return f"{direction} ({_SIDE_LABEL.get(direction, direction)})"


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
    reference = load_win_totals()
    ref_conf = {}   # index into `real` -> the reference's conference for that pick

    # 1) name resolution + conference/line cross-check against the frozen
    #    win-totals reference, per real pick.
    for idx, pick in enumerate(real):
        team = pick.get("team")
        manager = pick.get("manager", "?")

        # direction domain — checked FIRST, and independently of whether the
        # name resolves, so a pick with both faults reports both. The engine
        # tests `direction == "O"` and scores EVERYTHING else as an under
        # (scoring.signed_delta, projector.signed_delta, build_week_packet), so
        # a stray "o" does not error anywhere — it silently inverts the pick and
        # produces a clean, plausible, wrong board. Same bug class as an
        # unmapped name, which is why it is fatal here.
        direction_ok = pick.get("direction") in _SIDE_LABEL
        if not direction_ok:
            err(manager, team, "direction-invalid",
                f"direction {pick.get('direction')!r} is not \"O\" or \"U\" — "
                f"the engine tests `direction == \"O\"` and scores anything "
                f"else as an UNDER, so this pick would be scored on the wrong "
                f"side with no error anywhere")

        try:
            canonical = resolve_canonical(team)
        except UnknownTeamError as e:
            hint = f"did you mean: {', '.join(e.suggestions)}?" if e.suggestions else "no close match"
            err(manager, team, "not-canonical", hint)
            continue

        entry = reference.get(normalize_team_name(canonical))
        if entry is None:
            err(manager, team, "not-in-reference",
                f"'{canonical}' has no entry in {WIN_TOTALS_PATH.name} — there is "
                f"no draft-day line to score this pick against")
            continue
        ref_conf[idx] = entry.get("conference")

        pick_ok = direction_ok

        # conference — the reference is the authority, NOT teams_canonical.json
        declared = pick.get("conference")
        actual = entry.get("conference")
        if declared is None:
            err(manager, team, "missing-conference",
                "pick has no `conference` field to cross-check")
            pick_ok = False
        elif normalize_team_name(declared) != normalize_team_name(actual):
            err(manager, team, "conference-mismatch",
                f"CONFERENCE disagrees: {WIN_TOTALS_PATH.name} has '{canonical}' in "
                f"{actual!r}, the pick says {declared!r} — wrong team (e.g. Miami vs "
                f"Miami (OH)), or a stale pre-realignment conference")
            pick_ok = False

        # line — must equal the frozen draft-day win total exactly
        declared_line = pick.get("line")
        ref_line = _as_number(entry.get("win_total"))
        if declared_line is None:
            err(manager, team, "missing-line",
                f"pick has no `line` field; {WIN_TOTALS_PATH.name} has "
                f"win_total {entry.get('win_total')!r} for '{canonical}'")
            pick_ok = False
        else:
            declared_num = _as_number(declared_line)
            if declared_num is None:
                err(manager, team, "line-not-numeric",
                    f"pick line {declared_line!r} is not a number")
                pick_ok = False
            elif ref_line is None:
                err(manager, team, "reference-line-not-numeric",
                    f"{WIN_TOTALS_PATH.name} has a non-numeric win_total "
                    f"{entry.get('win_total')!r} for '{canonical}'")
                pick_ok = False
            elif abs(declared_num - ref_line) > 1e-9:
                err(manager, team, "line-mismatch",
                    f"LINE disagrees: {WIN_TOTALS_PATH.name} has '{canonical}' at "
                    f"{ref_line}, the pick says {declared_num} — the line is frozen "
                    f"at draft (§1) and does not move")
                pick_ok = False

        if pick_ok:
            checked += 1

    # 2) team-sharing gate (§5) — ALWAYS enforced. Two managers may hold the same
    #    team only on OPPOSITE sides; same team + same side means both "win" the
    #    same bet (the worst bug class here). A single manager holding a team twice
    #    is also invalid. Keyed by CANONICAL team so "Miami"/"Miami (FL)" collide.
    #    Picks that don't resolve are already reported above — skip them here.
    by_team = {}  # canonical -> [(manager, direction), ...]
    for pick in real:
        try:
            canonical = resolve_canonical(pick.get("team"))
        except UnknownTeamError:
            continue
        by_team.setdefault(canonical, []).append(
            (pick.get("manager", "?"), pick.get("direction")))
    for canonical, entries in by_team.items():
        # (a) same manager holding this team more than once — any side
        mgr_counts = {}
        for mgr, _d in entries:
            mgr_counts[mgr] = mgr_counts.get(mgr, 0) + 1
        for mgr, cnt in mgr_counts.items():
            if cnt > 1:
                err(mgr, canonical, "duplicate-team",
                    f"holds '{canonical}' {cnt} times; a manager may not hold the "
                    f"same team twice")
        # (b) two+ DISTINCT managers on the SAME side — the silent double-win bug.
        #     The bucket key is NORMALIZED (strip + upper). This is the second
        #     line of defense behind the direction-invalid check above: keying on
        #     the raw value put "O" and "o" in different buckets, each of length
        #     1, so two managers on the same real side slipped through the gate
        #     entirely. Every other comparison in this function already
        #     normalizes (conference via normalize_team_name, team via
        #     resolve_canonical, line via float) — this was the one outlier.
        by_side = {}  # normalized direction -> ordered distinct managers
        for mgr, direction in entries:
            lst = by_side.setdefault(str(direction).strip().upper(), [])
            if mgr not in lst:
                lst.append(mgr)
        for direction, mgrs in by_side.items():
            if len(mgrs) > 1:
                names = " & ".join(sorted(mgrs))
                err(names, canonical, "same-team-same-side",
                    f"managers [{names}] all hold '{canonical}' on the "
                    f"{_side_label(direction)} side — the same team may be shared "
                    f"only on OPPOSITE sides")

    # 3) draft rules — ALWAYS enforced (no null/unenforced path). Both keys are
    #    required; a missing one is a config failure, not a silent skip.
    ppm = config.get("picks_per_manager")
    mdc = config.get("min_distinct_conferences")
    if ppm is None or mdc is None:
        err("(config)", "(rules)", "rules-missing",
            f"picks_per_manager={ppm!r}, min_distinct_conferences={mdc!r}; both are "
            f"required — there is no unenforced path")
        return checked, errors

    # Conference-minimum waivers. OPTIONAL and opt-in per manager: a missing or
    # empty list waives nobody, so the default stays "the gate applies to all".
    # Shape is checked rather than trusted — a malformed entry is a config
    # failure, because the failure mode of skipping it silently is a waiver the
    # commissioner believes is on file that is not actually exempting anyone.
    # A typo'd manager_id needs no separate check: it waives nobody, so the
    # manager it was meant to cover still hard-fails loudly below.
    waived = {}   # manager_id -> waiver record
    # Only a malformed WAIVER CONFIG may abort the remaining rule checks, so
    # count what was already reported and compare, rather than testing the
    # whole accumulated list. Testing `if errors:` here meant any earlier pick
    # problem -- an unknown team, an ambiguous one, a disagreeing line --
    # returned before picks-per-manager and min-distinct-conferences ever ran,
    # so a bad roster reported four problems instead of six and looked closer
    # to correct than it was. Reporting every problem in ONE run is the whole
    # contract of this gate; test_enter_draft.py pins it.
    errors_before_waivers = len(errors)
    raw_waivers = config.get("conference_minimum_waivers") or []
    if not isinstance(raw_waivers, list):
        err("(config)", "(rules)", "waivers-malformed",
            f"conference_minimum_waivers must be a list, got "
            f"{type(raw_waivers).__name__}")
        return checked, errors
    for w in raw_waivers:
        if not isinstance(w, dict) or not str(w.get("manager_id") or "").strip():
            err("(config)", "(rules)", "waivers-malformed",
                f"every conference_minimum_waivers entry needs a non-empty "
                f"`manager_id`; got {w!r}")
            continue
        waived[str(w["manager_id"]).strip()] = w
    if len(errors) > errors_before_waivers:
        return checked, errors

    # group by manager over REAL picks only -> managers with no real picks (all
    # TODO placeholders) never appear here, so undrafted rosters are skipped.
    by_mgr = {}
    for idx, pick in enumerate(real):
        by_mgr.setdefault(pick.get("manager", "?"), []).append((idx, pick))
    for mgr, mpicks in by_mgr.items():
        if len(mpicks) != ppm:
            err(mgr, "(roster)", "picks-per-manager",
                f"has {len(mpicks)} pick(s), rule requires EXACTLY {ppm}")
        # Conference per the REFERENCE (the authority) where the pick resolved;
        # an unresolved pick falls back to its declared value, which is already
        # reported above — so the count still reflects the whole roster.
        confs = {ref_conf.get(i) or p.get("conference")
                 for i, p in mpicks if ref_conf.get(i) or p.get("conference")}
        if len(confs) < mdc:
            if mgr in waived:
                # Printed, not swallowed. This is the ONLY path that turns a
                # real rule violation into a pass, so it says so out loud in
                # every run — including enter_draft.py's, which calls this
                # function as its final roster check.
                w = waived[mgr]
                reason = str(w.get("reason") or "no reason recorded").strip()
                granted = str(w.get("granted") or "no date recorded").strip()
                print(f"  WAIVED: [{group_id}] {mgr} — conference minimum "
                      f"({len(confs)} of {mdc} required: {sorted(confs)}) — "
                      f"{reason} [granted {granted}]")
            else:
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
