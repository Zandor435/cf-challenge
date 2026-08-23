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

Live mode writes TWO files, and they are not redundant: the .md above is the
source of record (what memory names, what next week reads back for callbacks,
what a human reviews), and docs/data/<group>/column.json is the published form
the site renders — same text, pre-split into paragraphs and word-counted here
because the site computes nothing.

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


def publish_path(group_id):
    """The site's copy: docs/data/<group_id>/column.json — OVERWRITE.

    Two files, and they are not redundant. The .md under groups/<group>/output/
    is the SOURCE: it is what column_memory names, what next week's prompt reads
    back for callbacks, and what a human reviews before anything ships. This is
    the PUBLISHED form — the newest column only, pre-split into paragraphs and
    word-counted in Python, because the site renders JSON and computes nothing
    (playbook P2 #12). Writing it from here rather than from a separate publish
    step keeps the two from ever describing different columns.

    docs/data/<group>/ already carries more than the four contract boards
    (personas.json, banners.json); this joins them as an auxiliary publisher and
    is documented alongside them in docs/output-contract.md.
    """
    return utils.WEB_DATA_DIR / group_id / "column.json"


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


def last_column_text(group_id, memory):
    """The most recently filed column, for callbacks. Missing file is not an
    error — a deleted or not-yet-written column just means no callback."""
    for name in reversed(memory.get("columns", [])):
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
    last_name, last_text = last_column_text(group_id, memory)

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
    stakes_line = (f"Stakes for this group: {stakes}. You may reference them by "
                   f"their actual terms." if stakes else
                   "This group has no declared stakes. Do not invent any.")

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
    prior_season_line = (
        "NO PRIOR-SEASON LANGUAGE. The packet carries no history — no 2025, no "
        "last year, no previous record, no earlier expectations — and none is "
        "available to you. Do not write, or imply by any synonym: resurgence, "
        "comeback, bounce-back, rebound, turnaround, return to form, "
        "reversal of fortune, redemption, revenge, rebuild, "
        "\"back\" in the returning-to-glory sense (back on track, back to "
        "form, back where they belong), \"again\", \"still\", \"once more\", "
        "\"finally\", \"no longer\", \"used to\", \"has become\", or any "
        "construction that places a team or a manager on a trajectory from some "
        "earlier state. A number in the packet is a fact about NOW, never a "
        "recovery from or a decline since anything.")
    if not preseason:
        prior_season_line += (
            " The ONE movement you may describe is week-over-week movement that "
            "the packet itself measures, stated on its own stated basis — and "
            "that is movement WITHIN this season, never across seasons.")

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
        beat2_line = (
            "  Beat 2 - Worst Pick on the Board (~75-100 words), the preseason "
            "coda, from the packet's `worst_pick_on_the_board` block. That pick "
            "was chosen by computation - the lowest market_gap on the board - so "
            "write about THAT pick and no other, and address its manager "
            "directly by first name in the second person"
            + (f" ({w.get('name')}, on {w.get('team')})." if w else ".")
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
        beat1_line,
        beat2_line,
        "  End with the sign-off verbatim.",
        "",
        "Prose only — no headings, no lists, no bullets. Roughly 400 words total. "
        "Every number verbatim from the packet; if a number you want is not "
        "there, write around it. Character roasts must cite behavior visible in "
        "manager_profiles, not invented history.",
        "Return ONLY the column text. No preamble, no title, no explanation.",
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

def publish_doc(group_id, packet, column):
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
            "paragraphs": paragraphs,
            "word_count": len(column.split()),
        },
    }


# --- Modes -------------------------------------------------------------------

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
    column = call_openai(system_text, user_text, api_key)
    if not column:
        raise RuntimeError("OpenAI returned an empty column")

    path = column_path(group_id, week)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(column + "\n", encoding="utf-8")
    print(f"  -> wrote {path}")

    # Publish the same text the site renders. Written from the string that was
    # just filed, never re-read off disk, so the two files cannot describe
    # different columns.
    utils.save_json_atomic(publish_path(group_id), publish_doc(group_id, packet, column))

    # Append to memory LAST, and only the filename — the curated keys are
    # round-tripped untouched.
    memory = load_memory(group_id)
    cols = [c for c in memory.get("columns", []) if c != path.name]
    cols.append(path.name)
    memory["columns"] = cols[-MEMORY_COLUMNS_KEPT:]
    utils.save_json_atomic(memory_path(group_id), memory)
    print(f"  [{group_id}] filed column_week_{week}.md ({len(column.split())} words)")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Write the weekly SVP column")
    ap.add_argument("--group", required=True, help="group slug (panel/family/church)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the prompt and write the preview; NO network call")
    ap.add_argument("--packet", default=None,
                    help="read the packet from this path instead of the group's "
                         "week_packet.json (used for the Week 0 preseason packet)")
    args = ap.parse_args()

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
