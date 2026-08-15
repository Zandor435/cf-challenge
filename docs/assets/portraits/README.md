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

1. Drop the images in `assets/portraits/<group_id>/`.
2. Add one line per manager to `manifest.json`, keyed by **`manager_id`** from
   `groups/<group_id>/config.json` — the stable join key, never `display_name`.
3. Commit the images. They are site assets, not engine output; nothing in
   `scripts/` reads or regenerates them, and `docs/output-contract.md` does not
   cover them.

## Sizing

Avatars crop to a circle at 56 / 40 / 28 px (`object-fit: cover`,
`object-position: center top`), so anything square-ish or portrait works and the
head stays in frame. The design-lab direction is a **5:7 portrait** in
`--cf-muted-gold` (`docs/design-lab/NOTES.md`) — source at 5:7 and the same file
serves both the circles today and a dossier hero later. Keep files small (a few
hundred KB each); they ship in the Pages build.

## Where the source art lives

Generated coach profiles live **outside this repo**, on Zach's Windows box under
`C:\Users\zacha\Claude Code\cf-challenge\output\personas\` (see ARCHITECTURE §12).
Copy the chosen ones in by hand — no build step reaches onto a local drive.

### panel

`manifest.json` already lists all four Panel managers (`blaine`, `chris`,
`jonathan`, `zach`) pointing at `panel/<manager_id>.png`. The four images from
`output\personas\Fat friends\Fat...` go here under those names. Rename on copy,
or edit the manifest values to match whatever the files are actually called —
either works, but the extension has to be real.
