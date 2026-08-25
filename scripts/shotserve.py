#!/usr/bin/env python3
"""Serve docs/ for a screenshot run, and refuse to serve a tree that is not this one.

WHY THIS EXISTS — the incident, in full, because the fix only makes sense next
to it. The screenshot gate had no harness: every front-end pass served the site
by hand with `cd docs && python -m http.server 8899 &` and pointed a browser at
localhost:8899. During the 280px hero-band pass a leftover server from an
EARLIER session was still bound to 8899, serving a pre-branch checkout. The new
server bound the same port alongside it, the browser reached the old one, and
sixteen screenshots came back showing 360px bands and the previous focal values
-- the state of `main`, not the state on disk. Nothing failed. The run looked
clean. It was caught by eye, by noticing the numbers in a log line were the old
numbers, and only because they happened to be numbers somebody remembered.

Two things made that possible and both are closed here.

  1. A FIXED PORT can already be taken, and on Windows taking it twice is not
     even an error. HTTPServer sets allow_reuse_address = 1, and where SO_
     REUSEADDR on Unix only lets you rebind a socket in TIME_WAIT, on Windows
     it lets a second process bind a port another process is actively LISTENING
     on. Both sockets stay open, the OS routes new connections to one of them,
     and which one you get is not something your script decides. netstat during
     the incident showed exactly that: two listeners on 8899, one per checkout.

     So: the default port is 0 -- the OS hands out a free one and the URL is
     returned to the caller, which cannot collide with anything. And when a
     port IS named explicitly, allow_reuse_address is turned OFF so binding a
     taken one raises instead of silently doubling up.

  2. NOTHING CHECKED WHAT CAME BACK. A screenshot harness reads pixels; it has
     no opinion about whether the bytes behind them are the bytes you just
     edited. So before this module hands back a URL it fetches the whole text
     surface of the site over HTTP -- every .css, .js, .html, .json, .svg and
     .md under docs/, a few dozen files and under a megabyte, milliseconds on
     loopback -- and compares each one byte-for-byte against the file on disk.
     One mismatch is a hard stop naming the file.

     It also serves a sentinel at /__shotserve__ carrying this process's docs
     root and a digest of the tree, and REQUIRES it. That is what makes a
     foreign server fail immediately and unmistakably rather than subtly: a
     stale `python -m http.server` does not answer that path at all, so the
     answer is a 404 and the message says why, instead of a diff on some file
     that happens to differ while the rest match.

The two guards are deliberately different in kind. The port rule stops you
reaching the wrong server; the freshness check stops you trusting one you did
reach. Either alone would have caught this incident. Both together also catch
the ones where you serve the right port from the wrong directory, or edit a
file after the server started and forget the browser cached it.

USAGE — as a context manager, for a Python Playwright script:

    from shotserve import serve
    with serve() as base:                     # verified before it yields
        page.goto(f"{base}/index.html?group=panel")

USAGE — as a CLI, for the Playwright MCP flow where the browser is driven from
outside this process. Prints the verified base URL and stays up until Ctrl-C:

    python scripts/shotserve.py
    python scripts/shotserve.py --port 8899   # fails loudly if 8899 is taken

USAGE — to audit a server somebody else started:

    python scripts/shotserve.py --verify-url http://127.0.0.1:8899

    This will FAIL against a plain `python -m http.server`, and that is the
    intended answer, not a limitation: an unsentinelled server is one this
    module cannot vouch for, which is the entire class of thing that went
    wrong. Start it through here instead.

Playbook compliance (CLAUDE.md): rule 4 -- a mismatch fails loud and names the
file rather than degrading into a screenshot nobody can trust. No network, no
paid API, nothing written to the repo.
"""

import argparse
import hashlib
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

SENTINEL_PATH = "/__shotserve__"

# The text surface: everything served whose CONTENT decides what a screenshot
# looks like. Images are excluded on purpose -- they are large, they are
# content-addressed by the manifest that IS checked, and a stale tree is caught
# by any one of these long before it would be caught by a .webp.
VERIFY_SUFFIXES = {".css", ".js", ".html", ".json", ".svg", ".md"}

TIMEOUT = 10


class StaleTreeError(RuntimeError):
    """The server answering is not serving the tree on disk."""


def verifiable_files(root: Path):
    """docs-relative POSIX paths of every file whose bytes are checked."""
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VERIFY_SUFFIXES
    )


def tree_digest(root: Path) -> str:
    """One digest over (path, content) for the whole verifiable surface.

    Cheap identity for the sentinel. It is NOT a substitute for the per-file
    compare below -- this is computed from disk on both sides of the same
    process, so on its own it would only prove the server agrees with itself.
    Its job is to make two shotserve instances on different checkouts
    distinguishable at a glance.
    """
    h = hashlib.sha256()
    for rel in verifiable_files(root):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256((root / rel).read_bytes()).digest())
    return h.hexdigest()


class _Handler(SimpleHTTPRequestHandler):
    """Static files, plus the sentinel, minus the request log."""

    def do_GET(self):                                   # noqa: N802 -- stdlib API
        if self.path.split("?")[0] == SENTINEL_PATH:
            # Computed per REQUEST, from disk, not frozen at startup. A digest
            # captured when the server booted describes a tree that may since
            # have been edited, and a stale answer from the very endpoint whose
            # job is detecting staleness is the worst possible shape for this
            # bug to take. Hashing the surface costs about a megabyte of reads
            # and this path is hit once or twice a run.
            root = Path(self.directory).resolve()
            body = json.dumps({
                "root": str(root),
                "digest": tree_digest(root),
                "files": len(verifiable_files(root)),
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        # Same reason, for every static file: the freshness check compares the
        # bytes it FETCHED, so a cache between here and the browser would let
        # the browser render something the check never saw.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):                          # noqa: A003 -- stdlib API
        pass


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True if nothing is LISTENING on host:port.

    Asked by connecting, not by binding. Binding is exactly the test that lies
    on Windows -- with allow_reuse_address a bind against a live listener
    SUCCEEDS -- so the question has to be "does anyone answer" instead.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) != 0


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def verify_fresh(base: str, root: Path = DOCS, expect_digest: str = None) -> int:
    """Assert the server at `base` is serving `root` exactly. Returns file count.

    Raises StaleTreeError, naming what disagreed. Checks in escalating order so
    the message is the most useful one available: is it even a shotserve, is it
    OUR shotserve, and only then which file differs.
    """
    base = base.rstrip("/")

    try:
        raw = _fetch(base + SENTINEL_PATH)
    except urllib.error.HTTPError as e:
        raise StaleTreeError(
            f"{base}{SENTINEL_PATH} returned {e.code}. Whatever is on this port "
            f"was not started by shotserve.py, so its tree cannot be vouched "
            f"for -- and an unvouched server is the exact failure this check "
            f"exists to stop. Stop it and use `python scripts/shotserve.py`."
        ) from None
    except OSError as e:
        raise StaleTreeError(f"cannot reach {base}: {e}") from None

    try:
        sent = json.loads(raw)
    except json.JSONDecodeError:
        raise StaleTreeError(f"{base}{SENTINEL_PATH} did not return JSON") from None

    served_root = Path(sent.get("root", ""))
    if served_root != root.resolve():
        raise StaleTreeError(
            f"{base} is serving {served_root} -- this checkout is "
            f"{root.resolve()}. That is a different tree, which is how sixteen "
            f"screenshots of the previous branch once passed for the new one."
        )
    if expect_digest and sent.get("digest") != expect_digest:
        raise StaleTreeError(
            f"{base} reports tree digest {sent.get('digest', '')[:12]}, "
            f"expected {expect_digest[:12]} -- the tree changed under the "
            f"running server."
        )

    files = verifiable_files(root)
    for rel in files:
        want = (root / rel).read_bytes()
        try:
            got = _fetch(f"{base}/{rel}")
        except urllib.error.HTTPError as e:
            raise StaleTreeError(
                f"{rel} is on disk but the server returned {e.code}"
            ) from None
        if got != want:
            raise StaleTreeError(
                f"{rel} differs: server sent {len(got)} bytes, disk has "
                f"{len(want)}. The server is not serving this working tree; "
                f"do not screenshot it."
            )
    # One header check, on one file, and it is not decoration. Everything above
    # verifies what THIS process fetched; the browser fetches separately, and a
    # cache between it and the server would let it render bytes this check
    # never saw -- freshness proven against the wrong reader. no-store closes
    # that, so it is asserted rather than assumed.
    if files:
        with urllib.request.urlopen(f"{base}/{files[0]}", timeout=TIMEOUT) as r:
            cc = (r.headers.get("Cache-Control") or "").lower()
        if "no-store" not in cc:
            raise StaleTreeError(
                f"{base} served {files[0]} with Cache-Control: {cc!r}. A "
                f"screenshot run must be uncacheable end to end, or the "
                f"browser can render bytes this check never fetched."
            )
    return len(files)


@contextmanager
def serve(root: Path = DOCS, port: int = 0, verify: bool = True):
    """Serve `root` and yield a base URL that has been verified against disk.

    port=0 is the default and the recommendation: the OS picks something free,
    so there is no shared number for a previous run to still be holding.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        sys.exit(f"FATAL: no such directory to serve: {root}")

    if port and not _port_is_free(port):
        sys.exit(
            f"FATAL: something is already listening on 127.0.0.1:{port}. "
            f"Refusing to bind alongside it -- on Windows that succeeds, and "
            f"then which server your browser reaches is the OS's decision, not "
            f"yours. Stop the other process, or omit --port and take a free one."
        )

    handler = partial(_Handler, directory=str(root))

    class _Server(ThreadingHTTPServer):
        # OFF on purpose; see the module docstring. This is what turns "bind a
        # port someone else is listening on" from a silent success into an
        # OSError, on the platform where it would otherwise succeed.
        allow_reuse_address = False
        daemon_threads = True

    try:
        httpd = _Server(("127.0.0.1", port), handler)
    except OSError as e:
        sys.exit(f"FATAL: cannot bind 127.0.0.1:{port or '<any>'}: {e}")

    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        if verify:
            n = verify_fresh(base, root)
            print(f"shotserve: {base} verified against {n} files in {root}")
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(DOCS),
                    help="directory to serve (default: docs/)")
    ap.add_argument("--port", type=int, default=0,
                    help="port to bind; 0 (default) takes a free one, which is "
                         "the only setting that cannot collide with a leftover")
    ap.add_argument("--verify-url", metavar="URL",
                    help="do not serve: audit the server already at URL "
                         "against this working tree, then exit")
    a = ap.parse_args()
    root = Path(a.root).resolve()

    if a.verify_url:
        try:
            n = verify_fresh(a.verify_url, root)
        except StaleTreeError as e:
            print(f"STALE: {e}")
            return 1
        print(f"fresh: {a.verify_url} matches {n} files in {root}")
        return 0

    try:
        with serve(root=root, port=a.port) as base:
            print(f"serving {root} at {base}  (ctrl-c to stop)")
            threading.Event().wait()
    except StaleTreeError as e:
        print(f"FATAL: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
