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


def frontend_wiring():
    """The rotator is OFF the profile scroll, and what it renders elsewhere is
    still ONE image with no motion attached to it.

    The banner slot was removed from managers.html: the masthead already names
    the league and the page title names it again, so a third announcement above
    a text-heavy scroll pushed the first profile under the fold. This half of
    the test inverted with it — it now asserts the wiring is GONE, because a
    silently reinstated banner is exactly the drift worth catching.

    renderBanner() itself stays in managers.js. It is the only implementation
    of the one-image/no-motion contract, so the motion checks below still have
    something real to guard.
    """
    html = (ROOT / "docs" / "managers.html").read_text(encoding="utf-8")
    js = (ROOT / "docs" / "managers.js").read_text(encoding="utf-8")
    css = (ROOT / "docs" / "style.css").read_text(encoding="utf-8")

    check('id="mgr-banner"' not in html,
          "managers.html has NO banner slot (removed from the profile scroll)")
    check("renderBanner(bannersRes)" not in js,
          "managers.js does not call renderBanner()")
    check("fetchJSON(`data/${groupId}/banners.json`)" not in js,
          "managers.js does not fetch a manifest it has nowhere to paint")

    # The implementation is retained, unwired. Deleting it would mean rewriting
    # the rotator from the manifest spec the day a surface wants the band back.
    check("function renderBanner(" in js, "managers.js still defines renderBanner()")
    check("Math.random()" in js, "the pick is random")
    check("loading=\"eager\"" in js, "the banner loads eagerly (above the fold)")
    check("el.style.aspectRatio" in js,
          "the box is reserved from the manifest's dimensions")

    # No motion, of any kind, anywhere on this page. A timer or a transition
    # here would be an auto-rotator or a crossfade, and this feature is neither.
    for banned in ("setInterval", "setTimeout", "requestAnimationFrame"):
        check(banned not in js, f"managers.js contains no {banned} (no auto-rotation)")
    check(".mgr-banner-slot" in css, "style.css styles the banner slot")
    slot = css[css.index(".mgr-banner-slot"):]
    slot = slot[:slot.index("/* ---- page intro ---- */")]
    check("transition:" not in slot.replace("transition: none", ""),
          "no transition declared on the banner")
    check("animation:" not in slot.replace("animation: none", ""),
          "no animation declared on the banner")
    check("prefers-reduced-motion" in slot,
          "the reduced-motion fence is present")


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

    print("\nfrontend wiring")
    frontend_wiring()

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
