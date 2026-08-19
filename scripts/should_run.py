#!/usr/bin/env python3
"""should_run.py — the scheduled pipeline's run gate (CLAUDE.md rule 7).

The cron fires every day. Most days have no football, and the whole point of
this gate is that those days cost NOTHING: no API call, no dependency install,
no commit, no deploy. It decides purely off files already in the repo —
data/cfbd_cache.json (which carries every game's `start_date` and `week`) and
season.json. Zero network, so the gate can run before `pip install`.

    python scripts/should_run.py [--force] [--lookback-hours 30]
                                 [--settle-hours 5] [--now ISO8601]
                                 [--cache PATH]

DECISION LADDER (first match wins). The three RUN escapes come first on
purpose — a gate that can wedge the pipeline shut is worse than one that runs
a few extra times:

  1. --force                  manual dispatch always runs (rule 7).
  2. no cache on disk         nothing to decide from; a fetch must populate it.
  3. cache season != season.json season
                              the season flip just happened. The cache still
                              holds LAST season's dates, so every window test
                              below would say "no games" forever and the fetch
                              that would fix it would never run. Self-healing
                              escape from that deadlock.
  4. a game kicked off in the window
                              -> RUN. Window is [now-lookback, now-settle]:
                              far enough back to catch the whole slate, and
                              `settle` keeps us from firing on a game that is
                              still in progress.
  5. before the first kickoff -> SKIP (pre-season). This is the "self-removing
                              date check" rule 7 asks for, with NO date
                              literal — it compares against the schedule's own
                              first game, so it stops applying by itself.
  6. after the last game      -> SKIP (season complete / off-season).
  7. no game within +/- 4 days-> SKIP (bye week — handled explicitly, since a
                              football season has real gaps mid-schedule).
  8. otherwise                -> SKIP (in season, but nothing finished yet).

FAIL-OPEN. An unexpected error emits run=true plus a ::warning::, never a
skip. A gate that fails closed takes the site dark silently, which is the
exact failure mode rule 3 exists to prevent; running one extra time is cheap.

Emits `run` and `reason` to $GITHUB_OUTPUT for the workflow to branch on, and
a short block to $GITHUB_STEP_SUMMARY so a skipped run reads as deliberate
rather than broken.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import utils

# A game is assumed finished this long after kickoff. Keeps the gate from
# firing mid-game, when a fetch would bank a partial slate.
DEFAULT_SETTLE_HOURS = 5
# How far back to look for a kickoff. 30h + a daily cron means each game day
# is picked up by the next morning's run.
DEFAULT_LOOKBACK_HOURS = 30
# A week with no game inside this radius is a bye/off week, not a quiet day.
BYE_RADIUS_DAYS = 4


def parse_dt(s):
    """CFBD start_date -> aware UTC datetime, or None if unparseable."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def kickoffs(cache):
    """Every parseable kickoff in the cache, sorted. Unparseable dates are
    skipped rather than fatal (rule 10: parse upstream strings best-effort)."""
    out = [parse_dt(g.get("start_date")) for g in cache.get("games") or []]
    return sorted(d for d in out if d is not None)


def decide(now, cache_present, cache, season, lookback_h, settle_h, force):
    """Return (run: bool, reason: str). Pure — all I/O happens in main()."""
    if force:
        return True, "manual dispatch (--force)"
    if not cache_present:
        return True, "no cache on disk — a fetch must populate it"

    cache_season = cache.get("season")
    if cache_season != season:
        return True, (f"season flip pending — cache is tagged {cache_season!r} but "
                      f"season.json says {season!r}; fetching to re-tag it")

    games = kickoffs(cache)
    if not games:
        return True, "cache holds no parseable kickoffs — fetching to rebuild it"

    lo = now - timedelta(hours=lookback_h)
    hi = now - timedelta(hours=settle_h)
    recent = [d for d in games if lo <= d <= hi]
    if recent:
        return True, (f"{len(recent)} game(s) kicked off between {lookback_h}h and "
                      f"{settle_h}h ago (last {recent[-1]:%Y-%m-%d %H:%MZ})")

    first, last = games[0], games[-1]
    if now < first:
        return False, f"pre-season — first kickoff is {first:%Y-%m-%d %H:%MZ}"
    if now > last + timedelta(days=7):
        return False, f"season complete — last game was {last:%Y-%m-%d %H:%MZ}"

    radius = timedelta(days=BYE_RADIUS_DAYS)
    if not any(now - radius <= d <= now + radius for d in games):
        nxt = next((d for d in games if d > now), None)
        when = f"; next kickoff {nxt:%Y-%m-%d %H:%MZ}" if nxt else ""
        return False, f"bye week — no game within {BYE_RADIUS_DAYS} days{when}"

    nxt = next((d for d in games if d > now), None)
    when = f"; next kickoff {nxt:%Y-%m-%d %H:%MZ}" if nxt else ""
    return False, f"no game finished in the last {lookback_h}h{when}"


def emit(run, reason, now):
    """Write the decision to stdout, $GITHUB_OUTPUT and $GITHUB_STEP_SUMMARY."""
    verdict = "RUN" if run else "SKIP"
    print(f"should_run: {verdict} — {reason} (now {now:%Y-%m-%d %H:%MZ})")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\n")
            fh.write(f"reason={reason}\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        icon = "▶️" if run else "⏭️"
        head = "Running" if run else "Skipped — no work to do"
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {icon} Gate: {head}\n\n{reason}\n\n")
            if not run:
                fh.write("_This is the rule-7 week-window gate, not a failure. "
                         "The cron fires daily; days with no football cost no "
                         "API calls. Re-run with **Run workflow** to force._\n\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="always run (manual dispatch)")
    ap.add_argument("--lookback-hours", type=float, default=DEFAULT_LOOKBACK_HOURS)
    ap.add_argument("--settle-hours", type=float, default=DEFAULT_SETTLE_HOURS)
    ap.add_argument("--now", help="ISO8601 override, for tests")
    ap.add_argument("--cache", help="cache path override, for tests")
    args = ap.parse_args(argv)

    now = parse_dt(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        raise SystemExit(f"ERROR: could not parse --now {args.now!r}")

    try:
        present = utils.cache_exists(args.cache)
        # peek_cache, NOT load_cache: a season mismatch is a case this gate
        # must DECIDE on (escape 3), not crash on.
        cache = utils.peek_cache(args.cache) if present else {}
        run, reason = decide(now, present, cache, utils.get_season(),
                             args.lookback_hours, args.settle_hours, args.force)
    except Exception as exc:                                  # noqa: BLE001
        print(f"::warning title=Gate failed open::should_run.py could not decide "
              f"({type(exc).__name__}: {exc}). Running anyway — a gate that fails "
              f"closed would take the site dark silently.")
        emit(True, f"gate error, failed open ({type(exc).__name__})", now)
        return 0

    emit(run, reason, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
