"""Preserve the last published pre-kickoff expectation, never today's hindsight."""
from datetime import datetime, timezone
import json
import math

import utils


def _date(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def game_key(team, game):
    if game.get("id") is not None:
        return f"{team}|{game['id']}"
    return f"{team}|{game.get('week')}|{game['opponent']}|{game['home_away']}"


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _capture(projection, forecasts):
    meta = projection.get("meta", {})
    captured = meta.get("generated_at")
    timestamp = _date(captured)
    if timestamp is None or meta.get("as_of_week") is not None:
        return
    for manager in projection.get("managers", []):
        for pick in manager.get("picks", []):
            for game in pick.get("remaining_games", []):
                kickoff = _date(game.get("start_date"))
                probability = game.get("p_win")
                if kickoff is None or timestamp >= kickoff or probability is None or not math.isfinite(probability) or not 0 <= probability <= 1:
                    continue
                key = game_key(pick["team"], game)
                old = forecasts.get(key, {})
                old_time = _date(old.get("captured_at"))
                if old_time is None or timestamp > old_time:
                    forecasts[key] = {"p_win": probability, "captured_at": captured,
                                      "start_date": game["start_date"],
                                      "source": game.get("forecast_source", "published projection"),
                                      "probability_estimated": game.get("probability_estimated", False)}


def load_forecasts(group_id, season):
    directory = utils.WEB_DATA_DIR / group_id
    ledger = directory / f"pregame-{season}.json"
    stored = json.loads(ledger.read_text(encoding="utf-8")) if ledger.exists() else {"season": season, "games": {}}
    if stored.get("season") != season or not isinstance(stored.get("games"), dict):
        raise ValueError(f"Invalid pregame ledger: {ledger}")
    forecasts = stored["games"]
    previous = _read(directory / "projection.json")
    if previous.get("meta", {}).get("season") == season:
        _capture(previous, forecasts)
    return forecasts


def completed_rows(team, games, forecasts, bucket, percent):
    rows = []
    for game in games:
        saved = forecasts.get(game_key(team, game), {})
        captured, kickoff = _date(saved.get("captured_at")), _date(game.get("start_date"))
        p = saved.get("p_win") if captured and kickoff and captured < kickoff else None
        rows.append({**game, "completed": True, "p_win": p,
                     "p_win_pct": percent(p) if p is not None else None,
                     "bucket": bucket(p) if p is not None else None,
                     "forecast_captured_at": saved.get("captured_at") if p is not None else None,
                     "forecast_source": saved.get("source") if p is not None else None,
                     "probability_estimated": saved.get("probability_estimated", False)})
    return rows


def save_forecasts(projection):
    meta = projection["meta"]
    forecasts = load_forecasts(meta["group_id"], meta["season"])
    _capture(projection, forecasts)
    path = utils.WEB_DATA_DIR / meta["group_id"] / f"pregame-{meta['season']}.json"
    utils.save_json_atomic(path, {"season": meta["season"], "games": forecasts})
