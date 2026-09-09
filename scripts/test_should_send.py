#!/usr/bin/env python3
"""
test_should_send.py — the five conditions of the weekly email gate.

The gate is the one place that decides whether a run emails, so every step after
it keys off a single answer. These tests pin the two things most likely to go
wrong silently:

  1. The 08:00 UTC cutoff MUST stay aligned with the workflow's Sunday cron. The
     cron fires at 08:00, 09:00 and 12:00 UTC; a cutoff above 08 makes the two
     4am ET windows dead and the email only ever goes at noon UTC. There is a
     test below that reads the cron out of the workflow and asserts the
     relationship directly, so raising one without the other fails here rather
     than in production silence.

  2. The repeat-week refusal is what makes three cron windows safe. Without it,
     a Sunday would send three emails.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import should_send as SS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SUNDAY = datetime(2026, 9, 13, tzinfo=timezone.utc)


def at(hour, minute=0):
    return SUNDAY.replace(hour=hour, minute=minute)


@pytest.fixture
def group(tmp_path, monkeypatch):
    """A synthetic enabled group with a week-1 payload and no prior send."""
    cfg = {"group_id": "testgrp", "email_enabled": True,
           "managers": [{"manager_id": "a", "display_name": "A"}]}
    monkeypatch.setattr(SS, "load_group_config", lambda gid: cfg)
    monkeypatch.setattr(SS, "load_recipients", lambda gid, c=None: ["a@example.com"])
    monkeypatch.setattr(SS, "EMAIL_DIR", tmp_path)

    payload = tmp_path / "payload_testgrp.json"
    payload.write_text(json.dumps({"meta": {"week": 1}}), encoding="utf-8")
    return tmp_path


# --- the 08:00 cutoff ---------------------------------------------------------

def test_cutoff_is_0800_utc():
    assert SS.SEND_HOUR_UTC == 8


@pytest.mark.parametrize("hour", [0, 3, 7])
def test_before_0800_refuses(group, hour):
    ok, reason = SS.decide("testgrp", at(hour))
    assert not ok and "before 08:00 UTC" in reason


def test_0759_refuses(group):
    ok, reason = SS.decide("testgrp", at(7, 59))
    assert not ok and "before 08:00 UTC" in reason


@pytest.mark.parametrize("hour", [8, 9, 12])
def test_every_cron_window_can_send(group, hour):
    """The three Sunday windows must all be live. This is the regression that
    lowering the cutoff from 13:00 existed to fix."""
    ok, reason = SS.decide("testgrp", at(hour))
    assert ok, f"{hour:02d}:00 UTC should be allowed to send, got: {reason}"


def test_0800_exactly_is_allowed(group):
    ok, reason = SS.decide("testgrp", at(8, 0))
    assert ok, reason


# --- the repeat-week refusal that makes 3 windows safe ------------------------

def test_second_window_refuses_after_the_first_sent(group):
    """08:00 sends; 09:00 and 12:00 must then refuse, or Sunday sends 3 emails."""
    assert SS.decide("testgrp", at(8))[0] is True
    state = group / "state" / "testgrp"
    state.mkdir(parents=True)
    (state / "last_send.json").write_text(
        json.dumps({"group_id": "testgrp", "week": 1}), encoding="utf-8")
    for hour in (9, 12):
        ok, reason = SS.decide("testgrp", at(hour))
        assert not ok, f"{hour:02d}:00 should refuse a repeat week"
        assert "already emailed" in reason


def test_a_newer_week_reopens_the_gate(group):
    state = group / "state" / "testgrp"
    state.mkdir(parents=True)
    (state / "last_send.json").write_text(
        json.dumps({"group_id": "testgrp", "week": 1}), encoding="utf-8")
    (group / "payload_testgrp.json").write_text(
        json.dumps({"meta": {"week": 2}}), encoding="utf-8")
    ok, reason = SS.decide("testgrp", at(8))
    assert ok and "week 2" in reason


def test_first_run_with_no_state_sends(group):
    ok, reason = SS.decide("testgrp", at(8))
    assert ok and "no prior send" in reason


# --- the other conditions (unchanged by this branch) --------------------------

def test_disabled_group_refuses(group, monkeypatch):
    monkeypatch.setattr(SS, "load_group_config",
                        lambda gid: {"group_id": "testgrp", "email_enabled": False,
                                     "managers": [{"manager_id": "a", "display_name": "A"}]})
    ok, reason = SS.decide("testgrp", at(12))
    assert not ok and "email_enabled is false" in reason


def test_missing_payload_refuses(group):
    (group / "payload_testgrp.json").unlink()
    ok, reason = SS.decide("testgrp", at(12))
    assert not ok and "no payload" in reason


def test_unresolvable_recipients_refuse(group, monkeypatch):
    def boom(gid, c=None):
        raise SS.RecipientsError("2 of 4 managers have no address")
    monkeypatch.setattr(SS, "load_recipients", boom)
    ok, reason = SS.decide("testgrp", at(12))
    assert not ok and "do not resolve" in reason


def test_payload_without_a_week_refuses(group):
    (group / "payload_testgrp.json").write_text(json.dumps({"meta": {}}), encoding="utf-8")
    ok, reason = SS.decide("testgrp", at(12))
    assert not ok and "no week" in reason


# --- the cutoff and the cron must agree --------------------------------------

def test_cutoff_matches_the_workflows_earliest_sunday_cron():
    """Reads the real workflow. If someone edits one side, this fails loudly
    rather than leaving a cron window that can never send."""
    wf = (ROOT / ".github" / "workflows" / "update-data.yml").read_text(encoding="utf-8")
    crons = re.findall(r'cron:\s*"([^"]+)"', wf)
    sunday = [c for c in crons if c.strip().endswith(" 0")]
    assert sunday, f"no Sunday cron found in update-data.yml (found: {crons})"
    hours = sorted({int(h) for c in sunday for h in c.split()[1].split(",")})
    assert hours, "Sunday cron carries no hours"
    assert min(hours) >= SS.SEND_HOUR_UTC, (
        f"earliest Sunday cron window is {min(hours):02d}:00 UTC but the gate "
        f"refuses before {SS.SEND_HOUR_UTC:02d}:00 — that window can never send")
