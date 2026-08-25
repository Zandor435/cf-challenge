#!/usr/bin/env python3
"""The banner manifest agrees with what is actually published, the published
files honour the webp budget, and exactly one surface rotates them.

Four halves, which is two more than it used to be.

INTEGRITY. docs/data/panel/banners.json is the only thing the frontend reads to
decide which files exist, so every claim in it has to be true on disk: the file
is there, and its width/height are the PUBLISHED image's real pixels. A wrong
dimension is not cosmetic here — the page reserves the banner box from it, and
a wrong ratio reintroduces exactly the reflow the metadata exists to prevent.

THE PUBLISH CONTRACT, which replaced byte-parity. This script used to assert
that published bytes were identical to source bytes, because publishing was a
copy. It is an encode now, so identity is the wrong question and the right ones
are: is every published file actually WEBP, is it inside the size budget the
rotator was sized for, is its long edge within LONG_EDGE, and does re-encoding
today's source reproduce today's published bytes. That last one is the real
guard — it catches a manifest published from a source that has since changed,
which is the drift the old sha256 check used to catch.

FRAMING, which is the newest half. The manifest's width/height are no longer
decoration the page ignores - renderHero() sizes the band from them, so a
wrong ratio is now a visibly wrong box rather than a comment that lies. And
each entry carries a `focal`: the object-position the band crops that one
image to. Both are checked the same way - the value has to be a shape the page
can use, it has to agree with the sidecar it came from, and the frontend has
to actually consume it. A focal published and never read is the same masthead
full of torsos this branch started with.

SCOPE AND WIRING. This pass is panel only: the other three leagues must have no
manifest and must stay on a `fixed` hero_banner slot. And the rotation must
live on exactly ONE surface — app.js, reached through the slot resolver — with
the full fallback chain still spelled out behind it. A second reader of the
manifest is the specific regression this half exists to catch, because that is
what the profile page grew last time and what had to be deleted again.
"""
import json
import re
import shutil
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
SLOTS = ROOT / "docs" / "assets" / "art_slots.json"
DOCS = ROOT / "docs"
OTHER_GROUPS = ("family", "church", "browns")

# What the branch published. Not a lower bound: a count that drifts without
# anyone noticing is how a half-published set reaches the site -- which is not
# hypothetical here. It sat at 15 while a sixteenth banner, delivered in the
# same pack as five that DID ship, waited in a subdirectory the publish step
# never reads.
EXPECTED_COUNT = 16

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
    check(doc.get("count") == EXPECTED_COUNT,
          f"manifest declares {EXPECTED_COUNT} banners")
    names = [b.get("file") for b in (banners or [])]
    check(len(set(names)) == len(names), "no duplicate filenames in the manifest")
    # The published extension is a claim about the bytes, and the whole point
    # of the encode step is that the claim is now true. A .png here would mean
    # the mirroring publish path came back.
    check(all(str(n).endswith(".webp") for n in names),
          "every manifest entry names a .webp")
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
                real, fmt = im.size, im.format
        except Exception as e:                      # noqa: BLE001
            check(False, f"{name}: published file is a readable image ({e})")
            continue
        check(real == (w, h),
              f"{name}: manifest {w}x{h} matches the file's real {real[0]}x{real[1]}")
        check(fmt == "WEBP", f"{name}: published bytes really are WEBP (got {fmt})")


def budget(banners):
    """The rotator's worst case is its largest file, so the cap is per file."""
    total = 0
    for b in banners:
        name = str(b.get("file"))
        p = PUB_DIR / name
        if not p.is_file():
            continue
        size = p.stat().st_size
        total += size
        check(size <= build_banners.MAX_BYTES,
              f"{name}: {size // 1024} KB is within the "
              f"{build_banners.MAX_BYTES // 1024} KB cap")
        check(max(b.get("width") or 0, b.get("height") or 0)
              <= build_banners.LONG_EDGE,
              f"{name}: long edge within {build_banners.LONG_EDGE}px")
    print(f"  --    published set totals {total // 1024} KB "
          f"across {len(banners)} file(s)")


def alt_text(banners):
    """Alt text is optional by contract, but it must not carry standings prose
    if it is present — the hero headline beside the banner already says who
    leads, and a second telling in alt text goes stale the moment it moves."""
    for b in banners:
        if "alt" not in b:
            continue
        name, alt = str(b.get("file")), str(b.get("alt"))
        check(bool(alt.strip()), f"{name}: alt text is non-empty when present")
        # Whole words, and only words that can ONLY mean the board. "standing"
        # is not on the list on purpose: half this art is people standing
        # shoulder to shoulder, and a substring match on it fails a correct
        # description. The plural "standings" is the board and is on the list.
        leaky = [w for w in ("leader", "leaders", "leads", "banked", "rank",
                             "ranked", "ranking", "standings", "in first",
                             "first place", "points")
                 if re.search(rf"\b{re.escape(w)}\b", alt, re.I)]
        check(not leaky,
              f"{name}: alt text describes the art, not the board "
              f"(found: {leaky or 'nothing'})")


def focal(banners):
    """Every banner says where to crop it, in a shape the page can use.

    COMPLETE COVERAGE is asserted, not just validity. `focal` is optional by
    contract -- app.js falls back to a top-biased default -- but on this set an
    entry without one is a banner nobody framed, and framing them one at a time
    is the entire point of the sidecar. A missing one is silent: the picture
    still renders, just cropped to a default that was never measured against
    it, which is indistinguishable from the bug this branch fixed.
    """
    for b in banners:
        name = str(b.get("file"))
        f = b.get("focal")
        if not check(isinstance(f, str) and f, f"{name}: carries a focal"):
            continue
        check(bool(build_banners.FOCAL_RE.match(f)),
              f"{name}: focal {f!r} is a CSS object-position")
        # x is 50% on every published banner, and that is structural rather
        # than stylistic: the band is never squarer than the art, so cover
        # never crops horizontally and any other x would be a no-op that
        # reads like an intention.
        check(f.split(" ")[0] == "50%",
              f"{name}: focal x is 50% (the band never crops horizontally)")


def focal_sidecar(banners):
    """The published focal is the sidecar's, unchanged.

    Skipped without output/, same as the re-encode check. When it IS there,
    this catches a manifest published from a sidecar that has since been
    edited -- the focal equivalent of the source-drift guard.
    """
    if not SRC_DIR.is_dir():
        print("  --    output/banners/panel absent; focal sidecar check skipped")
        return
    srcs = build_banners.sources(SRC_DIR)
    side = build_banners.load_focals(SRC_DIR, srcs)
    check(bool(side), "output/banners/panel/focal.json exists and parses")
    by_out = {build_banners.published_name(s): side.get(s.name) for s in srcs}
    for b in banners:
        name = str(b.get("file"))
        check(b.get("focal") == by_out.get(name),
              f"{name}: manifest focal matches the sidecar")


def focal_guards():
    """Rule 4: a focal that cannot be used stops the build and names itself.

    Both cases are silent otherwise. A malformed value would be dropped and
    the banner would publish on the default crop; a key naming no source is
    somebody who believes they framed an image and did not -- the file was
    renamed, or the name was typed wrong -- and nothing would tell them. The
    sidecar is checkable against the sources in a way alt.json is not, so it
    is checked.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / GROUP
        src.mkdir()
        real = src / "a.png"
        real.write_bytes(b"")           # never opened; only its NAME is read
        srcs = [real]

        check(build_banners.load_focals(src, srcs) == {},
              "a missing focal.json is legal and yields no focals")

        (src / "focal.json").write_text('{"a.png": "50% 12%"}', encoding="utf-8")
        check(build_banners.load_focals(src, srcs) == {"a.png": "50% 12%"},
              "a well-formed focal.json is read")

        (src / "focal.json").write_text('{"a.png": "middle-ish"}', encoding="utf-8")
        try:
            build_banners.load_focals(src, srcs)
            check(False, "a malformed focal value is refused")
        except SystemExit as e:
            check("object-position" in str(e) and "a.png" in str(e),
                  "a malformed focal value is refused, and names the file")

        (src / "focal.json").write_text('{"ghost.png": "50% 12%"}', encoding="utf-8")
        try:
            build_banners.load_focals(src, srcs)
            check(False, "a focal key naming no source is refused")
        except SystemExit as e:
            check("ghost.png" in str(e),
                  "a focal key naming no source is refused, and names the key")

        (src / "focal.json").write_text('{"_x": "hi", "a.png": "50% 1%"}',
                                        encoding="utf-8")
        check(build_banners.load_focals(src, srcs) == {"a.png": "50% 1%"},
              'underscore-prefixed keys are notes, not filenames')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def framing_wiring(banners):
    """The page consumes what the manifest publishes, on both axes.

    Every assertion here pairs a manifest field with the code that reads it.
    The published geometry was inert once already: banners.json has carried
    width/height since the webp encode, and its own $note claimed the page
    reserved the box from them, while renderHero() emitted a bare <img> that
    reserved nothing. Metadata everybody believes is load-bearing and nothing
    reads is the failure this function exists to catch.
    """
    app = (DOCS / "app.js").read_text(encoding="utf-8")
    css = (DOCS / "style.css").read_text(encoding="utf-8")

    check("--banner-ratio" in app and "--banner-ratio" in css,
          "the published ratio reaches the page as --banner-ratio")
    check("aspect-ratio: var(--banner-ratio)" in css,
          "the band's height is derived from that ratio, not from a fixed px")
    check("--banner-focal" in app and "var(--banner-focal" in css,
          "the published focal reaches the page as --banner-focal")
    check("FOCAL_RE" in app,
          "app.js re-checks the focal shape before writing it to a style")
    check(".hero-banner.sized" in css,
          "the manifest-driven variant is a class, so the groups with no "
          "manifest are untouched")

    # The cap VALUES are the stylesheet's business. Two relations between them
    # are not: the poster band has to be the taller one or .tall means nothing,
    # and a phone must not inherit a desktop cap.
    caps = re.findall(r"\.hero-banner\.sized(\.tall)?[^{}]*\{[^}]*?"
                      r"max-height:\s*(\d+)px", css, re.S)
    plain = [int(px) for tall, px in caps if not tall]
    tall = [int(px) for tall, px in caps if tall]
    check(len(plain) >= 2 and len(tall) >= 1,
          f"both blocks cap .sized, and .tall is capped "
          f"(found sized={plain}, tall={tall})")
    if plain and tall:
        check(max(tall) > max(plain),
              f"the poster-format band is the taller one ({max(tall)}px "
              f"vs {max(plain)}px)")
        check(min(plain) < max(plain),
              f"a phone gets a shorter cap than the desktop band ({plain})")

    # TALL_RATIO is a boundary between two shapes, not a knife edge through the
    # middle of the set. Republish a banner at 2.15:1 and this fires -- which
    # is the moment to decide which band that shape belongs in, rather than
    # discovering it in the masthead.
    m = re.search(r"const TALL_RATIO = ([0-9.]+);", app)
    if check(m is not None, "app.js declares TALL_RATIO"):
        thr = float(m.group(1))
        for b in banners:
            w, h = b.get("width") or 0, b.get("height") or 1
            r = w / h
            check(abs(r - thr) > 0.1,
                  f"{b.get('file')}: ratio {r:.3f} is clear of the "
                  f"{thr} .tall threshold")


def no_extras(banners):
    """Nothing published that the manifest does not declare — an undeclared
    file is dead weight in the deploy that no page will ever request. This is
    also what proves the retired .png publications are actually gone."""
    if not PUB_DIR.is_dir():
        return
    on_disk = {p.name for p in PUB_DIR.iterdir() if p.is_file()}
    declared = {str(b.get("file")) for b in banners}
    check(on_disk == declared,
          f"published set matches the manifest exactly "
          f"(undeclared: {sorted(on_disk - declared) or 'none'})")


def reproducible(banners):
    """Re-encoding today's source reproduces today's published bytes.

    This is what byte-parity became. Skipped when output/ is absent — it is
    gitignored, so a fresh clone legitimately has no sources to compare — but
    when it IS present this catches a source that changed after publication,
    which is the drift that leaves the site serving art nobody can regenerate.
    """
    if not SRC_DIR.is_dir():
        print("  --    output/banners/panel absent; re-encode check skipped")
        return
    srcs = {build_banners.published_name(s): s
            for s in build_banners.sources(SRC_DIR)}
    for b in banners:
        name = str(b.get("file"))
        s = srcs.get(name)
        if not check(s is not None, f"{name}: has a source in output/"):
            continue
        d = PUB_DIR / name
        if not d.exists():
            continue
        data, w, h, _q = build_banners.encode(s)
        check(data == d.read_bytes(),
              f"{name}: re-encoding the source reproduces the published bytes")
        check((w, h) == (b.get("width"), b.get("height")),
              f"{name}: re-encode reproduces the manifest's dimensions")


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
        shutil.rmtree(tmp, ignore_errors=True)
    after = MANIFEST.read_bytes() if MANIFEST.exists() else None
    check(before == after, "refused runs left the existing manifest untouched")


def check_is_dry():
    """--check must write NOTHING. It encodes to memory to report exact sizes,
    which is precisely the code path most likely to grow an accidental write."""
    if not SRC_DIR.is_dir():
        print("  --    output/banners/panel absent; dry-run check skipped")
        return
    before_manifest = MANIFEST.read_bytes() if MANIFEST.exists() else None
    before_assets = ({p.name: p.stat().st_size for p in PUB_DIR.iterdir()
                      if p.is_file()} if PUB_DIR.is_dir() else {})
    build_banners.build(GROUP, check=True, prune=True)
    after_manifest = MANIFEST.read_bytes() if MANIFEST.exists() else None
    after_assets = ({p.name: p.stat().st_size for p in PUB_DIR.iterdir()
                     if p.is_file()} if PUB_DIR.is_dir() else {})
    check(before_manifest == after_manifest, "--check left the manifest untouched")
    # --prune passed alongside --check on purpose: the destructive flag must
    # also be inert in a dry run, which is the combination worth pinning.
    check(before_assets == after_assets,
          "--check --prune left every published file untouched")


def slot_wiring():
    """panel rotates from the manifest; the other three do not rotate at all."""
    try:
        slots = json.loads(SLOTS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        check(False, f"art_slots.json parses ({e})")
        return
    groups = slots.get("groups") or {}
    panel = (groups.get(GROUP) or {}).get("hero_banner") or {}
    check(panel.get("mode") == "rotate", "panel hero_banner is mode rotate")
    check(panel.get("source") == "data/{group}/banners.json",
          "panel hero_banner sources the group's banners.json")
    # An empty candidate list is not an oversight here: it is what keeps the
    # manifest the single answer to "which banner", with the single kickoff
    # webp reachable one tier down through $banners instead.
    check(not (panel.get("candidates") or []),
          "panel hero_banner declares no inline candidates")
    for g in OTHER_GROUPS:
        spec = (groups.get(g) or {}).get("hero_banner") or {}
        check(spec.get("mode") == "fixed", f"{g} hero_banner is still mode fixed")
        check(spec.get("candidates") == ["assets/banners/{group}.webp"],
              f"{g} hero_banner still resolves to its single kickoff banner")
        check("source" not in spec, f"{g} hero_banner names no manifest")


def one_rotator():
    """Exactly one surface reads the manifest, and the fallback behind it is
    still intact. Both halves matter: a second reader is the regression the
    profile page already grew once, and a rotator with no fallback turns a
    missing manifest into a missing masthead."""
    app = (DOCS / "app.js").read_text(encoding="utf-8")
    site = (DOCS / "site.js").read_text(encoding="utf-8")

    check("loadBannerPool" in app, "app.js loads the rotate pool")
    check("setArtPool" in site and "ART_POOLS" in site,
          "site.js owns the pool the resolver picks from")
    check("spec.mode === 'rotate'" in site,
          "resolveArt() handles the rotate mode")
    # The fallback tiers, by the code that implements each one.
    check("PORTRAITS[BANNER_KEY]" in app,
          "bannerFor() still falls back to the $banners list")
    check(f"assets/banners/${{groupId}}.webp" in app,
          "bannerFor() still falls back to the single kickoff banner")
    check("bimg.addEventListener('error', drop" in app,
          "the decode-failure handler still drops the banner block")

    # No OTHER surface fetches it. The word may appear in a comment explaining
    # where the rotator went — a test that cannot tell a comment from a call
    # would forbid documenting the decision — so this looks for the fetch.
    for name in ("managers.js", "site.js", "analytics.js"):
        js = (DOCS / name).read_text(encoding="utf-8")
        check("function renderBanner(" not in js,
              f"{name} does not define renderBanner()")
        check("banners.json`)" not in js and "banners.json')" not in js,
              f"{name} does not fetch a banner manifest")
    for name in ("managers.html", "index.html", "analytics.html", "svp.html"):
        html = (DOCS / name).read_text(encoding="utf-8")
        check('id="mgr-banner"' not in html, f"{name} has no profile banner slot")
    for name in ("style.css", "profile.css"):
        css = (DOCS / name).read_text(encoding="utf-8")
        check(".mgr-banner-slot" not in css,
              f"{name} carries no rules for the deleted slot")


def panel_only():
    """Scope guard for this pass: the other three mastheads are untouched."""
    for g in OTHER_GROUPS:
        check(not (ROOT / "docs" / "data" / g / "banners.json").exists(),
              f"{g} has no banner manifest (panel only this pass)")
        check(not (ROOT / "docs" / "assets" / "banners" / g).is_dir(),
              f"{g} has no published banner directory")
        check((ROOT / "docs" / "assets" / "banners" / f"{g}.webp").is_file(),
              f"{g} still has its single kickoff banner")
    check((ROOT / "docs" / "assets" / "banners" / f"{GROUP}.webp").is_file(),
          "panel still has its single kickoff banner as the rotate fallback")


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

    print("\npublish budget")
    budget(banners)

    print("\nalt text")
    alt_text(banners)

    print("\nframing")
    focal(banners)
    focal_sidecar(banners)
    focal_guards()
    framing_wiring(banners)

    print("\nre-encode reproducibility")
    reproducible(banners)

    print("\nbuilder guards")
    empty_source_guard()
    check_is_dry()

    print("\nscope")
    panel_only()

    print("\nslot wiring")
    slot_wiring()

    print("\none rotator, with its fallback")
    one_rotator()

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
