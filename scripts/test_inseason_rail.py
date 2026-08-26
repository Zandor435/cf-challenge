#!/usr/bin/env python3
"""
test_inseason_rail.py -- the in-season packet feeds the rail the same shapes
the Week 0 packet does.

WHY THIS EXISTS AS A TEST RATHER THAN A RUN. The blocks under test only appear
on a PACKED REAL WEEK, and there is no in-season data to pack: the only cache
on disk is season 2026 with zero played games, and the 2025 boards under
docs/data/test/ were scored against a cache that is gone. Waiting for week 1 to
discover that build_rail cannot read this builder's output is exactly the
handoff bug this replaces.

So the fixtures are the smallest thing that exercises the path: two managers on
opposite sides of one real team, a third pick that must NOT register, and a
projection row per pick. Real team names, because detect_collisions asks
utils.team_state for the scheduled-game count and that reads the live cache.

THE SHAPE ASSERTION IS AGAINST PRESEASON ITSELF, not against a copy of its key
list written out here. preseason_baseline._collisions is run over an equivalent
fixture and the key sets are compared, so the day someone adds a field to one
builder and not the other, this fails instead of the rail quietly losing a
value six months later. The two fields the in-season block deliberately does
NOT carry are pinned as an exact set rather than waved through, so dropping a
third one is a failure and not a silent narrowing -- see
test_shape_is_a_pinned_subset_of_preseason for why those two are absent.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_rail
import build_week_packet as bwp
import preseason_baseline as pre


NL = chr(10)   # the source-scanning tests below split on it

GROUP = {"group_id": "test-fixture"}

# Two sides of one line, plus a pick nobody else holds.
CUR = {
    "blaine": {
        "manager_id": "blaine", "display_name": "Blaine",
        "picks": {
            "Texas": {"team": "Texas", "line": 9.5, "direction": "U"},
            "Indiana": {"team": "Indiana", "line": 10.5, "direction": "U"},
        },
    },
    "chris": {
        "manager_id": "chris", "display_name": "Chris",
        "picks": {"Texas": {"team": "Texas", "line": 9.5, "direction": "O"}},
    },
}

PROJ_ROWS = {
    ("blaine", "Texas"): {"expected_final_wins": 7.819, "expected_delta": 1.68,
                          "p_beat_line": 0.86467},
    ("chris", "Texas"): {"expected_final_wins": 7.819, "expected_delta": -1.68,
                         "p_beat_line": 0.13533},
    ("blaine", "Indiana"): {"expected_final_wins": 9.39, "expected_delta": 1.11,
                            "p_beat_line": 0.797449},
}

BEAT = {"manager_id": "blaine", "team": "Indiana", "line": 10.5, "direction": "U"}


def collisions():
    return bwp.detect_collisions(CUR, PROJ_ROWS, GROUP)


# --- the collision block -----------------------------------------------------

def test_opposite_sides_on_one_line_collide():
    found = collisions()
    assert len(found) == 1
    c = found[0]
    assert c["team"] == "Texas"
    assert c["line"] == 9.5
    assert c["implied_expected_wins"] == 7.819
    assert [s["name"] for s in c["sides"]] == ["Blaine", "Chris"]
    assert [s["direction"] for s in c["sides"]] == ["U", "O"]


def test_a_pick_only_one_manager_holds_is_not_a_collision():
    # Indiana is in CUR and in the projection; it must not appear.
    assert all(c["team"] != "Indiana" for c in collisions())


def test_same_side_is_a_shared_bet_not_a_collision():
    same = {
        "a": {"manager_id": "a", "display_name": "A",
              "picks": {"Texas": {"team": "Texas", "line": 9.5, "direction": "O"}}},
        "b": {"manager_id": "b", "display_name": "B",
              "picks": {"Texas": {"team": "Texas", "line": 9.5, "direction": "O"}}},
    }
    rows = {("a", "Texas"): PROJ_ROWS[("chris", "Texas")],
            ("b", "Texas"): PROJ_ROWS[("chris", "Texas")]}
    assert bwp.detect_collisions(same, rows, GROUP) == []


def test_shape_is_a_pinned_subset_of_preseason():
    """Compared against preseason's own builder, with the delta pinned.

    The in-season block carries every collision key preseason does EXCEPT
    sp_ranking and games_scheduled. Those two are not omitted for tidiness:
    reading them means utils.season_sp_ratings()/utils.team_state(), both of
    which memoise the parsed cache for the whole process, and build_packet is
    called by test_commentary_prompt with utils.load_cache stubbed to a
    synthetic played slate -- which that memo then served to every later test
    in the run. Four preseason tests died on "LSU: 1 game(s) already played".
    build_rail.py reads neither field, so the fix is to not emit them.

    Asserted as a subset PLUS an exact delta. Subset alone would let a real
    drift through; equality is not true and pinning the difference says which
    two are missing and makes a third one a failure.
    """
    by_mgr = {
        "blaine": [{"team": "Texas", "line": 9.5, "direction": "U",
                    "implied_expected_wins": 7.819, "market_gap": 1.681,
                    "p_beat_line": 0.86467, "sp_ranking": 6,
                    "games_scheduled": 12}],
        "chris": [{"team": "Texas", "line": 9.5, "direction": "O",
                   "implied_expected_wins": 7.819, "market_gap": -1.681,
                   "p_beat_line": 0.13533, "sp_ranking": 6,
                   "games_scheduled": 12}],
    }
    want = pre._collisions(by_mgr, {"blaine": "Blaine", "chris": "Chris"})[0]
    got = collisions()[0]

    assert set(got) <= set(want), (
        "in-season collisions carry a key preseason does not: "
        f"{set(got) - set(want)}")
    assert set(want) - set(got) == {"sp_ranking", "games_scheduled"}, (
        "the preseason/in-season key delta moved: "
        f"{set(want) - set(got)}")
    # The sides, where there is no delta at all.
    assert set(got["sides"][0]) == set(want["sides"][0])
    # And everything build_rail actually reads is present.
    assert {"team", "line", "implied_expected_wins", "sides"} <= set(got)


# --- the featured pick -------------------------------------------------------

def test_featured_pick_reshapes_the_coda_it_is_given():
    f = bwp.featured_pick_from_coda(BEAT, CUR, PROJ_ROWS, GROUP)
    assert f["manager_id"] == "blaine"
    assert f["name"] == "Blaine"
    assert f["team"] == "Indiana"
    assert f["direction"] == "U"
    assert f["line"] == 10.5
    assert f["implied_expected_wins"] == 9.39
    assert f["market_gap"] == 1.11
    # It reshapes; it does not re-rank. Texas has the wider gap and is not it.
    assert "never a second ranking" in f["selected_by"]


def test_no_coda_means_no_featured_pick():
    assert bwp.featured_pick_from_coda(None, CUR, PROJ_ROWS, GROUP) is None


def test_degraded_projection_drops_the_block_rather_than_publishing_a_blank():
    assert bwp.featured_pick_from_coda(BEAT, CUR, {}, GROUP) is None
    assert bwp.detect_collisions(CUR, {}, GROUP) == []


# --- the handoff: build_rail reads it with no edit ---------------------------

def packet(**extra):
    doc = {"group_id": "panel", "week": 6,
           "generated_at": "2026-10-01T00:00:00+00:00"}
    doc.update(extra)
    return doc


def test_build_rail_reads_the_in_season_packet():
    rail = build_rail.build_rail(packet(
        collisions=collisions(),
        worst_pick_on_the_board=bwp.featured_pick_from_coda(
            BEAT, CUR, PROJ_ROWS, GROUP)))

    assert rail["collision"]["team"] == "Texas"
    assert rail["collision"]["line"] == 9.5
    assert rail["collision"]["implied_expected_wins"] == 7.819
    # Rounded in Python, published as the string the card prints.
    assert [p["p_beat_line"] for p in rail["collision"]["picks"]] == ["86%", "14%"]
    assert [p["manager"] for p in rail["collision"]["picks"]] == ["Blaine", "Chris"]

    assert rail["featured_pick"]["team"] == "Indiana"
    assert rail["featured_pick"]["expected_final_wins"] == 9.39
    assert rail["featured_pick"]["expected_delta"] == 1.11


def test_absent_collision_omits_the_key_and_keeps_the_other_card():
    rail = build_rail.build_rail(packet(
        worst_pick_on_the_board=bwp.featured_pick_from_coda(
            BEAT, CUR, PROJ_ROWS, GROUP)))
    assert "collision" not in rail
    assert rail["featured_pick"]["team"] == "Indiana"


def test_neither_block_means_no_rail_at_all():
    assert build_rail.build_rail(packet()) is None


# --- the card's title --------------------------------------------------------
#
# The two builders answer DIFFERENT selection questions with the SAME fields --
# preseason picks the lowest market_gap, in season it is the bad-beat coda --
# and the card was titled "<team> outlook" in both, so week 1 changed the
# card's subject under an unchanged heading. The title is a published field
# now, and these pin that it is written in Python, differs between the two
# states, and reaches the rail unedited.

def test_in_season_title_is_the_coda_not_the_outlook():
    f = bwp.featured_pick_from_coda(BEAT, CUR, PROJ_ROWS, GROUP)
    assert f["card_title"] == "Indiana bad beat"
    # The team survives into the head, which is the ONLY place the card prints
    # one -- the body is manager / pick / model implied / market gap.
    assert f["team"] in f["card_title"]


def test_preseason_title_is_the_wording_the_page_already_shipped():
    """Character for character what svp.html used to build in JS, so
    publishing the field changes nothing a Week 0 reader sees."""
    import re
    src = (Path(__file__).resolve().parent / "preseason_baseline.py").read_text(
        encoding="utf-8")
    assert re.search(r'"card_title":\s*f"\{worst\[.team.\]\} outlook"', src), (
        "preseason's card_title is no longer the '<team> outlook' string the "
        "page rendered before the field existed")


def test_the_two_states_do_not_share_a_title():
    """The whole point. If these ever converge the card is back to swapping
    subject silently."""
    in_season = bwp.featured_pick_from_coda(BEAT, CUR, PROJ_ROWS, GROUP)
    assert in_season["card_title"] != f"{in_season['team']} outlook"


def test_build_rail_copies_the_title_verbatim():
    rail = build_rail.build_rail(packet(
        worst_pick_on_the_board=bwp.featured_pick_from_coda(
            BEAT, CUR, PROJ_ROWS, GROUP)))
    assert rail["featured_pick"]["card_title"] == "Indiana bad beat"


def test_a_pick_with_no_title_stops_the_run(capsys):
    """Fail loud in Python (playbook rule 4). Both builders emit it, so an
    absent one is a packet-shape change, not the ordinary absence the missing
    `featured_pick` KEY represents. svp.html's fallback covers the other case
    -- a rail.json written before the field existed -- and is on presence, not
    on week."""
    w = bwp.featured_pick_from_coda(BEAT, CUR, PROJ_ROWS, GROUP)
    del w["card_title"]
    with pytest.raises(SystemExit) as e:
        build_rail.build_rail(packet(worst_pick_on_the_board=w))
    assert e.value.code == 1
    assert "card_title" in capsys.readouterr().err


def test_the_page_never_infers_the_title_from_the_week():
    """The rule the fix depends on: the frontend prints the field and decides
    nothing. A conditional on week/preseason in this function would put the
    state machine back in JS, which is what shipped the wrong heading."""
    src = (Path(__file__).resolve().parents[1] / "docs" / "svp.html").read_text(
        encoding="utf-8")
    body = src.split("function renderOutlook(f) {")[1].split(NL + "    }")[0]
    code = NL.join(ln for ln in body.splitlines()
                   if not ln.lstrip().startswith("//"))
    assert "card_title" in code
    for banned in ("week", "preseason"):
        assert banned not in code, (
            f"renderOutlook now reads {banned!r}; the card title must come "
            f"from rail.json, not from the page's idea of the season state")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
