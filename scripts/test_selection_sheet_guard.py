#!/usr/bin/env python3
"""
test_selection_sheet_guard.py — the sheet sweep cannot reach real people.

build_selection_sheet.py inlines every image it finds as base64 into ONE HTML
file that is opened by double-clicking and can be mailed, synced or dropped in
a shared folder. What it sweeps is a DISTRIBUTION decision. output/personas/ is
where this repo keeps likeness material of real people — camera originals,
un-approved renders, and the reference art derived from them — so an
unargumented collect() must not be able to reach any of it.

WHY THIS TEST IS WRITTEN AGAINST THE SUBTREE, NOT AGAINST FILENAMES. The first
version of the guard enumerated `personas/family/` plus `.jpg`/`.jpeg` under
`personas/`, and `personas/church/fat_joshb.png` walked around it the same day
— a PNG, in a group the list did not name. A test that asserts "these two
named files are absent" would have passed just as happily against that broken
guard, and would need editing the day somebody adds personas/browns/. So the
assertion here is a PROPERTY of the whole sweep: ZERO returned paths have a
`personas` segment, whatever is on disk, whoever added it.

TWO ASSERTIONS, and the second is the one that keeps the first honest:

  1. collect() with NO arguments returns nothing under a personas/ directory.
  2. the sweep is not vacuous — output/personas/ really does hold images that
     the sweep would otherwise have picked up, and collect() really does
     return files from elsewhere. Without this, an empty output/ or a
     collect() that returned [] would pass assertion 1 while proving nothing.

Needs no API key and opens no socket — safe to run in CI on every commit.

Runs both ways, and they are equivalent: pytest collects one test per section
and conftest.py raises on any check() the section recorded as FAIL; the
standalone runner sums the same ledger and exits 0/1.

Usage:
    python -m pytest scripts/test_selection_sheet_guard.py
    python scripts/test_selection_sheet_guard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_selection_sheet as B  # noqa: E402

SEGMENT = "personas"

_res = []


def check(name, ok, detail=""):
    _res.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _under_personas(rel_posix):
    return SEGMENT in Path(rel_posix).parts


def _on_disk_under_personas():
    """Images physically present under any personas/ dir in output/."""
    root = B.OUTPUT
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in B.IMAGE_EXT
            and SEGMENT in p.relative_to(root).parts]


def test_sweep_excludes_real_people():
    print("\nSelection-sheet real-people guard:")
    on_disk = _on_disk_under_personas()

    # (2a) Not vacuous: there is something the guard has to actually stop.
    check("output/personas/ holds images the sweep would otherwise take",
          bool(on_disk), f"{len(on_disk)} file(s) on disk")

    items = B.collect()

    # (2b) Not vacuous the other way: the sweep still returns real work.
    check("collect() still returns images from outside personas/",
          bool(items), f"{len(items)} item(s) swept")

    # (1) The property under test, stated over the whole sweep.
    leaked = sorted(i["rel"] for i in items if _under_personas(i["rel"]))
    check("an unargumented collect() returns ZERO paths under personas/",
          not leaked,
          f"LEAKED {len(leaked)}: {leaked[:5]}" if leaked else
          f"0 of {len(items)} swept")


def main():
    test_sweep_excludes_real_people()
    bad = [r for r in _res if not r[1]]
    print(f"\n{len(_res) - len(bad)}/{len(_res)} checks passed.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
