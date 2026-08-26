#!/usr/bin/env python3
"""
column_guard.py -- refuse to publish a column that asserts what the packet does not.

WHY THIS EXISTS. templates/svp_persona.md sacred rule 1 is that every fact in
the column comes from the week packet. The prompt says so at length. A prompt is
not an enforcement mechanism: it is a request the model can decline silently, on
a week nobody is reading closely, and the failure looks exactly like good prose.
This is the check that runs afterwards and can say no.

DETERMINISTIC AND CHEAP, on purpose. Regex and set membership, no model call and
no second pass. A judge that is itself a model has the same failure mode as the
writer and costs another call to have it.

WHAT IT DOES NOT DO. It does not read for sense, tone, or truth in any general
way. It answers one narrow question -- "is this number, or this class of claim,
something the packet could have supported?" -- and it answers conservatively in
both directions: it will not catch an invented CHARACTER claim, and it will
occasionally flag a harmless number. Both are stated where they matter below.

THE CASE THAT SHAPED IT. Panel's week 0 column filed "a challenging SEC
schedule, which boasts eight top-25 opponents". That reads exactly like a model
reciting the 2026 season from memory, and it was reported as a fabrication.
It is not one: the packet carries strength_of_schedule.opponents_sp_top25 = 8
for Texas. A guard built on the keywords alone -- "top-25", "opponents" -- would
have rejected a true sentence sourced from a real field, and would have taught
whoever hit it that the guard cries wolf. So the vocabulary tiers below are
split by WHAT THE PACKET CAN SUPPORT, not by what sounds like recall, and the
numeric check is traceability rather than a blocklist.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "data" / "teams_canonical.json"


# --- Tier 1: vocabulary NO packet field can support --------------------------
# Nothing in docs/output-contract.md or the week packet describes a roster, a
# staff, a game result, a poll, or a postseason. A column using these words is
# not quoting the packet, because there is nothing in the packet to quote.
#
# Substring match on the lowercased column, so each entry has to be a string
# that cannot occur innocently. "back" is a prior-season word and belongs to the
# prompt's own ban, not here -- it is far too common to test this way.
HARD_BANNED = (
    # Roster and staff
    "quarterback", "running back", "wide receiver", "linebacker",
    "defensive line", "offensive line", "depth chart", "returning starter",
    "head coach", "coaching staff", "coordinator", "transfer portal",
    "recruiting class", "signing day", "walk-on", "redshirt",
    # Health
    "injury", "injured", "torn acl", "out for the season",
    # Polls and rankings BY NAME (the packet's only ranking is an SP+ count)
    "ap poll", "coaches poll", "preseason poll", "heisman",
    # Postseason (no packet field describes one)
    "national championship", "college football playoff", "bowl game",
    "conference championship game", "final four",
    # Prior-season results (the cache is 2026-only; the prompt bans the
    # vocabulary, this catches the explicit phrasings)
    "last season", "last year", "a year ago", "previous season",
    # Schedule specifics. The packet carries COUNTS -- games_scheduled, and the
    # strength-of-schedule summary -- and no opponent, no date, no venue. Any
    # sentence placing a game somewhere or against someone is recall.
    "travel to", "travels to", "road trip", "at home against",
    "opens against", "opens the season against", "rivalry game",
)

# MONTHS, matched CASE-SENSITIVELY as proper nouns and not as substrings of the
# lowercased column. As lowercase substrings they are a false-positive machine:
# "may" is a modal verb, "march" and "august" are ordinary words, and the first
# regenerated column under this guard was rejected for "SP+ predictions may
# suggest an outcome" -- which asserts nothing about a calendar at all. A date
# in a column is a capitalised month, so that is what this looks for.
MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\b")

# A win-loss record. No packet field carries one -- standings.json counts
# BANKED DELTA against a line, never a team's record -- so any W-L pair is
# recall regardless of whether both integers happen to occur in the packet.
# "11-2" slipped the numeric check for exactly that reason: 11 is a
# games_scheduled and 2 is everywhere.
RECORD_RE = re.compile(r"\b\d{1,2}\s*[-\u2013]\s*\d{1,2}\b")

# A ranking claim. The packet's ONLY ranking is collisions[].sp_ranking, so a
# rank is licensed solely by matching one of those values -- not by appearing
# somewhere in the packet, which "4th" would (there are fours everywhere).
RANK_RE = re.compile(
    r"\branked\s+(?:no\.?\s*)?(\d+)|\bno\.\s*(\d+)\b|"
    r"\b(\d+)(?:st|nd|rd|th)\s+(?:in|nationally|overall|in the country)\b")

# --- Tier 2: vocabulary the packet CAN support, but only if the field is here -
# Each entry is (phrase, packet_field_that_licenses_it). The phrase is allowed
# when that field name appears anywhere in the packet JSON and refused when it
# does not -- so the same sentence is legal for a packet carrying strength of
# schedule and illegal for one that does not.
CONDITIONAL = (
    ("top-25", "opponents_sp_top25"),
    ("top 25", "opponents_sp_top25"),
    ("strength of schedule", "strength_of_schedule"),
    ("opponent", "strength_of_schedule"),
    ("scheduled game", "games_scheduled"),
)

# --- Numbers -----------------------------------------------------------------
# Spelled-out integers are only CHECKED when they modify a countable claim --
# "eight top-25 opponents" is a factual assertion, "a thing or two about bold
# moves" is not, and a guard that cannot tell them apart is a guard that gets
# switched off. Digits are always checked.
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}
CLAIM_NOUNS = (
    "win", "wins", "loss", "losses", "game", "games", "opponent", "opponents",
    "team", "teams", "point", "points", "season", "seasons", "week", "weeks",
    "yard", "yards", "ranking", "rankings", "percent", "chance", "odds",
    "conference", "conferences", "spot", "spots",
)

# Lexical units that contain digits but are not numeric claims: the digits are
# part of the name of a thing, not a measurement.
LITERAL_TOKENS = ("top-25", "top 25", "sp+", "24/7", "50-50")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'“‘(]?[A-Z0-9])")
# Case-SENSITIVE, on the original text: a school is a proper noun, and matching
# lowercased would turn "army", "navy", "rice" and "ohio" into false positives
# the first time a column reaches for one as an ordinary word.
_TEAM_RE_CACHE = {}
_DIGITS = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*|\d+(?:\.\d+)?%?")


class Violation:
    def __init__(self, kind, detail, sentence):
        self.kind = kind
        self.detail = detail
        self.sentence = sentence

    def __repr__(self):
        return f"<{self.kind}: {self.detail}>"


def canonical_teams():
    """{school} from the canonical spine, or an empty set if it is unreadable.

    DEGRADES TO SILENCE, deliberately. This file is not an input the column
    pipeline otherwise needs, and a guard that cannot be run is worse than a
    guard with one fewer rule -- it would fail every column on a bad path.
    """
    if "teams" not in _TEAM_RE_CACHE:
        try:
            doc = json.loads(CANONICAL.read_text(encoding="utf-8"))
            _TEAM_RE_CACHE["teams"] = {t["school"] for t in doc.get("teams", [])
                                       if t.get("school")}
        except (OSError, ValueError, KeyError):
            print("::warning:: column_guard: teams_canonical.json unreadable; "
                  "the opponent-name rule is skipped this run.", file=sys.stderr)
            _TEAM_RE_CACHE["teams"] = set()
    return _TEAM_RE_CACHE["teams"]


def _walk_numbers(node, out):
    if isinstance(node, bool):
        return                                  # True/False are not numbers
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _walk_numbers(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_numbers(v, out)


def packet_numbers(packet):
    """Every string a packet number could legitimately be printed as.

    The renderings matter as much as the values. A probability is stored as
    0.86467 and the prompt REQUIRES it be printed as a rounded percent, so 86
    has to be traceable to it. A win total stored as 9.456 may honestly appear
    as 9.456, 9.46 or 9.5. Emitting every allowed rendering here is what lets
    the scan stay a set membership test with no arithmetic at match time.
    """
    raw = []
    _walk_numbers(packet, raw)
    allowed = set()
    for v in raw:
        av = abs(v)
        if av == int(av):
            allowed.add(str(int(av)))
        for places in (1, 2, 3):
            allowed.add(f"{av:.{places}f}")
            allowed.add(f"{av:.{places}f}".rstrip("0").rstrip("."))
        allowed.add(repr(av))
        allowed.add(str(av))
        # Probabilities, as the percents the prompt insists on.
        if 0.0 <= av <= 1.0:
            allowed.add(str(int(av * 100 + 0.5)))
            allowed.add(f"{av * 100:.1f}")
            allowed.add(f"{av * 100:.1f}".rstrip("0").rstrip("."))
    allowed.discard("")
    return allowed


def sentences(text):
    parts = []
    for block in text.replace("\r\n", "\n").split("\n"):
        block = block.strip()
        if block:
            parts.extend(s.strip() for s in _SENTENCE_END.split(block) if s.strip())
    return parts


def _mask_literals(low):
    """Blank out the digit-bearing names so their digits are not read as claims.
    Same length replacement, so offsets stay usable if this ever needs them."""
    for tok in LITERAL_TOKENS:
        low = low.replace(tok, " " * len(tok))
    return low


def check_column(text, packet):
    """[Violation]. Empty means nothing this check can see is unsupported."""
    allowed = packet_numbers(packet)
    packet_keys = set()

    def collect_keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                packet_keys.add(k)
                collect_keys(v)
        elif isinstance(node, list):
            for v in node:
                collect_keys(v)
    collect_keys(packet)

    # Teams the packet actually talks about -- the drafted ones, plus any named
    # in persona material, which is packet content too. Membership is tested
    # against the packet's raw JSON text rather than a field walk so a school
    # mentioned inside a backstory string counts as licensed.
    packet_text = json.dumps(packet, ensure_ascii=False)
    known_teams = {t for t in canonical_teams() if t in packet_text}
    foreign_teams = canonical_teams() - known_teams

    # sp_ranking is the packet's only ranking. Collected separately from the
    # general number pool: a rank claim has to match a RANK, not any integer.
    ranks = set()

    def collect_ranks(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "sp_ranking" and isinstance(v, (int, float)):
                    ranks.add(str(int(v)))
                collect_ranks(v)
        elif isinstance(node, list):
            for v in node:
                collect_ranks(v)
    collect_ranks(packet)

    out = []
    for sent in sentences(text):
        low = sent.lower()

        for m in MONTH_RE.finditer(sent):
            out.append(Violation(
                "UNSUPPORTABLE",
                f"{m.group(0)!r} places a game on the calendar; the packet "
                f"carries no dates and no opponent schedule",
                sent))

        for m in RECORD_RE.finditer(sent):
            out.append(Violation(
                "UNSUPPORTABLE",
                f"{m.group(0)!r} reads as a win-loss record; no packet field "
                f"carries a team's record",
                sent))

        for m in RANK_RE.finditer(low):
            claimed = next(g for g in m.groups() if g)
            if claimed not in ranks:
                out.append(Violation(
                    "UNSUPPORTABLE",
                    f"ranking claim {claimed!r}; the packet's only ranking is "
                    f"sp_ranking ({sorted(ranks) or 'none in this packet'})",
                    sent))

        for team in foreign_teams:
            if re.search(r"\b" + re.escape(team) + r"\b", sent):
                out.append(Violation(
                    "UNSUPPORTABLE",
                    f"{team!r} is a real team the packet never mentions — the "
                    f"packet names no opponents, only schedule counts",
                    sent))

        for phrase in HARD_BANNED:
            if phrase in low:
                out.append(Violation(
                    "UNSUPPORTABLE",
                    f"{phrase!r} — no packet field describes rosters, staff, "
                    f"polls, postseason or prior seasons",
                    sent))

        for phrase, field in CONDITIONAL:
            if phrase in low and field not in packet_keys:
                out.append(Violation(
                    "UNLICENSED",
                    f"{phrase!r} needs packet field {field!r}, which this "
                    f"packet does not carry",
                    sent))

        masked = _mask_literals(low)

        for tok in _DIGITS.findall(masked):
            if tok in allowed or tok.rstrip("0").rstrip(".") in allowed:
                continue
            out.append(Violation(
                "UNTRACEABLE NUMBER",
                f"{tok} appears in no packet field, at any rounding",
                sent))

        words = _WORD.findall(masked)
        for i, w in enumerate(words):
            if w not in NUMBER_WORDS:
                continue
            window = words[i + 1:i + 4]
            if not any(n in window for n in CLAIM_NOUNS):
                continue                        # "a thing or two" — not a claim
            if str(NUMBER_WORDS[w]) in allowed:
                continue
            out.append(Violation(
                "UNTRACEABLE NUMBER",
                f"{w!r} ({NUMBER_WORDS[w]}) modifies a countable claim and "
                f"appears in no packet field",
                sent))

    seen, unique = set(), []
    for v in out:
        key = (v.kind, v.detail, v.sentence)
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


def report(group_id, violations, stream=sys.stderr):
    """Loud, and names the sentence. A guard that says only 'failed' sends the
    next person to diff two 400-word columns by eye."""
    print(f"::error:: [{group_id}] FACTUAL GUARD: {len(violations)} unsupported "
          f"claim(s). The column was NOT published.", file=stream)
    for v in violations:
        print(f"::error:: [{group_id}]   {v.kind}: {v.detail}", file=stream)
        print(f"             in: {v.sentence}", file=stream)
