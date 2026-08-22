#!/usr/bin/env python3
"""Load and validate one art style spec. Read-only, no network, no spend.

WHAT A STYLE IS HERE. Exactly one of five parts, and the smallest one:

    1. STYLE        medium/technique only        templates/art/<id>.json   <- this
    2. COMPOSITION  framing, arrangement         scripts/banner_batch.py
    3. CHARACTER    who is in the frame          output/personas/<group>/
    4. LOCKS        invariants                   BUILD/GROUP/LIKENESS/NO_TEXT
    5. RENDER       pure compose + call          generate_banners.py

A style is a MEDIUM FRAGMENT in the grammar generate_scenes.STYLE_SET already
uses -- "rendered as a THREE-COLOR SCREENPRINTED POSTER: hard flat shapes,
heavy solid black, visible halftone dots" -- and nothing else. It says how the
picture is made. It never says who is in it, how many, how they are posed, what
is behind them, what color they wear, or what must not change.

WHY THE BOUNDARY IS ENFORCED IN CODE RATHER THAN DOCUMENTED. Two live failures,
both on disk:

  * output/banners/panel/panel_comic_01.png renders OSU, CU, T and WF wordmarks
    on the garments -- which NO_TEXT explicitly forbids. That is a render LEAK,
    not a style choice. An LLM shown that image cannot tell the difference and
    will faithfully write "collegiate wordmarks on chest" into a spec. So the
    validator rejects a fragment that mentions logos or text at all.

  * output/banners/browns/browns_comic_01.png came back with two of five men as
    muscled superheroes in armor, slimmed, because the `comic` STYLES string
    contains "in the style of a classic SUPERHERO comic". A subject word inside
    a style string leaked into the subject and beat BUILD_LOCK. So the validator
    rejects subject and composition language too.

Every rejection names the phrase AND which of the five parts owns it, because
"invalid spec" sends you re-reading the schema and "'posed' belongs to
COMPOSITION" sends you to the right file.

FAIL BEFORE SPEND. Every check runs at load time, before any reference image is
read and before any paid call. Same posture as generate_scenes.check_prompt().
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART_DIR = ROOT / "templates" / "art"

STYLE_ID_RE = re.compile(r"^[a-z0-9_]+$")
RENDER_MODES = ("illustration", "photoreal")
TEXT_POLICIES = ("none", "typography")

REQUIRED = ("style_id", "version", "derived_from", "render_mode",
            "text_policy", "fragment")

MIN_WORDS, MAX_WORDS = 15, 60

# phrase -> the part that owns it. Word-boundary matched, case-insensitive.
# Keep the phrases SHORT and generic; a fragment that trips one of these is
# reaching into another part's job even when the wording is innocent.
OWNED_ELSEWHERE = {
    # 3. CHARACTER -- who is in the frame
    "men": "CHARACTER", "man": "CHARACTER", "coaches": "CHARACTER",
    "coach": "CHARACTER", "figures": "CHARACTER", "people": "CHARACTER",
    "faces": "CHARACTER", "subjects": "CHARACTER",
    # 2. COMPOSITION -- pose, framing, setting
    "posed": "COMPOSITION", "poses": "COMPOSITION", "pose": "COMPOSITION",
    "framing": "COMPOSITION", "background": "COMPOSITION",
    "foreground": "COMPOSITION", "stadium": "COMPOSITION",
    "standing": "COMPOSITION", "shoulder to shoulder": "COMPOSITION",
    "low-angle": "COMPOSITION", "three-quarter": "COMPOSITION",
    "superhero": "COMPOSITION",
    # wardrobe/color -- built from the ref list at render time
    "team color": "WARDROBE", "team colors": "WARDROBE", "logo": "WARDROBE",
    "logos": "WARDROBE", "wordmark": "WARDROBE", "wordmarks": "WARDROBE",
    "jersey": "WARDROBE", "uniform": "WARDROBE",
    # 4. LOCKS -- the pipeline appends these itself
    "recognizable as": "LOCKS", "do not slim": "LOCKS",
    "do not merge": "LOCKS", "do not blend": "LOCKS",
    "no typography": "LOCKS", "artwork only": "LOCKS",
    "absolutely unchanged": "LOCKS", "heavy-set": "LOCKS",
    "watermark": "LOCKS", "watermarks": "LOCKS",
}

# Imported lazily inside check_banned() -- see the note there.
_BANNED = None


def _banned_terms():
    """generate_scenes.BANNED_TERMS, loaded on demand.

    Import is side-effect free (its module-level PERSONAS is a Path
    construction, not a read), but it pulls dotenv and the billed image client
    into the process. Nothing here needs those unless a fragment is actually
    being checked, so pay for it only then.
    """
    global _BANNED
    if _BANNED is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from generate_scenes import BANNED_TERMS
        _BANNED = tuple(BANNED_TERMS)
    return _BANNED


class SpecError(SystemExit):
    """Loud, named, and fatal -- never a warning. Rule 4: a silent miss is
    worse than a stop."""


def _fail(path, msg):
    raise SpecError(f"ERROR: invalid style spec {path}\n  {msg}")


def check_leaks(fragment, path):
    """Reject any phrase that belongs to one of the other four parts."""
    low = fragment.lower()
    hits = []
    for phrase, owner in OWNED_ELSEWHERE.items():
        if re.search(r"\b" + re.escape(phrase) + r"\b", low):
            hits.append((phrase, owner))
    if hits:
        lines = "\n".join(f"    {p!r} belongs to {o}" for p, o in sorted(hits))
        _fail(path,
              f"`fragment` reaches into other parts of the prompt:\n{lines}\n"
              f"  A style says HOW the picture is made, never who is in it, "
              f"how they are posed, what they wear, or what must not change.\n"
              f"  The pipeline supplies all of that. Delete these from the spec.")


def check_banned(spec, path):
    """BANNED_TERMS, scoped to the claim it was written for.

    generate_scenes documents the guard as technical, not editorial: "photoreal
    staging is what breaks likeness once a body is in motion." So it applies to
    illustration specs, and a photoreal spec may waive it -- but the waiver has
    to be ARGUED in `_photoreal_why`, not taken by omission. A silent exemption
    is how a load-bearing guard rots.
    """
    if spec["render_mode"] == "photoreal":
        if not str(spec.get("_photoreal_why", "")).strip():
            _fail(path, "render_mode is 'photoreal', which waives BANNED_TERMS, "
                        "so `_photoreal_why` is REQUIRED and must say why this "
                        "style has to be photographic.")
        return
    low = spec["fragment"].lower()
    hit = [t for t in _banned_terms()
           if re.search(r"\b" + re.escape(t) + r"\b", low)]
    if hit:
        _fail(path, f"illustration fragment contains banned term(s): "
                    f"{', '.join(sorted(hit))}.\n  Photoreal staging breaks "
                    f"likeness once a body is in motion. Reword, or set "
                    f"render_mode='photoreal' with a `_photoreal_why`.")


def validate(spec, path):
    """-> spec, or raise SpecError naming exactly what is wrong."""
    if not isinstance(spec, dict):
        _fail(path, f"top level must be an object, got {type(spec).__name__}")

    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        _fail(path, f"missing required key(s): {', '.join(missing)}")

    sid = spec["style_id"]
    if not isinstance(sid, str) or not STYLE_ID_RE.match(sid):
        _fail(path, f"style_id {sid!r} must match ^[a-z0-9_]+$ -- it lands in "
                    f"an output filename ({{group}}_{{style}}_{{nn}}.png).")
    if sid != path.stem:
        _fail(path, f"style_id {sid!r} does not match the filename "
                    f"{path.stem!r}. Keep them equal so the spec is findable.")

    if spec["render_mode"] not in RENDER_MODES:
        _fail(path, f"render_mode must be one of {RENDER_MODES}")
    if spec["text_policy"] not in TEXT_POLICIES:
        _fail(path, f"text_policy must be one of {TEXT_POLICIES}")
    if not isinstance(spec["derived_from"], list) or not spec["derived_from"]:
        _fail(path, "derived_from must be a non-empty list of source paths, so "
                    "a spec can always be traced back to the art it came from.")

    frag = spec["fragment"]
    if not isinstance(frag, str) or not frag.strip():
        _fail(path, "`fragment` must be a non-empty string")

    # {n} and {colors} are COMPOSITION and WARDROBE tokens. A style carrying
    # them is a whole prompt wearing a style's name -- the exact conflation
    # that made "same picture, different medium" inexpressible in STYLES.
    for tok in ("{n}", "{colors}"):
        if tok in frag:
            _fail(path, f"`fragment` contains {tok}, which belongs to "
                        f"{'COMPOSITION' if tok == '{n}' else 'WARDROBE'}. "
                        f"A style fragment takes no format tokens.")
    if "{" in frag or "}" in frag:
        _fail(path, "`fragment` contains a stray brace. It is concatenated, "
                    "never .format()ed, so a brace here is a typo that would "
                    "reach the model verbatim.")

    words = len(frag.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        _fail(path, f"`fragment` is {words} words; allowed {MIN_WORDS}-"
                    f"{MAX_WORDS}. The seven shipped STYLE_SET entries are "
                    f"15-25. A long style paragraph competes with the locks "
                    f"for the model's attention, which is how a lock quietly "
                    f"stops working.")

    check_leaks(frag, path)
    check_banned(spec, path)

    import gemini_image
    asp = spec.get("aspect")
    if asp is not None and asp not in gemini_image.SUPPORTED_ASPECTS:
        _fail(path, f"aspect {asp!r} is not supported. Choose from: "
                    f"{', '.join(gemini_image.SUPPORTED_ASPECTS)}")
    return spec


def load(path):
    """Read, parse and validate one spec file."""
    path = Path(path)
    if not path.is_file():
        raise SpecError(f"ERROR: no style spec at {path}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(path, f"not valid JSON: {e}")
    return validate(spec, path)


def load_all(directory=ART_DIR):
    """Every spec in the library, keyed by style_id. Used by the test net so a
    hand-edit that breaks a spec fails in CI, not at spend time."""
    d = Path(directory)
    if not d.is_dir():
        return {}
    return {p.stem: load(p) for p in sorted(d.glob("*.json"))}


if __name__ == "__main__":
    specs = load_all()
    if not specs:
        print(f"no specs under {ART_DIR}")
    for sid, s in specs.items():
        print(f"  OK  {sid:<28} {s['render_mode']:<12} "
              f"text={s['text_policy']:<10} {len(s['fragment'].split())}w")
    print(f"\n{len(specs)} spec(s) valid.")
