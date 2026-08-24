#!/usr/bin/env python3
"""The banner manifest agrees with what is actually published, and the
masthead rotator stays one-image-per-load.

Two halves.

INTEGRITY. docs/data/panel/banners.json is the only thing the frontend reads
to decide which files exist, so every claim in it has to be true on disk: the
file is there, and its width/height are the image's real pixels. A wrong
dimension is not cosmetic here — the page reserves the banner box from it, and
a wrong ratio reintroduces exactly the reflow the metadata exists to prevent.
Byte-parity against output/ is checked whenever the (gitignored) source tree is
present, which is the guarantee that nothing re-encoded on the way through.

SCOPE. This pass is panel only. The other three leagues must have no manifest,
and the frontend must contain no timer, no crossfade and no controls — the
rotation is the page load and nothing else.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_banners  # noqa: E402

from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GROUP = "panel"
MANIFEST = ROOT / "docs" / "data" / GROUP / "banners.json"
PUB_DIR = ROOT / "docs" / "assets" / "banners" / GROUP
SRC_DIR = ROOT / "output" / "banners" / GROUP
OTHER_GROUPS = ("family", "church", "browns")

FAILURES = []


def check(cond, label):
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")
    return cond


def load():
    if not check(MANIFEST.exists(), "docs/data/panel/banners.json exists"):
        return None
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        check(False, f"manifest parses as JSON ({e})")
        return None


def manifest_shape(doc):
    banners = doc.get("banners")
    check(isinstance(banners, list) and len(banners) > 0,
          "manifest lists at least one banner")
    check(doc.get("group") == GROUP, "manifest group is panel")
    check(doc.get("dir") == f"assets/banners/{GROUP}",
          "manifest dir is the docs-relative publish path")
    check(doc.get("count") == len(banners or []),
          "manifest count matches the banner list length")
    names = [b.get("file") for b in (banners or [])]
    check(len(set(names)) == len(names), "no duplicate filenames in the manifest")
    return banners or []


def published(banners):
    """Every declared file is on disk, and its declared size is its real size."""
    for b in banners:
        name = b.get("file")
        p = PUB_DIR / str(name)
        if not check(p.is_file(), f"published: {name}"):
            continue
        w, h = b.get("width"), b.get("height")
        check(isinstance(w, int) and w > 0 and isinstance(h, int) and h > 0,
              f"{name}: positive integer dimensions in the manifest")
        try:
            with Image.open(p) as im:
                real = im.size
        except Exception as e:                      # noqa: BLE001
            check(False, f"{name}: published file is a readable image ({e})")
            continue
        check(real == (w, h),
              f"{name}: manifest {w}x{h} matches the file's real {real[0]}x{real[1]}")


def no_extras(banners):
    """Nothing published that the manifest does not declare — an undeclared
    file is dead weight in the deploy that no page will ever request."""
    if not PUB_DIR.is_dir():
        return
    on_disk = {p.name for p in PUB_DIR.iterdir() if p.is_file()}
    declared = {str(b.get("file")) for b in banners}
    check(on_disk == declared,
          f"published set matches the manifest exactly "
          f"(undeclared: {sorted(on_disk - declared) or 'none'})")


def byte_parity(banners):
    """No re-encode on the way through. Skipped when output/ is absent — it is
    gitignored, so a fresh clone legitimately has no sources to compare."""
    if not SRC_DIR.is_dir():
        print("  --    output/banners/panel absent; byte-parity check skipped")
        return
    for b in banners:
        name = str(b.get("file"))
        s, d = SRC_DIR / name, PUB_DIR / name
        if not s.exists() or not d.exists():
            check(False, f"{name}: present in BOTH output/ and docs/")
            continue
        check(build_banners.sha256(s) == build_banners.sha256(d),
              f"{name}: published bytes identical to the source")


def empty_source_guard():
    """Playbook rule 5. An empty source directory must be a hard stop that
    leaves the existing manifest exactly as it was, not an empty list that
    blanks the masthead on the live site."""
    before = MANIFEST.read_bytes() if MANIFEST.exists() else None
    saved = build_banners.SRC_ROOT
    tmp = Path(tempfile.mkdtemp())
    (tmp / GROUP).mkdir()
    try:
        build_banners.SRC_ROOT = tmp
        try:
            build_banners.build(GROUP, check=False, prune=False)
            check(False, "empty source directory is refused")
        except SystemExit as e:
            check("no images" in str(e).lower(),
                  "empty source directory is refused, and says why")
        # And the missing-directory case.
        try:
            build_banners.build("nosuchgroup", check=False, prune=False)
            check(False, "missing source directory is refused")
        except SystemExit as e:
            check("no source directory" in str(e).lower(),
                  "missing source directory is refused, and says why")
    finally:
        build_banners.SRC_ROOT = saved
    after = MANIFEST.read_bytes() if MANIFEST.exists() else None
    check(before == after, "refused runs left the existing manifest untouched")


def panel_only():
    """Scope guard for this pass: the other three mastheads are untouched."""
    for g in OTHER_GROUPS:
        check(not (ROOT / "docs" / "data" / g / "banners.json").exists(),
              f"{g} has no banner manifest (panel only this pass)")
        check(not (ROOT / "docs" / "assets" / "banners" / g).is_dir(),
              f"{g} has no published banner directory")


def nothing_renders_it():
    """THE FRONTEND HALF IS GONE, and this is what replaced it.

    This file used to assert three edits that made the manifest render on
    managers.html. The band came off the profile scroll, then renderBanner()
    itself was deleted -- it was the only caller of this manifest, and a
    rotator no surface calls is a second, drifting answer to "how does this
    site show a banner" sitting next to app.js's bannerFor(), which is the one
    the home page actually uses.

    So the remaining frontend claim is a NEGATIVE one, and it is worth keeping:
    nothing anywhere reads data/<group>/banners.json, and no stylesheet still
    carries rules for a slot that cannot exist. Both are the states a
    well-meaning re-wiring would silently undo.

    THE MANIFEST AND ITS IMAGES STAY ON DISK, and every check above still holds
    them to it. build_banners.py is the generator and banners.json is the spec;
    if a surface ever wants the band back, that is what it builds against.
    """
    docs = ROOT / "docs"
    for name in ("managers.js", "app.js", "site.js", "analytics.js"):
        js = (docs / name).read_text(encoding="utf-8")
        check("function renderBanner(" not in js,
              f"{name} does not define renderBanner()")
        # The fetch, not the word. managers.js names the manifest in a comment
        # explaining where the rotator's inputs went, and a test that cannot
        # tell a comment from a call would forbid documenting the decision.
        check("fetchJSON(`data/${groupId}/banners.json`)" not in js
              and "fetch('data/" + "banners.json'" not in js,
              f"{name} does not fetch a banner manifest")
    for name in ("managers.html", "index.html", "analytics.html", "svp.html"):
        html = (docs / name).read_text(encoding="utf-8")
        check('id="mgr-banner"' not in html, f"{name} has no banner slot")
    for name in ("style.css", "profile.css"):
        css = (docs / name).read_text(encoding="utf-8")
        check(".mgr-banner-slot" not in css,
              f"{name} carries no rules for the deleted slot")


def main() -> int:
    print("manifest shape")
    doc = load()
    if doc is None:
        print(f"\n{len(FAILURES)} FAILED (manifest unreadable)")
        return 1
    banners = manifest_shape(doc)

    print("\npublished files")
    published(banners)
    no_extras(banners)

    print("\nbyte parity with output/")
    byte_parity(banners)

    print("\nbuilder guards")
    empty_source_guard()

    print("\nscope")
    panel_only()

    print("\nnothing renders it")
    nothing_renders_it()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all banner checks passed.")
    return 0


def test_banners():
    assert main() == 0, f"{len(FAILURES)} banner check(s) failed"


if __name__ == "__main__":
    sys.exit(main())
