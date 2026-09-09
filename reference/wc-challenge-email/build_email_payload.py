#!/usr/bin/env python3
"""Resolve every email template variable into email/payload.json.

Stdlib only (same ethos as fetch_results.py / generate_commentary.py): read the
committed JSON the rest of the pipeline already produced, compute the derived
fields the *short newsletter* template needs, write one flat payload. The template
does NO logic — all of it lives here, so the email is fully inspectable as
payload.json.

This is the SLIM payload behind the redesigned newsletter email (hero image +
Rome hook + compact standings + one featured pundit + CTA). It deliberately drops
the old dashboard's full Rome column, four-pundit panel, per-owner spotlights,
scoreboard, analytics quotes and win-probability bars — those now live on the
site, which the email's CTA links to.

Reads (all paths overridable):
  site/data/commentary.json        pundit_takes[], optional rome_headline
  site/data/tournament_recap.md    Jim Rome's column -> the hook (rome_short)
  site/data/narrative_state.json   phase context (matchday, preseason flag)
  site/data/owner_standings.json   authoritative leaderboard (rank + points)
  data/draft_board.json            owners -> teams (for "Up Next" labelling)
  data/matches.csv                 schedule -> "Up Next"
  email/config.json                site_base_url (hero image + CTA URLs)
  email/last_sent_state.json       previous narrative_state snapshot -> rank deltas

Writes:
  email/payload.json   (OVERWRITE each run; idempotent)

The rank movement arrows are measured against last_sent_state.json, which
send_email.py refreshes only AFTER a successful send — so movement is "since the
last email," not "since yesterday." First run (no snapshot) -> no movement.

hero_image_url is the URL the hero image WILL live at once generate_hero_image.py
runs and the image deploys to Pages. generate_hero_image.py is the authority on
this field: on success it leaves the URL, on failure it patches it to null so the
template hides the (missing) hero. See email/generate_hero_image.py.

Usage:
    python email/build_email_payload.py --date 2026-06-13
    python email/build_email_payload.py            # --date defaults to today (UTC)
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE_DATA = os.path.join(ROOT, "site", "data")
DATA = os.path.join(ROOT, "data")

# --- league identity (mirrors generate_commentary.py / site/app.js) -----------
OWNER_COLORS = {
    "Zach": "#f4c430", "Gunner": "#2f6dff", "Gayden": "#28c060",
    "Devin": "#f0743a", "Rafe": "#a855f7",
}
RING_NAMES = {
    "Zach": "Mustard Boy", "Gunner": "Bubba G", "Gayden": "The Backpass Assassin",
    "Devin": "Ghost Pepper", "Rafe": "The Noisemaker",
}
# Pundit panel order + identity. Slugs match commentary.json pundit_takes[].pundit
# and the deployed avatar folders site/assets/portraits/pundits/<slug>/. The email
# now features ONE of these per send, rotating by matchday (index = matchday % 4).
PUNDITS = [
    {"slug": "wynalda", "name": "Eric Wynalda", "color": "#e2231a"},
    {"slug": "donovan", "name": "Landon Donovan", "color": "#2f6dff"},
    {"slug": "dempsey", "name": "Clint Dempsey", "color": "#28c060"},
    {"slug": "lalas", "name": "Alexi Lalas", "color": "#f4a423"},
]
MUTED = "#8b919c"
GREEN = "#28c060"
RED = "#ec4444"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------- IO
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def load_matches(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]
    except FileNotFoundError:
        return []


# ----------------------------------------------------------------- formatting
def fmtnum(x):
    """0 -> '0', 3.0 -> '3', 2.5 -> '2.5'."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return "0"
    return f"{f:g}"


def date_human(iso):
    """'2026-06-13' -> 'Jun 13' (no platform-specific strftime flags)."""
    try:
        y, m, d = (int(p) for p in iso.split("-"))
        return f"{MONTHS[m - 1]} {d}"
    except (ValueError, IndexError, AttributeError):
        return iso or ""


def _strip_md(text):
    return re.sub(r"[*_`#>]", "", text or "").strip()


def first_sentence(text, limit=60):
    """First sentence of the Rome column, markdown-stripped, truncated to `limit`.
    Used only as the headline fallback when commentary emits no rome_headline."""
    t = _strip_md(text)
    if not t:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", t)
    s = parts[0].strip() if parts else t
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def first_sentences(text, n=4, limit=420):
    """The hook: first `n` sentences of the Rome column, truncated cleanly at a
    sentence boundary, and hard-capped at `limit` chars. Markdown-stripped, leading
    paragraph only (the open), so the email teases — it never reprints the column."""
    # Take the first non-empty paragraph so we open on Rome's lead, not a mid-column aside.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    lead = _strip_md(paras[0]) if paras else _strip_md(text)
    if not lead:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", lead)
    out = []
    for s in parts:
        s = s.strip()
        if not s:
            continue
        candidate = (" ".join(out + [s])).strip()
        if out and len(candidate) > limit:
            break
        out.append(s)
        if len(out) >= n:
            break
    hook = " ".join(out).strip()
    if len(hook) > limit:
        hook = hook[: limit - 1].rstrip() + "…"
    return hook


def avatar_url(base_url, slug, rotation):
    """Daily-rotating avatar: idx 0 -> <slug>.png, idx 1..4 -> <slug>_v{idx+1}.png."""
    idx = rotation % 5
    fname = f"{slug}.png" if idx == 0 else f"{slug}_v{idx + 1}.png"
    return f"{base_url}/assets/portraits/pundits/{slug}/{fname}"


# ----------------------------------------------------------------- sections
def build_rome(commentary, recap_md):
    """The hook only: a headline + the first 3-4 sentences of the column. The full
    column lives on the site; the email teases it."""
    headline = (commentary or {}).get("rome_headline") or ""
    headline = headline.strip()
    if not headline:
        headline = first_sentence(recap_md, 60) or "The pool is heating up"
    return {"headline": headline, "short": first_sentences(recap_md, n=4)}


# ---------------------------------------------------------------- subject line
def _winner_team(m):
    """Winning team of a played match (penalties break level scores); None for a
    genuine draw or an unplayed match. Mirrors build_narrative_state.winner_loser."""
    hs, as_ = m.get("home_score"), m.get("away_score")
    if hs is None or as_ is None:
        return None
    if hs == as_:
        pw = (m.get("pen_home", 0) or 0) - (m.get("pen_away", 0) or 0)
        if pw == 0:
            return None
        return m.get("home") if pw > 0 else m.get("away")
    return m.get("home") if hs > as_ else m.get("away")


def _champion_team(results):
    """The team that won the Final, or None if no Final has been played yet."""
    for day in (results or {}).get("days", []):
        for m in day.get("matches", []):
            if str(m.get("stage", "group")).lower().startswith("group"):
                continue
            if str(m.get("round", "")).upper() == "FINAL":
                return _winner_team(m)
    return None


def _cap(subject, limit=80):
    """Subject lines never exceed `limit` chars; over-length truncates with an ellipsis."""
    subject = subject.strip()
    if len(subject) > limit:
        subject = subject[: limit - 1].rstrip() + "…"
    return subject


def build_subject(day_label, matchdays, is_preseason, narrative, recap_md, draft, results):
    """Priority-based inbox bait: the single most dramatic thing that happened on the
    latest scored matchday drives the subject. First match wins down the ladder
    (elimination > lead change > upset > biggest mover > rivalry > Rome tease); ties at
    the same priority break toward the highest-ranked owner (most at stake). Preseason
    and the Final are special-cased. Always <=80 chars. Uses ring names, never real
    names. All signals come from narrative_state.json (the deterministic accumulator);
    only the champion is read from daily_results.json, which narrative_state omits."""
    owner_of = {team: o for o, teams in (draft or {}).items() for team in teams}
    owners_state = (narrative or {}).get("owners") or {}
    phase = (narrative or {}).get("phase") or {}

    def rank_of(o):
        r = (owners_state.get(o) or {}).get("rank")
        return r if isinstance(r, int) else 999

    def ring(o):
        return RING_NAMES.get(o, o or "")

    def rome_tease():
        return first_sentence(recap_md, 50) or "The pool is heating up"

    # --- Final: the tournament is over -> crown the champion --------------------
    if phase.get("current_round") == "Final" and (phase.get("matches_remaining") or 0) == 0:
        champ = _champion_team(results)
        if champ:
            r = ring(owner_of.get(champ)) if owner_of.get(champ) else ""
            crown = f"{champ}'s {r} takes the crown." if r else f"{champ} takes the crown."
            return _cap(f"WC Challenge: It's over. {crown}")

    # --- Preseason / Day 0: nothing has happened -> tease Rome -----------------
    if is_preseason or matchdays == 0:
        return _cap(f"WC Challenge Preseason: {rome_tease()}")

    prefix = f"WC Challenge {day_label}: "

    # Everything below keys off the LATEST scored matchday only.
    history = (narrative or {}).get("history") or []
    event_date = history[-1].get("date") if history else None
    events = [e for e in ((narrative or {}).get("notable_events") or [])
              if event_date and e.get("date") == event_date]

    # 1) Elimination — a drafted team is knocked out of the tournament
    elims = [e for e in events if e.get("type") == "elimination" and e.get("owner")]
    if elims:
        e = min(elims, key=lambda e: rank_of(e.get("owner")))
        return _cap(f"{prefix}{e['team']} is dead. {ring(e['owner'])} loses a soldier.")

    # 2) Lead change — a different owner moved into #1 on this matchday
    leader = next((o for o, s in owners_state.items() if s.get("rank") == 1), None)
    if leader and (owners_state[leader].get("rank_change_from_prev_day") or 0) > 0:
        return _cap(f"{prefix}New leader. {ring(leader)} takes the throne.")

    # 3) Upset — an upset bonus fired
    upsets = [e for e in events if e.get("type") == "upset" and e.get("owner")]
    if upsets:
        e = min(upsets, key=lambda e: rank_of(e.get("owner")))
        return _cap(f"{prefix}UPSET. {e['team']} stuns {e['beat']}. "
                    f"+{fmtnum(e['bonus'])} for {ring(e['owner'])}.")

    # 4) Biggest mover — most points banked today
    movers = [(o, (s.get("points_today") or 0)) for o, s in owners_state.items()]
    if movers:
        o, pts = max(movers, key=lambda x: (x[1], -rank_of(x[0])))
        if pts > 0:
            return _cap(f"{prefix}{ring(o)} surges — {fmtnum(pts)} pts in one day.")

    # 5) Rivalry clash — two owners' teams met head-to-head today
    def clash_owners(c):
        if c.get("result") == "draw":
            return [o for o in (c.get("owners") or []) if o]
        return [o for o in (c.get("winner"), c.get("loser")) if o]
    clashes = [c for c in ((narrative or {}).get("head_to_head_log") or [])
               if c.get("date") == event_date and len(set(clash_owners(c))) == 2]
    if clashes:
        c = min(clashes, key=lambda c: min(rank_of(o) for o in clash_owners(c)))
        a, b = sorted(set(clash_owners(c)), key=rank_of)[:2]
        return _cap(f"{prefix}{ring(a)} vs {ring(b)}. It got personal.")

    # 6) Default — nothing dramatic -> tease Rome
    return _cap(f"{prefix}{rome_tease()}")


def build_standings(standings_doc, prev_owners):
    """Compact leaderboard: rank, owner (ring name), points, movement since last email."""
    rows = (standings_doc or {}).get("standings") or []
    out = []
    for r in rows:
        owner = r.get("owner")
        cur_rank = r.get("rank")
        prev_rank = (prev_owners.get(owner) or {}).get("rank") if prev_owners else None
        move = (prev_rank - cur_rank) if (prev_rank and cur_rank) else 0
        if move > 0:
            arrow, color = "▲", GREEN
        elif move < 0:
            arrow, color = "▼", RED
        else:
            arrow, color = "—", MUTED
        out.append({
            "rank": cur_rank,
            "owner": owner,
            "ring_name": RING_NAMES.get(owner, ""),
            "color": OWNER_COLORS.get(owner, MUTED),
            "total_points": fmtnum(r.get("total_points", 0)),
            "move_arrow": arrow,
            "move_color": color,
        })
    return out


def build_featured_pundit(commentary, base_url, rotation):
    """ONE pundit per email — the daily rotation (index = matchday % 4) — with the
    day's rotating avatar variant and that pundit's take. None if the rotated pundit
    filed no take this cycle (template hides the section)."""
    pu = PUNDITS[rotation % len(PUNDITS)]
    takes = {t.get("pundit"): t for t in (commentary or {}).get("pundit_takes") or []}
    t = takes.get(pu["slug"])
    if not t or not (t.get("headline") or "").strip():
        return None
    return {
        "slug": pu["slug"],
        "name": pu["name"],
        "color": pu["color"],
        "avatar_url": avatar_url(base_url, pu["slug"], rotation),
        "headline": t.get("headline", ""),
        "subtitle": t.get("subtitle", ""),
        "match": t.get("match", ""),
    }


def next_fixture_date(rows, after_date):
    """Smallest schedule date strictly after `after_date` with >=1 drafted-team match."""
    dates = sorted({
        r["date"] for r in rows
        if r.get("date") and (after_date is None or r["date"] > after_date)
        and (r.get("team1_owner") or r.get("team2_owner"))
    })
    return dates[0] if dates else None


def build_up_next(rows, after_date):
    """Kept for the slim 'up next' teaser line under the CTA."""
    nd = next_fixture_date(rows, after_date)
    if not nd:
        return {"date_human": "", "has_matches": False, "matches": []}
    games = []
    for r in rows:
        if r.get("date") != nd:
            continue
        if not (r.get("team1_owner") or r.get("team2_owner")):
            continue
        games.append({
            "team1": r.get("team1"),
            "team1_owner": r.get("team1_owner"),
            "team1_color": OWNER_COLORS.get(r.get("team1_owner"), MUTED),
            "team2": r.get("team2"),
            "team2_owner": r.get("team2_owner"),
            "team2_color": OWNER_COLORS.get(r.get("team2_owner"), MUTED),
            "group": r.get("group"),
            "time_et": r.get("time_et"),
            "venue": r.get("venue"),
        })
    return {"date_human": date_human(nd), "has_matches": bool(games), "matches": games}


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Build the WC Challenge email payload")
    ap.add_argument("--date", default=None, help="the email's 'today' (YYYY-MM-DD); default = today UTC")
    ap.add_argument("--commentary", default=os.path.join(SITE_DATA, "commentary.json"))
    ap.add_argument("--recap", default=os.path.join(SITE_DATA, "tournament_recap.md"))
    ap.add_argument("--narrative", default=os.path.join(SITE_DATA, "narrative_state.json"))
    ap.add_argument("--standings", default=os.path.join(SITE_DATA, "owner_standings.json"))
    ap.add_argument("--results", default=os.path.join(SITE_DATA, "daily_results.json"),
                    help="day's match results (used only to crown the Final's champion)")
    ap.add_argument("--draft", default=os.path.join(DATA, "draft_board.json"))
    ap.add_argument("--matches", default=os.path.join(DATA, "matches.csv"))
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--last-state", default=os.path.join(HERE, "last_sent_state.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "payload.json"))
    ap.add_argument("--generated", default=None, help="ISO timestamp for meta.generated")
    args = ap.parse_args()

    today = args.date or _dt.datetime.utcnow().strftime("%Y-%m-%d")

    commentary = load_json(args.commentary, {})
    recap_md = read_text(args.recap)
    narrative = load_json(args.narrative, {})
    standings_doc = load_json(args.standings, {})
    results = load_json(args.results, {})
    draft = (load_json(args.draft, {}) or {}).get("owners", {})
    rows = load_matches(args.matches)
    config = load_json(args.config, {})
    prev_state = load_json(args.last_state, None)
    prev_owners = (prev_state or {}).get("owners") if prev_state else None

    base_url = (config.get("site_base_url") or "").rstrip("/")

    phase = (narrative or {}).get("phase", {})
    is_preseason = bool(phase.get("is_preseason", True))
    matchdays = int(phase.get("matchdays_played", 0) or 0)
    day_label = "Preseason" if (is_preseason or matchdays == 0) else f"Day {matchdays}"

    rome = build_rome(commentary, recap_md)
    subject = build_subject(day_label, matchdays, is_preseason, narrative, recap_md, draft, results)

    # The hero image WILL deploy here (generate_hero_image.py writes day_{N}.png and,
    # on success, keeps this URL; on failure it nulls it so the template hides it).
    hero_image_url = f"{base_url}/assets/email/day_{matchdays}.png" if base_url else None
    site_url = base_url or None

    payload = {
        "meta": {
            "generated": args.generated or "",
            "today": today,
            "day_number": matchdays,
            "day_label": day_label,
            "is_preseason": is_preseason,
            "subject": subject,
            "site_base_url": base_url,
            "tournament": (narrative or {}).get("tournament", "FIFA World Cup 2026"),
        },
        "hero_image_url": hero_image_url,
        "site_url": site_url,
        "rome": rome,
        "standings": build_standings(standings_doc, prev_owners),
        "featured_pundit": build_featured_pundit(commentary, base_url, matchdays),
        "up_next": build_up_next(rows, today),
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    fp = payload["featured_pundit"]
    print(f"Wrote {args.out}")
    print(f"  subject: {subject}")
    print(f"  day: {day_label} · featured pundit: {fp['name'] if fp else '(none)'} · "
          f"hero: {hero_image_url or '(none)'} · up-next: {payload['up_next']['date_human'] or '(none)'}")


if __name__ == "__main__":
    main()
