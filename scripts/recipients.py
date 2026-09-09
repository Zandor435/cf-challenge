#!/usr/bin/env python3
"""
recipients.py — Resolve a group's email recipients (ARCHITECTURE §5, §8 CLONE).

Job: turn a group_id into the list of addresses its weekly email goes to.

WHY THIS FILE EXISTS AT ALL. `groups/<id>/config.json` is committed to a PUBLIC
repo, so it carries only `manager_id` + `display_name`. The 25 real addresses for
the family, church and poker groups live OUTSIDE git, in one of two places:

  1. `groups/<id>/recipients.json`  — gitignored local overlay (dev machine)
  2. `$RECIPIENTS_JSON`             — one secret holding every group (CI)

Both hold the same shape: manager_id -> email. The env var wins when set, so CI
never needs the files and a dev never needs the secret.

THE CONTRACT: FAIL LOUD, NEVER PARTIAL.
---------------------------------------
Every function here either returns a COMPLETE recipient list or raises
`RecipientsError`. There is deliberately no empty-list fallback, no
warning-and-continue, and no "send to whoever we could resolve" path. The failure
mode this guards against is a send that silently reaches nobody, or reaches 3 of
4 managers — both look like success in a CI log and are invisible until someone
mentions they never got the email. A hard exit is recoverable; a quiet partial
send is not.

So `load_recipients()` raises when, for a group with `email_enabled: true`:
  - no source is available (env unset AND overlay file missing)
  - the source is unreadable or not valid JSON
  - the group is absent from the map
  - ANY manager_id in config.json has no entry
  - an address is empty, still the literal "TODO", or malformed
  - two managers share an address (a duplicate double-sends)
  - the map names a manager_id the config does not have (stale drift)

PRIVACY NOTE: no exception message, log line or repr in this module ever contains
an address. Errors name `manager_id`s only. Exceptions surface in public CI logs,
so leaking there would defeat the whole point of the overlay.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    from utils import GROUPS_DIR, load_group_config
except ImportError:  # direct execution from the repo root
    from scripts.utils import GROUPS_DIR, load_group_config

ENV_VAR = "RECIPIENTS_JSON"
OVERLAY_NAME = "recipients.json"

# Deliberately permissive: this is a typo guard (empty, whitespace, a bare name, a
# stray "TODO"), not an RFC 5322 validator. Resend is the real authority on whether
# an address deliverable, and over-strict local regexes reject valid mail.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PLACEHOLDERS = {"todo", "tbd", "none", "null", ""}


class RecipientsError(RuntimeError):
    """Recipients could not be resolved COMPLETELY. Never catch this to continue."""


def _overlay_path(group_id: str) -> Path:
    return Path(GROUPS_DIR) / group_id / OVERLAY_NAME


def _load_source(group_id: str, env: dict) -> tuple[dict, str]:
    """Return (manager_id -> email, human-readable source label). Raises on any problem."""
    raw = env.get(ENV_VAR)
    if raw and raw.strip():
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RecipientsError(
                f"${ENV_VAR} is set but is not valid JSON ({e.msg} at line {e.lineno}). "
                f"Expected {{'<group_id>': {{'<manager_id>': '<email>'}}}}."
            ) from None
        if not isinstance(doc, dict):
            raise RecipientsError(f"${ENV_VAR} must be a JSON object, got {type(doc).__name__}.")
        if group_id not in doc:
            raise RecipientsError(
                f"${ENV_VAR} has no entry for group '{group_id}' "
                f"(groups present: {sorted(doc) or 'none'}). The secret must carry every "
                f"group whose email_enabled is true."
            )
        mapping = doc[group_id]
        label = f"${ENV_VAR}[{group_id}]"
    else:
        path = _overlay_path(group_id)
        if not path.exists():
            raise RecipientsError(
                f"No recipients for group '{group_id}': ${ENV_VAR} is unset and "
                f"{path} does not exist. This group has email_enabled: true, so there is "
                f"no safe fallback — create the overlay locally, or set the secret in CI."
            )
        try:
            mapping = json.loads(path.read_text(encoding="utf-8")).get("recipients")
        except json.JSONDecodeError as e:
            raise RecipientsError(f"{path} is not valid JSON ({e.msg} at line {e.lineno}).") from None
        except OSError as e:
            raise RecipientsError(f"{path} could not be read ({e.strerror}).") from None
        if mapping is None:
            raise RecipientsError(f"{path} has no top-level 'recipients' object.")
        label = str(path)

    if not isinstance(mapping, dict):
        raise RecipientsError(f"{label} must map manager_id -> email, got {type(mapping).__name__}.")
    return mapping, label


def load_recipients(group_id: str, config: dict | None = None, env: dict | None = None) -> list[str]:
    """Every address for `group_id`, ordered as config.json lists its managers.

    Raises RecipientsError unless the list is COMPLETE. Never returns [].
    """
    env = os.environ if env is None else env
    cfg = config if config is not None else load_group_config(group_id)

    managers = cfg.get("managers") or []
    if not managers:
        raise RecipientsError(f"Group '{group_id}' has no managers in config.json.")

    mapping, label = _load_source(group_id, env)

    missing, bad, out, seen = [], [], [], {}
    for m in managers:
        mid = m["manager_id"]
        addr = mapping.get(mid)
        if addr is None or str(addr).strip().lower() in _PLACEHOLDERS:
            missing.append(mid)
            continue
        addr = str(addr).strip()
        if not _EMAIL_RE.match(addr):
            bad.append(mid)  # never the address itself — this reaches public CI logs
            continue
        seen.setdefault(addr.lower(), []).append(mid)
        out.append(addr)

    if missing:
        raise RecipientsError(
            f"Group '{group_id}': {len(missing)} of {len(managers)} managers have no address "
            f"in {label} — {', '.join(sorted(missing))}. Refusing to send a partial email."
        )
    if bad:
        raise RecipientsError(
            f"Group '{group_id}': malformed address for {', '.join(sorted(bad))} in {label}. "
            f"(Value withheld — this message can reach a public CI log.)"
        )
    dupes = {a: ids for a, ids in seen.items() if len(ids) > 1}
    if dupes:
        raise RecipientsError(
            f"Group '{group_id}': one address is shared by "
            f"{'; '.join(', '.join(sorted(ids)) for ids in dupes.values())} in {label}. "
            f"A duplicate double-sends — give each manager their own address."
        )
    stale = sorted(set(mapping) - {m["manager_id"] for m in managers})
    if stale:
        raise RecipientsError(
            f"Group '{group_id}': {label} names manager_id(s) absent from config.json — "
            f"{', '.join(stale)}. Someone was removed from the roster but not the overlay; "
            f"reconcile before sending."
        )
    return out


def email_enabled(config: dict) -> bool:
    """The per-group kill switch. False means this group never sends, full stop."""
    return bool(config.get("email_enabled", False))


def preflight(group_id: str, config: dict | None = None, env: dict | None = None) -> tuple[bool, str]:
    """(ok, reason) — resolve recipients WITHOUT sending. For a CI dry-run gate.

    A disabled group is (True, "disabled...") — not sending is a correct outcome,
    not a failure. An ENABLED group that cannot resolve is (False, why).
    """
    cfg = config if config is not None else load_group_config(group_id)
    if not email_enabled(cfg):
        return True, f"{group_id}: email_enabled is false — not sending (ok)"
    try:
        n = len(load_recipients(group_id, cfg, env))
    except RecipientsError as e:
        return False, f"{group_id}: {e}"
    return True, f"{group_id}: {n} recipients resolved"


def main(argv=None):
    """Preflight every group. Exit 1 if any ENABLED group cannot resolve completely."""
    import argparse

    ap = argparse.ArgumentParser(description="Verify email recipients resolve for every group.")
    ap.add_argument("--group", action="append", help="limit to this group (repeatable)")
    args = ap.parse_args(argv)

    groups = args.group or sorted(
        d.name for d in Path(GROUPS_DIR).iterdir() if (d / "config.json").exists()
    )
    failed = False
    for gid in groups:
        ok, reason = preflight(gid)
        print(("  ok   " if ok else "  FAIL ") + reason)
        failed |= not ok
    if failed:
        print("\nAt least one enabled group cannot resolve a complete recipient list.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
