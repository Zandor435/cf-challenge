#!/usr/bin/env python3
"""
test_server_ownership.py — shotserve.py is the only thing in this repo that serves docs/.

Companion to test_shotserve.py. That file proves the harness REFUSES a tree
that is not this one; this file proves nothing in the repo can quietly go
around it.

Two halves, because the bypass has two routes and they need different tools.

GUARD 1 — SOURCE. No Python under scripts/, and no workflow under .github/,
may stand up a static file server. For Python this walks the AST rather than
raw lines, for the same reason test_cache_access.py does: shotserve.py's own
docstring says `python -m http.server` eleven times while explaining what went
wrong, and a grep would flag every one of them. Only a real import or a real
call counts. Workflows have no AST, so those are scanned as text.

GUARD 2 — THE HOOK. The incident did not come from a committed file. Nobody
wrote a bare server into a script; it was typed into a shell during a
front-end pass, and a leftover instance of it from an earlier session was
still holding the port. Guard 1 would not have caught it. So the repo also
ships a PreToolUse hook that denies those commands outright, and this asserts
the hook is wired in .claude/settings.json and that its matcher still fires on
the exact command that caused the incident. A hook that silently stopped being
registered is the same failure as never having written it.

Runs both ways, and they are equivalent: pytest collects one test per guard
and conftest.py raises on any check() the guard recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_server_ownership.py
    python scripts/test_server_ownership.py
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
HOOK = SCRIPTS / "hooks" / "block_bare_server.py"
SETTINGS = ROOT / ".claude" / "settings.json"

# The one sanctioned server, and the one file allowed to build bare ones.
#
# shotserve.py IS the harness -- it necessarily imports http.server.
# test_shotserve.py stands up bare servers as FIXTURES, to prove the harness
# refuses them; a test that cannot construct the thing it rejects cannot test
# the rejection. Both are listed by name with the reason, rather than the whole
# directory being skipped, so a third file cannot join them by accident.
SERVER_OWNERS = {
    "shotserve.py": "the sanctioned harness",
    "test_shotserve.py": "builds bare servers as fixtures, to prove they are refused",
}

SERVER_MODULES = {"http.server", "SimpleHTTPServer", "socketserver"}
SERVER_CALLS = {"HTTPServer", "ThreadingHTTPServer", "SimpleHTTPRequestHandler",
                "TCPServer", "ThreadingTCPServer"}

# Text patterns for the surfaces with no AST. Kept in step with the hook's own
# list; the hook is the authority at runtime, this is the authority in review.
TEXT_PATTERNS = ["http.server", "SimpleHTTPServer", "php -S", "npx serve",
                 "http-server"]

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def _server_uses(tree):
    """(line, what) for every real import/construction of a static server."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in SERVER_MODULES:
                    hits.append((node.lineno, f"import {a.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module in SERVER_MODULES:
                hits.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Call):
            f = node.func
            name = (f.attr if isinstance(f, ast.Attribute)
                    else f.id if isinstance(f, ast.Name) else None)
            if name in SERVER_CALLS:
                hits.append((node.lineno, f"{name}(...)"))
    return hits


def test_no_script_serves_docs():
    """GUARD 1a: no Python under scripts/ stands up a static server."""
    scanned = 0
    for path in sorted(SCRIPTS.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned += 1
        if path.name in SERVER_OWNERS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError) as e:
            check(f"{path.name} parses", False, str(e))
            continue
        hits = _server_uses(tree)
        check(f"{path.relative_to(ROOT).as_posix()} stands up no static server",
              not hits,
              "; ".join(f"line {ln}: {what}" for ln, what in hits))
    check(f"scanned {scanned} scripts", scanned > 10, f"only {scanned} found")
    for owner, why in SERVER_OWNERS.items():
        check(f"{owner} is still present as the exception ({why})",
              (SCRIPTS / owner).is_file())


def test_no_workflow_serves_docs():
    """GUARD 1b: no CI workflow starts one either. Text-scanned; no AST."""
    wf_dir = ROOT / ".github" / "workflows"
    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    check("workflow directory is present", bool(files), str(wf_dir))
    for path in files:
        text = path.read_text(encoding="utf-8")
        hits = [p for p in TEXT_PATTERNS if p in text]
        check(f".github/workflows/{path.name} starts no static server",
              not hits, f"found {hits}")


def test_the_hook_is_wired():
    """GUARD 2: the interactive route is closed, and still registered."""
    if not check("the hook script exists", HOOK.is_file(), str(HOOK)):
        return
    if not check(".claude/settings.json exists", SETTINGS.is_file(), str(SETTINGS)):
        return
    try:
        cfg = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        # A malformed settings.json does not error -- it silently disables
        # every setting in the file, hook included. So it is a hard check.
        check(".claude/settings.json parses", False, str(e))
        return
    check(".claude/settings.json parses", True)

    entries = [e for e in (cfg.get("hooks", {}).get("PreToolUse") or [])
               if e.get("matcher") == "Bash"]
    cmds = [h.get("command", "") for e in entries for h in (e.get("hooks") or [])
            if h.get("type") == "command"]
    check("a PreToolUse/Bash command hook is registered", bool(cmds))
    check("...and it is block_bare_server.py",
          any("block_bare_server.py" in c for c in cmds), str(cmds))

    # FAIL OPEN. A PreToolUse hook that exits non-zero does not decline to
    # answer -- it fails the tool call. When this file went missing, every Bash
    # call in the repo errored until it came back, so the registration carries a
    # `||` fallback that warns and allows. Asserted on the command STRING
    # because the failure it covers is the interpreter never reaching our code:
    # there is nothing runnable to test at that point, only the wiring.
    #
    # This does not soften the two checks above. A missing file or a wrong path
    # still FAILS here -- fail-open is about the shell staying usable, never
    # about a broken wire looking fine.
    check("...and the registration falls open if that file is missing",
          all("||" in c for c in cmds if "block_bare_server.py" in c), str(cmds))


def test_the_hook_denies_the_incident_command():
    """GUARD 2, behaviourally: the exact command that caused it is denied.

    Run as a subprocess, the way the harness runs it, rather than by importing
    and calling blocked_reason(). What matters is that the process emits a deny
    on stdin/stdout -- an import-level test would pass even if the script's
    main() had been broken into something that never printed.
    """
    incident = "cd docs && python -m http.server 8899 &"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": incident}})
    proc = subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)
    check("the hook exits 0 (it answers, it does not crash)", proc.returncode == 0,
          proc.stderr[:200])
    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        check("the hook emits JSON", False, proc.stdout[:200])
        return
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision")
    check("the incident command is DENIED, not warned", decision == "deny",
          f"decision={decision!r}")
    reason = (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
    check("...and the denial names the alternative",
          "shotserve" in reason, reason[:120])

    # A hook that CRASHES must also allow. Forced by feeding it a payload whose
    # command is not a string, which every code path below the json.load treats
    # as text; whatever that raises, main() must swallow it and exit 0.
    p = subprocess.run(
        [sys.executable, str(HOOK)], text=True, capture_output=True, timeout=30,
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": {"a": 1}}}))
    check("a hook that cannot do its job still exits 0 (fails open)",
          p.returncode == 0, f"exit={p.returncode} stderr={p.stderr[:150]}")

    # And the commands this repo actually runs all day are untouched. A hook
    # that blocks real work gets disabled, and a disabled hook guards nothing.
    for benign in ("python -m pytest -q", "python scripts/shotserve.py",
                   "git status", "python scripts/build_banners.py --group panel"):
        p = subprocess.run(
            [sys.executable, str(HOOK)], text=True, capture_output=True, timeout=30,
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": benign}}))
        check(f"allowed: {benign}", p.stdout.strip() == "", p.stdout[:120])


def test_the_hook_reads_prose_as_prose():
    """A heredoc body is data or code, and the hook has to tell them apart.

    This is not hypothetical tidiness -- it is the first false positive the
    hook produced, on the commit message introducing it. `git commit -F - <<MSG`
    carrying a description of the blocked command is prose, and a guard that
    cannot be written about is a guard whose reason dies with the person who
    added it.

    The other direction matters more. `bash <<SH` and `python <<PY` RUN their
    bodies, so those must still be matched -- "ignore heredocs" would have been
    a bypass wearing a convenience's clothes, and this pins that it was not
    what got implemented.
    """
    sys.path.insert(0, str(HOOK.parent))
    from block_bare_server import blocked_reason

    srv = "http" + "." + "server"          # assembled so this file's own text
    serve_cmd = "python -m " + srv         # is not a specimen for GUARD 1

    cases = [
        ("git commit -F - <<'MSG'\nprose about " + serve_cmd + "\nMSG",
         None, "prose heredoc fed to git"),
        ("cat > note.txt <<'EOF'\n" + serve_cmd + "\nEOF",
         None, "data heredoc fed to cat"),
        ("bash <<'SH'\n" + serve_cmd + "\nSH",
         serve_cmd, "interpreter heredoc, which RUNS it"),
        ("git commit -F - <<'MSG'\nprose\nMSG\ncd docs && " + serve_cmd,
         serve_cmd, "prose heredoc followed by the real thing"),
        ("cd docs && " + serve_cmd + " 8899 &",
         serve_cmd, "the incident command"),
    ]
    for cmd, want, label in cases:
        got = blocked_reason(cmd)
        check(f"{label}: {'denied' if want else 'allowed'}", got == want,
              f"got {got!r}, wanted {want!r}")


def main():
    print("no script or workflow serves docs/")
    test_no_script_serves_docs()
    test_no_workflow_serves_docs()
    print("\nthe interactive route is closed")
    test_the_hook_is_wired()
    test_the_hook_denies_the_incident_command()
    test_the_hook_reads_prose_as_prose()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print()
    if passed != total:
        for name, ok, detail in _res:
            if not ok:
                print(f"  FAILED: {name} — {detail}")
        return 1
    print(f"server ownership OK: {passed} checks — shotserve.py is the only "
          f"way to serve docs/, in the repo and at the shell.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
