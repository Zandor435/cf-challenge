# Coach profile portraits

Static images for the manager avatars on the site. Committed files only — there
is no upload path and no image host (GitHub Pages serves `docs/` as-is).

## How it wires up

`manifest.json` maps `group_id -> manager_id -> file` (paths relative to this
folder). `docs/app.js` reads it once at boot and renders the portrait inside the
avatar disc; anyone not listed keeps the initials disc. Three tiers, all silent:

1. Listed + file loads → portrait.
2. Not listed → initials (what every group did before portraits existed).
3. Listed but the file 404s → initials, via the `error` handler in `app.js`.

Because of tier 3, a wrong extension or a file that was never copied in looks
exactly like "no portrait yet" — if a portrait doesn't show up, check the
filename in `manifest.json` against what's actually on disk.

## Adding portraits for a group

Use `scripts/make_avatars.py` — it crops, shrinks, names, and updates
`manifest.json` in one pass:

```
python scripts/make_avatars.py --src "<folder of source art>" --group panel
```

Add `--dry-run` first to see the mapping without writing anything. It matches
source filenames to managers by name; when the filenames are opaque
(`persona_01.jpg`), it refuses to guess and prints the exact `--map` line to
paste back:

```
python scripts/make_avatars.py --src ... --map blaine="persona_01.jpg" --map chris="persona_02.jpg"
```

Then commit the PNGs it wrote. Doing it by hand instead is fine — drop files in
`assets/portraits/<group_id>/` and add one line per manager to `manifest.json`,
keyed by **`manager_id`** from `groups/<group_id>/config.json` (the stable join
key, never `display_name`).

Either way these are site assets, not engine output: nothing in the pipeline
reads or regenerates them, and `docs/output-contract.md` does not cover them.

## Sizing

Avatars crop to a circle at 56 / 40 / 28 CSS px (`object-fit: cover`,
`object-position: center top`), so anything square-ish or portrait works and the
head stays in frame. `make_avatars.py` writes **256 px squares** — enough for a
4x display, ~7-100 KB per file depending on the art — cropped high (`--anchor
upper`) to match that CSS, so a pre-squared file passes through the browser's
crop untouched.

Keep them small: there is no image host and no resizing at request time, so the
committed file is what every visitor downloads with the Pages build. Don't
commit 4 MB originals.

The design-lab direction is a **5:7 portrait** in `--cf-muted-gold`
(`docs/design-lab/NOTES.md`). Keep the 5:7 originals outside the repo — the same
source then feeds both the circles today and a dossier hero later.

## Where the source art lives

Generated coach profiles live **outside this repo**, on Zach's Windows box under
`C:\Users\zacha\Claude Code\cf-challenge\output\personas\` (see ARCHITECTURE §12).
Copy the chosen ones in by hand — no build step reaches onto a local drive.

### panel

The four profiles in `output\personas\Fat friends\Fat...` are Panel's. Point
the script at that folder and it does the rest:

```
python scripts/make_avatars.py --src "C:\Users\zacha\Claude Code\cf-challenge\output\personas\Fat friends\Fat" --dry-run
```

Check the mapping it prints, drop `--dry-run`, commit the four PNGs.
`manifest.json` already lists all four managers at `panel/<manager_id>.png`, so
even a hand copy only needs the files named `blaine.png`, `chris.png`,
`jonathan.png`, `zach.png`.
