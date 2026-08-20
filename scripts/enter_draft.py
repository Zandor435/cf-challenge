#!/usr/bin/env python3
"""
enter_draft.py — Turn a voice-dictated paste block into a validated picks.json
(ARCHITECTURE §5/§9; playbook rules 4 and 5).

The draft is dictated out loud, so the input has no reliable delimiter, no
consistent capitalization, and spoken numbers ("nine and a half"). This script
parses that block, resolves every team through the HUMAN-INPUT resolver
(utils.resolve_team — the ambiguity guard is load-bearing, see below), derives
`conference` and `line` from the frozen draft-day reference, validates the whole
roster, and only then writes.

WHY resolve_team AND NOT resolve_canonical (§9):
  validate_team_names.py — the downstream gate — resolves stored picks through
  resolve_canonical, which has NO ambiguity guard, because "Miami" and "USC" are
  legitimate canonical strings. That gate therefore CANNOT catch a speaker who
  said "Miami" meaning Miami (OH). This script is the only place the guard can
  fire, so a bare ambiguous token is fatal HERE and is never guessed.

WHAT IS STORED:
  The storage string is always resolve_team()'s RETURN VALUE, and it is checked
  to round-trip to a key of the reference file before it is written. It is never
  the reference's `display_name` (the key "Miami" has display_name "Miami (FL)",
  which resolve_canonical would reject) and never an AmbiguityError candidate
  string ("Southern California" is not a reference key; it resolves to "USC").

WHERE conference AND line COME FROM:
  The reference file, never the dictation. A dictated number is compared against
  the reference and a disagreement is FATAL — the line is frozen at draft (§1),
  so a mismatch means the reference or the pick is wrong, and neither is
  resolvable by precedence.

FAILURE DISCIPLINE:
  Every problem is fatal. Problems are COLLECTED across all four stages and
  reported together in one run, never one at a time. Nothing is written unless
  the entire roster passes — there is no partial write. The final roster check is
  validate_team_names.validate_group_data itself, so this script can never drift
  from the gate it has to satisfy.

  --dry-run is the DEFAULT. Writing requires an explicit --write.

Usage:
    python scripts/enter_draft.py --group panel --in draft.txt
    python scripts/enter_draft.py --group panel --in draft.txt --write
    cat draft.txt | python scripts/enter_draft.py --group panel
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from utils import (AmbiguityError, UnknownTeamError, normalize_team_name,
                   resolve_team)
import validate_team_names as gate

DEFAULT_REFERENCE = utils.DATA_DIR / "team_win_totals_2026.json"

# --- Grammar tables (all declared, none inferred) ---------------------------

# The direction keyword is the only structural anchor in a dictated line: the
# line is segmented AROUND it, not by position or delimiter. No canonical or
# alias name in the 137-team roster tokenizes to any of these.
FULL_DIRECTIONS = {"over": "O", "above": "O", "more": "O",
                   "under": "U", "below": "U", "less": "U"}
SHORT_DIRECTIONS = {"o": "O", "u": "U"}   # only when no full form is present

# Consumed at the head of the team span (after a matched manager, or on a line
# whose manager came from a header).
FILLERS = {"takes", "take", "has", "have", "picks", "pick", "gets", "get",
           "goes", "go", "wants", "want", "is", "with", "on", "the"}

# The team-resolution rewrite ladder's declared variants (see resolve_ladder).
NOISE_TAIL = {"football", "the"}

# Dropped from the head/tail of a dictated number span.
NUMBER_NOISE = {"than", "wins", "win", "games", "game", "total", "totals"}

WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
HALF_SUFFIXES = ("and a half", "and one half", "and half", "a half",
                 "point five", "point 5", "1/2", "half")

PUNCT_MAP = [("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
             ("—", "-"), ("–", "-"), ("½", " and a half")]

LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•>]|\(?\d+[.)])\s+")
POSSESSIVE_RE = re.compile(r"'s\b", re.IGNORECASE)
TRAILING_PUNCT_RE = re.compile(r"[.,;:!?]+$")
TRAILER_TOKENS = {"-", ":", "--"}

# --- Failure taxonomy -------------------------------------------------------
# Every code is fatal. STAGE_OF drives the grouped report.

STAGE_OF = {
    "E-GROUP-NOT-FOUND": "PREFLIGHT",
    "E-REFERENCE-NOT-FOUND": "PREFLIGHT",
    "E-CONFIG-RULES-MISSING": "PREFLIGHT",
    "E-EMPTY-INPUT": "PREFLIGHT",
    "E-NO-DIRECTION": "PARSE",
    "E-MULTI-DIRECTION": "PARSE",
    "E-EMPTY-TEAM": "PARSE",
    "E-NO-MANAGER": "PARSE",
    "E-UNKNOWN-MANAGER": "PARSE",
    "E-NUMBER-UNPARSEABLE": "PARSE",
    "E-AMBIGUOUS-TEAM": "RESOLVE",
    "E-UNKNOWN-TEAM": "RESOLVE",
    "E-NOT-IN-REFERENCE": "REFERENCE",
    "E-LINE-DISAGREES": "REFERENCE",
    "E-DUPLICATE-TEAM": "ROSTER",
    "E-SAME-TEAM-SAME-SIDE": "ROSTER",
    "E-PICK-COUNT": "ROSTER",
    "E-DISTINCT-CONFERENCES": "ROSTER",
    "E-MISSING-MANAGER": "ROSTER",
    "E-GATE-INTERNAL": "ROSTER",
    "E-REFUSE-OVERWRITE": "WRITE",
}
STAGE_ORDER = ["PREFLIGHT", "PARSE", "RESOLVE", "REFERENCE", "ROSTER", "WRITE"]

# validate_group_data's err() slugs -> this taxonomy. The five mapped to
# E-GATE-INTERNAL are structurally unreachable in a file this script writes
# (conference and line are DERIVED from the reference, never accepted from
# input), so a hit there is a bug in this script, not bad input.
SLUG_TO_CODE = {
    "not-canonical": "E-UNKNOWN-TEAM",
    "not-in-reference": "E-NOT-IN-REFERENCE",
    "duplicate-team": "E-DUPLICATE-TEAM",
    "same-team-same-side": "E-SAME-TEAM-SAME-SIDE",
    "picks-per-manager": "E-PICK-COUNT",
    "min-distinct-conferences": "E-DISTINCT-CONFERENCES",
    "rules-missing": "E-CONFIG-RULES-MISSING",
    "missing-conference": "E-GATE-INTERNAL",
    "conference-mismatch": "E-GATE-INTERNAL",
    "missing-line": "E-GATE-INTERNAL",
    "line-not-numeric": "E-GATE-INTERNAL",
    "reference-line-not-numeric": "E-GATE-INTERNAL",
}

EXIT_OK, EXIT_INVALID, EXIT_PREFLIGHT = 0, 1, 2


def problem(code, detail, line_no=None, raw="", manager=None, team=None):
    return {"code": code, "detail": detail, "line": line_no, "raw": raw,
            "manager": manager, "team": team}


# --- Line normalization -----------------------------------------------------

def _key(tok):
    """Lowercase-alnum form used for keyword/manager matching."""
    return re.sub(r"[^a-z0-9]", "", str(tok).lower())


def normalize_line(raw):
    s = raw
    for a, b in PUNCT_MAP:
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKC", s)
    s = LIST_MARKER_RE.sub("", s, count=1)
    s = POSSESSIVE_RE.sub("", s)
    s = TRAILING_PUNCT_RE.sub("", s.strip()).strip()
    return re.sub(r"\s+", " ", s)


def tokenize(line):
    out = []
    for tok in line.split():
        tok = tok.strip(",;:!?")
        if tok:
            out.append(tok)
    return out


# --- Number grammar ---------------------------------------------------------

def parse_number(tokens):
    """(value, None) on success — value is None when nothing was dictated.
    (None, reason) when a non-empty span cannot be read. An unreadable spoken
    number is fatal: it is exactly the case where a real disagreement with the
    frozen line would otherwise go unnoticed."""
    words = [t.lower() if re.match(r"^[\d./]+$", t) else _key(t)
             for t in tokens]
    while words and words[0] in NUMBER_NOISE:
        words.pop(0)
    while words and words[-1] in NUMBER_NOISE:
        words.pop()
    if not words:
        return None, None

    text = " ".join(w for w in words if w).strip()
    if not text:
        return None, None
    half = False
    for suffix in HALF_SUFFIXES:
        if text == suffix or text.endswith(" " + suffix):
            text = text[: len(text) - len(suffix)].strip()
            half = True
            break
    if not text:
        return None, f"'{' '.join(tokens)}' has a fraction but no whole number"

    if re.match(r"^\d+(\.\d+)?$", text):
        value = float(text)
    elif text in WORD_NUMBERS:
        value = float(WORD_NUMBERS[text])
    else:
        return None, f"cannot read '{' '.join(tokens)}' as a number"

    if half:
        if value != int(value):
            return None, (f"'{' '.join(tokens)}' combines a decimal with "
                          f"'a half'")
        value += 0.5
    return value, None


# --- Team resolution --------------------------------------------------------

def resolve_ladder(span):
    """resolve_team() with a tiny, DECLARED rewrite ladder. Returns
    (canonical, rewrite_note). Raises AmbiguityError / UnknownTeamError.

    AmbiguityError SHORT-CIRCUITS the ladder: a rewrite must never be able to
    launder an ambiguous token into a confident wrong answer, which is the exact
    failure the §9 guard exists to prevent."""
    variants = [(span, None)]
    if re.search(r"\band\b", span, re.IGNORECASE):
        variants.append((re.sub(r"\band\b", "&", span, flags=re.IGNORECASE),
                         "rewrote ' and ' -> '&'"))
    toks = span.split()
    if toks and _key(toks[-1]) in NOISE_TAIL and len(toks) > 1:
        variants.append((" ".join(toks[:-1]),
                         "dropped trailing '" + toks[-1] + "'"))

    first_unknown = None
    for cand, note in variants:
        if not cand.strip():
            continue
        try:
            return resolve_team(cand), note
        except AmbiguityError:
            raise
        except UnknownTeamError as e:
            if first_unknown is None:
                first_unknown = e
    raise first_unknown


# --- Parsing ----------------------------------------------------------------

def manager_index(config):
    """{normalized manager_id or display_name -> manager_id}."""
    idx = {}
    for m in config.get("managers", []):
        mid = m.get("manager_id")
        if not mid:
            continue
        idx[normalize_team_name(mid)] = mid
        dn = m.get("display_name")
        if dn:
            idx.setdefault(normalize_team_name(dn), mid)
    return idx


def find_pivot(tokens):
    """(index, direction, error_code). Full forms win; bare o/u are a last
    resort. Two direction keywords make segmentation undecidable -> fatal."""
    full = [(i, FULL_DIRECTIONS[_key(t)]) for i, t in enumerate(tokens)
            if _key(t) in FULL_DIRECTIONS]
    if len(full) == 1:
        return full[0][0], full[0][1], None
    if len(full) > 1:
        return None, None, "E-MULTI-DIRECTION"
    short = [(i, SHORT_DIRECTIONS[_key(t)]) for i, t in enumerate(tokens)
             if _key(t) in SHORT_DIRECTIONS]
    if len(short) == 1:
        return short[0][0], short[0][1], None
    if len(short) > 1:
        return None, None, "E-MULTI-DIRECTION"
    return None, None, "E-NO-DIRECTION"


def strip_head(tokens):
    """Drop trailer punctuation and filler verbs from the head of a team span."""
    out = list(tokens)
    while out and (out[0] in TRAILER_TOKENS or _key(out[0]) in FILLERS):
        out.pop(0)
    return out


def parse_block(text, config):
    """Stages 1-3. Returns (picks, traces, problems, failed_lines_by_manager).

    A line that fails one stage is excluded from the later stages but its
    problem is retained, so one run surfaces everything."""
    mgr_idx = manager_index(config)
    picks, traces, problems = [], [], []
    failed_by_mgr = {}
    current_manager = None

    def fail(code, detail, line_no, raw, manager=None, team=None):
        problems.append(problem(code, detail, line_no, raw, manager, team))
        if manager:
            failed_by_mgr.setdefault(manager, []).append(line_no)

    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        ended_colon = raw.rstrip().endswith(":")
        line = normalize_line(raw)
        if not line:
            continue
        tokens = tokenize(line)
        if not tokens:
            continue

        pivot, direction, perr = find_pivot(tokens)

        if perr == "E-NO-DIRECTION":
            # No direction keyword: this is a manager header, or it is broken.
            head = [t for t in tokens if t not in TRAILER_TOKENS]
            key = normalize_team_name("".join(head))
            if key in mgr_idx:
                current_manager = mgr_idx[key]
                traces.append({"kind": "header", "line": line_no,
                               "manager": current_manager, "raw": raw.strip()})
                continue
            if ended_colon or len(head) == 1:
                fail("E-UNKNOWN-MANAGER",
                     "'" + " ".join(head) + "' is not a manager in this group "
                     "— valid: " + ", ".join(sorted(set(mgr_idx.values()))),
                     line_no, raw.strip())
                continue
            fail("E-NO-DIRECTION",
                 "no 'over'/'under' keyword — a pick line is segmented around "
                 "the direction, so there is nothing to parse",
                 line_no, raw.strip())
            continue
        if perr == "E-MULTI-DIRECTION":
            fail("E-MULTI-DIRECTION",
                 "two or more direction keywords on one line — the team span "
                 "cannot be determined; re-dictate as one pick per line",
                 line_no, raw.strip())
            continue

        pre, post = tokens[:pivot], tokens[pivot + 1:]

        # manager: consumed only if the leading token(s) match the closed set
        manager, consumed = None, 0
        for n in (2, 1):
            if len(pre) >= n:
                cand = normalize_team_name("".join(pre[:n]))
                if cand in mgr_idx:
                    manager, consumed = mgr_idx[cand], n
                    break
        if manager is None:
            manager = current_manager
        else:
            current_manager = manager

        span_tokens = strip_head(pre[consumed:])
        if not span_tokens:
            fail("E-EMPTY-TEAM",
                 "nothing left of the direction keyword to read as a team",
                 line_no, raw.strip(), manager)
            continue
        if manager is None:
            fail("E-NO-MANAGER",
                 "no manager on this line and no manager header above it",
                 line_no, raw.strip(), team=" ".join(span_tokens))
            continue

        span = " ".join(span_tokens)

        spoken, nerr = parse_number(post)
        if nerr:
            fail("E-NUMBER-UNPARSEABLE", nerr, line_no, raw.strip(), manager, span)
            continue

        # stage 2 — resolve (the ambiguity guard is load-bearing here)
        try:
            canonical, rewrite = resolve_ladder(span)
        except AmbiguityError as e:
            fail("E-AMBIGUOUS-TEAM",
                 "'" + str(e.token) + "' could be " +
                 " or ".join(e.candidates) + " — re-dictate one of those; it "
                 "is never guessed",
                 line_no, raw.strip(), manager, span)
            continue
        except UnknownTeamError as e:
            hint = ("did you mean: " + ", ".join(e.suggestions) + "?"
                    if e.suggestions else "no close match in the roster")
            fail("E-UNKNOWN-TEAM",
                 "does not resolve (normalized '" + e.normalized + "') — " + hint,
                 line_no, raw.strip(), manager, span)
            continue

        # stage 3 — round-trip to a reference key, then derive the fields
        entry = gate.load_win_totals().get(normalize_team_name(canonical))
        if entry is None:
            fail("E-NOT-IN-REFERENCE",
                 "resolves to '" + canonical + "', which has no entry in " +
                 gate.WIN_TOTALS_PATH.name + " — there is no frozen draft-day "
                 "line to score this pick against",
                 line_no, raw.strip(), manager, span)
            continue

        ref_line = float(entry["win_total"])
        if spoken is not None and abs(spoken - ref_line) > 1e-9:
            fail("E-LINE-DISAGREES",
                 "dictated " + ("%g" % spoken) + " but " +
                 gate.WIN_TOTALS_PATH.name + " has '" + canonical + "' at " +
                 ("%g" % ref_line) + " — the line is frozen at draft (§1) and "
                 "a spoken number never overrides it",
                 line_no, raw.strip(), manager, canonical)
            continue

        picks.append({"manager": manager, "team": canonical,
                      "conference": entry["conference"],
                      "direction": direction, "line": ref_line})
        traces.append({
            "kind": "pick", "line": line_no, "manager": manager,
            "typed": span, "stored": canonical,
            "conference": entry["conference"], "direction": direction,
            "value": ref_line,
            "line_source": ("dictated and matched" if spoken is not None
                            else "taken from reference"),
            "note": rewrite or "",
        })

    return picks, traces, problems, failed_by_mgr


# --- Roster stage (the gate is the authority) -------------------------------

def roster_problems(group_id, config, picks, failed_by_mgr):
    """Stage 4. validate_group_data IS the check — its slugs are re-labelled
    here and annotated with paste-block line numbers. This script never
    reimplements a draft rule."""
    out = []

    for m in config.get("managers", []):
        mid = m.get("manager_id")
        if mid and not any(p["manager"] == mid for p in picks):
            detail = "has no picks in this block"
            if failed_by_mgr.get(mid):
                detail += (" (every line failed above: " +
                           ", ".join(str(n) for n in failed_by_mgr[mid]) + ")")
            out.append(problem("E-MISSING-MANAGER",
                               detail + " — a final draft has no empty rosters",
                               manager=mid, team="(roster)"))

    checked, errors = gate.validate_group_data(group_id, config, picks)
    for e in errors:
        code = SLUG_TO_CODE.get(e["problem"], "E-GATE-INTERNAL")
        detail = e["detail"]
        if code == "E-GATE-INTERNAL":
            detail = ("[" + e["problem"] + "] " + detail + " — conference and "
                      "line are DERIVED from the reference by enter_draft.py "
                      "and cannot come from input; this indicates a bug in "
                      "enter_draft.py, not bad input")
        derived = []
        for mid in re.split(r"\s*&\s*", str(e["manager"])):
            derived.extend(failed_by_mgr.get(mid.strip(), []))
        if derived:
            detail += (" (this manager also has " + str(len(derived)) +
                       " failed line(s) above: " +
                       ", ".join(str(n) for n in sorted(derived)) + ")")
        out.append(problem(code, detail, manager=e["manager"], team=e["team"]))
    return out, checked


# --- Reporting --------------------------------------------------------------

def print_problems(problems):
    print("FAILED — " + str(len(problems)) + " problem(s). Nothing written.\n")
    by_stage = {}
    for p in problems:
        by_stage.setdefault(STAGE_OF.get(p["code"], "ROSTER"), []).append(p)
    for stage in STAGE_ORDER:
        items = by_stage.get(stage)
        if not items:
            continue
        print("  " + stage)
        for p in items:
            where = (("line " + str(p["line"])) if p["line"]
                     else STAGE_OF.get(p["code"], "roster").lower())
            who = (" [" + str(p["manager"]) + "]") if p["manager"] else ""
            print("    [" + p["code"] + "] " + where + who)
            if p["raw"]:
                print("      input:    " + repr(p["raw"]))
            if p["team"]:
                print("      rejected: " + repr(p["team"]))
            print("      " + p["detail"])
        print()


def print_trace(traces):
    picks = [t for t in traces if t["kind"] == "pick"]
    headers = {t["line"]: t["manager"] for t in traces if t["kind"] == "header"}
    cols = ["#", "MANAGER", "DICTATED", "STORED", "CONF", "DIR", "LINE",
            "LINE SOURCE", "NOTE"]
    rows = [[str(t["line"]), t["manager"], t["typed"], t["stored"],
             t["conference"], t["direction"], "%g" % t["value"],
             t["line_source"], t["note"]] for t in picks]
    widths = []
    for i, c in enumerate(cols):
        widths.append(max([len(c)] + [len(r[i]) for r in rows]))
    fmt = "  " + "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*cols).rstrip())
    print("  " + "-" * (sum(widths) + 2 * (len(widths) - 1)))
    for r in rows:
        print(fmt.format(*r).rstrip())
    if headers:
        print("\n  manager headers: " +
              ", ".join("line " + str(n) + " -> " + m
                        for n, m in sorted(headers.items())))


def build_payload(group_id, status, picks, reference_path, reference_season):
    return {
        "_note": (
            "Written by scripts/enter_draft.py from a dictated paste block. "
            "`team` is the canonical name returned by utils.resolve_team (§9) "
            "— never a display_name, never an ambiguity candidate. "
            "`conference` and `line` are copied from " + reference_path.name +
            " (season " + str(reference_season) + "), the frozen draft-day "
            "reference; nothing dictated overrides them (§1). direction = "
            "\"O\" (over) | \"U\" (under), uppercase only — the engine tests "
            "`direction == \"O\"`. `manager` is the manager_id join key from "
            "config.json. Regenerate with enter_draft.py rather than "
            "hand-editing."
        ),
        "group_id": group_id,
        "draft_status": status,
        "picks": picks,
    }


# --- Entry point ------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Parse a dictated draft paste block into a validated picks.json")
    ap.add_argument("--group", required=True, help="group id (no default)")
    ap.add_argument("--in", dest="infile", default=None,
                    help="paste-block file (default: stdin)")
    ap.add_argument("--out", default=None,
                    help="write target (default: groups/<group>/picks.json)")
    ap.add_argument("--reference", default=str(DEFAULT_REFERENCE),
                    help="frozen draft-day reference (default: "
                         "data/team_win_totals_2026.json)")
    ap.add_argument("--status", choices=["final", "dummy"], default="final")
    ap.add_argument("--write", action="store_true",
                    help="actually write; WITHOUT this the run is a dry run")
    ap.add_argument("--force", action="store_true",
                    help="allow overwriting a picks.json already marked final")
    args = ap.parse_args()

    # --- preflight ---------------------------------------------------------
    pre = []
    cfg_path = utils.GROUPS_DIR / args.group / "config.json"
    if not cfg_path.exists():
        pre.append(problem("E-GROUP-NOT-FOUND", str(cfg_path) + " does not exist"))
    ref_path = Path(args.reference)
    if not ref_path.exists():
        pre.append(problem("E-REFERENCE-NOT-FOUND",
                           str(ref_path) + " does not exist"))
    if pre:
        print_problems(pre)
        return EXIT_PREFLIGHT

    config = utils.load_json(cfg_path)
    if config.get("picks_per_manager") is None or \
            config.get("min_distinct_conferences") is None:
        print_problems([problem(
            "E-CONFIG-RULES-MISSING",
            "picks_per_manager=" + repr(config.get("picks_per_manager")) +
            ", min_distinct_conferences=" +
            repr(config.get("min_distinct_conferences")) + " in " +
            str(cfg_path) + " — both are required; there is no unenforced path")])
        return EXIT_PREFLIGHT

    # Point the gate at the same reference this run uses, and drop its cache so
    # a --reference override cannot leave the two disagreeing.
    gate.WIN_TOTALS_PATH = ref_path
    gate._WIN_TOTALS = None
    reference_season = utils.load_json(ref_path).get("season")

    text = (Path(args.infile).read_text(encoding="utf-8") if args.infile
            else sys.stdin.read())
    if not text.strip():
        print_problems([problem("E-EMPTY-INPUT", "the paste block is empty")])
        return EXIT_PREFLIGHT

    out_path = Path(args.out) if args.out else \
        utils.GROUPS_DIR / args.group / "picks.json"

    # --- stages 1-3 --------------------------------------------------------
    picks, traces, problems, failed_by_mgr = parse_block(text, config)

    # --- stage 4 (always runs, so one run surfaces everything) --------------
    rprobs, _checked = roster_problems(args.group, config, picks, failed_by_mgr)
    problems.extend(rprobs)

    print("group:     " + args.group +
          " (" + str(config.get("display_name", args.group)) + ")")
    print("reference: " + str(ref_path) +
          "  (season " + str(reference_season) + ")")
    print("target:    " + str(out_path))
    print("status:    " + args.status)
    print("mode:      " + ("WRITE" if args.write else "DRY RUN (default)") + "\n")

    if problems:
        print_problems(problems)
        return EXIT_INVALID

    print("OK — " + str(len(picks)) + " pick(s) resolved, reference-checked "
          "and draft-rule-checked.\n")
    print_trace(traces)

    payload = build_payload(args.group, args.status, picks, ref_path,
                            reference_season)
    print("\n  payload that would be written:\n")
    for ln in json.dumps(payload, indent=2, ensure_ascii=False).splitlines():
        print("    " + ln)
    print()

    existing_final = (out_path.exists() and
                      utils.load_json(out_path).get("draft_status") == "final")

    if not args.write:
        if existing_final:
            print("NOTE: " + str(out_path) + " is already draft_status "
                  "\"final\" — --write will require --force.")
        print("DRY RUN — nothing written. Re-run with --write to commit.")
        return EXIT_OK

    if existing_final and not args.force:
        print_problems([problem(
            "E-REFUSE-OVERWRITE",
            str(out_path) + " is already draft_status \"final\"; pass --force "
            "to replace a committed draft")])
        return EXIT_INVALID

    utils.save_json_atomic(out_path, payload)
    print("WROTE " + str(len(picks)) + " pick(s) to " + str(out_path) +
          " (draft_status \"" + args.status + "\").")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
