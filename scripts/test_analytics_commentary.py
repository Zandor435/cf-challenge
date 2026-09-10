"""Source coverage, template safety, and reproducible weekly editorial rotation."""
from copy import deepcopy

import pytest

import race_narrative as R
import utils


@pytest.mark.parametrize('group', ['panel', 'church', 'browns', 'family'])
def test_current_group_commentary(group):
    root = utils.ROOT / 'groups' / group
    config = utils.load_json(root / 'config.json')
    picks = utils.load_json(root / 'picks.json')['picks']
    projection = {'managers': [{**m, 'picks': [p for p in picks if p['manager'] == m['manager_id']]}
                               for m in config['managers']]}
    commentary = utils.load_json(root / 'analytics_commentary.json')
    R.validate_commentary(commentary, projection)
    for m in projection['managers']:
        voice = commentary['managers'][m['manager_id']]
        for pick in m['picks']:
            args = (voice, m['manager_id'], pick['team'], pick['direction'])
            length = len(voice['default']) + len(voice['picks'].get(f'{pick["team"]}|{pick["direction"]}', []))
            cycle = [R.select_banter(*args, week) for week in range(length)]
            assert len(set(cycle)) == length
            assert cycle[0] == R.select_banter(*args, length)
            assert all('{team}' not in line for line in cycle)


def fixture():
    return ({'managers': {'m': {'default': ['One {team}', 'Two {team}', 'Three {team}'],
                               'picks': {'A|U': ['Under joke']}}}},
            {'managers': [{'manager_id': 'm', 'picks': [{'team': 'A', 'direction': 'U'}]}]})


@pytest.mark.parametrize('bad', ['missing', 'extra', 'team', 'direction', 'placeholder', 'empty', 'duplicate'])
def test_bad_editorial_sources_fail(bad):
    c, p = fixture()
    v = c['managers']['m']
    if bad == 'missing': c['managers'].clear()
    if bad == 'extra': c['managers']['other'] = deepcopy(v)
    if bad == 'team': v['picks'] = {'Typo|U': ['Joke']}
    if bad == 'direction': v['picks'] = {'A|O': ['Joke']}
    if bad == 'placeholder': v['default'][0] = '{team.secret}'
    if bad == 'empty': v['default'][0] = ' '
    if bad == 'duplicate': v['default'][0] = v['default'][1]
    with pytest.raises(ValueError) as error:
        R.validate_commentary(c, p)
    assert 'manager IDs' in str(error.value) if bad in ('missing', 'extra') else 'm/' in str(error.value) or 'm:' in str(error.value)


def test_pick_specific_joke_never_crosses_direction():
    c, _ = fixture()
    v = c['managers']['m']
    assert all(R.select_banter(v, 'm', 'A', 'O', w) != 'Under joke' for w in range(12))


def test_rotation_uses_snapshot_week_and_changes_on_monday():
    c, p = fixture()
    route = {'team': 'A', 'direction': 'U', 'needed': 1, 'next_games': 4,
             'event_probability': .4, 'games_to_watch': []}
    routes = {'managers': [{'manager_id': 'm', 'display_name': 'Manager',
                           'p_win_pool': .3, 'finished': False, 'routes': [route]}]}
    p['managers'][0]['display_name'] = 'Manager'
    def copy_for(stamp):
        p['meta'] = {'cache_fetched_at': stamp, 'generated_at': '2030-01-01T00:00:00Z'}
        return R.describe_title_routes(deepcopy(routes), p, c)['managers'][0]['routes'][0]['banter']
    assert copy_for('2026-09-10') == copy_for('2026-09-13')
    assert copy_for('2026-09-13') != copy_for('2026-09-14')
    assert copy_for('2026-09-10') == copy_for('2026-09-10')
