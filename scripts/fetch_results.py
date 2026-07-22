#!/usr/bin/env python3
"""
fetch_results.py — CFBD weekly fetch + shared cache (ARCHITECTURE §4, §6; build §10.1 BUILD 2/3).

ONE weekly pass -> data/cfbd_cache.json:
  - full regular-season results slate,
  - full schedules (per-team scheduled regular-season game count, 12 or 13 —
    never hardcoded; the 13th is the Hawaii exemption, §1),
  - current SP+ ratings.

§6 (the one silent failure mode): the pass MUST refresh SP+, not just scores. A
scores-only cache produces a correct exact board next to a projection that never
reseeds, and nothing errors. So SP+ is an explicit, guarded code path: if games
are present but SP+ is empty, we REFUSE to write.

Hardening (§4): the client retries/backs off (see cfbd_client). On a persistent
failure we fall back to the last good cache and exit non-fatally (commentary-
bypass) — we NEVER overwrite a good cache with a partial/failed pull. Regular
season only; conference championships/bowls/playoff excluded.

BUILD 3: prints exact API calls per pass and the projected monthly total at
twice-weekly cadence against the 1,000/month ceiling.

Usage:
    python scripts/fetch_results.py                    # season from season.json
    python scripts/fetch_results.py --simulate-failure # test the commentary-bypass
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils
from cfbd_client import CFBDClient, CFBDError

PASSES_PER_MONTH = 2 * 52 / 12  # twice-weekly cadence ≈ 8.67 passes/month
MONTHLY_CEILING = 1000
STALE_DAYS = 10  # fallback cache older than this = pipeline broken for cycles, not a blip

# BUILD 2 — SP+ vintage archive. CFBD's /ratings/sp returns only the season-FINAL
# rating (the `week` param is ignored), so past in-season vintages are
# unrecoverable — but every fetch already pulls the CURRENT rating, so future
# vintages are free. Snapshot each fetch here, keyed by fetch date + CFBD week,
# append-only. This IS the future non-leaky calibration set (calibrate.py
# --archive), so it is COMMITTED, never gitignored. See docs/calibration-report.md.
ARCHIVE_DIR = utils.DATA_DIR / "ratings_archive"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_conference_championship(g):
    """Tag (never a filter) for an FBS conference title game. CFBD returns these
    in the seasonType=regular feed tagged season_type='regular' (NOT postseason —
    an earlier §1 draft had this backwards). The ONLY raw signal is the free-text
    `notes` field: title games read '<Conference> Championship'. Robust rule:
    'championship' appears in notes AND both teams are FBS.

    The FBS check is load-bearing, not the notes string: 56 of the 65 games whose
    notes mention 'championship' are FCS/D-II/D-III playoff brackets or SWAC/SIAC
    titles that share the wording but always involve a non-FBS team. No
    neutral_site or week-number heuristic — 4 of the 9 title games are on-campus
    (higher seed hosts) and the championship week shifts year to year."""
    notes = (g.get("notes") or "").lower()
    if "championship" not in notes:
        return False
    return g.get("home_classification") == "fbs" and g.get("away_classification") == "fbs"


def normalize_game(g):
    ng = {
        "id": g.get("id"),
        "week": g.get("week"),
        "season_type": g.get("seasonType", g.get("season_type", "regular")),
        "start_date": g.get("startDate", g.get("start_date")),
        "completed": g.get("completed", False),
        "home_team": g.get("homeTeam", g.get("home_team")),
        "home_points": g.get("homePoints", g.get("home_points")),
        "home_conference": g.get("homeConference", g.get("home_conference")),
        "home_classification": g.get("homeClassification", g.get("home_classification")),
        "away_team": g.get("awayTeam", g.get("away_team")),
        "away_points": g.get("awayPoints", g.get("away_points")),
        "away_conference": g.get("awayConference", g.get("away_conference")),
        "away_classification": g.get("awayClassification", g.get("away_classification")),
        "neutral_site": g.get("neutralSite", g.get("neutral_site", False)),
        "conference_game": g.get("conferenceGame", g.get("conference_game", False)),
        # Raw championship indicator, persisted, never discarded (§1). CFBD's only
        # signal for a conf title game; the derived tag below reads it.
        "notes": g.get("notes"),
    }
    # Neutral per-game tag: mark it, exclude nothing. Filtering is a scoring-layer
    # decision, gated per group by config's count_conference_championship (§1, §5).
    ng["conference_championship"] = is_conference_championship(ng)
    return ng


def fetch_games(client, season):
    """Regular season only. Returns compact game dicts."""
    print(f"  fetching /games?year={season}&seasonType=regular")
    raw = client.get("/games", {"year": season, "seasonType": "regular"})
    games = [normalize_game(g) for g in raw]
    print(f"  -> {len(games)} regular-season games")
    return games


def fetch_sp(client, season):
    """Current SP+ ratings, keyed by team. §6: this MUST run every pass."""
    print(f"  fetching /ratings/sp?year={season}")
    raw = client.get("/ratings/sp", {"year": season})
    ratings = {}
    for r in raw:
        team = r.get("team")
        if not team:  # skip the national-average row (team is null)
            continue
        off = r.get("offense") or {}
        deff = r.get("defense") or {}
        ratings[team] = {
            "rating": _num(r.get("rating")),
            "ranking": r.get("ranking"),
            "offense": _num(off.get("rating")),
            "defense": _num(deff.get("rating")),
        }
    print(f"  -> {len(ratings)} teams with SP+ ratings")
    return ratings


def fetch_fpi(client, season):
    """Current FPI ratings, keyed by team. Secondary to SP+ (the projection
    spine, §4); carried so calibrate.py can A/B SP+ vs FPI. One extra call."""
    print(f"  fetching /ratings/fpi?year={season}")
    raw = client.get("/ratings/fpi", {"year": season})
    ratings = {}
    for r in raw:
        team = r.get("team")
        if not team:
            continue
        ratings[team] = {"fpi": _num(r.get("fpi"))}
    print(f"  -> {len(ratings)} teams with FPI ratings")
    return ratings


def build_team_index(games):
    """Per-team scheduled regular-season game count (12/13) + banked W/L.
    Counts ALL regular-season games on the schedule (completed or not), so
    games_remaining is derived from the real slate, never a hardcoded 12."""
    idx = {}

    def touch(team, conf):
        if team not in idx:
            idx[team] = {"conference": conf, "scheduled_games": 0,
                         "games_played": 0, "wins": 0, "losses": 0}
        if conf and not idx[team]["conference"]:
            idx[team]["conference"] = conf

    for g in games:
        h, a = g["home_team"], g["away_team"]
        hp, ap = g["home_points"], g["away_points"]
        touch(h, g["home_conference"])
        touch(a, g["away_conference"])
        idx[h]["scheduled_games"] += 1
        idx[a]["scheduled_games"] += 1
        if g["completed"] and hp is not None and ap is not None:
            idx[h]["games_played"] += 1
            idx[a]["games_played"] += 1
            if hp > ap:
                idx[h]["wins"] += 1
                idx[a]["losses"] += 1
            elif ap > hp:
                idx[a]["wins"] += 1
                idx[h]["losses"] += 1
    return idx


def assert_fetch_complete(games, sp_ratings):
    """The single pre-write gate: a fetch must be COMPLETE or we write NOTHING
    (playbook rules 3/5, ARCHITECTURE §4/§6). Any raise here is caught by main()'s
    `except CFBDError -> degraded_exit`, which keeps the last good cache untouched.
    Two ways a fetch is incomplete, both fatal-to-the-write:
      - zero games came back (a zero-result fetch must never clobber real data —
        rule 5); and
      - games present but SP+ empty (a scores-only cache freezes the projection
        with no error — §6, the silent no-reseed mode).
    Auth failure is handled even earlier: get_api_key() exits before the client
    is built, so no write path is reachable without a key. Together these mean the
    cache is only ever replaced by a whole, coherent fetch — never partially."""
    if not games:
        raise CFBDError(
            "fetch returned ZERO games. Refusing to overwrite the existing cache "
            "with an empty/partial slate (playbook rule 5 — zero-result clobber "
            "guard). Keeping the last good cache."
        )
    if not sp_ratings:
        raise CFBDError(
            "SP+ ratings came back EMPTY while games are present. Refusing to "
            "write a scores-only cache (ARCHITECTURE §6 — the silent no-reseed "
            "failure mode). Aborting without touching the existing cache."
        )


def current_week(games):
    weeks = [g["week"] for g in games if g["completed"] and g["week"] is not None]
    return max(weeks) if weeks else None


def report_budget(calls):
    monthly = calls * PASSES_PER_MONTH
    print("\n" + "-" * 60)
    print("API CALL BUDGET (BUILD 3)")
    print("-" * 60)
    print(f"  calls this pass:            {calls}")
    print(f"  cadence:                    twice weekly (~{PASSES_PER_MONTH:.2f} passes/mo)")
    print(f"  projected monthly total:    {monthly:.0f} calls")
    print(f"  free-tier ceiling:          {MONTHLY_CEILING} calls/mo")
    print(f"  headroom:                   {MONTHLY_CEILING - monthly:.0f} "
          f"({100 * monthly / MONTHLY_CEILING:.1f}% of budget used)")
    print("  (build_canonical.py adds 1 call, run seasonally — not per weekly pass.)")


def cache_age_days(fetched_at):
    """Age of the cache in days from its fetched_at stamp; None if unusable."""
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0


def degraded_exit(reason, requested_season):
    """Commentary-bypass (§4): keep the last good cache and exit non-fatally —
    but ONLY if it is (a) tagged for the requested season and (b) no older than
    STALE_DAYS. A wrong-season OR long-stale fallback would score a clean but
    entirely wrong board and exit 0, so those FAIL LOUD. Every degraded exit
    emits a ::warning::/::error:: so a green-but-degraded run is never silent."""
    if not utils.cache_exists():
        print(f"::error:: fetch failed ({reason}) and no prior cache exists to "
              f"fall back on. Cannot proceed.")
        sys.exit(1)
    try:
        prev = utils.peek_cache()
    except Exception as e:
        print(f"::error:: fetch failed ({reason}) and the existing cache is "
              f"unreadable ({e}). Cannot proceed.")
        sys.exit(1)

    cache_season = prev.get("season")
    if cache_season != requested_season:
        print(f"::error:: fetch failed ({reason}) AND the last good cache is for "
              f"season {cache_season}, but this run requested season "
              f"{requested_season}. REFUSING a wrong-season fallback — it would "
              f"score a clean but entirely wrong board. Failing loud.")
        sys.exit(3)

    age = cache_age_days(prev.get("fetched_at"))
    if age is None:
        print(f"::error:: fetch failed ({reason}) and the fallback cache has no "
              f"usable fetched_at ({prev.get('fetched_at')!r}); cannot verify "
              f"freshness. Failing loud.")
        sys.exit(4)
    if age > STALE_DAYS:
        print(f"::error:: fetch failed ({reason}) and the last good cache is "
              f"{age:.1f} days old (> {STALE_DAYS}-day ceiling). The pipeline has "
              f"been broken for multiple cycles, not a blip. Failing loud instead "
              f"of scoring stale data.")
        sys.exit(4)

    print(f"::warning:: fetch failed ({reason}). Keeping last good cache "
          f"(season={cache_season}, fetched_at={prev.get('fetched_at')}, "
          f"age={age:.1f}d, within {STALE_DAYS}d ceiling); NOT overwritten. "
          f"Running degraded.")
    sys.exit(0)  # non-fatal: downstream reshapes existing (correct-season) data


def archive_ratings(cache):
    """BUILD 2 — append this fetch's SP+/FPI (and closing lines, IF a future
    fetch already pulls them — no extra call) to the committed vintage archive:
    data/ratings_archive/<season>/<YYYY-MM-DD>.json, keyed by fetch date + the
    CFBD-reported week.

    Append-only, idempotent, never clobbers (playbook rule 5):
      - one file per fetch DATE; twice-weekly cadence -> two files/week (distinct
        vintages, same week — expected and kept),
      - if today's file already exists, SKIP (a same-day re-run is a no-op; the
        first good snapshot of the day wins), so no duplication and no overwrite,
      - a warning (never a failure) if a same-date file somehow holds a different
        week — we keep the existing file rather than overwrite it.

    Best-effort: the shared cache is already committed before this runs, so an
    archive hiccup emits ::warning:: and never fails the pipeline (rule 3).

    A keyless or degraded run NEVER reaches here (main() exits at get_api_key or
    via degraded_exit before save_cache), but as an explicit belt-and-suspenders
    we refuse to archive a cache without SP+ ratings — a vintage snapshot with no
    ratings would be worthless and must never enter the calibration set."""
    if not cache.get("sp_ratings"):
        print("  ratings archive: skipped — cache carries no SP+ ratings "
              "(degraded/empty fetch never archives).")
        return
    try:
        season = cache["season"]
        # Fetch date from the cache's own stamp, so archive date == cache vintage.
        fetched_at = cache.get("fetched_at", "")
        date = fetched_at[:10] if len(fetched_at) >= 10 else \
            datetime.now(timezone.utc).date().isoformat()
        week = cache.get("week")
        season_dir = ARCHIVE_DIR / str(season)
        out = season_dir / f"{date}.json"

        if out.exists():
            try:
                prev = utils.load_json(out)
                if prev.get("week") != week:
                    print(f"::warning:: ratings archive {out.name} already exists for "
                          f"week {prev.get('week')} but this fetch reports week {week}; "
                          f"keeping the existing snapshot (append-only, no overwrite).")
                else:
                    print(f"  ratings archive: {out.name} already present "
                          f"(week {week}) — skipping (idempotent).")
            except Exception:
                print(f"  ratings archive: {out.name} exists — skipping (idempotent).")
            return

        snapshot = {
            "date": date,
            "week": week,
            "fetched_at": fetched_at,
            "season": season,
            "source": cache.get("source", "CFBD"),
            "sp_ratings": cache.get("sp_ratings", {}),
            "fpi_ratings": cache.get("fpi_ratings", {}),
            # Closing lines are NOT pulled by this fetch today (§4: lines are
            # parked/cosmetic). If a future fetch adds them to the cache, they are
            # archived here automatically with no extra API call.
            "lines": cache.get("lines", {}),
        }
        utils.save_json_atomic(out, snapshot)
        n_files = len(list(season_dir.glob("*.json")))
        print(f"  ratings archive: appended {out.name} (week {week}, "
              f"{len(snapshot['sp_ratings'])} SP+ / {len(snapshot['fpi_ratings'])} FPI); "
              f"{n_files} vintage(s) archived for {season}.")
    except Exception as e:
        # Never let the archive take the pipeline dark — the cache is already saved.
        print(f"::warning:: ratings-archive append failed ({type(e).__name__}: {e}); "
              f"cache is unaffected. Continuing.")


def main():
    ap = argparse.ArgumentParser(description="CFBD weekly fetch -> shared cache")
    # No season literal here: default is the single source (season.json).
    ap.add_argument("--season", type=int, default=None,
                    help="season year (default: season.json cfbd_default_season)")
    ap.add_argument("--simulate-failure", action="store_true",
                    help="Force a fetch error to exercise the commentary-bypass path")
    args = ap.parse_args()
    season = args.season if args.season is not None else utils.get_cfbd_default_season()

    print("=" * 60)
    print(f"FETCH RESULTS — season {season}")
    print("=" * 60)

    client = CFBDClient(utils.get_api_key())
    try:
        if args.simulate_failure:
            client.call_count += 1  # a real attempt would have cost a call
            raise CFBDError("simulated fetch failure (--simulate-failure)")

        games = fetch_games(client, season)
        sp_ratings = fetch_sp(client, season)
        assert_fetch_complete(games, sp_ratings)        # pre-write gate (§4/§6, rule 5)
        fpi_ratings = fetch_fpi(client, season)         # secondary (calibration A/B)
    except CFBDError as e:
        degraded_exit(str(e), season)                    # §4 season-guarded fallback
        return

    teams = build_team_index(games)
    completed = sum(1 for g in games if g["completed"])
    # Neutral cache: scheduled_games counts EVERY game on the slate, including
    # conf-title games (the 13th for their participants). Excluding them is a
    # per-group scoring decision (config count_conference_championship, §1/§5),
    # applied off the per-game conference_championship tag — never here.
    champ_games = [g for g in games if g["conference_championship"]]
    n_champ = len(champ_games)
    # The teams that gain a 13th *regular-season* game are exactly the FBS
    # conf-title participants (both-FBS is baked into the tag). Derive from the
    # tag, NOT from a raw scheduled_games==13 scan — the latter also catches
    # FCS/D-II/D-III teams that reached 13 via their own playoff brackets.
    champ_participants = sorted({g["home_team"] for g in champ_games}
                                | {g["away_team"] for g in champ_games})

    cache = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "season_type": "regular",
        "week": current_week(games),
        "source": "CFBD",
        "counts": {
            "games": len(games),
            "completed": completed,
            "teams": len(teams),
            "sp_ratings": len(sp_ratings),
            "fpi_ratings": len(fpi_ratings),
            "conference_championship_games": n_champ,
            "conference_championship_participants": len(champ_participants),
        },
        "games": games,
        "teams": teams,
        "sp_ratings": sp_ratings,
        "fpi_ratings": fpi_ratings,
    }
    utils.save_cache(cache)
    archive_ratings(cache)          # BUILD 2: append this vintage (append-only, best-effort)

    print(f"\n  season {season}: {len(games)} games "
          f"({completed} completed), {len(teams)} teams, "
          f"{len(sp_ratings)} SP+ / {len(fpi_ratings)} FPI ratings, week={cache['week']}")
    print(f"  conference-championship games tagged (seasonType=regular, "
          f"identified by CFBD `notes` + both-FBS): {n_champ}")
    if champ_participants:
        print(f"  FBS teams gaining a 13th game as conf-title participants "
              f"(counts toward the line only if a group sets "
              f"count_conference_championship): {', '.join(champ_participants)}")
    report_budget(client.call_count)


if __name__ == "__main__":
    main()
