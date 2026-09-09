#!/usr/bin/env python3
"""
build_email_payload.py — Assemble the weekly email data (ARCHITECTURE §8 CLONE, §10.7).

Job: for a group, gather its standings (Board 1) + projection (Board 2) output
and the filed SVP column into the structured payload render.py turns into the
email body.

THE ONE RULE: THIS LAYER COMPUTES NOTHING.
------------------------------------------
Every number in the payload is copied verbatim from a committed pipeline artifact
that Python already computed. No scoring math, no re-ranking, no deriving a value
the packet does not carry. If the email wants a number that does not exist
upstream, the answer is to add it upstream — not here. A second implementation of
the scoring rules living in the email layer is exactly how the site and the email
start disagreeing about who is winning.

What this file IS allowed to do is FORMAT: round a probability to a percent
string, put a leading + on a positive delta, order rows the packet already ranked.
That mirrors the rule the rail already states in its own header — "the page is not
allowed to do arithmetic, so the rounding happens in Python." The email template
is held to the same standard, so the rounding happens here.

Reads (all committed, all overridable):
  docs/data/<g>/standings.json          Board 1 — exact banked delta + envelope
  docs/data/<g>/projection.json         Board 2 — expected totals, percentiles
  docs/data/<g>/analytics.json          race / championship_odds / best_worst
  docs/data/<g>/columns/index.json      which week is current
  docs/data/<g>/columns/week_<N>.json   the filed SVP column (deck + paragraphs)
  docs/data/<g>/columns/rail.json       collision + featured pick (optional)
  groups/<g>/config.json                display_name (NOT addresses — see below)
  email/config.json                     from, site_base_url

Writes:
  email/payload_<group>.json            OVERWRITE each run; idempotent; gitignored

WHY THE COLUMN COMES FROM columns/week_<N>.json AND NOT output/column_week_<N>.md:
both are committed and carry the same prose, but the JSON is the published form —
it adds the `deck` (which becomes the subject line) and splits paragraphs the way
the site renders them. Reading the raw .md would mean re-parsing prose the pipeline
has already structured, i.e. doing work upstream already did. The .md stays the
authored source; this reads the artifact built from it.

NO ADDRESSES. The payload carries not one recipient address — it is a content
document. Recipients are resolved at send time by scripts/recipients.py from the
gitignored overlay. This matters because payload_*.json is a build artifact that
could plausibly get committed by a careless `git add`; keeping it address-free
means that mistake stays harmless. assert_no_addresses() enforces it on write.

Usage:
    python scripts/build_email_payload.py --group panel
    python scripts/build_email_payload.py --group panel --week 1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = ROOT / "docs" / "data"
GROUPS = ROOT / "groups"
EMAIL_DIR = ROOT / "email"

# ---------------------------------------------------------------------------
# SVP SEED PROMPT — PLACEHOLDER
# ---------------------------------------------------------------------------
# The email does NOT generate prose. It wraps the column generate_commentary.py
# already filed for the week, so the voice work belongs upstream, in
# templates/svp_persona.md and the commentary prompt.
#
# Zach is supplying a seed prompt for the "fat Van Pelt writeup of the past
# weekend." When it lands it configures the COLUMN GENERATOR, not this file. The
# only thing that changes here is the intro line below, which exists so the email
# has a byline strip while the real voice is still being tuned.
SVP_SEED_PROMPT_PLACEHOLDER = {
    "status": "PLACEHOLDER — awaiting Zach's seed prompt",
    "applies_to": "scripts/generate_commentary.py (the column generator), not the email",
    "byline": "Scott Van Pelt",
    "standfirst": "The weekend, reviewed.",
}


class PayloadError(RuntimeError):
    """A required input was unusable. Fail loud rather than email a broken board."""


def load_json(path: Path, required: bool = True):
    """Read committed JSON. A missing OPTIONAL input returns None (section hides)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if required:
            raise PayloadError(f"required input missing: {path}") from None
        return None
    except json.JSONDecodeError as e:
        raise PayloadError(f"{path} is not valid JSON ({e.msg} at line {e.lineno})") from None


# --- formatting helpers: presentation only, never derivation -----------------

def fmt_signed(v):
    """-7.0 -> '-7.0';  17.0 -> '+17.0'. Sign only; the value is untouched."""
    if v is None:
        return None
    return f"{v:+.1f}"


def fmt_plain(v, places=1):
    return None if v is None else f"{v:.{places}f}"


def fmt_pct(p, places=1):
    """0.487175 -> '48.7%'. Rounding happens in Python, per the rail's own rule."""
    return None if p is None else f"{p * 100:.{places}f}%"


def move_arrow(week_move):
    """Upstream's week_move, rendered. None (no prior week) is a dash, not a zero."""
    if week_move is None:
        return {"arrow": "—", "label": None, "dir": "flat"}
    if week_move > 0:
        return {"arrow": "▲", "label": f"+{week_move}", "dir": "up"}
    if week_move < 0:
        return {"arrow": "▼", "label": str(week_move), "dir": "down"}
    return {"arrow": "—", "label": None, "dir": "flat"}


# --- section builders: each returns None when its input is absent ------------

def build_board1(analytics, standings):
    """Board 1 — EXACT banked delta + the floor/ceiling envelope. Settled fact."""
    race = (analytics or {}).get("race") or {}
    rows = race.get("managers")
    if not rows:
        return None
    out = []
    for m in rows:
        out.append({
            "manager_id": m["manager_id"],
            "display_name": m["display_name"],
            "rank": m.get("rank"),
            "banked_total": fmt_signed(m.get("banked_total")),
            "floor": fmt_signed(m.get("floor")),
            "ceiling": fmt_signed(m.get("ceiling")),
            "gap_to_leader": (None if not m.get("gap_to_leader")
                              else fmt_plain(m.get("gap_to_leader"))),
            "ceiling_remaining": fmt_plain(m.get("ceiling_remaining")),
            "move": move_arrow(m.get("week_move")),
            "is_leader": m.get("manager_id") == race.get("leader_id"),
        })
    return {
        "board": "exact",
        "label": "Board 1 — Banked",
        "sublabel": "Exact banked delta, with each manager's floor–ceiling envelope.",
        "prior_week": race.get("prior_week"),
        "rows": out,
    }


def build_board2(analytics, projection):
    """Board 2 — PROJECTION. Labeled as such in the email itself, never as standings."""
    odds = (analytics or {}).get("championship_odds") or {}
    if not odds.get("available") or not odds.get("managers"):
        return None
    proj_by = {m["manager_id"]: m for m in (projection or {}).get("managers", [])}
    rows = []
    for m in odds["managers"]:
        p = proj_by.get(m["manager_id"], {})
        rows.append({
            "manager_id": m["manager_id"],
            "display_name": m["display_name"],
            "p_win_pool": fmt_pct(m.get("p_win_pool")),
            "expected_total": fmt_signed(p.get("expected_total")),
            "p05": fmt_signed(p.get("p05")),
            "p95": fmt_signed(p.get("p95")),
            "move": move_arrow(m.get("week_move")),
        })
    meta = (projection or {}).get("meta") or {}
    return {
        "board": "projection",
        "label": "Board 2 — Projection",
        "sublabel": "Model projection, not standings. Odds to win the pool, "
                    "with each manager's expected finish and 5th–95th percentile range.",
        "disclaimer": "PROJECTION — modeled, not banked. Board 1 above is the settled board.",
        "ratings_source": meta.get("ratings_source"),
        "rows": rows,
    }


def build_best_worst(analytics):
    bw = (analytics or {}).get("best_worst") or {}
    steal = (bw.get("steal") or [None])[0]
    bust = (bw.get("bust") or [None])[0]
    if not steal and not bust:
        return None

    def one(p, kind):
        if not p:
            return None
        return {
            "kind": kind,
            "display_name": p.get("display_name"),
            "team": p.get("team"),
            "line": fmt_plain(p.get("line")),
            "direction": "Over" if p.get("direction") == "O" else "Under",
            "delta": fmt_signed(p.get("delta")),
        }
    return {"steal": one(steal, "Steal"), "bust": one(bust, "Bust")}


def build_column(week_doc):
    """The filed SVP column: deck + paragraphs, verbatim. No generation here."""
    col = (week_doc or {}).get("column") or {}
    paras = col.get("paragraphs") or []
    if not paras:
        return None
    return {
        "deck": col.get("deck"),
        "paragraphs": paras,
        "byline": SVP_SEED_PROMPT_PLACEHOLDER["byline"],
        "standfirst": SVP_SEED_PROMPT_PLACEHOLDER["standfirst"],
        "word_count": (week_doc.get("meta") or {}).get("word_count"),
    }


def build_rail(rail):
    """Optional garnish. A missing collision or featured_pick is an ORDINARY state."""
    if not rail:
        return None
    out = {}
    c = rail.get("collision")
    if c and c.get("picks"):
        out["collision"] = {
            "team": c.get("team"),
            "line": fmt_plain(c.get("line")),
            "implied_expected_wins": fmt_plain(c.get("implied_expected_wins"), 2),
            "picks": [{
                "manager": p.get("manager"),
                "direction": "Over" if p.get("direction") == "O" else "Under",
                "p_beat_line": p.get("p_beat_line"),  # already a rendered string upstream
            } for p in c["picks"]],
        }
    f = rail.get("featured_pick")
    if f:
        out["featured_pick"] = {
            "card_title": f.get("card_title") or "Featured pick",
            "manager": f.get("manager"),
            "team": f.get("team"),
            "direction": "Over" if f.get("direction") == "O" else "Under",
            "line": fmt_plain(f.get("line")),
            "expected_final_wins": fmt_plain(f.get("expected_final_wins"), 2),
            "expected_delta": fmt_signed(f.get("expected_delta")),
        }
    return out or None


def resolve_week(index_doc, requested):
    """Current week = columns[0] (index is newest-first). Explicit --week wins."""
    cols = (index_doc or {}).get("columns") or []
    if requested is not None:
        for c in cols:
            if c.get("week") == requested:
                return c
        raise PayloadError(f"--week {requested} has no filed column in columns/index.json")
    if not cols:
        return None
    return cols[0]


def subject_for(group_display, week, column):
    """Subject from the column's own deck — content that already exists."""
    base = f"{group_display} — Week {week}"
    deck = (column or {}).get("deck")
    if not deck:
        return base
    deck = re.sub(r"\s+", " ", deck).strip()
    if len(deck) > 78:
        deck = deck[:77].rstrip(" ,.;:") + "…"
    return f"{base}: {deck}"


_ADDR_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def assert_no_addresses(payload):
    """The payload is a CONTENT document. An address in it is a bug, not a feature."""
    hits = _ADDR_RE.findall(json.dumps(payload, ensure_ascii=False))
    # email/config.json's `from` is ours and intentionally present; nothing else may
    # be. Match on the bare address, since `from` carries a display name around it
    # ("CF Challenge <mb4@mustardboy.xyz>") and the regex extracts only the address.
    allowed = set(_ADDR_RE.findall((payload.get("meta") or {}).get("from_address") or ""))
    leaked = [h for h in hits if h not in allowed]
    if leaked:
        raise PayloadError(
            f"{len(leaked)} email address(es) reached the payload. It is a content "
            f"document and must stay address-free; recipients resolve at send time."
        )


def build(group_id: str, week: int | None = None) -> dict:
    gdir = DOCS_DATA / group_id
    gcfg = load_json(GROUPS / group_id / "config.json")
    ecfg = load_json(EMAIL_DIR / "config.json")

    standings = load_json(gdir / "standings.json", required=False)
    projection = load_json(gdir / "projection.json", required=False)
    analytics = load_json(gdir / "analytics.json", required=False)
    index_doc = load_json(gdir / "columns" / "index.json", required=False)
    rail = load_json(gdir / "columns" / "rail.json", required=False)

    entry = resolve_week(index_doc, week)
    week_no = entry.get("week") if entry else None
    week_doc = (load_json(gdir / "columns" / entry["file"], required=False)
                if entry else None)

    meta_src = (standings or {}).get("meta") or (analytics or {}).get("meta") or {}
    base_url = (ecfg.get("site_base_url") or "").rstrip("/")
    column = build_column(week_doc)
    display = gcfg.get("display_name") or group_id

    payload = {
        "meta": {
            "group_id": group_id,
            "display_name": display,
            "season": meta_src.get("season"),
            "week": week_no,
            "generated_at": meta_src.get("generated_at"),
            "cache_fetched_at": meta_src.get("cache_fetched_at"),
            "subject": subject_for(display, week_no, column),
            "from_address": ecfg.get("from"),
            "site_base_url": base_url,
            "svp_seed_prompt": SVP_SEED_PROMPT_PLACEHOLDER,
        },
        # `?group=` is the site's own entry form (docs/site.js) — the per-group
        # /panel/ path only redirects here, so link the canonical target.
        "cta_url": f"{base_url}/?group={group_id}" if base_url else None,
        "column": column,
        "board1": build_board1(analytics, standings),
        "board2": build_board2(analytics, projection),
        "best_worst": build_best_worst(analytics),
        "rail": build_rail(rail),
    }
    assert_no_addresses(payload)
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--group", required=True)
    ap.add_argument("--week", type=int, help="filed week to render (default: current)")
    ap.add_argument("--out", help="default: email/payload_<group>.json")
    args = ap.parse_args(argv)

    try:
        payload = build(args.group, args.week)
    except PayloadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else EMAIL_DIR / f"payload_{args.group}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Write via a temp file so a crash mid-write cannot leave a half payload that
    # the next step would happily render.
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, out)

    have = [k for k in ("column", "board1", "board2", "best_worst", "rail") if payload.get(k)]
    hidden = [k for k in ("column", "board1", "board2", "best_worst", "rail") if not payload.get(k)]
    print(f"{args.group}: week {payload['meta']['week']} -> {out}")
    print(f"  sections: {', '.join(have)}" + (f"  (hidden: {', '.join(hidden)})" if hidden else ""))
    print(f"  subject: {payload['meta']['subject']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
