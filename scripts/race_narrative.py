"""Draft-to-now race context. All arithmetic stays in Python.

Historical pool odds are preserved observations, not reconstructed with today's
ratings. Schedule comparisons use the SAME remaining games and hold the picked
team at its draft rating, isolating changes in opponent strength.
"""
import hashlib
from datetime import date
from string import Formatter

import projector
import utils


def pick_key(p):
    return (p['team'], p['direction'], float(p['line']))


def roster_signature(managers):
    return sorted((m['manager_id'], tuple(sorted(pick_key(p) for p in m.get('picks', []))))
                  for m in managers)


def baseline_status(config, projection, baseline):
    if not baseline:
        return False, 'No preserved draft projection is available for this league.'
    if baseline.get('season') != projection.get('meta', {}).get('season'):
        return False, 'The draft snapshot belongs to a different season.'
    if baseline.get('count_conference_championship') != utils.counts_conference_championship(config):
        return False, 'The scoring rules have changed since the draft.'
    if roster_signature(baseline['managers']) != roster_signature(projection['managers']):
        return False, 'The roster or picks changed after the draft; title odds are not directly comparable.'
    return True, None


def draft_comparison(config, projection, baseline):
    available, reason = baseline_status(config, projection, baseline)
    old = {m['manager_id']: m for m in (baseline or {}).get('managers', [])}
    return {
        'available': available, 'reason': reason,
        'frozen_at': (baseline or {}).get('frozen_at'),
        'method': (baseline or {}).get('method'),
        'managers': {m['manager_id']: {
            'draft_p_win_pool': old[m['manager_id']]['p_win_pool'] if available else None,
            'draft_move': round(m['p_win_pool'] - old[m['manager_id']]['p_win_pool'], 6)
                          if available and m.get('p_win_pool') is not None else None,
        } for m in projection.get('managers', [])},
    }


def build_race_story(projection, prior, baseline, draft):
    old_mgrs = {m['manager_id']: m for m in (baseline or {}).get('managers', [])}
    prior_mgrs = {m['manager_id']: m for m in (prior or {}).get('managers', [])}
    rows = []
    for manager in projection.get('managers', []):
        mid = manager['manager_id']
        prev = prior_mgrs.get(mid)
        old = old_mgrs.get(mid)
        # A previous scored week is preferred. Missing is never a zero change.
        source = prev if prev and prev.get('p_win_pool') is not None else old
        basis = 'week' if source is prev and prev is not None else 'draft'
        old_picks = {p['team']: p for p in (source or {}).get('picks', [])}
        current_signature = sorted(pick_key(p) for p in manager.get('picks', []))
        draft_pick_match = bool(old) and current_signature == sorted(pick_key(p) for p in old['picks'])
        drivers = []
        for p in manager.get('picks', []):
            before = old_picks.get(p['team'], {}).get('expected_delta')
            now = p.get('expected_delta')
            if before is None or now is None or (basis == 'draft' and not draft_pick_match):
                continue
            drivers.append({'team': p['team'], 'direction': p['direction'], 'line': p['line'],
                            'before': before, 'now': now, 'change': round(now - before, 2)})
        drivers.sort(key=lambda p: (-abs(p['change']), p['team']))
        before_odds = (source or {}).get('p_win_pool')
        change = (round(manager['p_win_pool'] - before_odds, 6)
                  if manager.get('p_win_pool') is not None and before_odds is not None
                  and (basis == 'week' or draft['available']) else None)
        rows.append({'manager_id': mid, 'display_name': manager['display_name'],
                     'basis': basis, 'odds_change': change, 'drivers': drivers,
                     'movement_summary': ('No directly comparable earlier title forecast.' if change is None else
                                          'Their title chances have improved.' if change > .005 else
                                          'They have lost some ground in the title race.' if change < -.005 else
                                          'Their title chances have barely moved.')})
    rows.sort(key=lambda m: (m['odds_change'] is None, -abs(m['odds_change'] or 0), m['manager_id']))
    return {'board': 'projection', 'available': True,
            'prior_week': (prior or {}).get('as_of_week'), 'managers': rows,
            'note': 'Pick changes are changes in expected final points, not an additive '
                    'explanation of championship probability. Results, ratings, and model '
                    'updates can all contribute.'}


def rating(ratings, team):
    record = ratings.get(team)
    return None if not record or record.get('rating') is None else float(record['rating'])


def build_schedule_watch(projection, states, draft_ratings, live_ratings, baseline_date):
    owners = {}
    for m in projection.get('managers', []):
        for p in m.get('picks', []):
            owners.setdefault(p['team'], []).append({
                'manager_id': m['manager_id'], 'display_name': m['display_name'],
                'direction': p['direction'], 'line': p['line']})
    teams = []
    for team, holders in owners.items():
        own = rating(draft_ratings, team)
        games = states[team]['remaining_games']
        compared, omitted = [], 0
        for game in games:
            before = rating(draft_ratings, game['opponent'])
            after = rating(live_ratings, game['opponent'])
            if own is None or before is None or after is None:
                omitted += 1
                continue
            args = (game['home_away'] == 'home', game['neutral'])
            p_before = float(projector.game_win_prob(own, before, *args))
            p_after = float(projector.game_win_prob(own, after, *args))
            compared.append({**game, 'draft_rating': before, 'current_rating': after,
                             'rating_change': round(after - before, 3),
                             'draft_p_win': round(p_before, 6),
                             'schedule_only_p_win': round(p_after, 6),
                             'win_change': p_after - p_before})
        shift = sum(g['win_change'] for g in compared) if compared else None
        rating_shift = (sum(g['rating_change'] for g in compared) / len(compared)
                        if compared else None)
        for holder in holders:
            effect = shift * (1 if holder['direction'] == 'O' else -1) if shift is not None else None
            holder['expected_points_change'] = round(effect, 3) if effect is not None else None
            holder['effect'] = ('unavailable' if effect is None else 'helps' if effect >= .005
                                else 'hurts' if effect <= -.005 else 'unchanged')
        compared.sort(key=lambda g: (-abs(g['win_change']), g['week'] or 0, g['opponent']))
        for game in compared:
            game['win_change'] = round(game['win_change'], 4)
        teams.append({'team': team, 'remaining_games': len(games),
                      'compared_games': len(compared), 'unrated_games': omitted,
                      'schedule_win_change': round(shift, 3) if shift is not None else None,
                      'opponent_rating_change': round(rating_shift, 3) if rating_shift is not None else None,
                      'direction': ('unavailable' if shift is None else 'easier' if shift >= .005
                                    else 'harder' if shift <= -.005 else 'unchanged'),
                      'owners': holders, 'opponents': compared})
    teams.sort(key=lambda t: (t['schedule_win_change'] is None,
                             -abs(t['schedule_win_change'] or 0), t['team']))
    changed = sum(t['direction'] in ('easier', 'harder') for t in teams)
    covered = sum(t['compared_games'] > 0 for t in teams)
    return {'board': 'projection', 'available': bool(draft_ratings),
            'baseline_date': baseline_date, 'teams': teams,
            'changed_teams': changed, 'compared_teams': covered,
            'summary': ('No meaningful schedule shift yet. The comparable opponents have '
                        'not moved enough in the ratings to change the outlook.' if covered and not changed
                        else 'Changes in opponent strength are reshaping the road ahead.' if changed
                        else 'No remaining games with ratings in both snapshots are available to compare.'),
            'method': 'Same remaining opponents and venues, picked team held at its draft '
                      'SP+ rating. Only opponent ratings change. Unrated opponents are excluded; '
                      'coverage is shown. This measures schedule change, not total team improvement.'}


def load_baseline(group, season):
    path = utils.DATA_DIR / 'draft_baselines' / str(season) / f'{group}.json'
    if not path.exists():
        return None, {}
    baseline = utils.load_json(path)
    if baseline.get('season') != season or baseline.get('group_id') != group:
        return None, {}
    archive_path = (utils.ROOT / baseline['ratings_archive']).resolve()
    archive_path.relative_to((utils.DATA_DIR / 'ratings_archive').resolve())
    archive = utils.load_json(archive_path)
    if archive.get('season') != season or archive.get('fetched_at', '') > baseline['frozen_at']:
        raise ValueError('Draft ratings have the wrong season or postdate the draft projection')
    return baseline, archive.get('sp_ratings', {})


def result_surprises(projection, states, draft_ratings):
    """Latest played week's outcomes versus the preserved draft ratings.

    These are result surprises in expected points, not claimed causal shares
    of a manager's probability movement. No current-rating hindsight.
    """
    weeks = [g['week'] for st in states.values() for g in st['played_games']
             if isinstance(g.get('week'), int)]
    week = max(weeks) if weeks else None
    rows = []
    seen = set()
    for m in projection.get('managers', []):
        for p in m.get('picks', []):
            team = p['team']
            own = rating(draft_ratings, team)
            if own is None:
                continue
            for game in states[team]['played_games']:
                opponent = rating(draft_ratings, game['opponent'])
                if game['week'] != week or opponent is None or game['result'] not in ('W', 'L'):
                    continue
                key = (team, game['opponent'], m['manager_id'])
                if key in seen:
                    continue
                seen.add(key)
                probability = float(projector.game_win_prob(own, opponent,
                                    game['home_away'] == 'home', game['neutral']))
                effect = ((1 if game['result'] == 'W' else 0) - probability) * (
                    1 if p['direction'] == 'O' else -1)
                rows.append({'manager_id': m['manager_id'], 'display_name': m['display_name'],
                             'team': team, 'opponent': game['opponent'], 'week': week,
                             'result': game['result'], 'direction': p['direction'],
                             'draft_p_win': round(probability, 6),
                             'points_surprise': round(effect, 3)})
    rows.sort(key=lambda r: (-abs(r['points_surprise']), r['team'], r['manager_id']))
    return rows


def validate_commentary(commentary, projection):
    """Reject missing voices, stale pick keys, and unsafe template placeholders."""
    voices = commentary.get('managers', {})
    managers = {m['manager_id']: m for m in projection['managers']}
    if set(voices) != set(managers):
        raise ValueError('Commentary manager IDs must match the current roster')
    for mid, voice in voices.items():
        valid = {f'{p["team"]}|{p["direction"]}' for p in managers[mid]['picks']}
        if not set(voice.get('picks', {})) <= valid:
            raise ValueError(f'{mid}: commentary references an unknown team or pick direction')
        for key, pool in [('default', voice.get('default'))] + list(voice.get('picks', {}).items()):
            if not isinstance(pool, list) or not pool or (key == 'default' and len(pool) < 3):
                raise ValueError(f'{mid}/{key}: provide a nonempty list, at least three defaults')
            if any(not isinstance(line, str) or not line.strip() for line in pool):
                raise ValueError(f'{mid}/{key}: commentary must contain nonempty strings')
            if len(set(pool)) != len(pool):
                raise ValueError(f'{mid}/{key}: duplicate commentary')
            for line in pool:
                for _, field, spec, conversion in Formatter().parse(line):
                    if field is not None and (field != 'team' or spec or conversion):
                        raise ValueError(f'{mid}/{key}: only the {{team}} placeholder is supported')


def select_banter(voice, mid, team, direction, rotation):
    """Weekly rotation with a stable per-pick offset; reloads never shuffle copy."""
    pool = voice.get('picks', {}).get(f'{team}|{direction}', []) + voice['default']
    offset = int.from_bytes(hashlib.sha256(f'{mid}|{team}|{direction}'.encode()).digest()[:4], 'big')
    return pool[(rotation + offset) % len(pool)].replace('{team}', team)


def describe_title_routes(routes, projection, commentary=None):
    """Plain-English copy composed only from measured routes and game odds."""
    if commentary is not None:
        validate_commentary(commentary, projection)
    # Use the forecast snapshot, never today's date, so archived runs reproduce.
    meta = projection.get('meta', {})
    stamp = meta.get('cache_fetched_at') or meta.get('generated_at')
    rotation = (date.fromisoformat(stamp[:10]).toordinal() - 1) // 7 if stamp else 0
    holders = {}
    for manager in projection.get('managers', []):
        for pick in manager.get('picks', []):
            holders.setdefault(pick['team'], []).append((manager['manager_id'],
                                                         manager['display_name'], pick['direction']))
    for manager in routes['managers']:
        name, chance = manager['display_name'], manager['p_win_pool']
        manager['position'] = ('Season settled' if manager['finished'] else
                               'In the thick of it' if chance >= .20 else
                               'Within striking distance' if chance >= .08 else 'Needs a few breaks')
        if manager['finished']:
            manager['intro'] = ('The games are finished. The title is theirs.' if chance == 1 else
                                'The games are finished. They share the top score.' if chance > 0 else
                                'The games are finished. They did not finish on top.')
        elif not manager['routes']:
            manager['intro'] = ('Their chances are already very strong. The job now is to avoid a setback.'
                                if chance > .95 else
                                'There is no clear short-term route in this forecast. That does not mean '
                                'they are eliminated; the rest of the season still has to play out.')
        else:
            manager['intro'] = ('Think of this as a rooting guide: the results that would give '
                                f'{name} a better shot, with the rest of the season still to play.')
        for route in manager['routes']:
            team, under = route['team'], route['direction'] == 'U'
            needed, n = route['needed'], route['next_games']
            action = 'loses' if under else 'wins'
            event = (f'{team} {action} its next game' if n == 1 else
                     f'{team} {action} all of its next {n} games' if needed == n else
                     f'{team} {action} at least {needed} of its next {n} games')
            route['condition'] = event
            route['headline'] = f'Root {"against" if under else "for"} {team}.'
            route['story'] = f'{name} gets a better shot if {event}.'
            route['why'] = (f'{name} picked {team} to {"fall short of" if under else "beat"} '
                            'its preseason win target. '
                            f'{"Losses" if under else "Wins"} are good news for that pick.')
            route['watch_heading'] = ('The toughest hurdles' if needed == n and n > 1 else
                                      'Games to circle')
            likelihood = route['event_probability']
            route['difficulty'] = ('A realistic opening' if likelihood >= .35 else 'Needs a few things to break right')
            route['likelihood_text'] = (f'This stretch comes through in about {round(likelihood * 100)} '
                                        'out of 100 simulated seasons.')
            others = [name for mid, name, direction in holders.get(team, [])
                      if mid != manager['manager_id'] and direction != route['direction']]
            route['rival_note'] = (f'{", ".join(others)} {"is" if len(others) == 1 else "are"} '
                                   f'pulling the other way on {team}.' if others else None)
            route['narrative'] = (
                f'The awkward part: {name} benefits when {team} has a bad Saturday. '
                'A close escape still counts as a win, so ugly football alone will not do the job.'
                if under else
                f'{name} does not need style points from {team}. A messy win does the same job '
                'for this pick as a blowout. Survive first; look impressive later.')
            if needed == n and n > 1:
                route['narrative'] += ' This particular route asks for a clean sweep; one wrong result breaks the streak, though other paths remain.'
            elif needed == 1 and n > 1:
                route['narrative'] += ' There is room for the other games to go the wrong way. One result is enough to satisfy this scenario.'
            voice = ((commentary or {}).get('managers') or {}).get(manager['manager_id'], {})
            route['banter'] = (select_banter(voice, manager['manager_id'], team, route['direction'], rotation) if voice else
                f'{name} has officially become a {team} {"opponent" if under else "fan"} for accounting purposes. '
                'Please respect this deeply held, spreadsheet-based conviction.')
            route['if_it_happens'] = (f'If this stretch lands, {name} has a stronger title case '
                + (f'and {", ".join(others)} will have been cheering against the very same results.' if others else
                   'and a ready-made reason to remind everyone about this pick.'))
            for game in route['games_to_watch']:
                favorable = 1 - game['p_win'] if under else game['p_win']
                if favorable >= .65:
                    game['rooting_note'] = (f'{game["opponent"]} is favored to beat {team}.' if under else
                                            f'{team} is favored to beat {game["opponent"]}.')
                elif favorable >= .35:
                    game['rooting_note'] = 'This one could go either way.'
                else:
                    game['rooting_note'] = (f'{team} is favored. A loss here would be an upset.' if under else
                                            f'{team} would need to pull off an upset.')
    return routes


def describe_watch_games(leverage):
    """Rooting interests from the two measured outcomes, not invented causation."""
    for game in leverage['games']:
        meaningful = [m for m in game['managers'] if abs(m['swing']) >= .005]
        if not meaningful:
            game['headline'] = 'No clear edge for either side.'
            game['story'] = 'This result does not meaningfully separate the managers in the forecast.'
            continue
        main = meaningful[0]
        wants_team = main['swing'] > 0
        winner = game['team'] if wants_team else game['opponent']
        game['headline'] = f'{main["display_name"]} is rooting for {winner}.'
        opposite = [m for m in meaningful if (m['swing'] > 0) != wants_team]
        game['story'] = (f'{main["display_name"]} has the most riding on this one. ' +
                         (f'{opposite[0]["display_name"]} is rooting for the other side.' if opposite else
                          'That result would give their title hopes the biggest boost of anyone in the group.'))
        game['root_for'] = winner
        game['conversation'] = (
            f'Watch the group chat if {winner} wins: good news for {main["display_name"]}, '
            f'bad news for {opposite[0]["display_name"]}\u2019s title chances. Same final score, very different moods.'
            if opposite else
            f'If {winner} wins, {main["display_name"]} gets the biggest title-odds boost from that outcome. '
            'Expect this game to feature prominently in their version of the weekend.')
    return leverage


def build_context(config, standings, projection, prior, as_of_week=None):
    if not projection:
        return None
    season = utils.get_season()
    baseline, draft_ratings = load_baseline(config['group_id'], season)
    draft = draft_comparison(config, projection, baseline)
    picks = [{**p, 'manager': m['manager_id']} for m in standings.get('managers', [])
             for p in m.get('picks', [])]
    live_ratings = utils.season_sp_ratings(season)
    tc = projector._team_cache(config, picks, as_of_week, live_ratings)
    states = {team: utils.team_state(team, config, as_of_week) for team in tc}
    schedule = build_schedule_watch(projection, states, draft_ratings, live_ratings,
                                    (baseline or {}).get('frozen_at'))
    story = build_race_story(projection, prior, baseline, draft)
    story['results'] = result_surprises(projection, states, draft_ratings)
    commentary_path = utils.ROOT / 'groups' / config['group_id'] / 'analytics_commentary.json'
    commentary = utils.load_json(commentary_path)
    return {'draft': draft, 'race_story': story,
            'schedule_watch': schedule,
            'title_routes': describe_title_routes(
                projector.build_title_routes(config, picks, as_of_week, tc), projection, commentary),
            'leverage': describe_watch_games(projector.build_game_leverage(config, picks, as_of_week, tc))}
