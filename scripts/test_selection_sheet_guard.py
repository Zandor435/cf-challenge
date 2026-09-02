#!/usr/bin/env python3
"""
test_selection_sheet_guard.py — the sheet sweep cannot reach real people.

build_selection_sheet.py inlines every image it finds as base64 into ONE HTML
file that is opened by double-clicking and can be mailed, synced or dropped in
a shared folder. What it sweeps is a DISTRIBUTION decision. output/personas/ is
where this repo keeps likeness material of real people — camera originals,
un-approved renders, and the reference art derived from them — so an
unargumented collect() must not be able to reach any of it.

WHY THIS TEST BUILDS ITS OWN TREE. The first version asserted against the real
output/ directory: that personas/ held images the guard had to stop, and that
collect() still returned work from elsewhere. Both are true on a machine that
has generated art. output/ is gitignored, so on a fresh CI checkout it does not
exist, both checks failed on "0 file(s) on disk", and the property they were
protecting passed VACUOUSLY — "0 of 0 swept". A guard test that only works
where the artifacts happen to be lying around is not a guard test. This one
synthesizes the tree it needs, so it means the same thing on a bare checkout as
on a loaded workstation.

WHY THE ASSERTION IS A PROPERTY, NOT A FILE LIST. The guard this protects
replaced one that enumerated `personas/family/` plus `.jpg`/`.jpeg` under
`personas/`, and `personas/church/fat_joshb.png` walked around it the same day
it was written — a PNG, in a group the list did not name. A test asserting
"these two named files are absent" would have passed against that broken guard
just as happily. So the fixture deliberately includes a group nobody has ever
named, an archive MIRROR (the match has to be on the path segment, not on a
leading prefix), a staged _source copy, and four extensions — and the assertion
is that ZERO swept paths carry a `personas` segment, whatever is under it.

Needs no API key, opens no socket, and touches nothing outside a temp dir.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_selection_sheet_guard.py
    python scripts/test_selection_sheet_guard.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_selection_sheet as B  # noqa: E402

SEGMENT = "personas"

# Every one of these must be unreachable.
MUST_NOT_REACH = [
    "personas/church/fat_joshb.png",
    "personas/family/holiday_photo.jpg",
    "personas/jonno/Fat/scan.jpeg",
    "personas/browns/nobody_named_this_group_yet.png",
    "personas/recolor/zach/zach_accent_gemini.webp",
    "archive/personas/superseded.png",
    "_source/personas/church/raw.png",
]

# Ordinary work the sweep must still return. Without these the property could
# hold by returning nothing at all — the exact failure that put this rewrite
# here.
MUST_REACH = [
    "scenes/church/church_fishing_josh_b_painted_01.png",
    "banners/church/church_trophychase_01.png",
    "halfcards/panel/panel_half_chris_left_noir_01.png",
]

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _build_tree(root):
    for rel in MUST_NOT_REACH + MUST_REACH:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        # collect() filters on suffix and is_file() and never opens the file,
        # so an empty one exercises the same path a 900KB render would.
        p.write_bytes(b"")


def test_sweep_excludes_real_people():
    print("\nSelection-sheet real-people guard:")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_tree(root)
        real_output = B.OUTPUT
        try:
            B.OUTPUT = root
            items = B.collect()
            narrowed = B.collect("personas/")
        finally:
            # Restore unconditionally: this module global is shared with every
            # other test in the process, and leaving it pointed at a deleted
            # temp dir is the cross-test poisoning CLAUDE.md rule 21 is about.
            B.OUTPUT = real_output

    swept = {i["rel"] for i in items}

    check("fixture planted files under personas/ for the guard to stop",
          len(MUST_NOT_REACH) == 7, f"{len(MUST_NOT_REACH)} planted")

    missing = sorted(r for r in MUST_REACH if r not in swept)
    check("collect() still returns the non-personas files",
          not missing,
          f"{len(swept)} swept" if not missing else f"MISSING {missing}")

    leaked = sorted(p for p in swept if SEGMENT in Path(p).parts)
    check("an unargumented collect() returns ZERO paths under personas/",
          not leaked,
          f"LEAKED {len(leaked)}: {leaked[:5]}" if leaked
          else f"0 of {len(swept)} swept")

    check("collect('personas/') is empty too -- --only cannot widen the guard",
          not narrowed, f"{len(narrowed)} item(s)")

    check("B.OUTPUT restored after the fixture",
          B.OUTPUT == B.ROOT / "output", str(B.OUTPUT))


def main():
    test_sweep_excludes_real_people()
    bad = [r for r in _res if not r[1]]
    print(f"\n{len(_res) - len(bad)}/{len(_res)} checks passed.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
