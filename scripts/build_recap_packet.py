#!/usr/bin/env python3
"""
build_recap_packet.py — The weekly recap's facts: how everyone is doing, and
which of next week's games matter to whom.

Job: for a group, SELECT and round the numbers the weekly recap email needs out
of boards the pipeline already committed. It is the recap's equivalent of the
column's week packet — the only thing the SVP recap writer (generate_recap.py)
is allowed to quote, and the data the email renders beside that prose.

COMPUTES NOTHING NEW. Every value is a field from one of these, copied or
rounded for print:

  docs/data/<g>/standings.json   pace, rank, each pick's results + next games
  docs/data/<g>/analytics.json   race.week_move, championship_odds, leverage
  groups/<g>/config.json         display_name

The one thing done here that is not a straight copy is choosing a SIDE: a
manager "roots for" whichever team leverage says leaves their pace higher
(pace_if_win vs pace_if_loss). That is a comparison of two numbers the projector
already published, not a model of its own. A manager with the same pace either
way has no stake and is left off that game.

Why only committed files, and not the gitignored week_packet.json: the email is
sent from a separate workflow hours after the data run (send-email.yml), and the
recap must be rebuildable there — and on any laptop — from a plain checkout.

Usage:
    python scripts/build_recap_packet.py --group panel          # print packet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = ROOT / "docs" / "data"
GROUPS = ROOT / "groups"

GAMES_THAT_MATTER = 5      # top-N leverage games the email features


class RecapError(RuntimeError):
    """A required board is missing or unusable. The caller decides severity."""


def _load(path: Path, required: bool = True):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if required:
            raise RecapError(f"required input missing: {path}") from None
        return None
    except json.JSONDecodeError as e:
        raise RecapError(f"{path} is not valid JSON ({e.msg})") from None


def _r(v, places=1):
    return None if v is None else round(float(v), places)


def _pct(p):
    """0.716 -> 72. An int percent, the form the prose is told to print."""
    return None if p is None else int(round(float(p) * 100))


def cache_week():
    """The week the committed boards were scored at — the same rule
    build_week_packet.resolve_week uses (standings as_of_week, else the cache's
    week). Only reached when standings carry no as_of_week. Through utils, the
    one owner of cache I/O; note _season_cache memoises in place (CLAUDE.md
    rule 21), which is why the tests pass week= explicitly instead."""
    try:
        import utils
        return utils.cache_meta(utils.get_season()).get("week")
    except (Exception, SystemExit):  # noqa: BLE001 — unknown week is reported by build()
        return None


def _side_label(pick):
    return f"{'Over' if pick['direction'] == 'O' else 'Under'} {pick['line']:g}"


def _pick_row(pick, lo_week, hi_week, next_week):
    """One pick: where it stands, what happened to it this week, what's next."""
    played = [g for g in pick.get("played_games") or []
              if isinstance(g.get("week"), int) and lo_week < g["week"] <= hi_week]
    upcoming = [g for g in pick.get("remaining_games") or []
                if g.get("week") == next_week]
    nxt = upcoming[0] if upcoming else None
    root = None
    if nxt:
        # An Over wants the team to win; an Under wants it to lose.
        root = pick["team"] if pick["direction"] == "O" else nxt["opponent"]
    return {
        "team": pick["team"],
        "direction": pick["direction"],
        "line": pick["line"],
        "side": _side_label(pick),
        "status": pick.get("status"),
        "pick_pace": _r(pick.get("expected_delta")),
        "expected_final_wins": _r(pick.get("expected_final_wins")),
        "this_week": [{"opponent": g["opponent"], "result": g.get("result"),
                       "home_away": g.get("home_away"),
                       "neutral": bool(g.get("neutral"))} for g in played],
        "next_game": None if not nxt else {
            "opponent": nxt["opponent"],
            "home_away": nxt.get("home_away"),
            "neutral": bool(nxt.get("neutral")),
            "start_date": nxt.get("start_date"),
            "team_win_pct": _pct(nxt.get("p_win")),
            "root_for": root,
        },
        # A live pick with no game in next_week is on a bye. A finished season
        # (next_week None) is not a bye, it is over.
        "bye": next_week is not None and nxt is None and pick.get("games_remaining", 0) > 0,
    }


def _kickoffs(standings):
    """(team, opponent, week) -> start_date, from the published schedules."""
    out = {}
    for m in standings.get("managers") or []:
        for p in m.get("picks") or []:
            for g in p.get("remaining_games") or []:
                out[(p["team"], g["opponent"], g.get("week"))] = g.get("start_date")
                out[(g["opponent"], p["team"], g.get("week"))] = g.get("start_date")
    return out


def _games_that_matter(leverage, standings, limit):
    if not (leverage or {}).get("available"):
        return []
    kick = _kickoffs(standings)
    games = []
    for g in leverage.get("games") or []:
        stakes = []
        for m in g.get("managers") or []:
            win, loss = m.get("pace_if_win"), m.get("pace_if_loss")
            if win is None or loss is None or abs(win - loss) < 1e-9:
                continue                      # same pace either way: no stake
            stakes.append({
                "manager_id": m["manager_id"],
                "name": m.get("display_name"),
                "root_for": g["team"] if win > loss else g["opponent"],
                "title_odds_if_team_wins_pct": _pct(m.get("p_if_win")),
                "title_odds_if_team_loses_pct": _pct(m.get("p_if_loss")),
                "pace_if_team_wins": _r(win),
                "pace_if_team_loses": _r(loss),
            })
        if not stakes:
            continue
        games.append({
            "game_key": f"{g['team']} vs {g['opponent']}",
            "team": g["team"],
            "opponent": g["opponent"],
            "home_away": g.get("home_away"),
            "neutral": bool(g.get("neutral")),
            "start_date": kick.get((g["team"], g["opponent"], g.get("week"))),
            "team_win_pct": _pct(g.get("p_team_win")),
            # Biggest swing in any one manager's title odds, in points.
            "title_swing_pts": _r((g.get("impact") or 0) * 100),
            "managers": stakes,
            "_impact": g.get("impact") or 0,
        })
    # leverage is already sorted by impact; keep that, but break the near-ties a
    # runaway race produces (every impact ~0.01) by how many managers care.
    games.sort(key=lambda x: (-round(x["_impact"], 3), -len(x["managers"]),
                              x["game_key"]))
    for g in games:
        g.pop("_impact")
    return games[:limit]


def build(group_id: str, week: int | None = None, limit: int = GAMES_THAT_MATTER) -> dict:
    gdir = DOCS_DATA / group_id
    gcfg = _load(GROUPS / group_id / "config.json")
    standings = _load(gdir / "standings.json")
    analytics = _load(gdir / "analytics.json", required=False) or {}
    # Never mix a stale analytics generation into a newer pace board (the same
    # rule build_email_payload.build applies).
    gen = (standings.get("meta") or {}).get("generated_at")
    if (analytics.get("meta") or {}).get("generated_at") != gen:
        analytics = {}

    meta = standings.get("meta") or {}
    if week is None:
        week = meta.get("as_of_week")
    if week is None:
        week = cache_week()
    if week is None:
        raise RecapError("cannot resolve the scored week (standings as_of_week "
                         "is null and the cache carries no week)")
    week = int(week)

    race = analytics.get("race") or {}
    prior_week = race.get("prior_week")
    lo_week = prior_week if isinstance(prior_week, int) else week - 1
    race_by = {m["manager_id"]: m for m in race.get("managers") or []}
    odds = analytics.get("championship_odds") or {}
    odds_by = ({m["manager_id"]: m for m in odds.get("managers") or []}
               if odds.get("available") else {})
    leverage = analytics.get("leverage") or {}
    next_week = leverage.get("week") if leverage.get("available") else None
    if next_week is None:
        weeks = [g.get("week") for m in standings.get("managers") or []
                 for p in m.get("picks") or [] for g in p.get("remaining_games") or []
                 if isinstance(g.get("week"), int)]
        next_week = min(weeks) if weeks else None

    managers = []
    for m in standings.get("managers") or []:
        mid = m["manager_id"]
        rc = race_by.get(mid, {})
        oc = odds_by.get(mid, {})
        managers.append({
            "manager_id": mid,
            "name": m.get("display_name") or mid,
            "rank": m.get("rank"),
            "rank_change": rc.get("week_move"),
            "pace": _r(m.get("expected_total")),
            "pace_move": _r(m.get("expected_total_move")),
            "gap_to_leader": _r(rc.get("gap_to_leader")),
            "title_odds_pct": _pct(oc.get("p_win_pool")) if oc else None,
            "title_odds_move_pts": (_r(oc["week_move"] * 100)
                                    if oc.get("week_move") is not None else None),
            "picks": [_pick_row(p, lo_week, week, next_week) for p in m.get("picks") or []],
        })
    managers.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0, x["manager_id"]))

    return {
        "group_id": group_id,
        "display_name": gcfg.get("display_name") or group_id,
        "season": meta.get("season"),
        "week": week,
        "prior_week": lo_week,
        "next_week": next_week,
        "generated_at": gen,
        "pace_stale": bool(meta.get("pace_stale")),
        "season_over": next_week is None,
        "managers": managers,
        "games_that_matter": _games_that_matter(leverage, standings, limit),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--group", required=True)
    ap.add_argument("--week", type=int)
    args = ap.parse_args(argv)
    try:
        packet = build(args.group, args.week)
    except RecapError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    json.dump(packet, sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
