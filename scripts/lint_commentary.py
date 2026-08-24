#!/usr/bin/env python3
"""Output-side lint for the generated SVP column — the enforcement half.

WHY THIS EXISTS. Every constraint the column keeps breaking has been fixed the
same way: another block in the prompt. Three revisions of that produced three
short columns (252 -> 332 -> 316 words) and, on the third, a REGRESSION -- the
prior-season ban that had been holding since a6c8aad let "Then again" through.
That is the predictable ceiling of prompt-only enforcement: every new block
competes for the model's attention with the blocks already there, and the
older ones lose. A page of prohibitions is also read as "the safest column is
the short one", which is where the word counts went.

So this module does not ask. It READS THE OUTPUT and reports what is wrong,
mechanically, after generation. The prompt still states every rule -- a model
that is told nothing writes nothing usable -- but the prompt is now advice and
this is the gate. generate_commentary.py runs it and retries against the
specific trips (see MAX_RETRIES there), so a violation costs a regeneration
rather than a filed column and a human noticing later.

ONE SOURCE OF TRUTH. PRIOR_SEASON_BANS lives here and generate_commentary
imports it to build its prompt block. The two lists drifting apart is exactly
how a ban gets stated to the model and then not checked, so there is only one.

RULES 1-6 are the specified set. RULE 7 (LENGTH) is an addition: the failure
this whole module was commissioned for is short columns, and a linter that
cannot see length would pass a 316-word column on attempt 1 and return. It is
one rule and one constant; delete LengthRule from RULES to drop it.

    python scripts/lint_commentary.py --group panel
    python scripts/lint_commentary.py --text col.md --packet packet.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# --- ban lists ---------------------------------------------------------------
# PRIOR_SEASON_BANS is imported by generate_commentary.build_prompt. Adding a
# word here adds it to the prompt AND to the gate, which is the point.

PRIOR_SEASON_BANS = (
    "again", "still", "once more", "finally", "no longer", "used to",
    "has become", "return", "returning", "resurgence", "comeback",
    "bounce-back", "rebound", "turnaround", "return to form",
    "reversal of fortune", "redemption", "revenge", "rebuild",
)

# "back" is banned in the RETURNING sense only, so it cannot be a bare word
# test -- "close to the chest", "background", "his back" are all fine and a
# bare \bback\b would trip the column for writing English. These are the
# returning constructions the prompt names, plus the came/come/comes/coming
# family that panel wk0 filed ("never quite came back").
PRIOR_SEASON_BACK_PHRASES = (
    "back on track", "back to form", "back where they belong",
    "back in the fold", "came back", "come back", "comes back",
    "coming back", "back at it",
)

# Both spellings of everything. Panel wk0 filed "The market gap here is a
# negative 1.044" and slipped the ban purely because the underscore was gone.
INTERNAL_STRUCTURES = (
    "packet", "cache", "manifest", "baseline", "schema",
    "storyline", "storyline pool", "coda", "coda pool", "coda candidate",
    "bad-beat candidate", "character bits", "column memory",
    "uniform profile fields", "manager profiles",
    "market_gap", "market gap",
    "narrative_score", "narrative score",
    "moment_size", "moment size",
    "delta_impact", "delta impact",
    "p_beat_line", "p beat line",
    "implied_expected_wins", "implied expected wins",
)

SUPERLATIVES = (
    "lowest", "highest", "only", "most", "biggest", "smallest",
    "worst", "best", "sharpest", "largest", "widest", "closest",
    "furthest", "least",
)

STAKES_TERMS = (
    "stakes", "at stake", "pride", "bragging rights", "needle",
    "on the line", "playing for", "glory",
)

# Rule 7. The prompt's own numbers, so the gate and the instruction agree.
TOTAL_MIN, TOTAL_MAX = 350, 450
BEAT1_MIN, BEAT1_MAX = 250, 300

REMEDIATION = {
    "PRIOR_SEASON":
        "Delete the sentence. Do not hedge it and do not swap in a "
        "near-synonym -- a synonym is the same violation with better manners. "
        "The packet carries no history, so any claim about a trajectory is "
        "invented. Replace the sentence with one about what is true now.",
    "INTERNAL_STRUCTURES":
        "The reader has never seen the packet and does not know one exists. "
        "Attribute the number to \"the model\", \"our projection\", \"SP+\" or "
        "\"the numbers\" -- or to nothing at all, and simply state it. Never "
        "read a field name aloud, with or without its underscore.",
    "SUPERLATIVES":
        "Describe what the pick IS, not where it sits in a sorted list: its "
        "number, its probability, the manager's character, what the "
        "disagreement actually claims. A hedge does not rescue a ranking "
        "claim -- \"one of the lowest\" and \"the lowest outside X\" are the "
        "same violation.",
    "INVENTED_STAKES":
        "There are no declared stakes for this group. Do not say or imply "
        "what anyone is playing for, what the winner gets, or what the loser "
        "suffers. Cut the clause; the column is about the picks, not a prize.",
    "RAW_PROBABILITY":
        "A probability is a whole-number percent on the page: 0.86467 is 86%. "
        "The decimal form never appears.",
    "RAW_ROUNDING":
        "Numbers other than probabilities are printed verbatim from the "
        "packet. Rounding a probability into a percent is required; it does "
        "not license rounding a win total.",
    "LENGTH":
        "The column is short, which is the predictable reading of a page of "
        "prohibitions -- the safest column is the shortest one. It is also "
        "wrong. Do not add stat recitation: expand the prose around the picks "
        "(context, character, what the disagreement says about the manager) "
        "until it lands in range.",
}


class Trip(object):
    """One rule violation, with enough context for a retry prompt to act on."""

    __slots__ = ("rule_name", "matched_text", "sentence", "remediation")

    def __init__(self, rule_name, matched_text, sentence):
        self.rule_name = rule_name
        self.matched_text = matched_text
        self.sentence = sentence
        self.remediation = REMEDIATION[rule_name]

    def as_dict(self):
        return {"rule": self.rule_name, "matched": self.matched_text,
                "sentence": self.sentence, "remediation": self.remediation}

    def __repr__(self):
        return "Trip(%s, %r)" % (self.rule_name, self.matched_text)


# --- text helpers ------------------------------------------------------------

_SENT_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text):
    """Split prose into sentences, paragraph by paragraph.

    Deliberately simple. A missed split makes a trip's `sentence` field longer
    than it needs to be; it never makes the trip itself wrong, because every
    rule matches on the token and only quotes the sentence for context.
    """
    out = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        out.extend(s.strip() for s in _SENT_END.split(para) if s.strip())
    return out


def paragraphs(text):
    return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


def find_term(term, sentence):
    """Word-boundary, case-insensitive. Returns the matched text or None.

    \\b works on the hyphenated and multi-word entries too: the boundary lands
    at the outer edges of the whole phrase.
    """
    pat = r"\b" + re.escape(term).replace(r"\ ", " ") + r"\b"
    m = re.search(pat, sentence, re.IGNORECASE)
    return m.group(0) if m else None


# Numbers that mark a sentence as making a quantitative claim.
_PCT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")
_DECIMAL = re.compile(r"(?<![\w.])-?\d*\.\d+")
_WINS = re.compile(r"\b\d+(?:\.\d+)?\s+wins?\b", re.IGNORECASE)
_HEDGED_WINS = re.compile(
    r"\b(about|roughly|around|nearly|almost|some|approximately)\s+"
    r"(\d+(?:\.\d+)?)\s+wins?\b", re.IGNORECASE)


# --- packet accessors --------------------------------------------------------

def packet_subjects(packet):
    """Every manager name/id and team name the column could be talking about."""
    names, teams = set(), set()
    if not packet:
        return names, teams

    def walk(o):
        if isinstance(o, dict):
            for key in ("name", "display_name", "manager_id"):
                v = o.get(key)
                if isinstance(v, str) and v:
                    names.add(v)
            v = o.get("team")
            if isinstance(v, str) and v:
                teams.add(v)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(packet)
    return names, teams


def packet_numbers(packet, key):
    """Every value of `key` anywhere in the packet, as floats."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get(key)
            if isinstance(v, (int, float)):
                found.append(float(v))
            for v2 in o.values():
                walk(v2)
        elif isinstance(o, list):
            for v2 in o:
                walk(v2)

    walk(packet)
    return found


def packet_expected_wins(packet):
    """team -> implied_expected_wins, for the rounding cross-check."""
    out = {}

    def walk(o):
        if isinstance(o, dict):
            t, w = o.get("team"), o.get("implied_expected_wins")
            if isinstance(t, str) and isinstance(w, (int, float)):
                out[t] = float(w)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(packet)
    return out


# --- rules -------------------------------------------------------------------
# Each takes (text, packet) and returns a list of Trip. Independently testable
# on purpose: a rule is a function, not a branch inside one big scanner.

def rule_prior_season(text, packet=None):
    trips = []
    for s in sentences(text):
        for term in PRIOR_SEASON_BANS:
            hit = find_term(term, s)
            if hit:
                trips.append(Trip("PRIOR_SEASON", hit, s))
        for phrase in PRIOR_SEASON_BACK_PHRASES:
            hit = find_term(phrase, s)
            if hit:
                trips.append(Trip("PRIOR_SEASON", hit, s))
    return trips


def rule_internal_structures(text, packet=None):
    trips = []
    for s in sentences(text):
        for term in INTERNAL_STRUCTURES:
            hit = find_term(term, s)
            if hit:
                trips.append(Trip("INTERNAL_STRUCTURES", hit, s))
    return trips


def rule_superlatives(text, packet=None):
    """CONTEXT test, not a raw word test.

    "odds only a Longhorn in-law could love" is a joke about in-laws and is
    fine. "implied odds of only 22%" sizes a probability and is not. The
    separator is whether the sentence is making a quantitative or comparative
    claim about a subject -- a number, or a manager/team the packet knows.
    Ambiguous cases TRIP by instruction: a false trip costs one regeneration,
    a miss ships the failure this rule exists for.
    """
    names, teams = packet_subjects(packet)
    trips = []
    for s in sentences(text):
        quantitative = bool(_PCT.search(s) or _DECIMAL.search(s)
                            or _WINS.search(s))
        about_subject = any(find_term(n, s) for n in names) or \
                        any(find_term(t, s) for t in teams)
        if not (quantitative or about_subject):
            continue
        for term in SUPERLATIVES:
            hit = find_term(term, s)
            if hit:
                trips.append(Trip("SUPERLATIVES", hit, s))
    return trips


def rule_invented_stakes(text, packet=None):
    """Only meaningful when the group declared no stakes."""
    if packet is not None and packet.get("stakes"):
        return []
    trips = []
    for s in sentences(text):
        for term in STAKES_TERMS:
            hit = find_term(term, s)
            if hit:
                trips.append(Trip("INVENTED_STAKES", hit, s))
    return trips


def rule_raw_probability(text, packet=None):
    """A decimal in 0-1 on the page, unless it is a market gap.

    Market gaps ARE printed verbatim -- that is the house rule -- and they can
    land inside 0-1 (0.669 is a real one), so the whitelist is the packet's own
    gap values rather than a range test.
    """
    gaps = set()
    for g in packet_numbers(packet, "market_gap"):
        gaps.add(round(abs(g), 3))
    trips = []
    for s in sentences(text):
        for m in _DECIMAL.finditer(s):
            raw = m.group(0)
            try:
                val = abs(float(raw))
            except ValueError:
                continue
            if round(val, 3) in gaps:
                continue          # a market gap, expected as-is
            if 0.0 < val < 1.0:
                trips.append(Trip("RAW_PROBABILITY", raw, s))
    return trips


def rule_raw_rounding(text, packet=None):
    """A hedged win total that does not match the packet's own number."""
    wins = packet_expected_wins(packet)
    if not wins:
        return []
    trips = []
    for s in sentences(text):
        mentioned = [t for t in wins if find_term(t, s)]
        if not mentioned:
            continue
        for m in _HEDGED_WINS.finditer(s):
            printed = float(m.group(2))
            if all(abs(wins[t] - printed) > 1e-9 for t in mentioned):
                trips.append(Trip("RAW_ROUNDING", m.group(0), s))
    return trips


def rule_length(text, packet=None):
    """RULE 7 -- the addition. See the module docstring.

    Beat 1 is everything before the coda, and the coda is the paragraph that
    addresses the worst-pick manager in the second person. Falling back to
    "everything but the last two paragraphs" keeps the rule working on a
    packet that carries no coda block rather than crashing on one.
    """
    paras = paragraphs(text)
    if not paras:
        return [Trip("LENGTH", "empty column", "")]

    coda_name = ((packet or {}).get("worst_pick_on_the_board") or {}).get("name")
    coda_i = None
    if coda_name:
        first = coda_name.split()[0]
        for i, p in enumerate(paras):
            if find_term(first, p) and re.search(r"\byou(r|'ve|'re)?\b", p, re.I):
                coda_i = i
                break
    if coda_i is None:
        coda_i = max(1, len(paras) - 2)

    total = len(" ".join(paras).split())
    beat1 = len(" ".join(paras[:coda_i]).split())

    trips = []
    if not (TOTAL_MIN <= total <= TOTAL_MAX):
        trips.append(Trip(
            "LENGTH", "%d words total" % total,
            "The whole column runs %d words; the target is %d-%d."
            % (total, TOTAL_MIN, TOTAL_MAX)))
    if not (BEAT1_MIN <= beat1 <= BEAT1_MAX):
        trips.append(Trip(
            "LENGTH", "%d words in Beat 1" % beat1,
            "Beat 1 (the One Big Thing) runs %d words; the target is %d-%d."
            % (beat1, BEAT1_MIN, BEAT1_MAX)))
    return trips


RULES = (
    rule_prior_season,
    rule_internal_structures,
    rule_superlatives,
    rule_invented_stakes,
    rule_raw_probability,
    rule_raw_rounding,
    rule_length,
)


def lint(text, packet=None, rules=None):
    """Every trip in `text`, deduped on (rule, matched, sentence)."""
    trips, seen = [], set()
    for rule in (rules or RULES):
        for t in rule(text, packet):
            key = (t.rule_name, t.matched_text.lower(), t.sentence)
            if key in seen:
                continue
            seen.add(key)
            trips.append(t)
    return trips


def format_trips(trips):
    """Human-readable summary, one block per trip."""
    if not trips:
        return "lint clean: 0 trips."
    by_rule = {}
    for t in trips:
        by_rule.setdefault(t.rule_name, []).append(t)
    lines = ["%d trip(s) across %d rule(s):" % (len(trips), len(by_rule))]
    for rule in sorted(by_rule):
        lines.append("  %s (%d)" % (rule, len(by_rule[rule])))
        for t in by_rule[rule]:
            lines.append("    - %r" % t.matched_text)
            lines.append("      in: %s" % t.sentence[:160])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", help="lint this group's filed column")
    ap.add_argument("--text", help="path to a column .md instead of --group")
    ap.add_argument("--packet", help="path to the week packet")
    ap.add_argument("--week", type=int, default=0)
    ap.add_argument("--json", action="store_true", help="emit trips as JSON")
    a = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    if a.text:
        text_path = Path(a.text)
    elif a.group:
        text_path = (root / "groups" / a.group / "output"
                     / ("column_week_%d.md" % a.week))
    else:
        ap.error("one of --group or --text is required")

    if not text_path.is_file():
        raise SystemExit("ERROR: no column at %s" % text_path)
    text = text_path.read_text(encoding="utf-8")

    packet = None
    packet_path = Path(a.packet) if a.packet else (
        root / "groups" / a.group / "output" / "week_packet.json"
        if a.group else None)
    if packet_path and packet_path.is_file():
        packet = json.loads(packet_path.read_text(encoding="utf-8"))

    trips = lint(text, packet)
    if a.json:
        print(json.dumps([t.as_dict() for t in trips], indent=2))
    else:
        print("%s  (%s)" % (text_path.name,
                            "packet loaded" if packet else "NO PACKET"))
        print(format_trips(trips))
    return 1 if trips else 0


if __name__ == "__main__":
    sys.exit(main())
