#!/usr/bin/env python3
"""
generate_commentary.py — The one pundit (ARCHITECTURE §8 ADAPT, build order §10.8).

Job: narrative garnish only — LLMs NEVER touch scoring (§2). This script does no
arithmetic. It assembles a prompt out of three already-written artifacts and, in
live mode, posts it to OpenAI:

  templates/svp_persona.md                        the voice (system prompt)
  groups/<group>/output/week_packet.json          the week's numbers + storylines
  groups/<group>/output/column_memory.json        season-long continuity
  groups/<group>/output/column_week_<N>.md        last week's column, for callbacks

Live mode writes the .md above (the source of record — what memory names, what
next week reads back for callbacks, what a human reviews) and publishes the same
text into the site's column ARCHIVE:

  docs/data/<group>/columns/week_<N>.json   one filed column, WRITE-ONCE-PER-WEEK
  docs/data/<group>/columns/index.json      the manifest, DERIVED from those files

The archive ACCUMULATES. It replaced a single docs/data/<group>/column.json that
held the newest column only and was overwritten every week, which meant week 1
erased week 0 from the site while the .md sources quietly piled up unpublished.

Filing week N opens week N's file and no other, so a publish cannot mutate an
already-filed column — the guarantee is structural, not a correctness argument
about merge logic. The index carries no prose and no timestamp of its own: it is
rebuilt by scanning the published week files, so re-running with nothing new
produces byte-identical output, and a lost index is regenerable (--reindex)
where a lost week file is not.

Persona: DECIDED — a Scott Van Pelt parody, a SINGLE pinned voice across all
three groups (ARCHITECTURE §12, landed 2026-07-27). Rome / Herbstreit / Berman
were dropped; do not reopen. The voice lives in the template and is passed
verbatim — this script composes, it does not characterize.

FAIL-SOFT IS THE POINT (playbook rule 3, ARCHITECTURE §4). Commentary is the
last and least important step in the pipeline. In live mode ANY failure — no key,
no packet, a dead API, a malformed response — logs a ::warning:: and exits 0, so
a missing column can never take down standings, the site deploy, or the rest of
the run. Note that utils.load_json() calls sys.exit(1) on a missing file, which
raises SystemExit, NOT Exception — so the live guard catches both, and the
loaders below raise instead of exiting.

--dry-run is developer-facing and does the opposite: it builds the complete
prompt, writes it to groups/<group>/output/commentary_prompt_preview.txt, makes
NO network call, and is allowed to exit non-zero so a broken prompt is loud.

Usage:
    python scripts/generate_commentary.py --group panel --dry-run   # no network
    python scripts/generate_commentary.py --group panel             # live (needs key)
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
import column_guard

# --- Knobs -------------------------------------------------------------------

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
TEMPERATURE = 0.8          # a column, not a report
MAX_TOKENS = 1200          # ~400 words with headroom
REQUEST_TIMEOUT = 60

# Two distinct retry loops, because they are different failure modes
# (playbook rule 2). Transient network errors back off fast; a rate limit has to
# wait out the provider's window.
NETWORK_BACKOFF = (5, 10, 20)
RATE_LIMIT_WAIT = 60
RATE_LIMIT_RETRIES = 2

# How many filed columns to remember. The pundit only reads the newest one; the
# list is continuity bookkeeping, not a corpus.
MEMORY_COLUMNS_KEPT = 8

PERSONA_PATH = utils.ROOT / "templates" / "svp_persona.md"


# --- Paths -------------------------------------------------------------------

def out_dir(group_id):
    return utils.GROUPS_DIR / group_id / "output"


def packet_path(group_id):
    return out_dir(group_id) / "week_packet.json"


def memory_path(group_id):
    return out_dir(group_id) / "column_memory.json"


def preview_path(group_id):
    return out_dir(group_id) / "commentary_prompt_preview.txt"


def column_path(group_id, week):
    return out_dir(group_id) / f"column_week_{week}.md"


def columns_dir(group_id):
    """The published archive for one group: docs/data/<group_id>/columns/."""
    return utils.WEB_DATA_DIR / group_id / "columns"


def publish_path(group_id, week):
    """One filed column's published form — docs/data/<group>/columns/week_<N>.json.

    ACCUMULATE, and write-once per week. This is the PUBLISHED form of the .md
    under groups/<group>/output/: same text, pre-split into paragraphs and
    word-counted in Python, because the site renders JSON and computes nothing
    (playbook P2 #12). Writing it from here rather than from a separate publish
    step keeps the two from ever describing different columns.

    Filing week N touches week N's file ONLY. Re-filing the same week after a
    prompt fix overwrites that one file, which is what a prompt fix is for; no
    other week is opened, read, or rewritten, so the archive cannot lose a
    column to a bad publish the way the old newest-only column.json did.

    It is also the DURABLE record of when a column was filed. The .md carries no
    date, week or byline inside it — week lives in its filename, everything else
    lives here — so this file, not the source, is what the index is built from.
    """
    return columns_dir(group_id) / f"week_{week}.json"


def index_path(group_id):
    """The archive manifest — docs/data/<group>/columns/index.json. DERIVED."""
    return columns_dir(group_id) / "index.json"


# --- Loaders (raise; never sys.exit — the live guard needs a catchable error) --

def read_text(path, what):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{what} not found: {p}")
    return p.read_text(encoding="utf-8")


def read_json(path, what):
    return json.loads(read_text(path, what))


def empty_memory(group_id):
    """First-run shape. `columns` is the ONLY key this script ever writes.

    nicknames / feuds / character_bits are CURATED BY HAND — deliberately no
    LLM-based extraction. A model inventing its own continuity is how a pundit
    starts citing a feud that never happened."""
    return {
        "group_id": group_id,
        "_note": ("Hand-curated season continuity. generate_commentary.py only "
                  "ever appends to `columns`; nicknames/feuds/character_bits are "
                  "edited by a human. Nicknames must be earned by behavior in a "
                  "filed column before they are added here."),
        "nicknames": {},
        "feuds": [],
        "character_bits": {},
        "columns": [],
    }


def load_memory(group_id):
    p = memory_path(group_id)
    if not p.exists():
        return empty_memory(group_id)
    mem = json.loads(p.read_text(encoding="utf-8"))
    for key, default in (("nicknames", {}), ("feuds", []),
                         ("character_bits", {}), ("columns", [])):
        mem.setdefault(key, default)
    return mem


def last_column_text(group_id, memory, skip_week=None):
    """The most recently filed column, for callbacks. Missing file is not an
    error — a deleted or not-yet-written column just means no callback.

    skip_week EXCLUDES one week's own file, and it is the week being written.
    Filing a column is idempotent — the same week can be regenerated after a
    prompt fix, which is exactly what a prompt fix is FOR — and on that path the
    newest name in memory is the file about to be overwritten. Handing it back
    as "last published column" tells the model to write callbacks to a draft
    that is being discarded, and the discarded draft is usually discarded
    because it was wrong: panel's first Week 0 column carried the prior-season
    language a6c8aad bans and the coda collision 8e327ce forbids, and feeding it
    in as an exemplar is the most direct way to get both back.
    """
    skip = (f"column_week_{skip_week}.md" if skip_week is not None else None)
    for name in reversed(memory.get("columns", [])):
        if name == skip:
            continue
        p = out_dir(group_id) / name
        if p.exists():
            return name, p.read_text(encoding="utf-8")
    return None, None


# --- Prompt assembly ---------------------------------------------------------

def build_prompt(group_id, packet_override=None):
    """Returns (system_text, user_text, packet). Shared by both modes so the
    dry-run preview is the real prompt, not an approximation of it.

    packet_override reads the packet from an explicit path instead of the
    group's regenerated week_packet.json. It exists for the Week 0 preseason
    packet, which lives at its own durable path (output/<group>/week_0_packet
    .json) because it is written once before kickoff rather than overwritten
    every run. Default None keeps every normal week on the usual path."""
    system_text = read_text(PERSONA_PATH, "persona template")
    packet = read_json(packet_override or packet_path(group_id),
                       "week packet (run build_week_packet.py first)")
    memory = load_memory(group_id)
    last_name, last_text = last_column_text(group_id, memory,
                                            skip_week=packet.get("week"))

    cmp_ = packet.get("comparison", {})
    basis = cmp_.get("basis", "unknown basis")
    elapsed = cmp_.get("weeks_elapsed")

    # WEEK 0. The preseason packet (scripts/preseason_baseline.py --week0-packet)
    # is the one packet describing a board where nothing has happened yet, so the
    # two beats change shape: there are no results to mine for a bad beat, and
    # every "movement" instruction below would be describing motion that does not
    # exist. Everything in this branch is additive and gated on the flag — a
    # normal week's prompt is byte-identical to what it was before.
    preseason = packet.get("preseason") is True

    # The packet's movement fields are named *_this_week but measure the gap to
    # the previous snapshot. If that gap is not one week, say so in the prompt —
    # otherwise the column compresses a multi-week move into one Saturday, which
    # is a false claim built out of true numbers.
    if preseason:
        basis_warning = (
            "THIS IS WEEK 0 — the draft, before a single snap has been played. "
            "No game has happened, so the packet carries NO final scores, NO "
            "margins, NO results and NO standings worth the name: total_delta is "
            "0.0 for every manager, gap_to_leader is 0.0 for every manager, and "
            "NOBODY LEADS. The standings order is projected pool odds, not earned "
            "position, so do not call anyone the leader, do not say anyone is "
            "ahead of or behind anyone, and do not describe movement of any kind. "
            "Every number in this packet is a projection from SP+ ratings and the "
            "frozen Vegas win-total lines.")
    elif elapsed is None:
        basis_warning = (
            "There is NO prior snapshot, so every *_this_week field is null. Do "
            "not describe week-over-week movement at all; write from the "
            "standings and the picks as they stand.")
    elif elapsed == 1:
        basis_warning = "Movement fields cover exactly one week. 'This week' is literal."
    else:
        basis_warning = (
            f"IMPORTANT: the fields named *_this_week actually measure movement "
            f"{basis} — {elapsed} weeks, not one. Do not call that 'this week'. "
            f"Say '{basis}' or 'over the last {elapsed} weeks'.")

    stakes = packet.get("stakes")
    # THE STAKES BAN, rewritten because the short form leaked. "This group has
    # no declared stakes. Do not invent any." names the field but not the
    # BEHAVIOUR, and the model does not read "stakes" as covering "what they
    # are playing for" -- so panel wk0 filed "both playing for pride and,
    # possibly, the chance to needle the other until next year", which invents
    # a prize, a motivation and a consequence out of a None. Same failure shape
    # as the prior-season ban before it became a word test: name the words.
    stakes_line = (f"Stakes for this group: {stakes}. You may reference them by "
                   f"their actual terms." if stakes else
                   "packet.stakes is None. Do not invent stakes, motivations "
                   "for winning, or what the managers are \"playing for\". Do "
                   "not write phrases like \"playing for pride\", \"bragging "
                   "rights\", \"the chance to needle\", or any description of "
                   "what the winner gets or the loser suffers. The column is "
                   "about the picks, not the prize.\n"
                   "  THIS SHIPPED AND WAS WRONG: \"both playing for pride "
                   "and, possibly, the chance to needle the other until next "
                   "year\". Nothing in the packet says they are playing for "
                   "anything. A season of ribbing, bragging rights, pride, "
                   "the loser buying dinner, who has to hear about it until "
                   "next draft -- all invented, all banned.\n"
                   "  There is no soft version of this. If a sentence says or "
                   "implies what is at stake, cut it.")

    # The packet publishes season_complete, but a bool buried in 12KB of JSON is
    # not an instruction — the same reason basis_warning and stakes_line exist as
    # prose rather than trusting the model to read the field. A finished season
    # read as a live one produces forward-looking sign-offs ("only time and more
    # Saturdays will tell") against a board where every pick has already
    # resolved, which is a false claim assembled out of true numbers.
    season_over = packet.get("season_complete")
    if preseason:
        season_line = (
            "The season has not started. Games remain to be played — all of "
            "them — and every pick is unresolved.")
    elif season_over is True:
        season_line = (
            "THE SEASON IS OVER — every game on the schedule has been played and "
            "nothing is left to decide. Do not write forward-looking prose: no "
            "'more Saturdays to come', no 'time will tell', no looking ahead to "
            "next week or to a rematch. This is the last word on a finished "
            "season, so write it in the past tense and settle it.")
    elif season_over is False:
        season_line = ("The season is still running — games remain to be played, "
                       "and picks that are still LIVE can still move.")
    else:
        season_line = ("Whether the season is finished is UNKNOWN (this packet "
                       "predates the season_complete field). Do not claim either "
                       "way, in either direction.")

    # Same reasoning as season_line: a dict buried in 12KB of JSON is not an
    # instruction. Week 16 the column read picks_alive: 0 off ONE profile and
    # filed "the only manager with no live picks" -- true number, fabricated
    # exclusivity, on a board where all four were zero (persona sacred rule 7).
    uniform = packet.get("uniform_profile_fields") or {}
    if uniform:
        listed = ", ".join(f"{k} ({v:g})" if isinstance(v, (int, float))
                           and not isinstance(v, bool) else f"{k} ({v})"
                           for k, v in sorted(uniform.items()))
        uniform_line = (
            f"THESE manager_profiles FIELDS DISTINGUISH NOBODY this week — every "
            f"manager shares the same value: {listed}. Do not write any of them "
            f"as a trait that sets someone apart: no 'the only one who', 'nobody "
            f"else', 'more than anyone', 'the first to'. You may still state such "
            f"a value as a fact about the whole group.")
    else:
        uniform_line = ("Every manager_profiles field varies across the group "
                        "this week, so any of them may support a comparison — "
                        "provided the numbers actually back it.")

    # The by-rule half of the same withholding. uniform_line covers the fields
    # that happen to be equal this week; these are the fields this GROUP'S RULES
    # make meaningless, and they are the case uniform_line structurally cannot
    # catch — family's conference numbers VARY (3 and 4 conferences, 25% and 50%
    # shares) while measuring nothing anyone agreed to, because the group's
    # written minimum is 1 and every legal roster clears it. A varying number is
    # exactly the shape a column reads as a personal trait, so it has to be named
    # in prose for the same reason uniform_line is: a dict buried in 12KB of
    # JSON is not an instruction.
    suppressed = packet.get("suppressed_profile_fields") or {}
    if suppressed:
        # Fields sharing a reason are listed against it once. Two conference
        # fields withheld for the identical reason printed that whole sentence
        # twice, which reads as two separate rules and is simply worse prose in
        # a prompt whose entire job is being read carefully.
        by_reason = defaultdict(list)
        for field, reason in sorted(suppressed.items()):
            by_reason[reason].append(field)
        why = " ".join(f"{', '.join(fields)}: {reason}"
                       for reason, fields in by_reason.items())
        suppressed_line = (
            f"THESE manager_profiles FIELDS MEASURE NOTHING IN THIS GROUP and "
            f"are off limits entirely — {why} Do not cite them, do not compare "
            f"managers on them, and do not praise or fault anyone for them — "
            f"not even as a fact about the whole group. They are in the packet "
            f"because the arithmetic produces them, not because they mean "
            f"anything here.")
    else:
        suppressed_line = None

    # HEAD-TO-HEAD, named in prose for the same reason. `collisions` is the one
    # preseason block that is not a scorer's output: it exists because two
    # managers sat at the same draft and took OPPOSITE sides of one team, and it
    # is the rarest thing a board can carry. Family drafted four of them and
    # they are that group's signature drama, so the count and the sides are
    # stated here rather than left to be noticed 12KB down.
    collisions = packet.get("collisions") or []
    if preseason and collisions:
        listed = "; ".join(
            "{team} {line:g} — {sides}".format(
                team=c.get("team"), line=float(c.get("line", 0)),
                sides=" vs ".join(
                    f"{s.get('name')} {'OVER' if s.get('direction') == 'O' else 'UNDER'}"
                    for s in (c.get("sides") or [])))
            for c in collisions)
        h2h_line = (
            f"HEAD-TO-HEAD: {len(collisions)} team(s) on this board are held by "
            f"two managers on OPPOSITE sides — {listed}. Each is one number "
            f"settling two bets in opposite directions, so one of those two "
            f"managers is wrong by construction. These are the most direct "
            f"conflicts the draft produced and the packet's `collisions` block "
            f"carries every number for them.")
    else:
        h2h_line = None

    # Character material, from the packet's manager_personas. Sacred rule 6 asks
    # for roasts that cite behavior rather than invented history, and until now
    # the only character source in the prompt was column_memory's hand-curated
    # bits — which are continuity, not biography. THE FIELDS PRESENT VARY BY
    # MANAGER AND THAT IS NOT A TONE SIGNAL: John, Rachel and Vic are authored
    # with fatal_flaw, running_gag and rival all null, so those fields simply do
    # not exist to be handed over. Less material, not a gentler column — the
    # gravity is the respect (persona template, "Group parameter").
    personas = packet.get("manager_personas") or {}
    if personas:
        persona_line = (
            "=== MANAGER PERSONAS (authored character material — who these "
            "people are. Use it the way you use manager_profiles: as evidence, "
            "not as a script. Managers carry DIFFERENT fields, and a manager "
            "with fewer of them is not off limits and is not owed a softer "
            "column — there is simply less on file. Same voice for everyone.) "
            "===")
    else:
        persona_line = None

    # Same failure family as uniform_line, one step further out: there the
    # column read a real number and invented its exclusivity; here it reads a
    # board with no history attached and invents the history. Week 0 panel filed
    # "Chris is betting on a Texas resurgence" and "you're hoping for a
    # turnaround" -- both describing a 2025 that the packet does not contain and
    # the pipeline has never fetched. The cache is 2026 only. There is no prior
    # season in this system, so EVERY trajectory claim is fabricated (sacred
    # rule 1), and the vocabulary has to be named explicitly because none of
    # these words look like a factual assertion while you are writing them.
    #
    # REWRITTEN 2026-08-23, because the list version above did not hold. It
    # reached the model intact -- the assembled panel prompt carries the block
    # and every term in it exactly once, verified -- and the model wrote "once
    # again in the fray" anyway, in two independent generations, in both
    # groups. The failure is diagnosable from the sentence it produced: the old
    # block named the WORDS but justified them as a CONCEPT ("any construction
    # that places a team on a trajectory"), so the model applied the concept
    # test, found that "once again" there means "as usual" rather than "unlike
    # last season", and passed itself. A ban the writer gets to adjudicate is
    # not a ban. So this one is a string test with no interpretive step, and it
    # spends its words on the cases where the word is innocent -- those are the
    # ones that get written, precisely because they do not feel like a
    # violation while you are writing them.
    prior_season_line = (
        "NO PRIOR-SEASON LANGUAGE. The packet carries no history — no 2025, no "
        "last year, no previous record, no earlier expectations — and none is "
        "available to you, so every claim about a trajectory is invented.\n"
        "  THESE ARE BANNED AS WORDS, NOT AS THEMES: again, still, once more, "
        "finally, no longer, used to, has become, back (in any returning "
        "sense: back on track, back to form, back where they belong), return, "
        "returning, resurgence, comeback, bounce-back, rebound, turnaround, "
        "return to form, reversal of fortune, redemption, revenge, rebuild.\n"
        "  FORBIDDEN IN EVERY GRAMMATICAL CONTEXT, including the ones carrying "
        "no historical meaning at all. Do not stop to decide whether a "
        "particular use is \"really\" about the past — if the word is in the "
        "sentence, the sentence is wrong. Specifically banned, all of which "
        "read as harmless: \"is once again in the fray\" (means \"as usual\" "
        "— still banned); \"the waters have risen again\" (a metaphor about "
        "weather — still banned); \"in the Big Ten again this year\" (names no "
        "season — still banned); \"a returning starter\", \"the returning "
        "coach\" (describes a roster, not a trajectory — still banned); \"he "
        "is still the favorite\", \"the numbers still favor him\" (about now, "
        "not about before — still banned).\n"
        "  These are not hypotheticals. Every one of the following shipped in a "
        "real column and every one was wrong: \"is once again in the fray\", "
        "\"the waters have risen again\", \"betting on a Texas resurgence\", "
        "\"you're hoping for a turnaround\".\n"
        "  IF YOU WRITE ONE, DELETE THE SENTENCE. Do not hedge it, do not "
        "soften it, and do not swap in a near-synonym that means the same "
        "thing — a synonym is the same violation with better manners. If the "
        "sentence cannot be rewritten without the word, it is not a sentence "
        "you can file: cut it and move on. A column one sentence shorter is "
        "correct. A column carrying one of these words is not.")
    if not preseason:
        prior_season_line += (
            " The ONE movement you may describe is week-over-week movement that "
            "the packet itself measures, stated on its own stated basis — and "
            "that is movement WITHIN this season, never across seasons.")

    # NO SUPERLATIVES, and it is a WORD test for the same reason the
    # prior-season ban is. The coda block already forbids "the lowest on the
    # board" specifically, and panel wk0 answered it with "the lowest on the
    # board OUTSIDE Blaine and Chris's tussle" -- the hedge that makes the
    # claim true is precisely what makes it a ranking claim the packet never
    # made. Naming the concept ("do not overstate") hands the writer a test it
    # can pass itself, so this names the words instead.
    superlative_line = (
        "NO SUPERLATIVES ABOUT RANK OR MAGNITUDE. You may not rank picks "
        "against each other, size one market gap against another, call a "
        "probability the largest or smallest, or place anything on the board "
        "relative to anything else. The packet orders material FOR you; that "
        "ordering is not a fact about the season and never reaches the page.\n"
        "  BANNED AS WORDS in any claim about picks, gaps, probabilities or "
        "board position: lowest, highest, only, most, biggest, smallest, "
        "worst, best, sharpest, largest, widest, closest, furthest, "
        "least.\n"
        "  A HEDGE DOES NOT RESCUE ONE. These are the same violation with "
        "better manners and are equally banned: \"second-lowest\", \"one of "
        "the lowest\", \"among the biggest\", \"the lowest outside X\", "
        "\"arguably the worst\", \"close to the largest\", \"one of the "
        "few\", \"nobody else is near it\".\n"
        "  THIS SHIPPED AND WAS WRONG: \"the lowest on the board outside "
        "Blaine and Chris's tussle\". It is hedged, it is technically true, "
        "and it is still a ranking claim -- the exact failure this ban "
        "exists for.\n"
        "  WHAT TO DO INSTEAD: if you find yourself ranking or comparing "
        "magnitudes, describe what the pick IS. Its number, its probability, "
        "the manager's character, what the disagreement actually says. A pick "
        "is interesting because of what it claims, not because of where it "
        "sits in a sorted list.")

    # NOTHING OUTSIDE THE PACKET, and it sits with the other two word-tests
    # because it is the same kind of rule and fails the same way: the model
    # knows real college football, the packet is a thin slice of it, and the
    # gap between them gets filled silently and fluently.
    #
    # IT NAMES WHAT IS ALLOWED FIRST, and that ordering is the lesson from the
    # incident that prompted it. Panel wk0 filed "a challenging SEC schedule,
    # which boasts eight top-25 opponents" and it was read as recall. It is
    # not: strength_of_schedule.opponents_sp_top25 is 8 for Texas and the
    # sentence is exactly right. A rule written as "never mention opponents or
    # rankings" would have banned a true sentence quoting a real field, and a
    # rule the writer discovers is wrong is a rule the writer stops trusting.
    # So the boundary is drawn at the FIELD, not at the topic.
    packet_only_line = (
        "NOTHING OUTSIDE THE PACKET IS A FACT YOU HAVE. You know real college "
        "football. This packet is a narrow slice of it. Everything you know "
        "that is not in the packet is unavailable to you this week, and the "
        "gap between the two gets filled fluently and silently if you let "
        "it.\n"
        "  THE TEST IS A FIELD, NOT A TOPIC. If a number or a claim IS in the "
        "packet, print it — including the ones that sound like recall. "
        "strength_of_schedule carries mean_opponent_sp_rating and "
        "opponents_sp_top25, so \"eight top-25 opponents\" is CORRECT when "
        "the packet says 8, and you should write it. If it is not in the "
        "packet, you do not have it, however certain it feels.\n"
        "  THESE HAVE NO FIELD ANYWHERE IN THE PACKET AND ARE BANNED "
        "OUTRIGHT: a team's win-loss record; a national ranking or poll "
        "position; the name of any team nobody in this group drafted; any "
        "specific opponent, date, month, venue, home/away split or kickoff; "
        "any player, coach, coordinator, transfer, recruit or injury; any "
        "conference title, bowl or playoff; any result, expectation or "
        "narrative from a prior season.\n"
        "  THE PACKET NAMES NO OPPONENTS. It carries COUNTS about a schedule "
        "— how many games, how many top-25 opponents, the mean opponent "
        "rating — and not one opponent's name. \"They travel to Georgia in "
        "October\" is invented in every particular even when the team is real "
        "and the trip is plausible.\n"
        "  WHERE THE PACKET IS SILENT, THE COLUMN IS SILENT. Do not "
        "characterize, contextualize or explain a team beyond its numbers. If "
        "you want to say WHY a team is rated where it is, you cannot — say "
        "what the number is, or write about the manager instead, which is the "
        "half of this column that never needed the packet's permission.\n"
        "  THIS IS CHECKED AFTER YOU FILE. A deterministic scan reads the "
        "column against the packet's own fields, and a column that fails it is "
        "not published at all. A sentence you are unsure of costs the whole "
        "column; cut it.")

    # The two beats. Beat 2 is the recurring coda and it SWAPS in preseason:
    # "Bad Beat of the Week" needs a pick that died, and in Week 0 nothing has
    # died. "Worst Pick on the Board" keeps the coda's DNA — one target, direct
    # second-person address, mock gravity aimed at a very small pool — but its
    # charge is disagreement with SP+ rather than a death. The target is picked
    # by Python (lowest market_gap), never by the model, exactly as
    # bad_beat_candidates is.
    if preseason:
        beat1_line = (
            "  Beat 1 - One Big Thing (~250-300 words), built from the "
            "HIGHEST-RANKED storyline in the packet that you can tell as one "
            "story. The storylines are pre-ranked by a scorer; prefer a "
            "collision over a concentration over a market_defiance over an "
            "envelope when scores are close. It is ONE story, fully told. NEVER "
            "a roundup: do not tour all four managers in sequence. A manager who "
            "is not the story's subject may be named only in service of that one "
            "story - as the other side of a collision, for instance.")
        w = packet.get("worst_pick_on_the_board") or {}
        # HOW THE CODA WAS CHOSEN, stated to match what actually happened. This
        # sentence used to read "the lowest market_gap on the board" flatly, and
        # that stopped being true when the coda-exclusion rule landed: the coda
        # is now the lowest gap among the picks the lead did NOT already cover,
        # and on both Week 0 boards a LOWER gap exists on the lead's own side
        # (panel: Chris/Texas at -1.681 against the coda's -1.044; family:
        # John/Miami at -1.223 against -0.898). The prompt was therefore telling
        # the model to write a superlative the packet contradicts, and both
        # columns duly wrote it -- persona sacred rule 7 again, one level up:
        # not a number read off one profile, but a ranking claim the selection
        # rule never made.
        # TWO COUNTS (build_week_packet.exclude_lead_subject). `excluded` is
        # every subject-collision drop; `excluded_lower_gap` is the subset that
        # actually sits at a lower gap. Only the second one may appear in the
        # sentence below, because the sentence says "with a lower gap". Panel
        # Week 0 read `excluded` and told the model 8 when the true answer was
        # 1, and the column hedged a superlative onto the false count.
        _ce = packet.get("coda_exclusion") or {}
        lower = _ce.get("excluded_lower_gap")
        dropped = _ce.get("excluded") or 0
        if lower is None:          # packet predates the split; best available
            lower = dropped
        if lower:
            how_chosen = (
                f"It is the lowest market_gap among the picks the One Big Thing "
                f"did NOT already cover — {lower} pick(s) with a lower gap "
                f"were set aside because they share the lead's manager or team. "
                f"So it is NOT the lowest on the board and you may not call it "
                f"that, or the worst, or the biggest disagreement anywhere. "
                f"State its number and make the case from the number.")
        elif dropped:
            # Picks were set aside, but none of them at a lower gap. Do NOT
            # invite the superlative back in on the technicality.
            how_chosen = (
                "It is the lowest market_gap among the picks the One Big Thing "
                "did NOT already cover. Picks were set aside for sharing the "
                "lead's manager or team; none of them sits at a lower gap. You "
                "still may not call this the lowest on the board, the worst, or "
                "the biggest disagreement anywhere. State its number and make "
                "the case from the number.")
        else:
            how_chosen = ("It is the lowest market_gap on the board, and nothing "
                          "was set aside to get there.")
        beat2_line = (
            "  Beat 2 - Worst Pick on the Board (~75-100 words), the preseason "
            "coda, from the packet's `worst_pick_on_the_board` block. That pick "
            "was chosen by computation, never by you, so write about THAT pick "
            "and no other, and address its manager directly by first name in "
            "the second person"
            + (f" ({w.get('name')}, on {w.get('team')})." if w else ".")
            + " " + how_chosen
            + " Same DNA as the usual Bad Beat coda: one target, mock gravity "
            "aimed at a very small pool, warmth underneath. But nothing has been "
            "played, so the pick has NOT died and you may not describe it dying, "
            "backdooring, or missing by a half-win. The entire charge is that "
            "SP+ disagrees with the bet by the number in the packet.")
    else:
        beat1_line = (
            "  Beat 1 — One Big Thing (~250-300 words), built from the "
            "HIGHEST-RANKED storyline in the packet that you can tell as one "
            "story. The packet's storylines are pre-ranked; prefer a feud over a "
            "collapse over an irony over a heater when scores are close.")
        beat2_line = (
            "  Beat 2 — Bad Beat of the Week (~75-100 words), from "
            "bad_beat_candidates. The `how_it_died` text is limited to final "
            "scores — the pipeline has no play-by-play, so do NOT invent drives, "
            "onside kicks, or clock situations that are not in the packet.")

    # Week 0 has no results at all, so the template's anti-fabrication guard —
    # which is written as "you get a final score, a margin, home or away" — is
    # describing a world that does not exist yet. Name the real universe.
    if preseason:
        fabrication_line = (
            "YOUR ENTIRE FACTUAL UNIVERSE THIS WEEK is what the packet carries: "
            "which teams were picked, by whom, their frozen Vegas line, the "
            "over/under direction, SP+ implied expected wins, market_gap, "
            "strength of schedule (mean opponent SP+ rating and count of top-25 "
            "opponents), the number of scheduled games, the floor/ceiling "
            "envelope, p_beat_line and the pool odds. That is ALL. You do not "
            "know anything about 2026 college football beyond those numbers: no "
            "rosters, no players, no coaches, no transfers, no injuries, no "
            "returning starters, no recruiting, no schedule specifics beyond the "
            "counts, no last-season results, no expectations, no hype. Do not "
            "characterize any team in any way the packet does not. If you want "
            "to say why a team is rated where it is, you cannot - say what the "
            "number is instead.")
    else:
        fabrication_line = None

    parts = [
        f"GROUP: {group_id}    WEEK: {packet.get('week')}    "
        f"SEASON: {packet.get('season')}",
        f"BASIS FOR MOVEMENT: {basis}",
        basis_warning,
        season_line,
        uniform_line,
        prior_season_line,
        superlative_line,
        packet_only_line,
        stakes_line,
    ]
    if suppressed_line:
        parts.append(suppressed_line)
    if h2h_line:
        parts.append(h2h_line)
    if fabrication_line:
        parts.append(fabrication_line)
    if persona_line:
        parts += ["", persona_line,
                  json.dumps(personas, indent=2, ensure_ascii=False)]
    parts += [
        "",
        "=== COLUMN MEMORY (season continuity — established nicknames, feuds, "
        "and character bits. Reuse what is here; coin nothing new unless this "
        "week's packet earns it.) ===",
        json.dumps({k: memory.get(k) for k in
                    ("nicknames", "feuds", "character_bits")},
                   indent=2, ensure_ascii=False),
        "",
        "=== LAST PUBLISHED COLUMN ===",
        (f"({last_name})\n\n{last_text}" if last_text else
         "(none — this is the first column of the season. No callbacks yet.)"),
        "",
        "=== WEEK PACKET (every number you print must appear here, verbatim) ===",
        json.dumps(packet, indent=2, ensure_ascii=False),
        "",
        "=== YOUR ASSIGNMENT ===",
        "File this week's column now, following the template exactly:",
        # LENGTH, FIRST, AND AS A FLOOR. It used to sit after the assignment,
        # underneath the house style, the deck rule and the paragraphing rule
        # -- at the bottom of an unbroken run of prohibitions. Three
        # consecutive columns came back at 261, 290 and 319 words against a
        # 350-450 "target", and nothing in any of them was wrong: that is what
        # a page of bans reads as when the length rule is the last thing under
        # it. The safest column is the short one. So the requirement is stated
        # BEFORE the prohibitions rather than after them, and as a minimum
        # rather than as a range a writer can approach from below.
        #
        # THE FLOOR IS 300, LOWERED FROM 350 ON 2026-08-26. Repositioning the
        # rule moved four generations 261 -> 290 -> 319 -> 325 and none of them
        # cleared 350. A floor nobody reaches is not a floor, it is a number
        # the reader of this file learns to discount, and the next person
        # tuning length would have been calibrating against a spec that had
        # never once been met. 300 is where the model actually lands, so it is
        # the line worth enforcing; 350-450 stays as the TARGET, which is what
        # it always honestly was. No column was regenerated for this -- the
        # spec now describes the columns already filed rather than the other
        # way round.
        "LENGTH — READ THIS BEFORE ANYTHING ELSE BELOW: MINIMUM 300 WORDS. "
        "350-450 is the target; 300 is the floor and it is a hard one, not an "
        "aspiration. Beat 1 (the One Big Thing) is 250-300 of them on its own. "
        "A column under 300 words is not a tighter column, it is an unfinished "
        "one, and length is the single most common way this assignment is "
        "failed.",
        "The rules further down are absolute and none of them is relaxed by "
        "the length floor — but they must not clip the column short either. "
        "When a sentence has to go, REPLACE IT: expand the prose around the "
        "picks (character, what the disagreement claims, what is at stake) "
        "rather than shortening the column overall. Every number is carried "
        "for you, so the column earns its length in voice, not in stat "
        "recitation.",
        beat1_line,
        beat2_line,
        "  End with the sign-off verbatim.",
        "",
        "Prose only — no headings, no lists, no bullets. "
        "Every number verbatim from the packet; if a number you want is not "
        "there, write around it. Character roasts must cite behavior visible in "
        "manager_profiles, not invented history.",
        # Same failure mode, one level down: a constrained writer reaches for
        # scaffolding. Week 0 panel filed "fortified by Texas's implications",
        # which is not a sentence a person says.
        "Write the joins in plain speech. Constructions like \"fortified by "
        "Texas's implications\" or \"bolstered by Miami's projections\" read "
        "as assembled rather than written — say the plain thing instead.",
        # HOUSE STYLE, and it lives HERE rather than up with the factual
        # constraints on purpose. The prior-season ban sits 18% into a 55KB
        # prompt with ~36KB of packet JSON between it and this assignment, and
        # it did not hold; these three rules keep getting broken the same way,
        # so they are stated at the point the model is actually told to write.
        #
        # One block, not three. "Don't print narrative_score", "don't name the
        # packet" and "don't print a raw field name" are the same rule seen
        # from three sides -- the reader has never seen the artifact -- and
        # three separate statements of one rule is how they drift apart.
        "HOUSE STYLE, and these are the three the column keeps getting wrong:",
        "  1. NEVER NAME AN INTERNAL DATA STRUCTURE. The reader has never seen "
        "the packet and does not know one exists. Banned as reader-facing "
        "words: packet, cache, manifest, baseline, schema, storyline, "
        "storyline pool, narrative score, coda, coda pool, coda candidate, "
        "bad-beat candidate, uniform profile fields, manager profiles, "
        "character bits, column memory. Also banned as PHRASES, because the "
        "space is not a disguise: \"packet says\", \"packet gives\", "
        "\"the packet has\", \"according to the packet\". Panel wk0 filed "
        "\"The packet gives Blaine a 0.86467 "
        "chance\" and the reader has no idea what the packet is. Attribute a "
        "number to \"the model\", \"our projection\", \"SP+\", \"the "
        "numbers\" — or to nothing at all, and simply state it.",
        "  2. NEVER PRINT A RAW FIELD NAME OR THE PACKET'S OWN BOOKKEEPING. "
        "Write \"the market disagrees by 0.669\", never \"a market_gap of "
        "0.669\". THE SPACE IS NOT A DISGUISE: \"market gap\" and \"the "
        "market gap\" are the same violation as \"market_gap\" and are "
        "equally banned, as are \"delta impact\", \"narrative score\", "
        "\"moment size\", \"p beat line\" and \"implied expected wins\". "
        "Panel wk0 filed \"The market gap here is a negative 1.044\", which "
        "slipped the ban only because the underscore was gone -- it is a raw "
        "field name read aloud. Say \"the market disagrees by 1.044\", or "
        "name no field at all and just state the number. "
        "narrative_score and moment_size exist to ORDER the material "
        "for you and are not facts about the season: \"this clash carries a "
        "narrative score of 6.338, the highest on the board\" reports the "
        "scorer's opinion as though it were a result. Neither they nor the "
        "storyline ordering ever reach the page.",
        "  3. PROBABILITIES ARE ROUNDED PERCENTAGES. In the packet a "
        "probability is a decimal between 0 and 1; on the page it is a "
        "whole-number percent. 0.86467 is 86%. 0.13533 is 14%. The decimal "
        "form never appears. One decimal place is allowed only where the half "
        "genuinely carries the point — family wk0's \"The implied odds give "
        "him a 66.5% chance\" is the target — and two or more decimals are "
        "never correct. THIS IS THE ONE EXCEPTION to printing numbers "
        "verbatim: rounding a probability into a percentage is required, not "
        "permitted, and it does not license rounding anything else.",
        # LAST LINE, deliberately. The measurement above is the whole argument:
        # a rule stated once at 18% of a 55KB prompt did not survive to the
        # output, twice. This costs one sentence and puts the check after
        # everything else the model has been asked to hold.
        # THE DECK, and it is specified HERE rather than at the top for the
        # same reason the house style is: it is an output-shape instruction,
        # and it belongs at the point the model is told what to hand back.
        #
        # It rides this call. A second call would double the cost of every
        # column to buy one sentence, and — worse — a deck written by a
        # separate call has not read the column it is decking, so it would be a
        # summary of the packet rather than of the prose that was actually
        # filed. Same reply, written last, having just written the thing.
        #
        # Every prohibition above applies to it WITHOUT BEING RESTATED: they
        # are stated over this whole reply, and the deck is part of this reply.
        # Restating them here is how a second, shorter, drifting copy of the
        # ban list gets created — the bans are named once and the deck is told
        # it is not exempt.
        "FIRST LINE OF YOUR REPLY: a deck. Write \"DECK: \" and then ONE "
        "sentence of 15-25 words saying what this column is about — the "
        "standfirst a reader sees under the headline before they start. Then "
        "one blank line, then the column itself. The deck is prose like "
        "everything else here and EVERY rule above applies to it unchanged: no "
        "banned prior-season word, no internal structure named, no field name "
        "read aloud, no superlative, no probability as a decimal, nothing "
        "about what anyone is playing for. It is one sentence, not a headline "
        "and not a label — no colon-separated title, no \"In this week's "
        "column\", and it does not name the column or the reader. "
        "THE DECK IS NOT PART OF THE COLUMN'S LENGTH and does not come out of "
        "it: the 350-450 words above are the column BELOW the deck, still in "
        "the beats and the paragraphs described above. Panel wk0 answered the "
        "first version of this instruction with a good deck over 261 words in "
        "two paragraphs, which is a different column, not a decked one.",
        # PARAGRAPHING, and it is here because it is the other thing the deck
        # instruction cost. Every column filed before decks existed put the
        # sign-off on its own line; the first two decked drafts ran it onto the
        # end of Beat 2. Nothing above ever said it stood alone -- the persona
        # template makes it the last line OF Beat 2 -- so the model was not
        # wrong, it just stopped doing the thing it had always done. The page
        # sets the sign-off apart by POSITION, so it has to be its own
        # paragraph for the typography to find it.
        "PARAGRAPHS: separate them with ONE BLANK LINE, and give the fixed "
        "sign-off a paragraph of its own -- a blank line before it, nothing "
        "after it. Beat 1 runs to more than one paragraph when the story has "
        "more than one move in it; do not compress the whole column into two "
        "blocks.",
        "Before you return it, read the column back and check six things: no "
        "banned prior-season word is in it (again, still, once more, finally, "
        "no longer, used to, back, return, returning, resurgence, comeback, "
        "bounce-back, rebound, turnaround, redemption, revenge, rebuild) — "
        "delete any sentence that carries one; no internal structure is named; "
        "every probability is a rounded percentage, not a decimal; no field "
        "name is read aloud with the underscore removed (\"market gap\", "
        "\"narrative score\"); no superlative or hedged superlative ranks "
        "one pick, gap or probability against another (lowest, highest, only, "
        "most, biggest, worst, best — including \"one of the\" and \"outside "
        "X\" forms); and nothing says or implies what anyone is playing for. "
        "Read the DECK back against the same six, and against the superlative "
        "rule especially -- a deck is one sentence trying to say why the "
        "column matters, which is the exact shape that reaches for \"the "
        "biggest\", \"the most\" or \"the only\". A decked draft filed "
        "\"the season's most direct conflict\"; say what the conflict IS "
        "instead of where it ranks.",
        "Return ONLY the DECK line and the column text. No preamble, no title, "
        "no explanation.",
    ]
    return system_text, "\n".join(parts), packet


# --- Live call (raw urllib, no SDK) ------------------------------------------

def call_openai(system_text, user_text, api_key, model=None, temperature=None,
                max_tokens=None, response_format=None):
    """POST to the chat-completions endpoint with the two retry loops rule 2
    prescribes. Re-raises after the last attempt — the caller's fail-soft guard
    decides what a persistent outage means (here: warn and exit 0).

    The optional arguments exist so a second caller can reuse this retry shell
    rather than growing a fourth copy of it (scripts/derive_style.py, which
    needs a vision message, a lower temperature and a JSON response format).
    Every one defaults to the column's own settings, so the commentary call
    site is unchanged. `user_text` may be a plain string or an already-built
    list of content parts — the vision API's shape.
    """
    body = json.dumps({
        "model": model or OPENAI_MODEL,
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens or MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        **({"response_format": response_format} if response_format else {}),
    }).encode("utf-8")

    rate_limit_attempts = 0
    for attempt in range(len(NETWORK_BACKOFF) + 1):
        req = urllib.request.Request(
            OPENAI_URL, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            # Rate limit: wait the provider's window, on its own budget.
            if e.code == 429 and rate_limit_attempts < RATE_LIMIT_RETRIES:
                rate_limit_attempts += 1
                print(f"::warning:: OpenAI 429 (rate limited); waiting "
                      f"{RATE_LIMIT_WAIT}s (attempt {rate_limit_attempts}/"
                      f"{RATE_LIMIT_RETRIES}).", file=sys.stderr)
                time.sleep(RATE_LIMIT_WAIT)
                continue
            if e.code < 500 or attempt == len(NETWORK_BACKOFF):
                raise                      # 4xx is our bug; last 5xx re-raises
            _backoff(attempt, f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == len(NETWORK_BACKOFF):
                raise                      # persistent outage: fail loud upward
            _backoff(attempt, type(e).__name__)
    raise RuntimeError("unreachable")      # loop always returns or raises


def _backoff(attempt, why):
    wait = NETWORK_BACKOFF[attempt]
    print(f"::warning:: OpenAI call failed ({why}); retrying in {wait}s "
          f"({attempt + 1}/{len(NETWORK_BACKOFF)}).", file=sys.stderr)
    time.sleep(wait)


# --- Publish -----------------------------------------------------------------

DECK_PREFIX = "DECK:"
DECK_MAX_CHARS = 400


def split_deck(reply):
    """(deck, column) from one model reply. deck is '' when there is none.

    FAIL SOFT, ALWAYS. A reply with no DECK line is the whole reply as the
    column and no deck — which is exactly the state every column filed before
    this existed, and the state the page already renders (it falls back to the
    standing teaser). A model that forgets the line must cost us a standfirst,
    never the column.

    The line is only taken when it is the FIRST non-empty line: a "DECK:" that
    turns up in the middle of the prose is the column talking, not the shape
    instruction being obeyed late, and lifting it would silently delete a
    paragraph's opening. The length cap is the same guard from the other side —
    a model that ignores the blank line and returns the entire column on one
    line would otherwise have all of it lifted into the deck.
    """
    text = reply.replace("\r\n", "\n").lstrip("\n")
    head, sep, rest = text.partition("\n")
    if not head.strip().upper().startswith(DECK_PREFIX):
        return "", reply
    deck = head.strip()[len(DECK_PREFIX):].strip()
    if not deck or len(deck) > DECK_MAX_CHARS:
        return "", reply
    return deck, rest.lstrip("\n") if sep else ""


def publish_doc(group_id, packet, column, deck=""):
    """The site's shape for one filed column. Everything the page shows is
    computed HERE — the site renders JSON and computes nothing (playbook P2 #12).

    `paragraphs` rather than one blob: the page needs <p> elements, and splitting
    prose in JS is the kind of "small" computation that ends up owning the
    column's typography. Blank-line separated, blanks dropped, which is exactly
    the shape the model is asked to return (prose only, no headings, no lists).
    """
    paragraphs = [p.strip() for p in column.replace("\r\n", "\n").split("\n\n")]
    paragraphs = [p for p in paragraphs if p]
    return {
        "meta": {
            "group_id": group_id,
            "season": packet.get("season"),
            "week": packet.get("week"),
            # The site labels a Week 0 column "Preseason", not "Week 00", and
            # this is the same flag the prompt branches on rather than a second
            # opinion about what week 0 means.
            "preseason": packet.get("preseason") is True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": OPENAI_MODEL,
            "source": f"groups/{group_id}/output/column_week_{packet.get('week')}.md",
        },
        "column": {
            # The standfirst under the headline. OMITTED, not null, when the
            # model did not return one — the page's absent-state teaser is the
            # fallback and it tests presence, so an empty string here would be
            # a deck that renders as a blank line under the headline.
            **({"deck": deck} if deck else {}),
            "paragraphs": paragraphs,
            # Prose only. The deck is not part of the column's length and is
            # not what the archive list is reporting when it says "332 words".
            "word_count": len(column.split()),
        },
    }


def build_index(group_id):
    """Rebuild docs/data/<group>/columns/index.json by scanning the week files.

    DERIVED, and derived from the PUBLISHED files rather than from the .md
    sources — that is the whole reason the index is safe to regenerate. The .md
    carries no date inside it (week is in its filename and nothing else is), so
    `generated_at` exists only in the published week file. Rebuilding from the
    sources would silently restamp every historical column with today.

    IDEMPOTENT AT THE BYTE LEVEL. Nothing here is a clock reading: no
    `generated_at` on the manifest itself, no ordering that depends on anything
    but the week number. Re-running with no new column rewrites the same bytes,
    so a republish produces no git churn and no false "the archive changed".

    Carries NO prose. The list is what the archive page needs to draw its index
    of the season — week, its label flag, when it was filed, how long it is —
    and the page fetches a week's file when a reader opens that week. Putting a
    teaser here would put the same sentences in two published files.

    Defensive per file (playbook rule 10): a week file that will not parse is
    skipped with a warning rather than taking down the index for every other
    week. Returns the manifest that was written, or None when the group has no
    published columns at all — the ordinary state before Week 0.
    """
    d = columns_dir(group_id)
    entries = []
    for path in sorted(d.glob("week_*.json")) if d.is_dir() else []:
        try:
            week = int(path.stem.split("_", 1)[1])
        except (IndexError, ValueError):
            print(f"  ::warning::[{group_id}] skipping unparseable name {path.name}")
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            meta = doc["meta"]
            col = doc["column"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"  ::warning::[{group_id}] skipping unreadable {path.name}: {e}")
            continue
        entries.append({
            "week": week,
            "preseason": meta.get("preseason") is True,
            "generated_at": meta.get("generated_at"),
            "word_count": col.get("word_count"),
            "file": path.name,
        })

    if not entries:
        return None

    # Newest first: the archive page reads columns[0] as the current column and
    # lists the rest beneath it, so the order IS the page's reading order and is
    # not re-sorted in JS.
    entries.sort(key=lambda e: e["week"], reverse=True)
    doc = {
        "$note": [
            "The published column archive for this group. DERIVED — rebuilt by",
            "generate_commentary.py from the week_<N>.json files beside it, and",
            "regenerable at any time with `--reindex` and no network.",
            "Newest first: columns[0] is the current column. Carries no prose;",
            "a week's paragraphs live in the file its `file` key names.",
            "A 404 on this file is an ordinary state (no column filed yet) and",
            "every consumer must render its own empty state rather than error.",
        ],
        "$version": 1,
        "group_id": group_id,
        "count": len(entries),
        "columns": entries,
    }
    utils.save_json_atomic(index_path(group_id), doc)
    return doc


# --- Modes -------------------------------------------------------------------

def check_published(group_id, packet_override=None):
    """Guard the column already on the site. No network, writes nothing."""
    packet = read_json(packet_override or packet_path(group_id), "week packet")
    week = packet.get("week")
    doc_path = publish_path(group_id, week)
    if not doc_path.exists():
        print(f"  [{group_id}] no published column for week {week} — nothing to check")
        return 0
    doc = read_json(doc_path, "published column")
    paras = (doc.get("column") or {}).get("paragraphs") or []
    text = "\n\n".join(paras)

    violations = column_guard.check_column(text, packet)
    if not violations:
        print(f"  [{group_id}] week {week}: guard PASSES "
              f"({len(text.split())} words, {len(paras)} paragraph(s))")
        return 0
    column_guard.report(group_id, violations)
    return 1


def run_dry(group_id, packet_override=None):
    system_text, user_text, packet = build_prompt(group_id, packet_override)
    preview = "\n".join([
        "=" * 78,
        f"COMMENTARY PROMPT PREVIEW — group {group_id}, week {packet.get('week')}",
        f"model={OPENAI_MODEL}  temperature={TEMPERATURE}  max_tokens={MAX_TOKENS}",
        "DRY RUN — no network call was made. This is the exact prompt live mode "
        "would post.",
        "=" * 78,
        "",
        "----- SYSTEM (templates/svp_persona.md) " + "-" * 39,
        system_text,
        "",
        "----- USER " + "-" * 67,
        user_text,
        "",
        "=" * 78,
        f"END PREVIEW — system {len(system_text)} chars, user {len(user_text)} chars",
        "=" * 78,
        "",
    ])
    path = preview_path(group_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(preview, encoding="utf-8")
    print(f"  -> wrote {path}")
    print(f"  [{group_id}] dry run: week {packet.get('week')}, "
          f"{len(packet.get('storylines', []))} storyline(s), "
          f"{len(packet.get('bad_beat_candidates', []))} bad beat(s), "
          f"prompt {len(system_text) + len(user_text)} chars. No network call.")
    return 0


def run_live(group_id, packet_override=None):
    utils.load_env_file()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set (env or .env)")

    system_text, user_text, packet = build_prompt(group_id, packet_override)
    week = packet.get("week")
    reply = call_openai(system_text, user_text, api_key)
    if not reply:
        raise RuntimeError("OpenAI returned an empty column")

    # The deck comes off the front before anything is written, so the .md and
    # the published JSON hold the same prose. The .md is THE COLUMN — it is fed
    # back to the next week's prompt as memory, and a DECK: line sitting in
    # that memory is an instruction artifact the model would learn to imitate.
    deck, column = split_deck(reply)
    if not column.strip():
        raise RuntimeError("OpenAI returned a deck with no column")
    if not deck:
        print(f"::warning:: [{group_id}] no DECK line in the reply; filing the "
              f"column without a deck (the page falls back to its standing "
              f"teaser).", file=sys.stderr)

    # THE FACTUAL GUARD, before anything is written anywhere the site or the
    # next prompt can see. A column that asserts what the packet does not is
    # not a column with a flaw in it -- sacred rule 1 is the whole basis on
    # which a reader is asked to believe any number in it -- so it does not
    # get published, indexed, or remembered.
    #
    # FAIL SOFT, LOUDLY. Rejection is not an exception: main() catches those
    # and the pipeline continues either way (rule 3), but an exception here
    # would read in the log as "the model call broke" when the model call was
    # fine and the OUTPUT was wrong. Those want different fixes, so they get
    # different exits. Standings and every other board are untouched.
    violations = column_guard.check_column(column, packet)
    if violations:
        column_guard.report(group_id, violations)
        # Quarantined, not discarded. The text is the evidence for whatever
        # prompt change comes next, and reading it is the only way to tell a
        # real fabrication from a guard that needs a rule loosened. A distinct
        # name, NOT column_week_<N>.md: that path is the filed column, and
        # last_column_text() would hand a rejected draft to next week's prompt
        # as an exemplar. Nothing appends it to memory either.
        reject = out_dir(group_id) / f"column_week_{week}.rejected.md"
        reject.parent.mkdir(parents=True, exist_ok=True)
        # The DECK goes in the quarantine file too. It is split off the reply
        # before this point and is not part of `column`, so writing only the
        # prose drops it on the floor -- which is exactly what happened to the
        # 352-word draft the month false positive rejected: it was the better
        # column and could not be recovered, because recovering it meant
        # regenerating the deck it had already been paid for.
        header = f"DECK: {deck}\n\n" if deck else ""
        reject.write_text(header + column + "\n", encoding="utf-8")
        print(f"::warning:: [{group_id}] rejected draft kept at {reject} for "
              f"inspection. NOTHING under docs/ was touched: the previously "
              f"published column for this week, if any, still stands.",
              file=sys.stderr)
        return 0

    path = column_path(group_id, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(column + "\n", encoding="utf-8")
    print(f"  -> wrote {path}")

    # Publish into the archive. Written from the string that was just filed,
    # never re-read off disk, so the .md and its published form cannot describe
    # different columns. This opens week N's file and no other — every earlier
    # column is untouched by construction, not by being carefully merged.
    utils.save_json_atomic(publish_path(group_id, week),
                           publish_doc(group_id, packet, column, deck))
    # Then the manifest, rebuilt from what is now on disk. Second, so a crash
    # between the two leaves an unindexed column (invisible, recoverable with
    # --reindex) rather than an index naming a file that was never written.
    build_index(group_id)

    # Append to memory LAST, and only the filename — the curated keys are
    # round-tripped untouched.
    memory = load_memory(group_id)
    cols = [c for c in memory.get("columns", []) if c != path.name]
    cols.append(path.name)
    memory["columns"] = cols[-MEMORY_COLUMNS_KEPT:]
    utils.save_json_atomic(memory_path(group_id), memory)
    print(f"  [{group_id}] filed column_week_{week}.md ({len(column.split())} words"
          f"{', + deck' if deck else ', NO deck'})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Write the weekly SVP column")
    ap.add_argument("--group", required=True, help="group slug (panel/family/church)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the prompt and write the preview; NO network call")
    ap.add_argument("--packet", default=None,
                    help="read the packet from this path instead of the group's "
                         "week_packet.json (used for the Week 0 preseason packet)")
    ap.add_argument("--reindex", action="store_true",
                    help="rebuild the archive manifest from the already-published "
                         "week files; NO network call, writes no column")
    ap.add_argument("--check-published", action="store_true",
                    help="run the factual guard over the column ALREADY published "
                         "for this group's packet week and report; no network "
                         "call, writes nothing. Exits 1 on a violation.")
    args = ap.parse_args()

    # The guard, run against what is already on the site. A rule change can be
    # tested against every filed column for the price of nothing, which is the
    # difference between tuning this check and guessing at it.
    if args.check_published:
        return check_published(args.group, args.packet)

    # The no-fetch escape hatch (playbook rule 3 / P1 #6): regenerate the derived
    # layer from committed data with no API key and no packet, so a corrupted or
    # deleted index is a one-command fix rather than a re-run of the model.
    if args.reindex:
        doc = build_index(args.group)
        n = doc["count"] if doc else 0
        print(f"  [{args.group}] indexed {n} column(s)")
        return 0

    if args.dry_run:
        # Developer-facing: let it fail loudly.
        return run_dry(args.group, args.packet)

    # Live: commentary is garnish and must never block the pipeline. SystemExit
    # is caught too — utils.load_json() exits rather than raising.
    try:
        return run_live(args.group, args.packet)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — deliberate catch-all
        print(f"::warning:: [{args.group}] commentary FAILED "
              f"({type(e).__name__}: {e}); continuing. Standings and the rest of "
              f"the pipeline are unaffected (playbook rule 3).", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
