#!/usr/bin/env python3
"""
test_lint_commentary.py — Validates scripts/lint_commentary.py.

THE POINT OF THE MODULE UNDER TEST. Three prompt revisions produced three
short columns and, on the third, a REGRESSION of a ban that had been holding.
Prompt-only enforcement had hit its ceiling, so the gate moved to the output.
A gate nobody tests is just a slower way to ship the same failure, so every
rule is exercised against a known-good AND a known-bad input, and every
sentence that actually shipped in a prior regen appears here as a fixture.

SHIPPED FAILURES COVERED (all three regens):
  regen 1 (252w): "is once again in the fray", "the waters have risen again",
                  "betting on a Texas resurgence", "you're hoping for a
                  turnaround", "The packet gives Blaine a 0.86467 chance"
  regen 2 (332w): "The market gap here is a negative 1.044", "the lowest on
                  the board outside Blaine and Chris's tussle", "playing for
                  pride and, possibly, the chance to needle the other"
  regen 3 (316w): "Then again, Chris is a man who...", "implied odds of only
                  22%", "the stakes couldn't be more straightforward",
                  "about 9.5 wins"

Fixtures are built IN MEMORY (playbook rule 14) — nothing here writes into the
files production reads.

Runs both ways: pytest collects one test per section and conftest.py raises on
any check() recorded as FAIL; the standalone runner sums the same ledger.

Usage:
    python -m pytest scripts/test_lint_commentary.py
    python scripts/test_lint_commentary.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_commentary as L

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# --- fixtures ----------------------------------------------------------------

def _packet(stakes=None):
    """A minimal panel-shaped packet: the real Week 0 numbers, nothing else."""
    return {
        "group_id": "panel",
        "week": 0,
        "stakes": stakes,
        "worst_pick_on_the_board": {
            "manager_id": "jonathan", "name": "Jonathan", "team": "Oregon",
            "line": 10.5, "direction": "O",
            "implied_expected_wins": 9.456,
            "market_gap": -1.044, "p_beat_line": 0.218695,
        },
        "manager_profiles": {
            "blaine": {"display_name": "Blaine", "picks": [
                {"team": "Texas", "market_gap": 1.681,
                 "implied_expected_wins": 7.819, "p_beat_line": 0.86467}]},
            "chris": {"display_name": "Chris", "picks": [
                {"team": "Texas", "market_gap": -1.681,
                 "implied_expected_wins": 7.819, "p_beat_line": 0.13533},
                {"team": "USC", "market_gap": -0.983,
                 "implied_expected_wins": 6.5, "p_beat_line": 0.44}]},
            "jonathan": {"display_name": "Jonathan", "picks": [
                {"team": "Oregon", "market_gap": -1.044,
                 "implied_expected_wins": 9.456, "p_beat_line": 0.218695}]},
        },
    }


def _words(n, word="filler"):
    return " ".join([word] * n)


def _column(beat1_words, coda_words):
    """A structurally valid column: two lead paragraphs then a coda addressed
    to Jonathan in the second person, which is how rule_length finds Beat 1."""
    return (_words(beat1_words) + "\n\n"
            + "Jonathan, your Oregon pick. " + _words(coda_words) + "\n\n"
            + "That's the column.")


def only(trips, rule):
    return [t for t in trips if t.rule_name == rule]


# --- rule 1 ------------------------------------------------------------------

def test_prior_season_rule():
    print("\nRule 1 — PRIOR_SEASON:")
    clean = "Blaine took the under at 9.5 wins and the model likes it."
    check("clean prose does not trip", not L.rule_prior_season(clean))

    shipped = [
        ("is once again in the fray", "again"),
        ("the waters have risen again", "again"),
        ("betting on a Texas resurgence", "resurgence"),
        ("you're hoping for a turnaround", "turnaround"),
        ("Then again, Chris is a man who spent two years in Paris.", "again"),
    ]
    for text, word in shipped:
        trips = L.rule_prior_season(text)
        check(f"shipped failure trips — {text[:44]}",
              any(t.matched_text.lower() == word for t in trips),
              f"got {[t.matched_text for t in trips]}")

    check("'still' is caught in a present-tense use too (the ban is a WORD test)",
          bool(L.rule_prior_season("He is still the favorite.")))
    check("'returning' is caught",
          bool(L.rule_prior_season("A returning starter anchors the line.")))

    # "back" is banned by SENSE, so it is matched as phrases, not as a word.
    check("'came back' (a returning sense) trips",
          bool(L.rule_prior_season("He never quite came back.")))
    check("'background' does NOT trip — word boundaries, not substrings",
          not L.rule_prior_season("Whether his audience gets the background."))
    check("a plain anatomical 'back' does NOT trip",
          not L.rule_prior_season("He keeps his cards close and his back straight."))

    t = L.rule_prior_season("Then again, Chris is a man.")[0]
    check("a trip carries the full sentence for the retry prompt",
          t.sentence == "Then again, Chris is a man.", t.sentence)
    check("a trip carries a remediation hint",
          "Delete the sentence" in t.remediation)


# --- rule 2 ------------------------------------------------------------------

def test_internal_structures_rule():
    print("\nRule 2 — INTERNAL_STRUCTURES:")
    clean = "The model gives Blaine an 86% chance and the numbers agree."
    check("clean attribution does not trip",
          not L.rule_internal_structures(clean))

    check("underscored form trips — market_gap",
          bool(L.rule_internal_structures("a market_gap of 0.669")))
    check("SPACE-SEPARATED form trips — the regen-2 failure",
          any(t.matched_text.lower() == "market gap" for t in
              L.rule_internal_structures(
                  "The market gap here is a negative 1.044.")),
          "this is the exact sentence that slipped the prompt-side ban")
    check("the regen-1 failure trips — 'The packet gives Blaine...'",
          bool(L.rule_internal_structures(
              "The packet gives Blaine a 0.86467 chance.")))
    for term in ("cache", "manifest", "baseline", "storyline", "coda",
                 "column memory", "narrative score", "moment size"):
        check(f"structure named as unprintable — {term}",
              bool(L.rule_internal_structures(f"The {term} says so.")))
    check("'packets' plural still trips on the stem boundary",
          bool(L.rule_internal_structures("The packet is wrong.")))


# --- rule 3 ------------------------------------------------------------------

def test_superlatives_rule():
    print("\nRule 3 — SUPERLATIVES (context test, not a raw word test):")
    pk = _packet()

    check("bare superlative with NO number and NO subject does not trip",
          not L.rule_superlatives("These are odds only an in-law could love.",
                                  pk),
          "no quantitative claim, no packet subject")

    check("the regen-3 failure trips — 'implied odds of only 22%'",
          any(t.matched_text.lower() == "only" for t in L.rule_superlatives(
              "An over at 10.5 wins with implied odds of only 22%?", pk)))
    check("the regen-2 failure trips — the hedged board ranking",
          any(t.matched_text.lower() == "lowest" for t in L.rule_superlatives(
              "The gap here is negative 1.044, the lowest on the board "
              "outside Blaine and Chris's tussle.", pk)),
          "hedged into technical truth and still a ranking claim")
    check("a superlative about a NAMED MANAGER trips",
          bool(L.rule_superlatives("Blaine made the biggest call of the day.",
                                   pk)))
    check("a superlative about a NAMED TEAM trips",
          bool(L.rule_superlatives("Oregon carries the worst number here.",
                                   pk)))
    check("a superlative beside a win total trips",
          bool(L.rule_superlatives("The smallest of the 10.5 wins lines.", pk)))

    # DOCUMENTED INTERACTION, not a defect. The spec calls "only a Longhorn
    # in-law could love" fine, and it is -- until the sentence also names a
    # manager, which is the spec's own context trigger. The spec resolves this
    # itself: "Err on the side of tripping when ambiguous." A false trip costs
    # one regeneration; a miss ships the failure the rule exists for.
    check("naming a manager pulls an idiomatic 'only' into scope (err-to-trip)",
          bool(L.rule_superlatives(
              "Blaine has taken the under at odds only a Longhorn in-law "
              "could love.", pk)),
          "documented over-trip, per 'err on the side of tripping'")

    check("with no packet, only the numeric context can trip",
          bool(L.rule_superlatives("The lowest at 22%.", None))
          and not L.rule_superlatives("Only an in-law could love it.", None))


# --- rule 4 ------------------------------------------------------------------

def test_invented_stakes_rule():
    print("\nRule 4 — INVENTED_STAKES:")
    none_stakes = _packet(stakes=None)
    real_stakes = _packet(stakes="loser buys dinner")

    shipped = ("But the stakes are real enough for Blaine and Chris, who are "
               "both playing for pride and, possibly, the chance to needle "
               "the other until next year.")
    trips = L.rule_invented_stakes(shipped, none_stakes)
    got = {t.matched_text.lower() for t in trips}
    check("the regen-2 failure trips on every stakes term it carries",
          {"stakes", "pride", "needle", "playing for"} <= got, f"got {got}")

    check("the regen-3 failure trips — 'the stakes couldn't be more...'",
          bool(L.rule_invented_stakes(
              "The stakes couldn't be more straightforward.", none_stakes)))
    check("'bragging rights' trips",
          bool(L.rule_invented_stakes("It is bragging rights and nothing else.",
                                      none_stakes)))
    check("'on the line' trips",
          bool(L.rule_invented_stakes("A season is on the line.", none_stakes)))

    check("clean prose about the PICKS does not trip",
          not L.rule_invented_stakes(
              "Blaine took the under and Chris took the over.", none_stakes))
    check("a group that HAS declared stakes is not linted for inventing them",
          not L.rule_invented_stakes(shipped, real_stakes),
          "the rule is about inventing, not about mentioning")


# --- rule 5 ------------------------------------------------------------------

def test_raw_probability_rule():
    print("\nRule 5 — RAW_PROBABILITY:")
    pk = _packet()
    check("the regen-1 failure trips — a raw 0.86467 on the page",
          any(t.matched_text == "0.86467" for t in L.rule_raw_probability(
              "The model gives Blaine a 0.86467 chance.", pk)))
    check("a rounded percent is correct and does not trip",
          not L.rule_raw_probability("The model gives Blaine an 86% chance.",
                                     pk))
    check("a market gap IS printed verbatim and is whitelisted — 1.044",
          not L.rule_raw_probability(
              "The market disagrees by 1.044 wins.", pk),
          "gaps are expected as-is; the whitelist is the packet's own values")
    check("a market gap inside 0-1 is whitelisted too — 0.983",
          not L.rule_raw_probability("It disagrees by 0.983.", pk))
    check("a decimal that is NOT a packet gap still trips — 0.500",
          bool(L.rule_raw_probability("A flat 0.500 chance.", pk)))
    check("a win total above 1 is not a probability and does not trip",
          not L.rule_raw_probability("Implied 9.456 wins.", pk))


# --- rule 6 ------------------------------------------------------------------

def test_raw_rounding_rule():
    print("\nRule 6 — RAW_ROUNDING:")
    pk = _packet()
    check("the regen-3 failure trips — 'about 9.5 wins' against 9.456",
          bool(L.rule_raw_rounding(
              "The market sees Oregon finishing with about 9.5 wins.", pk)))
    check("'roughly 9 wins' for the same team trips",
          bool(L.rule_raw_rounding("Oregon lands roughly 9 wins.", pk)))
    check("the verbatim number does not trip",
          not L.rule_raw_rounding("Oregon is implied at 9.456 wins.", pk))
    check("a hedge that matches the packet exactly does not trip",
          not L.rule_raw_rounding("Oregon sits at about 9.456 wins.", pk))
    check("a sentence naming no packet team does not trip",
          not L.rule_raw_rounding("Somebody wins about 9 wins.", pk))


# --- rule 7 ------------------------------------------------------------------

def test_length_rule():
    print("\nRule 7 — LENGTH (the addition; see the module docstring):")
    pk = _packet()

    good = _column(270, 90)          # ~270 Beat 1, ~365 total
    trips = L.rule_length(good, pk)
    check("an in-range column is clean",
          not trips, L.format_trips(trips))

    short = _column(202, 96)         # the regen-3 shape: 316 total, 202 Beat 1
    trips = L.rule_length(short, pk)
    kinds = " ".join(t.matched_text for t in trips)
    check("the regen-3 shape trips on BOTH totals",
          len(trips) == 2 and "total" in kinds and "Beat 1" in kinds, kinds)
    check("the trip states the actual number and the target",
          any("target is 350-450" in t.sentence for t in trips),
          "the retry prompt needs the gap, not just a verdict")

    over = _column(400, 200)
    check("an over-long column trips too — the target is a band, not a floor",
          bool(L.rule_length(over, pk)))

    check("an empty column trips rather than crashing",
          bool(L.rule_length("", pk)))
    check("no packet: Beat 1 falls back and the rule still runs",
          bool(L.rule_length(short, None)))


# --- aggregate ---------------------------------------------------------------

def test_lint_aggregates_and_dedupes():
    print("\nlint() over a whole column:")
    pk = _packet()
    # The regen-2 column, verbatim in the parts that matter.
    shipped = (
        "The 2026 season is upon us and Blaine has taken the under on Texas "
        "at 9.5 wins.\n\n"
        "But the stakes are real enough for Blaine and Chris, who are both "
        "playing for pride and, possibly, the chance to needle the other.\n\n"
        "Jonathan, your Oregon pick. The market gap here is a negative 1.044, "
        "the lowest on the board outside Blaine and Chris's tussle.")
    trips = L.lint(shipped, pk)
    rules = {t.rule_name for t in trips}
    check("it catches the regen-2 structure leak",
          "INTERNAL_STRUCTURES" in rules, str(rules))
    check("it catches the regen-2 hedged superlative", "SUPERLATIVES" in rules)
    check("it catches the regen-2 invented stakes", "INVENTED_STAKES" in rules)
    check("it catches the length miss", "LENGTH" in rules)

    keys = [(t.rule_name, t.matched_text.lower(), t.sentence) for t in trips]
    check("trips are deduped on (rule, match, sentence)",
          len(keys) == len(set(keys)), f"{len(keys)} trips, {len(set(keys))} unique")

    check("every trip carries all four fields populated",
          all(t.rule_name and t.matched_text and t.remediation is not None
              and t.sentence is not None for t in trips))
    check("as_dict is JSON-shaped for the companion trips file",
          set(trips[0].as_dict()) == {"rule", "matched", "sentence",
                                      "remediation"})

    clean = _column(270, 90).replace("filler", "prose")
    check("a structurally clean in-range column returns []",
          L.lint(clean, pk) == [], L.format_trips(L.lint(clean, pk)))
    check("format_trips says so in words",
          L.format_trips([]) == "lint clean: 0 trips.")


def test_ban_list_is_shared_with_the_prompt():
    """One source of truth: the prompt builder renders the linter's own tuple.

    A list stated to the model and a different list enforced afterwards is how
    a ban gets announced and never checked -- which is the bug that put this
    module here.
    """
    print("\nThe ban list is shared, not copied:")
    import generate_commentary as G
    check("generate_commentary imports the linter's constant",
          G.PRIOR_SEASON_BANS is L.PRIOR_SEASON_BANS)
    rendered = G._ps_enumeration()
    check("every banned word reaches the prompt text",
          all(w in rendered for w in L.PRIOR_SEASON_BANS),
          rendered[:90])
    check("the 'back' phrases are rendered as the sense parenthetical",
          "back (in any returning sense:" in rendered)


def main():
    print("lint_commentary.py — output-side gate, rule by rule")
    test_prior_season_rule()
    test_internal_structures_rule()
    test_superlatives_rule()
    test_invented_stakes_rule()
    test_raw_probability_rule()
    test_raw_rounding_rule()
    test_length_rule()
    test_lint_aggregates_and_dedupes()
    test_ban_list_is_shared_with_the_prompt()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
