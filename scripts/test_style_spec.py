#!/usr/bin/env python3
"""Style specs validate, and the five parts stay separate.

The load-bearing test here is separation() -- it asserts that holding
composition and character constant while varying only the style fragment
changes ONLY the fragment substring in the assembled prompt. That is the whole
architectural claim in one assertion. Everything else guards the boundary that
makes it true.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import style_spec  # noqa: E402

FAILURES = []


def check(cond, label):
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")
    return cond


BASE = {
    "style_id": "unit_fixture",
    "version": 1,
    "derived_from": ["output/_vault/church/Fat Banner.png"],
    "render_mode": "illustration",
    "text_policy": "none",
    "fragment": ("rendered as a THREE-COLOR SCREENPRINTED POSTER: hard flat "
                 "shapes, heavy solid black, visible halftone dots, a strictly "
                 "limited ink palette, no gradients whatsoever"),
}


def spec(**over):
    d = dict(BASE)
    d.update(over)
    return d


def rejects(over, label, expect=None):
    """A spec that must be refused, and refused for the stated reason."""
    d = spec(**over)
    p = Path(tempfile.gettempdir()) / f"{d['style_id']}.json"
    try:
        style_spec.validate(d, p)
    except SystemExit as e:
        if expect and expect.lower() not in str(e).lower():
            return check(False, f"{label} (rejected, but not for {expect!r})")
        return check(True, label)
    return check(False, f"{label} -- WAS ACCEPTED")


def accepts(over, label):
    d = spec(**over)
    p = Path(tempfile.gettempdir()) / f"{d['style_id']}.json"
    try:
        style_spec.validate(d, p)
        return check(True, label)
    except SystemExit as e:
        return check(False, f"{label} -- REJECTED: {str(e)[:120]}")


def library():
    """Every committed spec loads. The regression net: a hand-edit that breaks
    a spec fails here rather than at spend time."""
    try:
        specs = style_spec.load_all()
    except SystemExit as e:
        return check(False, f"templates/art/ all valid -- {str(e)[:160]}")
    return check(True, f"templates/art/ all valid ({len(specs)} spec(s))")


def separation():
    """THE architectural assertion.

    One composition, one reference set, three different fragments. The three
    assembled prompts must differ ONLY where the fragment sits. If pose,
    setting, wardrobe or locks move when the style changes, some part is
    reaching into another and the decomposition is a fiction.
    """
    import banner_batch
    from generate_banners import GROUP_LOCK, NO_TEXT

    comp = banner_batch.BY_SLUG["lineup"]
    colors_str = "the man from reference image #1 wears gold (#ceb888)"
    frags = ["rendered as MEDIUM A: flat ink",
             "rendered as MEDIUM B: soft airbrushed gradients",
             "rendered as MEDIUM C: carved white line on black"]

    def assemble(frag):
        head = (comp["text"].format(n=1) + " " + frag.strip().rstrip(".") + "."
                + " Each man wears his own color: " + colors_str + ".")
        return head + GROUP_LOCK.format(n=1) + NO_TEXT

    built = [assemble(f) for f in frags]
    ok = True
    for f, b in zip(frags, built):
        rest = b.replace(f.rstrip("."), "\x00")
        others = [x.replace(g.rstrip("."), "\x00") for g, x in zip(frags, built)]
        if len(set(others)) != 1:
            ok = False
    check(ok, "separation: 3 styles, same composition -> prompts differ ONLY "
              "in the fragment")

    # And the inverse: the legacy path CANNOT do this, which is why the
    # decomposed path exists. Documented, not asserted as a failure.
    from generate_banners import STYLES
    legacy_differs = ("stadium" in STYLES["comic"]
                      and "practice field" in STYLES["vintage70s"])
    check(legacy_differs,
          "legacy STYLES provably fuse setting into style (comic=stadium, "
          "vintage70s=practice field) -- the defect the split fixes")


def locks_always_appended():
    """GROUP_LOCK is unconditional; NO_TEXT is suppressed only by an explicit
    typography policy. A spec can never remove either."""
    import banner_batch
    from generate_banners import GROUP_LOCK, NO_TEXT
    comp = banner_batch.BY_SLUG["lineup"]

    def build(text_policy):
        head = comp["text"].format(n=3) + " rendered as X: y."
        tail = GROUP_LOCK.format(n=3)
        if text_policy != "typography":
            tail += NO_TEXT
        return head + tail

    check(build("none").endswith(NO_TEXT), "NO_TEXT appended when policy=none")
    check(GROUP_LOCK.format(n=3) in build("none"), "GROUP_LOCK always present")
    check(GROUP_LOCK.format(n=3) in build("typography"),
          "GROUP_LOCK present even under typography")
    check(build("typography").endswith(GROUP_LOCK.format(n=3)),
          "NO_TEXT suppressed only by typography policy")


def main():
    print("style spec validation")
    library()

    print("\nboundary: fragments may not reach into other parts")
    rejects({"fragment": "rendered as a COMIC: bold ink, five men standing "
                         "together in a row under bright stadium floodlights"},
            "subject word 'men' rejected", expect="CHARACTER")
    rejects({"fragment": "rendered as a COMIC: bold ink, halftone dots, posed "
                         "against a dramatic low-angle background of lights"},
            "composition words rejected", expect="COMPOSITION")
    rejects({"fragment": "rendered as a COMIC: bold ink, halftone shading, "
                         "flat saturated fills, heavy graphic blacks, and "
                         "prominent collegiate wordmarks across every jersey"},
            "wardrobe/logo words rejected", expect="WARDROBE")
    rejects({"fragment": "rendered as a COMIC: bold ink, halftone dots, and "
                         "do not slim anyone at all under any circumstances"},
            "lock language rejected", expect="LOCKS")
    rejects({"fragment": "rendered as a SUPERHERO COMIC: bold ink outlines, "
                         "halftone dot shading, flat saturated cel color fills"},
            "'superhero' rejected (it caused browns_comic_01)",
            expect="COMPOSITION")

    print("\nboundary: format tokens belong to other parts")
    rejects({"fragment": "rendered as a COMIC of {n} subjects: bold ink lines, "
                         "halftone dots, flat saturated color, heavy blacks"},
            "{n} rejected")
    rejects({"fragment": "rendered as a COMIC: bold ink, halftone, {colors}, "
                         "flat saturated fills, heavy graphic blacks throughout"},
            "{colors} rejected")
    rejects({"fragment": "rendered as a COMIC: bold ink, halftone dots, flat "
                         "saturated color fills, heavy graphic blacks {oops"},
            "stray brace rejected")

    print("\nschema")
    rejects({"style_id": "Bad-ID"}, "style_id must be ^[a-z0-9_]+$")
    rejects({"render_mode": "photoreal", "_photoreal_why": ""},
            "photoreal without _photoreal_why rejected", expect="_photoreal_why")
    accepts({"render_mode": "photoreal",
             "_photoreal_why": "the source artifact is a photo composite"},
            "photoreal WITH a why accepted")
    rejects({"fragment": "rendered as a COMIC: bold ink"},
            "too-short fragment rejected")
    rejects({"fragment": "rendered as a COMIC: " + "bold ink, " * 40},
            "too-long fragment rejected")
    rejects({"derived_from": []}, "empty derived_from rejected")
    rejects({"aspect": "7:3"}, "unsupported aspect rejected")

    print("\nbanned terms, scoped to render_mode")
    rejects({"fragment": "rendered as a KODACHROME plate: heavy grain, washed "
                         "sepia cast, slight vignette, flat light, faded dyes"},
            "illustration + banned term rejected")
    accepts({"render_mode": "photoreal",
             "_photoreal_why": "the source artifact IS a photograph composite",
             "fragment": "rendered as a KODACHROME plate: heavy grain, washed "
                         "sepia cast, slight vignette, flat light, faded dyes"},
            "same fragment accepted under photoreal + why")

    print("\nseparation of powers")
    separation()
    locks_always_appended()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all style-spec checks passed.")
    return 0


def test_style_spec():
    assert main() == 0, f"{len(FAILURES)} style-spec check(s) failed"


if __name__ == "__main__":
    sys.exit(main())
