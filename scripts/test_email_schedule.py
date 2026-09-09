#!/usr/bin/env python3
"""
test_email_schedule.py — the weekly email's SCHEDULE, in both places it lives.

The Sunday recap email is scheduled twice over: once as a cron line in
.github/workflows/update-data.yml, and once as should_send.SEND_HOUR_UTC, the
floor the gate enforces. They are two encodings of ONE decision (4am ET Sunday),
and nothing but this file makes them agree.

Why that deserves a test of its own: the drift is silent in the direction that
actually happens. Move the cron to 09:00 and forget the constant and every run
still goes green — it fetches, scores, files the column, deploys, and then the
gate says "before 08:00 UTC" and skips. A skip is the NORMAL outcome of that
gate (most runs skip), so the log reads healthy and the email simply never
arrives again. Nobody notices until someone asks why they stopped getting it.
Moving the constant without the cron fails the same way, one hour later.

The second pair is the email WINDOW: the workflow decides whether a run may
email at all by string-comparing github.event.schedule against the Sunday cron.
Change the cron alone and that comparison never matches — same silent death.

So four things are checked here:
  1. Both crons exist — the Sunday email pass AND the daily data refresh.
  2. The Sunday cron's hour == SEND_HOUR_UTC.
  3. The workflow's email-window comparison names that same cron string, and the
     email steps are gated on it, ordered after the deploy, and fail-soft in the
     right places (payload/send yes, state commit no).
  4. The gate's own hour boundary: 07:59 refuses, 08:00 does not.

Fixtures are synthetic payloads in a tempdir and a synthetic recipient map in
the environment — never the real overlay, and never written into the files
production reads (CLAUDE.md P2 #14). Both are restored afterwards, because this
process is shared with every other test file (CLAUDE.md P3 #21).

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_email_schedule.py
    python scripts/test_email_schedule.py
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import should_send as ss
import utils
from recipients import email_enabled

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "update-data.yml"

# The one decision, written once. Everything below asserts that the workflow and
# should_send.py both still say this and nothing else.
SUNDAY_CRON = "0 8 * * 0"
DAILY_CRON = "0 13 * * *"

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def cron_lines(text):
    """Every `- cron: "..."` value in the workflow, in file order."""
    return re.findall(r'^\s*-\s*cron:\s*["\']([^"\']+)["\']', text, re.MULTILINE)


def step_block(text, step_name):
    """The YAML text of one named step, from its `- name:` to the next one."""
    start = text.find(f"- name: {step_name}")
    if start == -1:
        return None
    nxt = text.find("\n      - name: ", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


def test_cron_lines():
    """Both schedules present: the email pass and the daily refresh it rides on."""
    print("\nupdate-data.yml — cron lines")
    crons = cron_lines(workflow_text())
    check("workflow declares at least one cron", bool(crons), f"found {crons}")
    check(f"the weekly email cron {SUNDAY_CRON!r} is declared",
          SUNDAY_CRON in crons,
          "Sunday 08:00 UTC = 4am ET; without it nothing ever emails")
    check(f"the daily data cron {DAILY_CRON!r} survives",
          DAILY_CRON in crons,
          "the site's Tue-Fri refresh — the email cron was ADDED alongside it, "
          "not swapped in")


def test_cron_hour_matches_the_gate():
    """THE drift guard. Two encodings of one hour; a mismatch kills the email silently."""
    print("\ncron hour <-> should_send.SEND_HOUR_UTC")
    crons = cron_lines(workflow_text())
    sunday = [c for c in crons if c.split()[-1] == "0"]
    check("exactly one Sunday-only cron exists", len(sunday) == 1, f"{sunday}")
    if len(sunday) != 1:
        return

    fields = sunday[0].split()
    minute, hour = fields[0], fields[1]
    check("the Sunday cron fires on the hour", minute == "0", f"minute={minute}")
    check("the Sunday cron hour equals SEND_HOUR_UTC",
          hour.isdigit() and int(hour) == ss.SEND_HOUR_UTC,
          f"cron says {hour}, should_send.SEND_HOUR_UTC says {ss.SEND_HOUR_UTC} — "
          "move BOTH or every Sunday run refuses on time and no email is ever sent")
    check("SEND_HOUR_UTC is a real UTC hour", 0 <= ss.SEND_HOUR_UTC <= 23,
          f"{ss.SEND_HOUR_UTC}")


def test_email_window_and_step_wiring():
    """The window comparison, the gating, the ordering, and where fail-soft belongs."""
    print("\nupdate-data.yml — email step wiring")
    text = workflow_text()

    window = step_block(text, "Email window")
    check("an 'Email window' step exists", window is not None)
    if window:
        check("the window compares against the Sunday cron string",
              f'"{SUNDAY_CRON}"' in window or f"'{SUNDAY_CRON}'" in window,
              "it string-matches github.event.schedule; change the cron without "
              "this and the window never opens")
        check("manual dispatch also opens the window",
              "workflow_dispatch" in window,
              "the recovery path for a missed or failed Sunday")

    for name, must_be_soft in (("Build email payloads", True),
                               ("Send the weekly email", True),
                               ("Commit email send state", False)):
        block = step_block(text, name)
        check(f"step {name!r} exists", block is not None)
        if not block:
            continue
        soft = "continue-on-error: true" in block
        check(f"{name!r} continue-on-error is {str(must_be_soft).lower()}",
              soft == must_be_soft,
              "payload/send must never redden a run that already committed and "
              "deployed; the state commit MUST be loud — a lost marker means a "
              "re-run emails the group twice")

    payload = step_block(text, "Build email payloads")
    send = step_block(text, "Send the weekly email")
    if payload:
        check("the payload step is gated on the email window",
              "steps.emailwindow.outputs.run == 'true'" in payload)
        check("no group id literal in the payload step",
              not re.search(r"\b(panel|browns|church|family)\b", payload),
              "enabled groups are read from config at runtime, so flipping "
              "email_enabled is the only edit needed to go live")
    if send:
        check("the send step is gated on the email window",
              "steps.emailwindow.outputs.run == 'true'" in send)
        check("the send step calls should_send.py before send_email.py",
              "should_send.py" in send and "send_email.py" in send
              and send.index("should_send.py") < send.index("send_email.py"),
              "one place decides")
        for secret in ("RESEND_API_KEY", "RECIPIENTS_JSON"):
            check(f"the send step is passed {secret}", secret in send,
                  "without it every enabled group fails its gate")

    # Ordering: the email is the last thing that happens, so no failure in it can
    # cost the site its data commit or its deploy.
    deploy_at = text.find("- name: Deploy to GitHub Pages")
    payload_at = text.find("- name: Build email payloads")
    commit_at = text.find("- name: Commit and push")
    check("the email steps run after the Pages deploy",
          -1 not in (deploy_at, payload_at) and deploy_at < payload_at,
          "rule 3: the least important artifact, with the only outside "
          "dependency, goes last")
    check("the email steps run after the data commit",
          -1 not in (commit_at, payload_at) and commit_at < payload_at,
          "the payload wraps the column this run just filed and committed")


def _first_enabled_group():
    for g in utils.get_all_group_ids():
        if email_enabled(utils.load_group_config(g)):
            return g
    return None


def _first_dark_group():
    for g in utils.get_all_group_ids():
        if not email_enabled(utils.load_group_config(g)):
            return g
    return None


def test_gate_hour_boundary():
    """07:59 refuses, 08:00 does not — against a real enabled group's config."""
    print("\nshould_send gate — the hour boundary")
    group = _first_enabled_group()
    check("some group has email_enabled: true", group is not None,
          "with all four dark this boundary is untestable end-to-end")
    if not group:
        return

    cfg = utils.load_group_config(group)
    # A synthetic roster keyed by the group's REAL manager_ids, so recipients
    # resolve completely without reading the gitignored overlay or naming one
    # real address. Restored below — this env var is process-wide.
    fake_map = {group: {m["manager_id"]: f"{m['manager_id']}@example.invalid"
                        for m in cfg.get("managers", [])}}
    saved_env = os.environ.get("RECIPIENTS_JSON")
    saved_dir = ss.EMAIL_DIR

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        payload = tmp / f"payload_{group}.json"
        payload.write_text(json.dumps({"meta": {"week": 99}}), encoding="utf-8")
        try:
            os.environ["RECIPIENTS_JSON"] = json.dumps(fake_map)
            ss.EMAIL_DIR = tmp          # no state file here: first-send state

            def at(hh, mm=0):
                return ss.decide(group, datetime(2026, 9, 13, hh, mm, tzinfo=timezone.utc),
                                 payload)

            ok, why = at(ss.SEND_HOUR_UTC - 1, 59)
            check("one minute before the hour: refuses", not ok, why)
            check("...and says WHY it refused (the hour, not something else)",
                  "UTC" in why and "before" in why, why)

            ok, why = at(ss.SEND_HOUR_UTC, 0)
            check("on the hour: sends", ok, why)
            check("...and reports the recipient count, not addresses",
                  "@" not in why, why)

            ok, why = at(23, 59)
            check("later the same day: still sends", ok, why)

            # Cadence: the same week, twice, is one email.
            state = tmp / "state" / group / "last_send.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"week": 99}), encoding="utf-8")
            ok, why = at(ss.SEND_HOUR_UTC, 30)
            check("a week already sent: refuses even past the hour", not ok, why)

            state.write_text(json.dumps({"week": 98}), encoding="utf-8")
            ok, why = at(ss.SEND_HOUR_UTC, 30)
            check("a newer week than the last send: sends", ok, why)
        finally:
            ss.EMAIL_DIR = saved_dir
            if saved_env is None:
                os.environ.pop("RECIPIENTS_JSON", None)
            else:
                os.environ["RECIPIENTS_JSON"] = saved_env


def test_kill_switch_outranks_the_clock():
    """A dark group is dark at every hour — the flag is checked first, always."""
    print("\nshould_send gate — email_enabled outranks the schedule")
    group = _first_dark_group()
    check("some group has email_enabled: false", group is not None,
          "browns/church/family are dark until Zach flips them")
    if not group:
        return

    for hh in (0, ss.SEND_HOUR_UTC, 23):
        ok, why = ss.decide(group, datetime(2026, 9, 13, hh, 0, tzinfo=timezone.utc))
        check(f"{group} stays dark at {hh:02d}:00 UTC", not ok, why)


def main():
    for fn in (test_cron_lines, test_cron_hour_matches_the_gate,
               test_email_window_and_step_wiring, test_gate_hour_boundary,
               test_kill_switch_outranks_the_clock):
        fn()
    passed = sum(1 for _, ok, _ in _res if ok)
    print(f"\nRESULT: {passed}/{len(_res)} checks passed")
    return 0 if passed == len(_res) else 1


if __name__ == "__main__":
    raise SystemExit(main())
