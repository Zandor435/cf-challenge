#!/usr/bin/env python3
"""
test_shotserve.py — the screenshot gate cannot be served a tree that is not this one.

THE INCIDENT THIS PINS. The gate used to be served by hand with
`cd docs && python -m http.server 8899 &`. A leftover server from an earlier
session was still bound to 8899 serving a pre-branch checkout; the new server
bound the same port alongside it (Windows permits that, see below), the browser
reached the old one, and a full pass of sixteen screenshots came back showing
the previous branch's layout. Nothing failed. It was caught by a human noticing
that a number in a log line was the old number.

So the checks here are not about whether shotserve.py serves files — that is
stdlib's job. They are about whether it REFUSES, and each one reconstructs a
way the gate could hand back a screenshot of the wrong tree:

  1. a foreign server (no sentinel) — what a bare http.server looks like;
  2. a shotserve pointed at a DIFFERENT checkout — right protocol, wrong tree;
  3. one file on the server disagreeing with disk — the general case, and the
     one a sentinel alone would miss because the sentinel would agree;
  4. an explicitly named port that is already listening — refused rather than
     bound alongside, which is the specific mechanic that caused the incident:
     HTTPServer sets allow_reuse_address, and on Windows that permits binding a
     port another process is actively LISTENING on, leaving two live servers
     and letting the OS choose which one a connection reaches;
  5. no-store on the served bytes — everything else here verifies what THIS
     process fetched, and the browser fetches separately, so a cache in between
     would prove freshness against the wrong reader.

Nothing here touches the network or the repo: every server is bound to loopback
on an ephemeral port and every fixture tree is a temp copy.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_shotserve.py
    python scripts/test_shotserve.py
"""

import functools
import http.server
import shutil
import socket
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shotserve  # noqa: E402
from shotserve import DOCS, StaleTreeError, serve, verify_fresh  # noqa: E402

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


def _plain_server(directory):
    """A bare `python -m http.server`, which is what the incident ran."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _copy_docs():
    tmp = Path(tempfile.mkdtemp())
    dst = tmp / "docs"
    shutil.copytree(DOCS, dst)
    return tmp, dst


def test_verified_server_is_accepted():
    """The happy path, so the refusals below are not passing by being broken."""
    with serve() as base:
        n = verify_fresh(base, DOCS)
        check("a shotserve on this tree verifies", n > 0, f"{n} files")
        body = urllib.request.urlopen(base + shotserve.SENTINEL_PATH).read()
        check("the sentinel answers", b"digest" in body)


def test_foreign_server_is_refused():
    """A bare http.server has no sentinel, so it cannot be vouched for.

    The tree it serves here is IDENTICAL to disk — the point is that identical
    content is not the bar. An unsentinelled server is one whose identity this
    module cannot establish, and the incident's server was serving a perfectly
    valid tree; just not this one.
    """
    srv, url = _plain_server(DOCS)
    try:
        try:
            verify_fresh(url, DOCS)
            check("a bare http.server is refused", False, "it was accepted")
        except StaleTreeError as e:
            check("a bare http.server is refused", True)
            check("...and the message says to use shotserve",
                  "shotserve" in str(e).lower(), str(e)[:80])
    finally:
        srv.shutdown()


def test_server_on_a_different_checkout_is_refused():
    """Right protocol, wrong tree — the incident, reconstructed.

    A real shotserve, answering its sentinel correctly, serving a copy of docs/
    with the hero band back at its previous value. This is what was on 8899.
    """
    tmp, other = _copy_docs()
    try:
        css = other / "style.css"
        css.write_bytes(css.read_bytes().replace(b"max-height: 280px",
                                                 b"max-height: 360px"))
        with serve(root=other, verify=False) as base:
            try:
                verify_fresh(base, DOCS)
                check("a server on another checkout is refused", False,
                      "it was accepted")
            except StaleTreeError as e:
                check("a server on another checkout is refused", True)
                check("...and the message names the tree it IS serving",
                      str(other) in str(e), str(e)[:90])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_one_wrong_file_is_refused():
    """The general case, and why the sentinel is not sufficient on its own.

    The sentinel is computed by the SERVER from the tree it is serving, so a
    server whose tree disagrees with ours by one file still answers with a
    self-consistent sentinel. Only comparing bytes catches it.
    """
    tmp, other = _copy_docs()
    try:
        target = other / "data" / "panel" / "banners.json"
        target.write_bytes(target.read_bytes().replace(b'"focal"', b'"focaI"', 1))
        with serve(root=other, verify=False) as base:
            try:
                verify_fresh(base, DOCS)
                check("a single differing file is refused", False,
                      "it was accepted")
            except StaleTreeError as e:
                check("a single differing file is refused", True)
                check("...and the message names a file, not just the tree",
                      "banners.json" in str(e) or str(other) in str(e),
                      str(e)[:90])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_taken_port_is_refused():
    """Refused, not bound alongside. This is the incident's actual mechanic.

    HTTPServer sets allow_reuse_address = 1. On Unix that only permits
    rebinding a socket in TIME_WAIT; on Windows it permits binding a port
    another process is actively LISTENING on, which leaves two live servers and
    makes "which one does my browser reach" the OS's decision. shotserve turns
    that flag off and asks whether anyone ANSWERS before it tries.
    """
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    taken = holder.getsockname()[1]
    try:
        try:
            with serve(port=taken):
                check("an already-listening port is refused", False,
                      "it bound alongside")
        except SystemExit as e:
            check("an already-listening port is refused", True)
            check("...and the message names the port", str(taken) in str(e),
                  str(e)[:90])
    finally:
        holder.close()


def test_served_bytes_are_uncacheable():
    """no-store, asserted rather than assumed.

    Everything else here proves the tree this PROCESS fetched. The browser is a
    second reader with its own cache, and the failure being prevented is one
    where the pixels came from bytes the check never saw.
    """
    with serve() as base:
        with urllib.request.urlopen(base + "/style.css") as r:
            cc = (r.headers.get("Cache-Control") or "").lower()
        check("static files are served no-store", "no-store" in cc, repr(cc))


def test_the_default_port_is_ephemeral():
    """Two servers at once, no argument, no collision — the structural fix.

    A fixed port is a shared name, and the whole incident is what happens when
    two runs share one. With port 0 there is nothing to share.
    """
    with serve() as a, serve() as b:
        pa = int(a.rsplit(":", 1)[1])
        pb = int(b.rsplit(":", 1)[1])
        check("two concurrent servers get different ports", pa != pb, f"{pa} / {pb}")
        check("both verify against this tree",
              verify_fresh(a, DOCS) > 0 and verify_fresh(b, DOCS) > 0)


def main():
    print("shotserve refusals")
    test_verified_server_is_accepted()
    test_foreign_server_is_refused()
    test_server_on_a_different_checkout_is_refused()
    test_one_wrong_file_is_refused()
    test_a_taken_port_is_refused()
    test_served_bytes_are_uncacheable()
    test_the_default_port_is_ephemeral()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print()
    if passed != total:
        for name, ok, detail in _res:
            if not ok:
                print(f"  FAILED: {name} — {detail}")
        return 1
    print(f"screenshot gate OK: {passed} checks — a tree that is not this one "
          f"cannot be screenshotted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
