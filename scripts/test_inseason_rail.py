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
value six months later.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_rail
import build_week_packet as bwp
import preseason_baseline as pre


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

SP = {"Texas": {"ranking": 6}, "Indiana": {"ranking": 14}}

BEAT = {"manager_id": "blaine", "team": "Indiana", "line": 10.5, "direction": "U"}


def collisions():
    return bwp.detect_collisions(CUR, PROJ_ROWS, GROUP, SP)


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
    assert bwp.detect_collisions(same, rows, GROUP, SP) == []


def test_shape_matches_preseason_exactly():
    """The key sets, compared against preseason's own builder."""
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

    assert set(got) == set(want), (
        "in-season collision keys drifted from preseason's: "
        f"only in-season={set(got) - set(want)}, only preseason={set(want) - set(got)}")
    assert set(got["sides"][0]) == set(want["sides"][0])


# --- the featured pick -------------------------------------------------------

def test_featured_pick_reshapes_the_coda_it_is_given():
    f = bwp.featured_pick_from_coda(BEAT, CUR, PROJ_ROWS, GROUP, SP)
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
    assert bwp.featured_pick_from_coda(None, CUR, PROJ_ROWS, GROUP, SP) is None


def test_degraded_projection_drops_the_block_rather_than_publishing_a_blank():
    assert bwp.featured_pick_from_coda(BEAT, CUR, {}, GROUP, SP) is None
    assert bwp.detect_collisions(CUR, {}, GROUP, SP) == []


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
            BEAT, CUR, PROJ_ROWS, GROUP, SP)))

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
            BEAT, CUR, PROJ_ROWS, GROUP, SP)))
    assert "collision" not in rail
    assert rail["featured_pick"]["team"] == "Indiana"


def test_neither_block_means_no_rail_at_all():
    assert build_rail.build_rail(packet()) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
