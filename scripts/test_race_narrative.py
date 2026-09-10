"""Race-context math: isolated schedules, honest baselines, coherent scenarios."""
from copy import deepcopy

import pytest

import projector as P
import race_narrative as R
import utils


def config():
    return {'group_id': 'unit', 'count_conference_championship': False,
            'managers': [{'manager_id': 'over', 'display_name': 'Over'},
                         {'manager_id': 'under', 'display_name': 'Under'}]}


def pick(mid, team='A', direction='O'):
    return {'manager': mid, 'team': team, 'direction': direction, 'line': .5,
            'conference': 'Test'}


def game(opponent='B', week=2, home=True):
    return {'week': week, 'opponent': opponent,
            'home_away': 'home' if home else 'away', 'neutral': False}


def projection():
    return {'meta': {'season': 2026}, 'managers': [
        {'manager_id': 'over', 'display_name': 'Over', 'p_win_pool': .6,
         'picks': [dict(pick('over'), expected_delta=.1)]},
        {'manager_id': 'under', 'display_name': 'Under', 'p_win_pool': .4,
         'picks': [dict(pick('under', direction='U'), expected_delta=-.1)]}]}


def baseline():
    return {'season': 2026, 'frozen_at': '2026-08-21T12:00:00Z',
            'count_conference_championship': False,
            'managers': deepcopy(projection()['managers'])}


def test_schedule_isolates_opponents_and_reverses_under_effect():
    old = {'A': {'rating': 0}, 'B': {'rating': 0}}
    new = {'A': {'rating': 100}, 'B': {'rating': 10}}
    states = {'A': {'remaining_games': [game()]}}
    result = R.build_schedule_watch(projection(), states, old, new, '2026-08-21')
    row = result['teams'][0]
    expected = float(P.game_win_prob(0, 10, True, False) - P.game_win_prob(0, 0, True, False))
    assert row['schedule_win_change'] == round(expected, 3)
    assert row['direction'] == 'harder'
    assert row['owners'][0]['effect'] == 'hurts'
    assert row['owners'][1]['effect'] == 'helps'
    # Improving the picked team never contaminates the schedule-only metric.
    new['A']['rating'] = -100
    assert R.build_schedule_watch(projection(), states, old, new, '')['teams'] == result['teams']


def test_schedule_compares_same_remaining_games_and_reports_missing_ratings():
    ratings = {'A': {'rating': 0}, 'B': {'rating': 0}}
    states = {'A': {'played_games': [game('Already played', 1)],
                    'remaining_games': [game(), game('Unrated', 3)]}}
    row = R.build_schedule_watch(projection(), states, ratings, ratings, '')['teams'][0]
    assert row['schedule_win_change'] == 0
    assert row['compared_games'] == 1 and row['remaining_games'] == 2
    assert row['unrated_games'] == 1
    assert [g['opponent'] for g in row['opponents']] == ['B']
    missing = R.build_schedule_watch(projection(), states, {}, ratings, '')
    assert missing['available'] is False
    assert missing['teams'][0]['schedule_win_change'] is None


@pytest.mark.parametrize('change', ['season', 'roster', 'line', 'direction', 'rules'])
def test_incomparable_draft_odds_are_null(change):
    c, pr, old = config(), projection(), baseline()
    if change == 'season':
        old['season'] = 2025
    elif change == 'roster':
        old['managers'].pop()
    elif change == 'line':
        old['managers'][0]['picks'][0]['line'] = 1.5
    elif change == 'direction':
        old['managers'][0]['picks'][0]['direction'] = 'U'
    else:
        old['count_conference_championship'] = True
    result = R.draft_comparison(c, pr, old)
    assert not result['available'] and result['reason']
    assert all(m['draft_move'] is None for m in result['managers'].values())


def test_draft_and_weekly_movements_use_separate_baselines():
    pr, old = projection(), baseline()
    old['managers'][0]['p_win_pool'] = .3
    old['managers'][0]['picks'][0]['expected_delta'] = -.4
    draft = R.draft_comparison(config(), pr, old)
    assert draft['managers']['over']['draft_move'] == .3
    prior = {'as_of_week': 1, 'managers': [{'manager_id': 'over', 'p_win_pool': .5,
             'picks': [{'team': 'A', 'expected_delta': -.1}]}]}
    row = next(m for m in R.build_race_story(pr, prior, old, draft)['managers']
               if m['manager_id'] == 'over')
    assert row['basis'] == 'week' and row['odds_change'] == .1
    assert row['drivers'][0]['change'] == .2
    assert R.draft_comparison(config(), pr, None)['available'] is False


def test_forcing_a_game_respects_opposing_holders_and_rare_outcomes(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    monkeypatch.setattr(P, 'POOL_SIM_TRIALS', 1000)
    picks = [pick('over'), pick('under', direction='U')]
    tc = {'A': {'banked_wins': 0, 'probs': [1e-9], 'remaining_games': [game()]}}
    g = P.build_game_leverage(config(), picks, team_cache=tc)['games'][0]
    by = {m['manager_id']: m for m in g['managers']}
    assert by['over']['p_if_win'] == 1 and by['over']['p_if_loss'] == 0
    assert by['under']['p_if_win'] == 0 and by['under']['p_if_loss'] == 1
    assert g['impact'] == 1


def test_forcing_head_to_head_never_lets_both_sides_win(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    monkeypatch.setattr(P, 'POOL_SIM_TRIALS', 1000)
    tc = {'A': {'banked_wins': 0, 'probs': [.4], 'remaining_games': [game()]},
          'B': {'banked_wins': 0, 'probs': [.6], 'remaining_games': [game('A', home=False)]}}
    result = P.build_game_leverage(config(), [pick('over'), pick('under', 'B')], team_cache=tc)
    assert len(result['games']) == 1
    by = {m['manager_id']: m for m in result['games'][0]['managers']}
    assert by['over']['p_if_win'] == 1 and by['under']['p_if_win'] == 0
    assert by['over']['p_if_loss'] == 0 and by['under']['p_if_loss'] == 1


def test_identical_portfolios_split_title_and_no_games_are_invented(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    monkeypatch.setattr(P, 'POOL_SIM_TRIALS', 1000)
    tc = {'A': {'banked_wins': 0, 'probs': [.5, .5],
                'remaining_games': [game(week=2), game('C', week=3)]}}
    picks = [pick('over'), pick('under')]
    result = P.build_game_leverage(config(), picks, team_cache=tc)
    assert result['week'] == 2 and len(result['games']) == 1
    assert result['games'][0]['impact'] == 0
    assert all(m['p_if_win'] == .5 and m['p_if_loss'] == .5
               for m in result['games'][0]['managers'])
    tc['A'].update(probs=[], remaining_games=[])
    assert P.build_game_leverage(config(), picks, team_cache=tc)['games'] == []


def test_conditionals_recover_base_odds_within_sampling_error(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    monkeypatch.setattr(P, 'POOL_SIM_TRIALS', 20000)
    tc = {'A': {'banked_wins': 0, 'probs': [.4, .7],
                'remaining_games': [game(), game('C', 3)]}}
    picks = [pick('over'), pick('under', direction='U')]
    _, _, base = P.simulate_totals(config(), picks, team_cache=tc)
    g = P.build_game_leverage(config(), picks, team_cache=tc)['games'][0]
    for m in g['managers']:
        weighted = .4 * m['p_if_win'] + .6 * m['p_if_loss']
        assert abs(weighted - base[m['manager_id']]) < .015
    assert sum(m['p_if_win'] for m in g['managers']) == pytest.approx(1, abs=2e-6)


def test_result_surprise_uses_frozen_ratings_and_pick_direction():
    states = {'A': {'played_games': [dict(game(week=1), result='L')]}}
    result = R.result_surprises(projection(), states,
                                {'A': {'rating': 0}, 'B': {'rating': 0}})
    by = {r['manager_id']: r for r in result}
    assert by['over']['points_surprise'] < 0
    assert by['under']['points_surprise'] == -by['over']['points_surprise']
    assert by['over']['draft_p_win'] == round(float(P.game_win_prob(0, 0, True, False)), 6)


def test_title_routes_condition_on_shared_results_and_only_next_four(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    picks = [dict(pick('over'), line=2.5), dict(pick('under', direction='U'), line=2.5)]
    tc = {'A': {'banked_wins': 0, 'probs': [.5] * 6,
                'remaining_games': [game(f'Opponent{i}', week=i) for i in range(1, 7)]}}
    result = P.build_title_routes(config(), picks, team_cache=tc)
    _, _, base, draws, _ = P.simulate_totals(config(), picks, team_cache=tc, return_draws=True)
    for manager in result['managers']:
        assert manager['routes']
        r = manager['routes'][0]
        assert r['next_games'] == 4
        assert [g['week'] for g in r['stretch']] == [1, 2, 3, 4]
        favorable = draws['A'][:, :4] if r['direction'] == 'O' else ~draws['A'][:, :4]
        mask = favorable.sum(axis=1) >= r['needed']
        assert r['matching_trials'] == int(mask.sum())
        final = draws['A'].sum(axis=1)
        won = (final >= 3) if r['direction'] == 'O' else (final <= 2)
        assert r['p_title_if'] == round(float(won[mask].mean()), 6)
        assert r['p_title_now'] == round(base[manager['manager_id']], 6)
        assert r['p_title_if'] > r['p_title_now']
        assert .08 <= r['event_probability'] <= .8


def test_no_route_is_invented_for_identical_picks_or_tiny_samples(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    tc = {'A': {'banked_wins': 0, 'probs': [.5] * 4,
                'remaining_games': [game(week=i) for i in range(1, 5)]}}
    same = P.build_title_routes(config(), [pick('over'), pick('under')], team_cache=tc)
    assert all(m['routes'] == [] for m in same['managers'])
    monkeypatch.setattr(P, 'POOL_SIM_TRIALS', 100)
    rare = P.build_title_routes(config(), [pick('over'), pick('under', direction='U')], team_cache=tc)
    assert all(m['routes'] == [] for m in rare['managers'])


def test_routes_shorten_at_season_end_and_finish_honestly(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    picks = [pick('over'), pick('under', direction='U')]
    tc = {'A': {'banked_wins': 0, 'probs': [.5], 'remaining_games': [game()]}}
    routes = P.build_title_routes(config(), picks, team_cache=tc)
    assert all(m['routes'][0]['next_games'] == 1 for m in routes['managers'])
    described = R.describe_title_routes(routes, projection())
    assert all('its next game' in m['routes'][0]['condition'] for m in described['managers'])
    assert all('next 4' not in m['routes'][0]['story'] for m in described['managers'])
    tc['A'].update(probs=[], remaining_games=[])
    finished = R.describe_title_routes(P.build_title_routes(config(), picks, team_cache=tc), projection())
    assert all(m['finished'] and not m['routes'] for m in finished['managers'])
    assert all('games are finished' in m['intro'] for m in finished['managers'])


def test_friendly_copy_distinguishes_an_upset_from_a_likely_loss():
    routes = {'managers': [{'manager_id': 'under', 'display_name': 'Under', 'p_win_pool': .4,
               'finished': False, 'routes': [{'team': 'A', 'direction': 'U', 'line': 2.5,
               'needed': 2, 'next_games': 4, 'event_probability': .2,
               'games_to_watch': [{'opponent': 'B', 'p_win': .9},
                                  {'opponent': 'C', 'p_win': .1}]}]}]}
    r = R.describe_title_routes(routes, projection())['managers'][0]['routes'][0]
    assert r['headline'] == 'Root against A.'
    assert 'loses at least 2 of its next 4' in r['story']
    assert 'upset' in r['games_to_watch'][0]['rooting_note']
    assert r['games_to_watch'][1]['rooting_note'] == 'C is favored to beat A.'
    assert 'Over is pulling the other way' in r['rival_note']
    assert 'guarantee' not in r['story'] and 'must' not in r['story']


def test_sweep_highlights_the_hardest_game(monkeypatch):
    monkeypatch.setattr(utils, 'resolve_canonical', lambda t: t)
    picks = [dict(pick('over'), line=3.5), dict(pick('under', direction='U'), line=3.5)]
    tc = {'A': {'banked_wins': 0, 'probs': [.9, .9, .9, .5],
                'remaining_games': [game(f'Opponent{i}', week=i) for i in range(1, 5)]}}
    route = next(m for m in P.build_title_routes(config(), picks, team_cache=tc)['managers']
                 if m['manager_id'] == 'over')['routes'][0]
    assert route['needed'] == 4
    assert route['games_to_watch'][0]['opponent'] == 'Opponent4'
