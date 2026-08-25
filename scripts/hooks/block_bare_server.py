#!/usr/bin/env python3
"""PreToolUse/Bash hook: refuse to start a static server outside shotserve.py.

WHY A HOOK AND NOT ONLY A TEST. The stale-tree incident was not caused by
anything committed to this repo. Nobody wrote `python -m http.server` into a
script or a workflow -- it was typed into a shell, interactively, during a
front-end pass, and a leftover instance of it from an earlier session was still
holding port 8899. A test that scans scripts/ and .github/ closes the door on
the habit being baked in, and would not have caught the thing that happened.
This closes the door it actually came through.

It denies, it does not warn. A warning on the exact command that produced a
whole pass of wrong screenshots is a warning that gets scrolled past, and the
correct alternative is one line away.

Reads the PreToolUse payload on stdin and answers with a permissionDecision.
Silence (exit 0, no output) is the normal case: every command that is not
starting a static file server is none of this hook's business.

THE FILENAME IS LOAD-BEARING and is asserted in test_server_ownership.py. It is
named here exactly as .claude/settings.json invokes it; a hook whose path does
not resolve is not a disabled hook, it is a hook that ERRORS on every Bash call
in the repo, which is how this file briefly came to be named twice.

FAIL OPEN, LOUDLY, IN BOTH HALVES. That error mode is not hypothetical: this
file went missing mid-task and every Bash call in the repo failed until it came
back. So neither half of the wiring may turn a problem with the GUARD into a
problem with the SHELL.

  * .claude/settings.json appends a `|| { ... }` fallback. It covers this file
    being absent or unreadable -- the interpreter never gets to run, so nothing
    written here could help. It warns on stderr, returns a systemMessage, allows.
  * main() below catches everything, so a bug in THIS file -- a bad regex, a
    payload shape nobody anticipated -- also allows rather than exiting non-zero.

Allowing is not going quiet, and the two are the whole design. Both paths say
the guard did not run, and scripts/test_server_ownership.py FAILS while the file
is missing or the registration is wrong -- so a guard that is off cannot be
mistaken for a guard that is on beyond the next test run.

The asymmetry is the argument: the worst case of allowing is one unguarded
command, and the worst case of erroring is a repo where nothing runs at all.

EVERY SHELL TOOL, NOT JUST BASH. main() has always accepted a PowerShell
payload, but the matcher in .claude/settings.json read "Bash", so this hook was
never invoked for one -- the same command that was denied through Bash ran
unguarded through PowerShell. That was not theoretical either: a session
recovered from the broken-hook state above by reaching for PowerShell, which is
how it surfaced. The matcher is "Bash|PowerShell" now, and
test_server_ownership.py asserts BOTH the matcher covers PowerShell and that a
PowerShell payload is actually denied -- the pair matters, because the matcher
alone is what silently regressed. A new shell tool would need adding to both.

THE REGISTRATION'S FALLBACK ASSUMES A POSIX SHELL (`||`, `{ ...; }`, `>&2`).
That is not a new dependency and it is not worth removing: the command already
expands $CLAUDE_PROJECT_DIR, which is POSIX-only too, so the whole line requires
sh/bash long before the fallback does. Under cmd.exe the hook would not resolve
its own path, fallback or no fallback. If this repo ever runs hooks under a
non-POSIX shell, the fix is the path expansion and the fallback together -- not
a second fallback bolted on for a machine that does not exist yet.
"""

import json
import re
import sys

# Each pattern is a way to serve a directory over HTTP. The list is not meant
# to be exhaustive against a determined author -- it is meant to cover what a
# person reaches for by reflex when they want to look at docs/ in a browser.
BLOCKED = [
    (r"\bpython[0-9.]*\s+-m\s+http\.server\b", "python -m http.server"),
    (r"\bpy\s+-m\s+http\.server\b", "py -m http.server"),
    (r"\b-m\s+SimpleHTTPServer\b", "python -m SimpleHTTPServer"),
    (r"\bphp\s+-S\b", "php -S"),
    (r"\bnpx\s+(-[^\s]+\s+)*serve\b", "npx serve"),
    (r"\bnpx\s+(-[^\s]+\s+)*http-server\b", "npx http-server"),
    (r"(?<!/)\bhttp-server\b(?!\.py)", "http-server"),
]

REASON = (
    "Blocked: `{what}` serves docs/ with nothing checking WHAT it serves.\n"
    "\n"
    "A leftover server on a fixed port once served a whole screenshot pass "
    "from a pre-branch checkout -- sixteen captures of the wrong tree, and "
    "nothing failed. Use the harness instead:\n"
    "\n"
    "    python scripts/shotserve.py            # prints a verified base URL\n"
    "\n"
    "or, from a Python Playwright script:\n"
    "\n"
    "    from shotserve import serve\n"
    "    with serve() as base: ...              # verified before it yields\n"
    "\n"
    "It takes a free port (so it cannot collide with a leftover) and proves "
    "every served file matches disk before handing back a URL. See the "
    "docstring in scripts/shotserve.py for the incident."
)


# Shells and runtimes that would actually EXECUTE a heredoc body, as opposed to
# consuming it as data. See strip_prose_heredocs().
INTERPRETERS = re.compile(
    r"(^|[|;&]|\$\()\s*(sudo\s+)?(python[0-9.]*|py|node|deno|bun|perl|ruby|php|"
    r"sh|bash|zsh|dash)\b[^<]*$")


def strip_prose_heredocs(command: str) -> str:
    """Drop heredoc bodies that are DATA; keep the ones that are CODE.

    The first false positive this hook produced was a commit message about the
    hook. A `git commit -F - <<MSG ... MSG` body describing the blocked command
    is prose, and blocking prose makes the guard unwritable-about -- which is a
    real cost, because the reason this rule exists only survives if people can
    describe it. Bodies fed to git, cat, tee or jq are input, and input cannot
    start a server.

    A body fed to an INTERPRETER is a different animal: `python <<PY` runs
    whatever is inside it. Those stay in the text being matched. That
    distinction is the entire reason this is not simply "ignore heredocs",
    which would be a bypass wearing a convenience's clothes.
    """
    out, i = [], 0
    for m in re.finditer(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", command):
        head = command[i:m.end()]
        body_start = command.find("\n", m.end())
        if body_start < 0:
            break
        end = re.search(rf"^\s*{re.escape(m.group(2))}\s*$",
                        command[body_start:], re.M)
        if not end:
            break
        stop = body_start + end.end()
        keep = bool(INTERPRETERS.search(command[i:m.start()]))
        out.append(head)
        out.append(command[body_start:stop] if keep else "\n")
        i = stop
    out.append(command[i:])
    return "".join(out)


def blocked_reason(command: str):
    """The human-readable name of the first blocked form found, or None."""
    command = strip_prose_heredocs(command)
    for pattern, what in BLOCKED:
        if re.search(pattern, command):
            return what
    return None


def _run() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # A payload this hook cannot read is not a reason to block a command.
        # Failing open here is deliberate: the test in test_server_ownership.py
        # is the half that cannot be dodged, and a hook that bricks every Bash
        # call because a schema changed would simply get switched off.
        return 0

    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    what = blocked_reason(command)
    if not what:
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(what=what),
        }
    }))
    return 0


def main() -> int:
    """Never raise, never exit non-zero. See FAIL OPEN in the module docstring.

    A PreToolUse hook that exits non-zero does not decline to answer -- it makes
    the tool call itself fail. For a guard that is one regex over one string,
    that trade is never worth taking.
    """
    try:
        return _run()
    except Exception as e:                      # noqa: BLE001 -- deliberate
        why = f"{type(e).__name__}: {e}"
        print(f"block_bare_server.py failed ({why}) -- the bare-static-server "
              f"guard is OFF for this command.", file=sys.stderr)
        print(json.dumps({"systemMessage":
                          f"The bare-static-server guard crashed ({why}) and did "
                          f"not check this command. It was allowed anyway so a "
                          f"broken hook cannot brick every Bash call. Fix "
                          f"scripts/hooks/block_bare_server.py."}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
