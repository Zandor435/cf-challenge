# Profile creative template

The art-direction form for one manager. Fill it in, then transcribe it into
`groups/<group>/personas.json`. The bottom half feeds the image prompt-writer
and never reaches the browser.

Copy the block below per manager. **Leave anything you don't know blank** —
every field is optional and the page composes around absence. An empty field is
always better than an invented one: omitting a man's college costs nothing,
guessing it puts a false fact under his name.

---

## The form

**Manager:** `<manager_id>` · **Group:** `<group>`
**Tone register:** `roast` / `warm` / `straight` — *this decides what may be
printed about them at all. If unsure, choose the quieter one.*

### Identity

| | |
|---|---|
| **Display name** | |
| **Archetype** (eyebrow, 1–4 words) | e.g. "The Agitator" |
| **Thesis** (one line under the name) | e.g. "Sarcasm, sourdough, and a Longhorn in-law situation." |
| **Pull quote** | Something they'd actually say. Skip for `straight` unless it's genuinely theirs. |
| **Footer, left half** | e.g. "Agitates by day." |
| **Footer, right half** | e.g. "Overthinks by night." — **both halves or neither** |

### Dossier — leave blank rather than guess

| | |
|---|---|
| **Role** | |
| **Nicknames** | comma-separated |
| **Known for** | one sentence |
| **Hometown** | |
| **College** | their *alma mater*, never their drafted team |
| **Drafted** | when they joined the pool |
| **Status** | a standing fact, not a joke |

### Modules

Each module is a **label + headline** over a body that already exists as prose
in `personas.json`. The module never carries the body.

> **The headline must not restate the body's opening words.** The first draft of
> Blaine's used the reference board's "Contrarian for the Bit" over prose
> beginning *"Contrarian for the bit…"* — one sentence printed twice at two
> sizes. A headline earns its place by adding an angle the body does not.

| Module | Headline | Available in |
|---|---|---|
| **Draft tendency** | | all registers |
| **Fatal flaw** | | `roast` only |
| **Running gag** | | `roast`, `warm` |
| **Rival** | | `roast`, `warm` |

*A module for a register that withholds it will fail the build. That is
intended — don't work around it.*

### Colour direction

| | |
|---|---|
| **Accent** | `#______` — an **editorial** choice. Their team's colour is a reasonable default, not a requirement. |
| **Secondary accent** | `#______` |

The system derives readable variants automatically (45% darker for small labels
on paper, 50% lighter for the black surfaces), so pick for character, not for
contrast — any hue works.

### Layout

`sideline` (portrait left — the default) · `headliner` (full-bleed, overlapping
name) · `dossier` (no art needed) · `program` (centred, framed, squarer crop)

*Pick from the art you have, not the art you want: choose `dossier` if there is
no portrait, `program` for a square-ish crop, `headliner` for something tall
with headroom.*

---

## Art direction — never published

This half is the brief for the image pipeline. It stays in the repo.

**North star** — one sentence naming the artefact this profile should feel like.
Not a description of the person; a description of the *object*.
> e.g. "A vintage college-football game program that spent a year on the counter
> of a Dallas coffee shop — warm newsprint, heavy condensed ink, one loud
> orange, and a coffee ring somewhere it shouldn't be."

**Motifs (2)** — the visual ideas that should recur.
1.
2.

**Easter eggs (3–5)** — small, findable, never load-bearing.
1.
2.
3.

### Per-asset direction

| Asset | Aspect | Direction |
|---|---|---|
| **Hero** | 4:5 / 3:4 (≈1:1 for `program`) | Subject, wardrobe, setting, crop |
| **Nameplate** | ~4:1, transparent | Lettering style |
| **Signature** | ~5:1, transparent | |
| **Badge** | 1:1 | The seal at the centre of the closing band |
| **Spots** | 1:1 / 4:3 | One per module that wants an illustration |

**Two non-negotiables for the hero:**

1. **Keep the bottom ~30% flat and empty.** No text, no numbers, no busy
   detail — HTML overlays land there and `headliner` pulls the name over it.
2. **No baked lettering.** The existing `-ripped` assets carry text in the
   pixels, which is why the layout can never mirror them.

**Silhouette cue** — the one shape that identifies them at distance (a mullet, a
visor, a backwards cap). This is separate from `traits` and already exists for
the panel; carry it forward.

---

## Worked example

`groups/panel/personas.json` → `blaine` is the reference implementation. Read it
alongside this form. Its `_creative_note` records exactly which values are real
and which are placeholder — **do that too.** Six months from now nobody can tell
a researched fact from an invented one unless the record says so.
