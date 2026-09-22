"""Recover published pregame probabilities from Git, without rerunning a model.

Run once after adopting the pregame ledger. Only exact, unique schedule matches
whose projection was generated before kickoff are eligible. Runtime refreshes
use pregame.py and do not depend on a full Git checkout.
"""
import argparse
import copy
import json
import subprocess

import pregame
import utils


def recover(projection, config, forecasts, commit):
    """Enrich old published rows with fixture identity, then apply the time gate."""
    document = copy.deepcopy(projection)
    if document.get("meta", {}).get("season") != utils.get_season():
        return
    for manager in document.get("managers", []):
        for pick in manager.get("picks", []):
            state = utils.team_state(pick["team"], config)
            slate = state["played_games"] + state["remaining_games"]
            for game in pick.get("remaining_games", []):
                matches = [g for g in slate if all(g.get(k) == game.get(k)
                           for k in ("week", "opponent", "home_away", "neutral"))]
                if len(matches) != 1:
                    game["start_date"] = None
                    continue
                game["id"] = matches[0].get("id")
                game["start_date"] = matches[0].get("start_date")
                game["forecast_source"] = f"git:{commit}"
    pregame._capture(document, forecasts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="all")
    args = parser.parse_args()
    season = utils.assert_season_matches_cache()
    groups = utils.get_all_group_ids() if args.group == "all" else [args.group]
    for group in groups:
        config, _ = utils.load_group(group)
        forecasts = pregame.load_forecasts(group, season)
        path = f"docs/data/{group}/projection.json"
        commits = subprocess.run(["git", "log", "--format=%H", "--", path],
                                 cwd=utils.ROOT, check=True, capture_output=True, text=True).stdout.splitlines()
        for commit in commits:
            result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=utils.ROOT,
                                    capture_output=True, encoding="utf-8")
            if result.returncode:
                continue
            recover(json.loads(result.stdout), config, forecasts, commit)
        destination = utils.WEB_DATA_DIR / group / f"pregame-{season}.json"
        utils.save_json_atomic(destination, {"season": season, "games": forecasts})
        print(f"[{group}] {len(forecasts)} preserved forecasts; no model recomputed")


if __name__ == "__main__":
    main()
