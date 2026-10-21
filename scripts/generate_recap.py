#!/usr/bin/env python3
"""
generate_recap.py — SVP writes the weekly recap email.

Job: hand the recap packet (build_recap_packet.py: every manager's standing and
week, plus next week's games that matter) to the pinned Van Pelt voice and file
what comes back for the Sunday email. Narrative only — no arithmetic here, and
the LLM never touches scoring (ARCHITECTURE §2).

  templates/svp_persona.md      the voice — the SAME pinned persona the column
                                uses (playbook P2 #16: one voice, all season)
  templates/svp_recap.md        the recap's job, which overrides the column's
                                structure: this piece IS the roundup

Writes (ACCUMULATE, one file per week, committed with groups/*/output/):

  groups/<g>/output/recap_week_<N>.json
      meta    status "filed" (SVP prose present) or "data_only"
      packet  the recap packet, frozen — the email renders its numbers, so the
              prose and the tables beside it can never come from two generations
      prose   {deck, open, managers{id: text}, games{game_key: text}, close} | null

SKIP-IF-EXISTS (playbook rule 7). A week already filed with prose is never
re-paid for; --force overrides. A data_only file IS retried on the next run, so
an OpenAI outage costs one week's prose at most, never the email: the email
renders the data_only packet without SVP's words.

FACTUAL GUARD. The prose goes through column_guard.check_column against the
packet (schedule_licensed=True, since this packet carries opponents). A
violation gets ONE rewrite with the offending sentences quoted back; a second
failure files data_only. Fail closed on prose, open on the email.

FAIL-SOFT IN LIVE MODE (playbook rule 3). Any failure warns and exits 0 — the
recap is garnish on top of standings that are already committed. --dry-run is
the opposite: it writes the prompt to recap_prompt_preview.txt, makes no call,
and is allowed to exit non-zero.

Usage:
    python scripts/generate_recap.py --group panel --dry-run
    OPENAI_API_KEY=... python scripts/generate_recap.py --group panel
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_recap_packet  # noqa: E402
import column_guard  # noqa: E402
import generate_commentary  # noqa: E402  (the OpenAI retry shell + model knob)

ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = ROOT / "templates" / "svp_persona.md"
RECAP_BRIEF_PATH = ROOT / "templates" / "svp_recap.md"

TEMPERATURE = 0.8
MAX_TOKENS = 1800          # an open, a close, ~8 manager blurbs, 5 game blurbs
REWRITES = 1               # one second chance after a guard rejection
DECK_MAX_CHARS = 160


class RecapProseError(ValueError):
    """The model's reply is not a usable recap. Message says why."""


def out_dir(group_id):
    return ROOT / "groups" / group_id / "output"


def recap_path(group_id, week):
    return out_dir(group_id) / f"recap_week_{week}.json"


def preview_path(group_id):
    return out_dir(group_id) / "recap_prompt_preview.txt"


def latest_recap(group_id):
    """(week, doc) of the newest filed recap, or (None, None)."""
    best = None
    for p in out_dir(group_id).glob("recap_week_*.json"):
        try:
            wk = int(p.stem.rsplit("_", 1)[1])
        except ValueError:
            continue
        if best is None or wk > best[0]:
            best = (wk, p)
    if best is None:
        return None, None
    with open(best[1], encoding="utf-8") as f:
        return best[0], json.load(f)


def build_prompt(packet):
    system = (PERSONA_PATH.read_text(encoding="utf-8").rstrip() + "\n\n---\n\n"
              + RECAP_BRIEF_PATH.read_text(encoding="utf-8"))
    user = ("Week " + str(packet["week"]) + " recap packet for "
            + packet["display_name"] + ". Return the JSON object described in "
            "your brief.\n\n" + json.dumps(packet, indent=2, ensure_ascii=False))
    return system, user


def parse_prose(reply, packet):
    """Validate the model's JSON against the packet's ids. Raises RecapProseError."""
    try:
        doc = json.loads(reply)
    except json.JSONDecodeError as e:
        raise RecapProseError(f"reply is not JSON ({e.msg})") from None
    if not isinstance(doc, dict):
        raise RecapProseError("reply is not a JSON object")

    def text(v, what):
        if not isinstance(v, str) or not v.strip():
            raise RecapProseError(f"{what} is missing or empty")
        return " ".join(v.split())

    want_m = [m["manager_id"] for m in packet["managers"]]
    got_m = doc.get("managers") if isinstance(doc.get("managers"), dict) else {}
    missing = [mid for mid in want_m if mid not in got_m]
    if missing:
        raise RecapProseError(f"no blurb for manager(s): {', '.join(missing)}")
    want_g = [g["game_key"] for g in packet["games_that_matter"]]
    got_g = doc.get("games") if isinstance(doc.get("games"), dict) else {}

    deck = text(doc.get("deck"), "deck")
    if len(deck) > DECK_MAX_CHARS:
        deck = deck[:DECK_MAX_CHARS - 1].rstrip(" ,.;:") + "…"
    return {
        "deck": deck,
        "open": text(doc.get("open"), "open"),
        "managers": {mid: text(got_m[mid], f"managers.{mid}") for mid in want_m},
        # A missing game blurb is tolerable — the card still renders its data —
        # and an invented key is dropped rather than shown against no game.
        "games": {k: " ".join(got_g[k].split()) for k in want_g
                  if isinstance(got_g.get(k), str) and got_g[k].strip()},
        "close": " ".join(doc["close"].split()) if isinstance(doc.get("close"), str) else "",
    }


def prose_text(prose):
    parts = [prose["deck"], prose["open"], *prose["managers"].values(),
             *prose["games"].values(), prose.get("close") or ""]
    return "\n".join(p for p in parts if p)


def check(prose, packet):
    return column_guard.check_column(prose_text(prose), packet, schedule_licensed=True)


def write_doc(group_id, packet, prose, model=None):
    path = recap_path(group_id, packet["week"])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "meta": {
            "group_id": group_id,
            "season": packet.get("season"),
            "week": packet["week"],
            "status": "filed" if prose else "data_only",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": model if prose else None,
            "byline": "Scott Van Pelt",
        },
        "packet": packet,
        "prose": prose,
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def write_prose(packet, api_key, call=None):
    """(prose | None, reason). `call` is injectable for tests."""
    call = call or (lambda s, u: generate_commentary.call_openai(
        s, u, api_key, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
        response_format={"type": "json_object"}))
    system, user = build_prompt(packet)
    for attempt in range(REWRITES + 1):
        try:
            prose = parse_prose(call(system, user), packet)
        except RecapProseError as e:
            reason = f"unusable reply: {e}"
            user = user + f"\n\nYour last reply was rejected ({e}). Return the JSON object exactly as briefed."
            continue
        violations = check(prose, packet)
        if not violations:
            return prose, "filed"
        reason = f"{len(violations)} unsupported claim(s)"
        for v in violations:
            print(f"::warning:: [{packet['group_id']}] recap guard: {v.kind}: "
                  f"{v.detail}\n             in: {v.sentence}", file=sys.stderr)
        quoted = "\n".join(f"- {v.sentence} ({v.detail})" for v in violations)
        user = (user + "\n\nYour last draft was rejected by the factual guard. "
                "These sentences assert things the packet does not support:\n"
                + quoted + "\nRewrite the whole object without them.")
    return None, reason


def run(group_id, force=False, dry_run=False):
    packet = build_recap_packet.build(group_id)
    if dry_run:
        system, user = build_prompt(packet)
        p = preview_path(group_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("=== SYSTEM ===\n" + system + "\n\n=== USER ===\n" + user + "\n",
                     encoding="utf-8")
        print(f"[{group_id}] week {packet['week']} recap prompt -> {p}")
        return 0

    path = recap_path(group_id, packet["week"])
    if path.exists() and not force:
        with open(path, encoding="utf-8") as f:
            if (json.load(f).get("meta") or {}).get("status") == "filed":
                print(f"[{group_id}] week {packet['week']} recap already filed — skipping "
                      f"(--force to rewrite).")
                return 0

    try:
        import utils
        utils.load_env_file()
    except Exception:  # noqa: BLE001  — a dev convenience; CI sets the env var
        pass
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        prose, reason = None, "OPENAI_API_KEY not set"
    else:
        try:
            prose, reason = write_prose(packet, api_key)
        except Exception as e:  # noqa: BLE001  — fail soft: the email still goes
            prose, reason = None, f"OpenAI call failed: {type(e).__name__}: {e}"

    if not prose and path.exists():
        with open(path, encoding="utf-8") as f:
            if (json.load(f).get("meta") or {}).get("status") == "filed":
                # A failed --force rewrite must not downgrade filed prose.
                print(f"::warning:: [{group_id}] recap rewrite failed ({reason}); "
                      f"keeping the filed week {packet['week']} recap.", file=sys.stderr)
                return 0
    out = write_doc(group_id, packet, prose, model=generate_commentary.OPENAI_MODEL)
    if prose:
        print(f"[{group_id}] week {packet['week']} SVP recap filed -> {out}")
    else:
        print(f"::warning title=Recap prose missing::[{group_id}] week {packet['week']}: "
              f"{reason}. Filed data_only — the email still sends, without SVP's prose, "
              f"and the next run retries.", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--group", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="rewrite a week that is already filed")
    args = ap.parse_args(argv)
    if args.dry_run:
        return run(args.group, dry_run=True)
    try:
        return run(args.group, force=args.force)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        print(f"::warning title=Recap failed::[{args.group}] {type(e).__name__}: {e}. "
              f"No recap written this run; standings are unaffected (rule 3).",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
