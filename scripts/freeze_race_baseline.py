#!/usr/bin/env python3
"""Recover a group's original draft projection from Git; never overwrite it.

Example: python scripts/freeze_race_baseline.py --group panel --ref 6a23196
Only public board fields are copied. This does not regenerate the forecast.
"""
import argparse
import json
import subprocess
from pathlib import Path

import utils


def freeze(group, revision):
    commit = subprocess.check_output(
        ['git', 'rev-parse', '--verify', revision + '^{commit}'], cwd=utils.ROOT,
        text=True).strip()

    def read(path):
        return json.loads(subprocess.check_output(
            ['git', 'show', f'{commit}:{path}'], cwd=utils.ROOT))

    st = read(f'docs/data/{group}/standings.json')
    pr = read(f'docs/data/{group}/projection.json')
    config = read(f'groups/{group}/config.json')
    assert st['meta']['draft_status'] == 'final', 'Not a completed draft'
    assert all(not p['banked_wins'] and not p['banked_losses']
               for m in st['managers'] for p in m['picks']), 'Results already banked'
    season = st['meta']['season']
    assert pr['meta']['season'] == season
    assert pr['meta']['cache_fetched_at'] == st['meta']['cache_fetched_at']
    vintage = pr['meta']['ratings_asof'][:10]
    archive = f'data/ratings_archive/{season}/{vintage}.json'
    assert (utils.ROOT / archive).exists(), f'Missing frozen ratings: {archive}'
    managers = []
    by_id = {m['manager_id']: m for m in st['managers']}
    for m in pr['managers']:
        signature = lambda rows: sorted((p['team'], p['direction'], p['line']) for p in rows)
        assert signature(m['picks']) == signature(by_id[m['manager_id']]['picks'])
        managers.append({k: m[k] for k in ('manager_id', 'display_name', 'p_win_pool',
                                          'expected_total', 'picks')})
    result = {
        'season': season, 'group_id': group, 'frozen_at': pr['meta']['generated_at'],
        'source_commit': commit, 'ratings_archive': archive,
        'count_conference_championship': utils.counts_conference_championship(config),
        'method': 'Original published draft projection, preserved from Git. Changes since '
                  'draft can include subsequent model changes as well as results and ratings.',
        'managers': managers,
    }
    path = utils.DATA_DIR / 'draft_baselines' / str(season) / f'{group}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write('\n')
    print(f'{group}: preserved {result["frozen_at"]} from {commit[:8]} -> {path}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--group', required=True)
    ap.add_argument('--ref', required=True)
    args = ap.parse_args()
    freeze(args.group, args.ref)
