#!/usr/bin/env python3
"""
build_rail.py -- publish the column page's data rail from the week packet.

WHY THIS EXISTS. svp.html wants two cards beside the column: the two-sided
dispute over one team, and the one pick the model likes least. Both were
already computed -- they are `collisions` and `worst_pick_on_the_board` in the
week packet -- but the packet is an internal prompt input under output/ and
groups/<g>/output/, neither of which GitHub Pages serves. The page could not
reach them, so the cards shipped omitted.

This is the publisher for exactly those two blocks, and nothing else.

IT COMPUTES NOTHING. Every value it writes is copied out of the packet. It does
not re-select which collision, does not re-rank the picks, does not recompute a
probability or a gap. The packet's selection IS the selection -- the coda rule
in preseason_baseline.exclude_lead_subject already decided which pick the
column is about, and a second opinion here would put a different pick in the
rail than the one the prose discusses.

DISPLAY-READY, because the page is not allowed to do arithmetic. The site
renders JSON and computes nothing (playbook P2 #12), and a probability that
reaches the browser as 0.86467 forces the page to either print that -- which is
not what a reader wants -- or divide it, which is the browser computing. So
p_beat_line is rounded HERE and published as the string the card prints ("86%"),
on the same whole-number-percent rule the column's own house style uses. The
key keeps the packet's name so the provenance is obvious; the VALUE is a
rendered percentage, and that is deliberate. Line and win-total fields are
already display-ready decimals and are copied as numbers.

ABSENCE IS A STATE, never a stub. A week with no collision omits the
`collision` key; a packet with no featured pick omits `featured_pick`. A packet
carrying neither has no rail at all, and any rail.json already on disk is
DELETED rather than left standing -- week 0's Texas dispute sitting beside a
week 7 column is a stale card that reads as current, which is worse than no
card. The page treats every one of these as ordinary and renders fewer cards.

FAIL LOUD ON A HALF-BLOCK. A block that is present but missing a field it is
supposed to carry is a packet-shape change, not an absence, and it exits 1
naming the field (playbook rule 4). Fail loud in Python; fail soft at render.

SCOPE. RAIL_GROUPS below is the whole gate -- see the comment on it. The code
is group-agnostic; the tuple is the only thing that knows panel is special.

Reads   the week packet dict, in memory, from whichever builder produced it
WRITES  docs/data/<group>/columns/rail.json   OVERWRITE (fully regenerated)

Usage: not a CLI. Imported by preseason_baseline.py (the Week 0 packet) and
build_week_packet.py (every other week), each calling write_rail(packet) right
after it writes its own packet.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import utils


# THE GATE, and the only line that knows which groups get a rail.
#
# ONE LINE TO EXTEND. Add slugs to the tuple -- ("panel", "family") -- or set it
# to the string "all" for every group. Nothing else changes: the writer is fed
# by each packet builder's existing per-group loop and has no per-group code in
# it at all.
#
# It started at panel alone, on a pass where only panel's rail had been looked
# at on a page. All four are enabled now: every group's Week 0 packet was
# confirmed present and carrying a worst_pick_on_the_board first, and church --
# which has no collisions at all -- is what proves the absent-key path is real
# rather than theoretical.
RAIL_GROUPS = ("panel", "family", "church", "browns")


def is_enabled(group_id):
    return RAIL_GROUPS == "all" or group_id in RAIL_GROUPS


def rail_path(group_id):
    """Beside the column archive it belongs to, not at the group root: it is
    part of the column surface and is meaningless without one."""
    return utils.WEB_DATA_DIR / group_id / "columns" / "rail.json"


def _fail(group_id, msg):
    print(f"::error:: [{group_id}] rail: {msg}", file=sys.stderr)
    sys.exit(1)


def _need(group_id, obj, key, where):
    """A field the block promised to carry. Absent or null is a packet-shape
    change and stops the run naming the field -- silently dropping it would
    publish a card with a blank in it."""
    value = obj.get(key)
    if value is None:
        _fail(group_id, f"{where} carries no {key!r}. The packet's shape "
                        f"changed; the rail will not publish a partial card.")
    return value


def _percent(group_id, probability, where):
    """A probability in [0, 1] as the whole-number percent the card prints.

    Rounded here so the browser never divides. +0.5 rather than round(), which
    is banker's rounding in Python: round(0.125 * 100) is 12, and a card
    reading 12% for a 12.5% chance is a rounding rule nobody asked for."""
    if not isinstance(probability, (int, float)) or isinstance(probability, bool):
        _fail(group_id, f"{where} p_beat_line is {probability!r}, not a number.")
    if not 0.0 <= probability <= 1.0:
        _fail(group_id, f"{where} p_beat_line is {probability!r}, outside [0, 1] "
                        f"-- that is not a probability and would render as a "
                        f"nonsense percentage.")
    return f"{int(probability * 100 + 0.5)}%"


def build_rail(packet):
    """The published shape, or None when the packet supports no card at all."""
    group_id = packet.get("group_id")
    doc = {
        "$note": [
            "The column page's data rail. DERIVED -- every value is copied out",
            "of the week packet by scripts/build_rail.py, which computes",
            "nothing and re-selects nothing. Regenerable with no network.",
            "collision.implied_expected_wins is the model's win total for the",
            "collided team -- the dispute card's footer strip. Absent, the",
            "strip is omitted and the card still renders.",
            "p_beat_line is a RENDERED PERCENT STRING, not a probability: the",
            "page is not allowed to do arithmetic, so the rounding happens in",
            "Python. line / expected_final_wins / expected_delta are the",
            "packet's own decimals, verbatim.",
            "featured_pick.card_title is the LITERAL card heading, written by",
            "whichever builder selected the pick, because preseason and",
            "in-season answer different selection questions with the same",
            "fields. Consumers print it verbatim and must not infer a title",
            "from `week` or any other field; absent, fall back to the",
            "preseason wording rather than dropping the card.",
            "A missing `collision` or `featured_pick` key is an ORDINARY state",
            "(no collision this week / no pick selected) and every consumer",
            "must drop that card rather than render a placeholder. A 404 on",
            "this whole file is equally ordinary -- it is what every group",
            "without a rail serves.",
        ],
        "$version": 1,
        "group_id": group_id,
        "week": packet.get("week"),
        "generated_at": packet.get("generated_at"),
    }

    # THE DISPUTE. collisions[] is already ordered by the packet; [0] is the one
    # the column leads with. Taking any other index would put a different team
    # in the rail than the one the prose is about.
    collisions = packet.get("collisions") or []
    if collisions:
        c = collisions[0]
        where = f"collisions[0] ({c.get('team')})"
        sides = c.get("sides") or []
        if len(sides) < 2:
            _fail(group_id, f"{where} has {len(sides)} side(s); a collision is "
                            f"two managers on opposite sides by definition.")
        doc["collision"] = {
            "team": _need(group_id, c, "team", where),
            "line": _need(group_id, c, "line", where),
            # The card's footer strip. _need, not .get: a collision block that
            # has lost this is a packet-shape change and should stop the run,
            # not quietly publish a card with its footer missing. The page
            # omits the strip when the key is absent, which covers a rail.json
            # written before this field existed.
            "implied_expected_wins": _need(group_id, c, "implied_expected_wins",
                                           where),
            "picks": [
                {
                    # `name` is the display name the packet already resolved
                    # off config.json. manager_id is not published: the card
                    # prints a person, and a second id in the payload is a
                    # second thing that can disagree with the first.
                    "manager": _need(group_id, s, "name", f"{where} side {i}"),
                    "direction": _need(group_id, s, "direction", f"{where} side {i}"),
                    "p_beat_line": _percent(
                        group_id, _need(group_id, s, "p_beat_line", f"{where} side {i}"),
                        f"{where} side {i}"),
                }
                for i, s in enumerate(sides[:2])
            ],
        }

    # THE FEATURED PICK. The packet's coda selection, verbatim. Its
    # `implied_expected_wins` / `market_gap` are the same two numbers
    # projection.json calls expected_final_wins / expected_delta -- the packet
    # carries them at three decimals where projection.json rounds to two, so
    # the packet's copy is the one published.
    w = packet.get("worst_pick_on_the_board")
    if w:
        where = "worst_pick_on_the_board"
        doc["featured_pick"] = {
            # THE CARD'S HEAD, copied like everything else here. Both builders
            # write card_title into the packet because each one -- and only
            # each one -- knows which coda it selected: preseason's "the pick
            # the model likes least" and in-season's "the pick that died
            # ugliest" are different questions wearing the same fields, and the
            # card was titled the same in both, so week 1 swapped its subject
            # under an unchanged heading.
            #
            # NAMED FOR THE PACKET'S KEY, on the same provenance rule
            # p_beat_line follows. _need, not .get: both builders emit it, so
            # an absent one is a packet-shape change and stops the run (fail
            # loud in Python). svp.html falls back to the preseason wording for
            # a rail.json written before this field existed -- fail soft at
            # render, and that fallback is on PRESENCE, never on week.
            "card_title": _need(group_id, w, "card_title", where),
            "manager": _need(group_id, w, "name", where),
            "team": _need(group_id, w, "team", where),
            "direction": _need(group_id, w, "direction", where),
            "line": _need(group_id, w, "line", where),
            "expected_final_wins": _need(group_id, w, "implied_expected_wins", where),
            "expected_delta": _need(group_id, w, "market_gap", where),
        }

    if "collision" not in doc and "featured_pick" not in doc:
        return None
    return doc


def write_rail(packet):
    """Publish, or clear. Returns the path written, or None.

    Called by both packet builders immediately after they write their own
    packet, so the rail cannot describe a week the packet does not.
    """
    group_id = packet.get("group_id")
    if not group_id:
        return None
    if not is_enabled(group_id):
        return None

    path = rail_path(group_id)
    doc = build_rail(packet)

    if doc is None:
        # Nothing to show. Clear rather than leave: a rail.json from an earlier
        # week is a card that reads as this week's and is not (playbook rule 5
        # in reverse -- the danger here is staleness, not an empty overwrite).
        if path.exists():
            path.unlink()
            print(f"  [{group_id}] rail.json REMOVED (this packet carries no "
                  f"collision and no featured pick; a stale card would read as "
                  f"current)")
        return None

    utils.save_json_atomic(path, doc)
    cards = [k for k in ("collision", "featured_pick") if k in doc]
    print(f"  [{group_id}] rail.json ({', '.join(cards)})")
    return path
