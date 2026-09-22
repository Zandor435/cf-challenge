"""Behavioral acceptance tests for canonical pace and preserved game expectations."""
import math

import pytest

import backfill_pregame
import build_email_payload
import pace
import pregame
import projector
import run_groups
import scoring
import utils


def test_acceptance_examples():
    assert pace.team_pace("O", pace.projected_final_wins(0, [.85] * 10), 6.5) == 2
    finish = pace.projected_final_wins(3, [.8, .7, .6, .6, .6, .5, .5, .5, .4])
    assert finish == pytest.approx(8.2)
    assert pace.team_pace("O", finish, 6.5) == pytest.approx(1.7)
    assert pace.team_pace("U", finish, 6.5) == pytest.approx(-1.7)
    assert pace.projected_final_wins(9, []) == 9
    assert pace.team_pace("O", 9, 6.5) == 2.5
    assert pace.team_pace("U", 9, 6.5) == -2.5
    assert pace.manager_pace([1.7, .7, -.4, -.8]) == pytest.approx(1.2)


def test_precision_ranking_and_rounding():
    values = [.0249] * 4
    assert pace.manager_pace(values) == .0996
    total, parts = projector.display_deltas(.0996, values)
    assert total == "+0.1" and parts == ["0.0"] * 4
    assert projector.display_deltas(-.001, [-.001]) == ("0.0", ["0.0"])
    rows = [{"manager_id": "a", "expected_total": 1.01, "floor": 10},
            {"manager_id": "b", "expected_total": 1.02, "floor": -10},
            {"manager_id": "c", "expected_total": 1.02, "floor": 0},
            {"manager_id": "d", "expected_total": 1.02, "floor": 0}]
    assert [r["manager_id"] for r in sorted(rows, key=pace.rank_key)] == ["c", "d", "b", "a"]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -.1, 1.1])
def test_invalid_probability_is_not_a_missing_game(value):
    with pytest.raises(ValueError) as caught:
        pace.projected_final_wins(3, [.5, value])
    assert "Every remaining game" in str(caught.value)


def test_thresholds_do_not_change_math():
    assert [projector.game_bucket(p) for p in [.39999, .4, .5, .6, .60001]] == [
        "likely_loss", "toss_up", "toss_up", "toss_up", "likely_win"]
    assert pace.projected_final_wins(0, [.75, .52, .41, .18]) == pytest.approx(1.86)


def test_results_byes_cancellation_ties_and_rescheduling(monkeypatch):
    utils.pin_contract_fixture()
    base = {"home_team": "Texas", "away_team": "Ohio State", "neutral_site": False,
            "completed": False, "home_points": None, "away_points": None}
    games = [{**base, "id": 1, "week": 1, "completed": True, "home_points": 10, "away_points": 3},
             {**base, "id": 2, "week": 3, "completed": True, "home_points": 0, "away_points": 3},
             {**base, "id": 3, "week": 4, "completed": True, "home_points": 3, "away_points": 3},
             {**base, "id": 4, "week": 5, "cancelled": True},
             {**base, "id": 5, "week": 6},
             {**base, "id": 5, "week": 8, "status": "postponed"}]
    monkeypatch.setattr(utils, "_season_games", lambda season: games)
    state = utils.team_state("Texas", {"count_conference_championship": False})
    assert state["banked_wins"] == state["banked_losses"] == 1
    assert [g["result"] for g in state["played_games"]] == ["W", "L", "T"]
    assert [g["week"] for g in state["remaining_games"]] == [8]
    assert pace.projected_final_wins(state["banked_wins"], [.7]) == 1.7
    games[-1] = {**games[-1], "completed": True, "home_points": 20, "away_points": 0}
    state = utils.team_state("Texas", {"count_conference_championship": False})
    assert state["remaining_games"] == []
    assert pace.projected_final_wins(state["banked_wins"], []) == 2


def test_saved_pregame_shading_never_uses_hindsight():
    game = {"id": 1, "week": 1, "opponent": "Ohio State", "home_away": "home",
            "start_date": "2026-09-05T17:00:00Z", "result": "W"}
    saved = {pregame.game_key("Texas", game): {"p_win": .2, "captured_at": "2026-09-04T12:00:00Z"}}
    rows = pregame.completed_rows("Texas", [game], saved, projector.game_bucket, projector._percent)
    assert rows[0]["result"] == "W" and rows[0]["bucket"] == "likely_loss"
    assert rows[0]["p_win"] == .2
    saved[pregame.game_key("Texas", game)]["captured_at"] = "2026-09-06T12:00:00Z"
    assert pregame.completed_rows("Texas", [game], saved, projector.game_bucket, projector._percent)[0]["p_win"] is None
    assert pregame.completed_rows("Texas", [game], {}, projector.game_bucket, projector._percent)[0]["bucket"] is None


def _fixture():
    utils.pin_contract_fixture()
    return ({"group_id": "pace_test", "count_conference_championship": False,
             "managers": [{"manager_id": "a", "display_name": "A"}]},
            [{"manager": "a", "team": "Texas", "line": 6.5, "direction": "O", "conference": "SEC"}])


def test_simulation_failure_does_not_block_pace(monkeypatch):
    config, picks = _fixture()
    monkeypatch.setattr(projector, "simulate_totals", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulation failed")))
    projection = projector.build_projection(config, picks, 3)
    assert projection["managers"][0]["p_win_pool"] is None
    standings = scoring.build_standings(config, picks, 3, projection=projection)
    assert math.isfinite(standings["managers"][0]["expected_total"])
    assert standings["managers"][0]["rank"] == 1


def test_failed_refresh_retains_coherent_last_pace(monkeypatch, tmp_path):
    config, picks = _fixture()
    monkeypatch.setattr(utils, "WEB_DATA_DIR", tmp_path)
    first, first_projection = run_groups.publish_pace(config, picks, 3)
    monkeypatch.setattr(projector, "build_projection", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad probability")))
    stale, projection = run_groups.publish_pace(config, picks, 3)
    assert stale["meta"]["pace_stale"] is True
    assert stale["managers"] == first["managers"]
    assert projection["meta"]["generated_at"] == stale["meta"]["generated_at"] == first["meta"]["generated_at"]
    assert projection["managers"] == first_projection["managers"]


def test_first_failure_shows_unavailable_and_legacy_history_stays_missing(monkeypatch, tmp_path):
    config, picks = _fixture()
    monkeypatch.setattr(utils, "WEB_DATA_DIR", tmp_path)
    monkeypatch.setattr(projector, "build_projection", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad probability")))
    standings, _ = run_groups.publish_pace(config, picks, 3)
    assert standings["managers"][0]["expected_total"] is None
    assert standings["managers"][0]["rank"] is None
    assert pace.snapshot_total({"picks": [{"banked_delta": 2}]}) is None
    assert pace.snapshot_total({"picks": [{"expected_delta": .123}, {"expected_delta": .456}]}) == pytest.approx(.579)


def test_probability_refresh_and_missing_rating_fallback(monkeypatch):
    config, picks = _fixture()
    before = projector.build_projection(config, picks, 3, include_simulation=False)
    monkeypatch.setattr(utils, "season_sp_ratings", lambda season: {})
    after = projector.build_projection(config, picks, 3, include_simulation=False)
    original = before["managers"][0]["picks"][0]
    updated = after["managers"][0]["picks"][0]
    assert updated["banked_wins"] == original["banked_wins"]
    assert updated["expected_final_wins"] != original["expected_final_wins"]
    assert all(g["probability_estimated"] for g in updated["remaining_games"])
    assert updated["expected_final_wins"] == pytest.approx(
        updated["banked_wins"] + math.fsum(g["p_win"] for g in updated["remaining_games"]))


def test_backfill_requires_unique_match_and_prekickoff_publication(monkeypatch):
    monkeypatch.setattr(utils, "get_season", lambda: 2026)
    game = {"id": 9, "week": 1, "opponent": "Ohio State", "home_away": "home", "neutral": False,
            "start_date": "2026-09-05T17:00:00Z", "result": "W"}
    monkeypatch.setattr(utils, "team_state", lambda *a: {"played_games": [game], "remaining_games": []})
    old = {"meta": {"season": 2026, "generated_at": "2026-09-04T12:00:00Z"}, "managers": [{"picks": [
        {"team": "Texas", "remaining_games": [{k: v for k, v in game.items() if k not in ("id", "start_date", "result")}]}]}]}
    old["managers"][0]["picks"][0]["remaining_games"][0]["p_win"] = .2
    saved = {}
    backfill_pregame.recover(old, {}, saved, "abc")
    assert saved["Texas|9"]["p_win"] == .2
    assert saved["Texas|9"]["source"] == "git:abc"
    old["meta"]["generated_at"] = "2026-09-06T12:00:00Z"
    old["managers"][0]["picks"][0]["remaining_games"][0]["p_win"] = .9
    backfill_pregame.recover(old, {}, saved, "after")
    assert saved["Texas|9"]["p_win"] == .2
    monkeypatch.setattr(utils, "team_state", lambda *a: {"played_games": [game, game], "remaining_games": []})
    ambiguous = {}
    old["meta"]["generated_at"] = "2026-09-04T12:00:00Z"
    backfill_pregame.recover(old, {}, ambiguous, "ambiguous")
    assert ambiguous == {}


def test_latest_pregame_forecast_is_frozen_at_kickoff():
    game = {"id": 9, "week": 1, "opponent": "Ohio State", "home_away": "home",
            "start_date": "2026-09-05T17:00:00Z", "p_win": .2}
    document = {"meta": {"generated_at": "2026-09-01T12:00:00Z"},
                "managers": [{"picks": [{"team": "Texas", "remaining_games": [game]}]}]}
    saved = {}
    pregame._capture(document, saved)
    document["meta"]["generated_at"] = "2026-09-04T12:00:00Z"
    game["p_win"] = .3
    pregame._capture(document, saved)
    assert saved["Texas|9"]["p_win"] == .3
    document["meta"]["generated_at"] = "2026-09-05T17:00:00Z"
    game["p_win"] = .9
    pregame._capture(document, saved)
    assert saved["Texas|9"]["p_win"] == .3


def test_completed_game_without_score_never_becomes_a_probability(monkeypatch):
    config, _ = _fixture()
    monkeypatch.setattr(utils, "_season_games", lambda season: [{"id": 1, "week": 1,
        "home_team": "Texas", "away_team": "Ohio State", "completed": True}])
    with pytest.raises(ValueError) as caught:
        utils.team_state("Texas", config)
    assert "has no final score" in str(caught.value)


def test_conditional_pace_uses_results_not_awarded_points():
    config = {"group_id": "conditional", "managers": [{"manager_id": m, "display_name": m} for m in ("a", "b")]}
    picks = [{"manager": m, "team": "Texas", "direction": d, "line": 3.5, "conference": "SEC"}
             for m, d in (("a", "O"), ("b", "U"))]
    cache = {"Texas": {"banked_wins": 3, "probs": [.7], "conference": "SEC", "remaining_games": [
        {"week": 4, "opponent": "Rice", "home_away": "home", "neutral": False}]}}
    game = projector.build_game_leverage(config, picks, team_cache=cache)["games"][0]
    managers = {m["manager_id"]: m for m in game["managers"]}
    assert managers["a"]["pace_now"] == pytest.approx(.2)
    assert managers["a"]["pace_if_win"] == pytest.approx(.5)
    assert managers["a"]["pace_if_loss"] == pytest.approx(-.5)
    assert managers["b"]["pace_if_win"] == pytest.approx(-.5)
    assert managers["b"]["pace_if_loss"] == pytest.approx(.5)


def test_email_standings_do_not_take_scores_from_optional_analytics():
    canonical = {"managers": [{"manager_id": "a", "display_name": "A", "rank": 1,
                               "expected_total": .0996, "banked_total": -10}]}
    old_analysis = {"race": {"managers": [{"manager_id": "b", "display_name": "B",
                                           "rank": 1, "expected_total": 999}]}}
    board = build_email_payload.build_board1(old_analysis, canonical)
    assert [r["manager_id"] for r in board["rows"]] == ["a"]
    assert board["rows"][0]["expected_total"] == "+0.1"
    assert build_email_payload.fmt_signed(-.001) == "0.0"
