#!/usr/bin/env python3
"""
test_add_to_banner.py — the edit path sends the right two images, in order.

add_to_banner.py exists because generate_banners.py cannot render church any
more: six managers against five character slots. It feeds the FINISHED banner
in as reference #1 and the new manager's poster as #2 and asks for the same
picture with one more man in it. Three things in that sentence can break
silently, and each is checked here:

  1. ORDER IS THE BINDING. The prompt addresses "reference image #1" and
     "reference image #2"; the model has no other way to tell the finished
     artwork from the man being added. Swap the list and the prompt reads as
     "reproduce the poster, add the banner to it" — a plausible-looking render
     of the wrong thing, for full price.
  2. THE MIME MUST MATCH THE BYTES. The base is a PNG master when one is on the
     machine and the published WEBP when it is not, so the type is read off the
     suffix instead of hardcoded. A wrong declaration is a 400 that costs a
     request to find out about.
  3. VARIANTS MUST NOT LAND IN THE PUBLISH DIRECTORY. build_banners.py
     publishes every image at output/banners/<group>/, so a default that wrote
     candidates there would push four unreviewed banners into the rotation.

Nothing here opens a socket: gemini_image.generate is stubbed and records what
it was handed. No image is generated and nothing is billed.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_add_to_banner.py
    python scripts/test_add_to_banner.py
"""

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import add_to_banner  # noqa: E402
import gemini_image  # noqa: E402

# The check ledger. Each entry is (label, ok, detail) — the LABEL is carried so a
# failure is diagnosable from the pytest report alone, not only from the printed
# transcript above it. conftest.py clears this before every pytest test and raises
# on any recorded FAIL; main() sums it for the standalone `python scripts/...` run.
_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


_PNG = b"\x89PNG\r\n\x1a\n-generated-"


class _Recorder:
    """Stands in for gemini_image.generate and keeps every call's arguments."""

    def __init__(self, result=_PNG):
        self.calls = []
        self.result = result

    def __call__(self, key, model, refs, prompt, aspect, **kw):
        self.calls.append(dict(key=key, model=model, refs=refs, prompt=prompt,
                               aspect=aspect, **kw))
        return self.result


@contextlib.contextmanager
def _stub_generate(rec):
    saved = gemini_image.generate
    gemini_image.generate = rec
    try:
        yield
    finally:
        gemini_image.generate = saved


@contextlib.contextmanager
def _sandbox(base_size=(2172, 724), base_name="church_trophychase_01.png"):
    """A temp dir holding a real base image and a real poster, plus a key in the
    environment. Both files are genuine images because main() reads the base's
    pixel size to choose the aspect."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        base = d / base_name
        Image.new("RGB", base_size, (10, 20, 30)).save(base)
        add = d / "church_josh_b_fat_01.png"
        Image.new("RGB", (1200, 800), (40, 50, 60)).save(add)
        saved_key = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key-not-a-real-one"
        try:
            yield d, base, add
        finally:
            if saved_key is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = saved_key


def _run(argv):
    """Call main() with argv, returning (exit_code, stdout+stderr)."""
    saved = sys.argv
    out, err = io.StringIO(), io.StringIO()
    sys.argv = ["add_to_banner.py"] + argv
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = add_to_banner.main()
    except SystemExit as e:            # argparse errors exit rather than return
        code = e.code
    finally:
        sys.argv = saved
    return code, out.getvalue() + err.getvalue()


def test_prompt_addresses_the_references_by_index():
    """The prompt's only handle on either image is its ordinal."""
    print("\n[1] the prompt names #1 and #2 and locks the existing figures")
    p = add_to_banner.build_prompt("at the right-hand end of the row",
                                   "Duke blue team gear", existing=5)
    check("reference image #1 is named", "reference image #1" in p)
    check("reference image #2 is named", "reference image #2" in p)
    check("no manager id leaks into the prompt", "josh_b" not in p)
    check("the existing figures are locked",
          "must survive unchanged" in p and "do NOT restyle" in p)
    check("exactly one addition is asked for", "ONE CHANGE ONLY" in p)
    check("the placement rides in", "at the right-hand end of the row" in p)
    check("the wardrobe rides in", "Duke blue team gear" in p)
    check("the base count is stated", "same 5 people" in p)
    check("the total is stated", "All 6 figures" in p, p[-260:])
    # The added man's reference is a whole poster — josh_b's is a Duke Divinity
    # piece with a chapel, a cream panel and its own wordmarks. An earlier
    # revision preserved "lettering already present in either reference", which
    # invited every one of those into the banner.
    check("only the man is taken from #2", "Take ONLY THE MAN" in p)
    check("the typography clause is scoped to #1",
          "in reference image #1 — and only there — stays exactly as it is" in p)
    check("its background is refused",
          "Do not carry over its setting" in p)

    bare = add_to_banner.build_prompt("at one end", "his own gear")
    check("an unknown base count states no number at all",
          "same people in the same left-to-right order" in bare
          and "All figures must be fully visible" in bare)


def test_mime_is_read_off_the_suffix():
    """The base may be the PNG master or the published WEBP."""
    print("\n[2] mime types are declared from the suffix, unknown ones are None")
    for name, want in (("a.png", "image/png"), ("a.webp", "image/webp"),
                       ("a.jpg", "image/jpeg"), ("a.JPEG", "image/jpeg")):
        got = add_to_banner.mime_for(Path(name))
        check(f"{name} -> {want}", got == want, f"got {got!r}")
    check("an unknown suffix is None, not a guess",
          add_to_banner.mime_for(Path("a.tiff")) is None)


def test_the_banner_goes_in_first():
    """Order is the binding, and the aspect comes from the base's own ratio."""
    print("\n[3] refs are [base, added] and the aspect follows the base")
    rec = _Recorder()
    # Every check stays INSIDE the sandbox: it holds the only copies of the two
    # source images, and the comparison below is against their real bytes.
    with _sandbox() as (d, base, add), _stub_generate(rec):
        code, out = _run(["--group", "church", "--base", str(base),
                          "--add", f"josh_b={add}", "--existing", "5",
                          "--n", "2", "--out", str(d / "review")])
        check("exit 0", code == 0, out)
        check("one call per variant", len(rec.calls) == 2,
              f"{len(rec.calls)} calls")
        if rec.calls:
            refs = rec.calls[0]["refs"]
            check("two references, not six", len(refs) == 2)
            check("#1 is the finished banner", refs[0][0] == base.read_bytes())
            check("#2 is the man being added", refs[1][0] == add.read_bytes())
            check("both declare image/png",
                  [r[1] for r in refs] == ["image/png", "image/png"])
            check("2172x724 renders at 21:9", rec.calls[0]["aspect"] == "21:9",
                  rec.calls[0]["aspect"])
            check("the budget is threaded through (rule 6)",
                  isinstance(rec.calls[0].get("budget"), dict))
        written = sorted(p.name for p in (d / "review").glob("*.png"))
        check("both variants written, numbered off the base name",
              written == ["church_trophychase_01_plus_josh_b_01.png",
                          "church_trophychase_01_plus_josh_b_02.png"],
              str(written))


def test_variants_do_not_default_into_the_publish_directory():
    """output/banners/<group>/ is published wholesale by build_banners.py."""
    print("\n[4] the default output directory is _review/, one level down")
    rec = _Recorder()
    with _sandbox() as (d, base, add), _stub_generate(rec):
        code, out = _run(["--group", "church", "--base", str(base),
                          "--add", f"josh_b={add}", "--n", "1", "--preview"])
    check("preview exits 0", code == 0, out)
    check("preview bills nothing", not rec.calls)
    check("the default lands in _review",
          "output/banners/church/_review" in out, out)
    check("the prompt is shown for review", "reference image #1" in out)


def test_a_published_webp_base_is_accepted():
    """The master PNG is gitignored, so the published webp is the fallback."""
    print("\n[5] a .webp base is sent as image/webp")
    rec = _Recorder()
    with _sandbox(base_size=(1600, 533),
                  base_name="church_trophychase_01.webp") as (d, base, add), \
            _stub_generate(rec):
        code, out = _run(["--group", "church", "--base", str(base),
                          "--add", f"josh_b={add}", "--n", "1",
                          "--out", str(d / "review")])
    check("exit 0", code == 0, out)
    if rec.calls:
        check("#1 declares image/webp",
              rec.calls[0]["refs"][0][1] == "image/webp")
        check("3:1 renders at its nearest supported ratio, 21:9",
              rec.calls[0]["aspect"] == "21:9", rec.calls[0]["aspect"])


def test_bad_input_stops_before_it_bills():
    """Rule 4: fail loud, name the file, spend nothing."""
    print("\n[6] unreadable inputs stop the run before any request")
    rec = _Recorder()
    with _sandbox() as (d, base, add), _stub_generate(rec):
        code, out = _run(["--group", "church", "--base", str(d / "nope.png"),
                          "--add", f"josh_b={add}", "--n", "1",
                          "--out", str(d / "review")])
        check("a missing base exits 1", code == 1, out)
        check("it names the file", "nope.png" in out, out)

        odd = d / "base.tiff"
        odd.write_bytes(b"not really a tiff")
        code, out = _run(["--group", "church", "--base", str(odd),
                          "--add", f"josh_b={add}", "--n", "1",
                          "--out", str(d / "review")])
        check("an unsendable image type exits 1", code == 1, out)
        check("it lists what it does accept", "image/png" not in out
              and ".webp" in out, out)

        code, out = _run(["--group", "church", "--base", str(base),
                          "--add", str(add), "--n", "1",
                          "--out", str(d / "review")])
        check("--add without an id exits non-zero", code not in (0, None), out)
    check("nothing was billed on any of the three", not rec.calls)


def test_existing_variants_are_not_rebilled():
    """Rule 7: --skip-if-exists is the default; --force is opt-in."""
    print("\n[7] a variant already on disk is skipped, not re-bought")
    rec = _Recorder()
    with _sandbox() as (d, base, add), _stub_generate(rec):
        out_dir = d / "review"
        args = ["--group", "church", "--base", str(base), "--add",
                f"josh_b={add}", "--n", "2", "--out", str(out_dir)]
        _run(args)
        check("first run billed twice", len(rec.calls) == 2)
        code, out = _run(args)
        check("second run billed nothing", len(rec.calls) == 2, out)
        check("and said why", "skip (exists)" in out, out)
        code, out = _run(args + ["--force"])
        check("--force re-bills", len(rec.calls) == 4, out)


def main():
    test_prompt_addresses_the_references_by_index()
    test_mime_is_read_off_the_suffix()
    test_the_banner_goes_in_first()
    test_variants_do_not_default_into_the_publish_directory()
    test_a_published_webp_base_is_accepted()
    test_bad_input_stops_before_it_bills()
    test_existing_variants_are_not_rebilled()

    passed, total = sum(1 for r in _res if r[1]), len(_res)
    print(f"\nRESULT: {passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
