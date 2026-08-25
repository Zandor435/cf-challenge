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
image to, and a `face_band`, the measurement that focal was derived from.

The last of those is what makes the geometry checkable rather than merely
documented. A band shorter than the face band x the frame's rendered height
cannot show every face at any object-position, so the largest of them is a
hard floor under the cap in style.css -- and both halves are now asserted:
that every banner CAN be framed inside its cap (cap_floor), and that the
focal actually shipped DOES frame it (focal_clears_faces). The second is the
likelier failure. Shortening the cap from 360 to 280 left every floor intact
while invalidating fourteen of the sixteen focals at a stroke.

SCOPE AND WIRING. This pass is panel only: the other three leagues must have no
manifest and must stay on a `fixed` hero_banner slot. And the rotation must
live on exactly ONE surface — app.js, reached through the slot resolver — with
the full fallback chain still spelled out behind it. A second reader of the
manifest is the specific regression this half exists to catch, because that is
what the profile page grew last time and what had to be deleted again.
"""
import io
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
SLOTS = ROOT / "docs" / "assets" / "art_slots.json"
DOCS = ROOT / "docs"

# EVERY ROTATOR GROUP, and how many banners each publishes. The whole battery
# below -- integrity, budget, framing, reproducibility -- runs once per entry.
# It was panel-only while panel was the only group with a manifest; browns has
# one now, and a guard that covers one of two rotators is a guard with a hole
# in exactly the place the next one will be added.
#
# The count is exact, not a lower bound, for the reason it always was: a count
# that drifts without anyone noticing is how a half-published set reaches the
# site. See unscanned_is_reported() for the other half of that.
ROTATORS = {"panel": 16, "browns": 1}

# Still on a single kickoff banner and mode `fixed`. These have no manifest and
# no published directory, and scope() asserts they stay that way.
FIXED_GROUPS = ("family", "church")

# The group currently under test. REBOUND per pass by _select() rather than
# threaded through as a parameter: thirty-seven references across a dozen
# functions would be thirty-seven signature changes for no gain, and the rebind
# is contained -- one module, one process, passes run strictly in sequence and
# never interleave. main() sets it before each pass and nothing outside this
# file reads it.
GROUP = "panel"
MANIFEST = ROOT / "docs" / "data" / GROUP / "banners.json"
PUB_DIR = ROOT / "docs" / "assets" / "banners" / GROUP
SRC_DIR = ROOT / "output" / "banners" / GROUP
EXPECTED_COUNT = ROTATORS[GROUP]


def _select(group):
    """Point the module-level group paths at one group for the next pass."""
    global GROUP, MANIFEST, PUB_DIR, SRC_DIR, EXPECTED_COUNT
    GROUP = group
    EXPECTED_COUNT = ROTATORS[group]
    MANIFEST = ROOT / "docs" / "data" / group / "banners.json"
    PUB_DIR = ROOT / "docs" / "assets" / "banners" / group
    SRC_DIR = ROOT / "output" / "banners" / group

# The count that sat at 15 while a sixteenth banner, delivered in the same pack
# as five that DID ship, waited in a subdirectory the publish step never reads,
# is now ROTATORS above -- one number per group instead of one for panel.

FAILURES = []

# The widest the band ever renders: --maxw (1400px) less .page's 20px of
# padding either side. .hero-banner's negative margins cancel .hero's padding,
# so the band spans that full 1360 and no more -- confirmed by measurement at
# 1440 and 1920 viewports, where it is 1360 at both. This is the WORST CASE for
# cropping: the wider the band, the taller the un-clamped image, so the smaller
# the fraction of it a fixed pixel cap can show. A focal that clears the faces
# here clears them at every narrower viewport. css_maxw() re-checks the 1400 so
# this constant cannot quietly stop being true.
BAND_W = 1360


def local_sources():
    """The source images, or [] when this checkout has none.

    THE PREDICATE, and the bug it is replacing: three checks below compare the
    published set against the sources that produced it, and each guarded
    itself with `SRC_DIR.is_dir()` -- "output/ is gitignored, so a fresh clone
    legitimately has nothing to compare." That was true right up until
    focal.json was un-ignored, which made output/banners/panel/ EXIST on CI
    while still holding no images. The guard read "directory is here, sources
    must be too", ran the comparisons against an empty source list, and
    load_focals() correctly reported all sixteen focal keys as naming no file.

    A directory was never the thing being asked about. Images were. Asking for
    them directly is immune to whatever else gets tracked in there later.
    """
    return build_banners.sources(SRC_DIR) if SRC_DIR.is_dir() else []


def check(cond, label):
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")
    return cond


def load():
    if not check(MANIFEST.exists(), f"docs/data/{GROUP}/banners.json exists"):
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
    check(doc.get("group") == GROUP, f"manifest group is {GROUP}")
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

    Skipped when this checkout has no source IMAGES, same as the re-encode
    check -- and note that is not the same question as whether output/ exists,
    which is what this used to ask and what broke CI the moment focal.json
    became a tracked file in an otherwise gitignored directory. When the
    sources ARE here, this catches a manifest published from a sidecar that
    has since been edited: the focal equivalent of the source-drift guard.
    """
    srcs = local_sources()
    if not srcs:
        print("  --    no local banner sources; focal sidecar check skipped")
        return
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


def _strip_media(css):
    """style.css with every @media block removed, leaving the base rules.

    Splitting on the first "@media" does NOT work and is worth spelling out,
    because it silently returns a passing-looking answer: the first media
    query in this stylesheet opens hundreds of lines above the banner rules,
    so that split throws away the very rules being looked for and the regex
    then finds nothing. Braces are matched instead. What is being isolated is
    the DESKTOP cap -- the mobile override caps a band ~366px wide, where
    nothing published is tall enough to reach it.
    """
    out, i = [], 0
    while True:
        j = css.find("@media", i)
        if j < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:j])
        k = css.find("{", j)
        if k < 0:
            return "".join(out)
        depth, k = 1, k + 1
        while k < len(css) and depth:
            depth += (css[k] == "{") - (css[k] == "}")
            k += 1
        i = k


def _css_caps():
    """(standard, tall) max-height in px for .hero-banner.sized, desktop block.

    Read out of the stylesheet rather than duplicated here, because a test that
    hardcodes the cap it is checking against stops being a check the moment
    somebody edits the CSS: it would keep passing on the number the test
    remembers while the site ships the number the browser reads.
    """
    desktop = _strip_media((DOCS / "style.css").read_text(encoding="utf-8"))
    std = re.search(r"\.hero-banner\.sized img\s*\{[^}]*?max-height:\s*(\d+)px",
                    desktop, re.S)
    tall = re.search(r"\.hero-banner\.sized\.tall img\s*\{[^}]*?max-height:\s*(\d+)px",
                     desktop, re.S)
    check(std is not None, "style.css declares a standard .sized cap")
    check(tall is not None, "style.css declares a .tall cap")
    return (int(std.group(1)) if std else None,
            int(tall.group(1)) if tall else None)


def cap_floor(banners):
    """No published banner may need a taller band than its cap allows.

    THE ARITHMETIC. A banner's face band spans (bottom - top) percent of its
    frame. At BAND_W the frame renders BAND_W / ratio pixels tall, so the
    faces occupy (bottom - top)% of that many pixels. A band shorter than that
    cannot show them all AT ANY object-position -- the window is simply
    shorter than the thing it has to contain, and moving it only chooses which
    face loses its chin. That product is the banner's floor, and the largest
    floor in the set is the floor for the cap.

    WHY THIS IS A TEST AND NOT A COMMENT. Every previous time this number
    mattered it was established by a person looking at screenshots, and it has
    changed under us twice: once when the band was re-shaped from a fixed
    260px window to per-image ratios, and once when a sixteenth banner was
    published. A re-crop that lowers somebody's chin by six percent of frame,
    or a new piece with a deeper group pose, moves the floor silently and the
    only symptom is a face cut off in the masthead on one load in sixteen --
    which is exactly the kind of thing nobody sees for a month.

    The .tall banners are checked against the .tall cap: that class exists
    BECAUSE their floors (510px and 490px) are far above what the standard
    band can hold, so measuring them against the standard cap would report a
    failure the design already answers.
    """
    std, tall_cap = _css_caps()
    if std is None or tall_cap is None:
        return
    check(re.search(r"--maxw:\s*1400px", (DOCS / "style.css").read_text(encoding="utf-8"))
          is not None,
          f"--maxw is still 1400px, so BAND_W={BAND_W} is still the widest band")

    floors = []
    for b in banners:
        name = str(b.get("file"))
        band = b.get("face_band")
        if not check(isinstance(band, (list, tuple)) and len(band) == 2,
                     f"{name}: carries a face_band"):
            continue
        top, bot = float(band[0]), float(band[1])
        check(0 <= top < bot <= 100,
              f"{name}: face_band [{top}, {bot}] is a sane fraction of frame")
        ratio = (b.get("width") or 0) / (b.get("height") or 1)
        nat = BAND_W / ratio if ratio else 0
        floor = (bot - top) / 100 * nat
        is_tall = ratio < _tall_ratio()
        cap = tall_cap if is_tall else std
        floors.append((name, floor, cap, is_tall))
        check(floor <= cap,
              f"{name}: needs {floor:.0f}px of band to show every face, "
              f"{'.tall ' if is_tall else ''}cap is {cap}px")

    landscape = [f for f in floors if not f[3]]
    if landscape:
        worst = max(landscape, key=lambda f: f[1])
        print(f"  --    landscape floor is {worst[1]:.0f}px ({worst[0]}), "
              f"cap {std}px, headroom {std - worst[1]:.0f}px")
    tallest = [f for f in floors if f[3]]
    if tallest:
        worst = max(tallest, key=lambda f: f[1])
        print(f"  --    .tall floor is {worst[1]:.0f}px ({worst[0]}), "
              f"cap {tall_cap}px, headroom {tall_cap - worst[1]:.0f}px")


def focal_clears_faces(banners):
    """The published focal actually keeps every face inside the band.

    cap_floor() proves a banner CAN be framed without cutting a face. This
    proves the value shipped alongside it DOES. They are different failures
    and the second is the likelier one: shortening the cap left every floor
    intact while invalidating fourteen of the sixteen focals at a stroke,
    because the window got shorter and the values that used to sit inside it
    no longer did.

    Same worst case as cap_floor -- the widest band, where the crop is
    deepest. object-position places the window's top edge at focal x the
    overflow, so the visible span is [p(1-r), p(1-r)+r] as a fraction of the
    frame, and both the highest hairline and the lowest chin have to be inside
    it.
    """
    std, tall_cap = _css_caps()
    if std is None or tall_cap is None:
        return
    for b in banners:
        name = str(b.get("file"))
        band, focal = b.get("face_band"), b.get("focal")
        if not (isinstance(band, (list, tuple)) and len(band) == 2
                and isinstance(focal, str)):
            continue
        ratio = (b.get("width") or 0) / (b.get("height") or 1)
        nat = BAND_W / ratio
        cap = tall_cap if ratio < _tall_ratio() else std
        r = min(cap / nat, 1.0)
        p = float(focal.split()[1].rstrip("%")) / 100
        top = p * (1 - r) * 100
        bot = top + r * 100
        f0, f1 = float(band[0]), float(band[1])
        check(top <= f0 + 1e-6 and bot >= f1 - 1e-6,
              f"{name}: focal {focal} shows {top:.1f}-{bot:.1f}% of frame, "
              f"faces are at {f0:.0f}-{f1:.0f}%")


def _tall_ratio():
    """The .tall threshold, from app.js, so this file has one source for it."""
    m = re.search(r"const TALL_RATIO = ([0-9.]+);",
                  (DOCS / "app.js").read_text(encoding="utf-8"))
    return float(m.group(1)) if m else 2.2


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

    This is what byte-parity became. Skipped when this checkout holds no
    source images — they are gitignored, so a fresh clone legitimately has
    nothing to compare — but when they ARE here this catches a source that
    changed after publication, which is the drift that leaves the site serving
    art nobody can regenerate.
    """
    local = local_sources()
    if not local:
        print("  --    no local banner sources; re-encode check skipped")
        return
    srcs = {build_banners.published_name(s): s for s in local}
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


def unscanned_is_reported():
    """An image in a subdirectory is skipped, and the skip is announced.

    This is the check that would have caught the trophy panorama. A pack of
    eleven arrived as output/banners/panel/banners/, five were renamed up into
    the flat directory and published, six were not, and the build reported
    "15 banners" with no hint that it had walked past the rest. Everything
    downstream agreed with it, because every other check in this file starts
    from what was PUBLISHED rather than from what was delivered -- so a
    silently half-published pack is invisible from here by construction.

    Deliberately not fatal, and the test pins that too: a subdirectory is a
    legitimate place for an original delivery or a rejected take, and a build
    that refuses to run because one exists is a build nobody can use.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / GROUP
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "hidden-banner.png").write_bytes(b"")
        (src / "sub" / "notes.txt").write_bytes(b"")

        buf = io.StringIO()
        stdout = sys.stdout
        try:
            sys.stdout = buf
            build_banners.report_unscanned(src)
        finally:
            sys.stdout = stdout
        out = buf.getvalue()

        check("hidden-banner.png" in out,
              "an image in a subdirectory is named, not silently skipped")
        check("notes.txt" not in out,
              "a non-image in a subdirectory is not reported as skipped art")
        check(bool(out.strip()) and "NOTE" in out,
              "the skip is announced on the build's own output")

        # And no subdirectory means no noise -- a run that skipped nothing must
        # not print a warning nobody can act on.
        (src / "flat.png").write_bytes(b"")
        buf = io.StringIO()
        try:
            sys.stdout = buf
            build_banners.report_unscanned(src / "sub")
        finally:
            sys.stdout = stdout
        check("hidden-banner.png" not in buf.getvalue(),
              "a directory with no image subdirectories reports nothing")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_is_dry():
    """--check must write NOTHING. It encodes to memory to report exact sizes,
    which is precisely the code path most likely to grow an accidental write."""
    if not local_sources():
        print("  --    no local banner sources; dry-run check skipped")
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
    for g in ROTATORS:
        spec = (groups.get(g) or {}).get("hero_banner") or {}
        check(spec.get("mode") == "rotate", f"{g} hero_banner is mode rotate")
        check(spec.get("source") == "data/{group}/banners.json",
              f"{g} hero_banner sources the group's banners.json")
        # An empty candidate list is not an oversight here: it is what keeps the
        # manifest the single answer to "which banner", with the single kickoff
        # webp reachable one tier down through $banners instead.
        check(not (spec.get("candidates") or []),
              f"{g} hero_banner declares no inline candidates")
    for g in FIXED_GROUPS:
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


def scope():
    """Which groups rotate and which do not, asserted both ways.

    Was panel_only(). The list moved rather than loosened: a group is either in
    ROTATORS, in which case it must have a manifest, a published directory AND
    still keep its single kickoff banner as the fallback tier, or it is in
    FIXED_GROUPS, in which case it must have neither of the first two. What is
    NOT allowed is a group in neither list, which is how a fourth rotator would
    get published with nothing checking its framing."""
    for g in FIXED_GROUPS:
        check(not (ROOT / "docs" / "data" / g / "banners.json").exists(),
              f"{g} has no banner manifest (still mode fixed)")
        check(not (ROOT / "docs" / "assets" / "banners" / g).is_dir(),
              f"{g} has no published banner directory")
        check((ROOT / "docs" / "assets" / "banners" / f"{g}.webp").is_file(),
              f"{g} still has its single kickoff banner")
    for g in ROTATORS:
        check((ROOT / "docs" / "data" / g / "banners.json").exists(),
              f"{g} has a banner manifest")
        check((ROOT / "docs" / "assets" / "banners" / g).is_dir(),
              f"{g} has a published banner directory")
        check((ROOT / "docs" / "assets" / "banners" / f"{g}.webp").is_file(),
              f"{g} still has its single kickoff banner as the rotate fallback")

    known = set(ROTATORS) | set(FIXED_GROUPS)
    declared = set((json.loads(SLOTS.read_text(encoding="utf-8"))
                    .get("groups") or {}))
    check(declared <= known,
          f"every group in art_slots.json is classified here: {sorted(declared - known)}")


def main() -> int:
    # The group-scoped battery, once per rotator. Everything in here reads the
    # paths _select() rebinds; everything after the loop is about surfaces and
    # wiring, which are properties of the site rather than of one group.
    for group in ROTATORS:
        _select(group)
        print(f"===== {group} =====")
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
        cap_floor(banners)
        focal_clears_faces(banners)
        focal_sidecar(banners)
        framing_wiring(banners)

        print("\nre-encode reproducibility")
        reproducible(banners)

        print("\nbuilder guards")
        empty_source_guard()
        unscanned_is_reported()
        check_is_dry()
        print()

    print("===== site =====")
    print("framing guards")
    focal_guards()

    print("\nscope")
    scope()

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
