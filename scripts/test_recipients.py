#!/usr/bin/env python3
"""
test_recipients.py — the fail-loud contract of scripts/recipients.py.

These tests exist for ONE failure mode: a weekly email that silently reaches
nobody, or reaches 3 of 4 managers. Both look like success in a CI log and stay
invisible until someone mentions they never got it. So every test below asserts
that a broken/incomplete source RAISES — not that it returns [] or a short list.

If you ever find yourself relaxing one of these into a warning, don't. The whole
design of the public-repo overlay depends on the loader refusing to guess.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recipients as R  # noqa: E402

CONFIG = {
    "group_id": "testgrp",
    "managers": [
        {"manager_id": "a", "display_name": "A"},
        {"manager_id": "b", "display_name": "B"},
        {"manager_id": "c", "display_name": "C"},
    ],
    "email_enabled": True,
}
GOOD = {"a": "a@example.com", "b": "b@example.com", "c": "c@example.com"}


def env_with(mapping, group="testgrp"):
    return {R.ENV_VAR: json.dumps({group: mapping})}


# --- the happy path -----------------------------------------------------------

def test_resolves_complete_list_in_config_order():
    got = R.load_recipients("testgrp", CONFIG, env_with(GOOD))
    assert got == ["a@example.com", "b@example.com", "c@example.com"]


def test_env_beats_overlay_file():
    """CI sets the secret and has no overlay files; the env must win outright."""
    got = R.load_recipients("testgrp", CONFIG, env_with(GOOD))
    assert len(got) == 3


# --- THE failure mode: partial sends ------------------------------------------

@pytest.mark.parametrize("missing", ["a", "b", "c"])
def test_one_missing_manager_raises_not_partial(missing):
    m = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(m))
    assert missing in str(e.value)
    assert "partial" in str(e.value).lower()


def test_todo_placeholder_counts_as_missing():
    m = dict(GOOD, b="TODO")
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(m))
    assert "b" in str(e.value)


@pytest.mark.parametrize("junk", ["", "   ", "none", "null", "TBD"])
def test_placeholder_variants_count_as_missing(junk):
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(dict(GOOD, b=junk)))
    assert "b" in str(e.value)


def test_empty_map_raises_rather_than_returning_empty_list():
    """The nobody-gets-it case. Must never be a quiet no-op."""
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with({}))
    assert "partial" in str(e.value).lower()


# --- unusable sources ---------------------------------------------------------

def test_no_source_at_all_raises():
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, {})
    assert "unset" in str(e.value)


def test_unparseable_env_raises():
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, {R.ENV_VAR: "{not json"})
    assert "not valid JSON" in str(e.value)


def test_group_absent_from_secret_raises():
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(GOOD, group="othergrp"))
    assert "no entry for group" in str(e.value)


def test_env_not_an_object_raises():
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, {R.ENV_VAR: '["a@example.com"]'})
    assert "must be a JSON object" in str(e.value)


def test_group_maps_to_a_list_not_an_object_raises():
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, {R.ENV_VAR: json.dumps({"testgrp": ["a@x.com"]})})
    assert "manager_id -> email" in str(e.value)


# --- data-quality guards ------------------------------------------------------

@pytest.mark.parametrize("bad", ["not-an-email", "a@", "@example.com", "a b@example.com", "a@example"])
def test_malformed_address_raises(bad):
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(dict(GOOD, b=bad)))
    assert "malformed" in str(e.value)


def test_duplicate_address_raises_because_it_double_sends():
    m = dict(GOOD, c=GOOD["a"])
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(m))
    assert "double-sends" in str(e.value)


def test_duplicate_detection_is_case_insensitive():
    m = dict(GOOD, c="A@EXAMPLE.COM")
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(m))
    assert "double-sends" in str(e.value)


def test_stale_manager_in_overlay_raises():
    """Someone left the roster but stayed in the overlay — reconcile, don't mail them."""
    m = dict(GOOD, zz="gone@example.com")
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", CONFIG, env_with(m))
    assert "absent from config" in str(e.value)


def test_group_with_no_managers_raises():
    cfg = dict(CONFIG, managers=[])
    with pytest.raises(R.RecipientsError) as e:
        R.load_recipients("testgrp", cfg, env_with(GOOD))
    assert "no managers" in str(e.value)


# --- privacy: errors reach PUBLIC ci logs -------------------------------------

def test_no_error_message_ever_contains_an_address():
    """The overlay exists so addresses stay out of the public tree; an exception
    string that echoes one would defeat it just as thoroughly."""
    cases = [
        (CONFIG, env_with(dict(GOOD, b="malformed-value"))),
        (CONFIG, env_with(dict(GOOD, c=GOOD["a"]))),
        (CONFIG, env_with(dict(GOOD, zz="leaked@secret.com"))),
    ]
    for cfg, env in cases:
        with pytest.raises(R.RecipientsError) as e:
            R.load_recipients("testgrp", cfg, env)
        msg = str(e.value)
        for addr in list(GOOD.values()) + ["malformed-value", "leaked@secret.com"]:
            assert addr not in msg, f"address leaked into error: {msg}"


# --- the kill switch ----------------------------------------------------------

def test_disabled_group_preflights_ok_without_any_source():
    """email_enabled: false is a correct outcome, not a failure — no source needed."""
    ok, reason = R.preflight("testgrp", dict(CONFIG, email_enabled=False), {})
    assert ok and "not sending" in reason


def test_enabled_group_with_no_source_preflights_as_failure():
    ok, reason = R.preflight("testgrp", CONFIG, {})
    assert not ok and "testgrp" in reason


def test_preflight_ok_for_a_complete_enabled_group():
    ok, reason = R.preflight("testgrp", CONFIG, env_with(GOOD))
    assert ok and "3 recipients" in reason


# --- the real repo ------------------------------------------------------------

def test_every_real_group_preflights():
    """Whatever is enabled right now must resolve completely, or CI fails here.

    TWO ENVIRONMENTS, TWO DIFFERENT QUESTIONS, AND THIS USED TO CONFLATE THEM.
    Addresses are deliberately kept out of a public repo, so a plain checkout
    has neither the overlay nor $RECIPIENTS_JSON. Asserting resolution there
    asks whether a SECRET IS PROVISIONED, which is a fact about the environment
    and not about this repo — and it made CI red on every run from the moment
    panel was flipped to email_enabled, with no code change able to fix it.

    So the environment decides which question is asked, and BOTH are real
    assertions — neither branch can pass by doing nothing:

      - source present  -> every enabled group must resolve COMPLETELY. Exactly
                           the check this test has always made.
      - no source at all -> every enabled group must REFUSE, and refuse with the
                           specific "no source" error. That is the property the
                           whole overlay design rests on, and it is worth
                           pinning in its own right: a loader that returned []
                           here would send to nobody and look fine doing it.

    What this is NOT is a skip. An enabled group that fails for any OTHER reason
    — a manager with no address, a malformed one, a duplicate, a stale
    manager_id — raises plain RecipientsError, not the subclass, and fails this
    test in either environment. Broken configuration is still caught in a bare
    public checkout; only "nobody handed this machine the addresses" is not
    treated as this repo's fault.
    """
    import glob
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    enabled = []
    for cfg_path in sorted(glob.glob(str(root / "groups" / "*" / "config.json"))):
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
        if R.email_enabled(cfg):
            enabled.append(cfg)

    provisioned = [c for c in enabled if R.source_available(c["group_id"])]
    unprovisioned = [c for c in enabled if not R.source_available(c["group_id"])]

    failures = []
    for cfg in provisioned:
        ok, reason = R.preflight(cfg["group_id"], cfg)
        if not ok:
            failures.append(reason)
    assert not failures, "enabled group(s) cannot resolve recipients:\n" + "\n".join(failures)

    for cfg in unprovisioned:
        gid = cfg["group_id"]
        # Must refuse, and refuse for THIS reason. A different RecipientsError
        # is a real fault and propagates; returning a list at all would be the
        # silent-partial-send bug and fails on the raises-check itself.
        with pytest.raises(R.RecipientsSourceMissing):
            R.load_recipients(gid, cfg)
        print(f"  [{gid}] no recipients source in this environment "
              f"(${R.ENV_VAR} unset, no overlay) — asserted the loader refuses. "
              f"Set the secret to assert full resolution instead.")

    # Never vacuous: a repo where nothing is enabled would silently assert
    # nothing at all, and this test would keep passing after email went dark.
    assert enabled, "no group has email_enabled: true — this test asserts nothing"
