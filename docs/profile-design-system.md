# The editorial profile system

How `managers.html` works: the visual principles, the schema, the four layout
variants, the asset spec, and how to add a manager or an asset without touching
layout code.

**Visual north star:** `docs/design/profile-reference-blaine.png`.
> ⚠️ That file is **not yet committed**. The reference board exists but has only
> ever been shared in conversation. Drop it at that path so this document points
> at something real — a design doc citing a missing image is the stale-doc
> failure CLAUDE.md rule 11 exists to prevent.

---

## 0. The page is one scroll per group

**`managers.html?group=<id>` is the only profiles route.** There are no
per-manager pages and none should be added. One group is one long editorial
page: a masthead (group name, manager count, standings order), then every
manager in that group as a full-width **section**, stacked in standings order,
read as one continuous magazine.

Each manager is `<article class="pf" id="<manager_id>">`, so `#blaine` links
directly to a section. Everything below — hero, dossier, picks, scouting,
modules, quote, band — is a chapter *within* the scroll, not a page.

Two consequences the design leans on:

- **Variants alternate down the page.** Four sections all set in SIDELINE read
  as four identical stamps, so the `layout` key is chosen for the *rhythm of the
  scroll* as much as for the individual manager. The Panel runs
  sideline → program → sideline → program.
- **The section boundary is a design moment.** Sections butt flush (zero gap)
  and every section ends on the dark ground — the footer band when one is
  authored, the closing strip when not — so the page always reads *dark → hard
  cut → next hero*. No dividers, no floating cards on a shared ground. That
  consistency is why the closing strip is dark for everyone: only 3 of 24
  managers have an authored `footer`, and hanging the rhythm on the band alone
  would give three managers a proper cut and everyone else a trailing edge of
  paper.

**Portraits below the fold are lazy-loaded**, and that is only safe because the
figure reserves the art's true aspect ratio first (§6). The two changes work
together — do not re-enable one without the other.

---

## 1. Principles

The rest of the site is a **box score**: white ground, hairline borders, dense
rows. This page is a **game program**: warm newsprint, black ink, one dominant
accent per person, and type set to be looked at rather than scanned. Both are
the same publication; they are different sections of it.

Five rules the design holds to:

1. **Asymmetry over symmetry.** Portraits bleed past the card edge into the page
   margin. Columns are unequal. Prose runs to a narrower measure than the table
   above it — the width change is what tells you it is reading matter.
2. **One accent, used at three strengths.** Full strength for rules, fills and
   display type; `--profile-accent-deep` for small labels on paper;
   `--profile-accent-on-dark` for anything on the black surfaces. Never a
   second decorative colour.
3. **Distress is built, not baked.** Grain, glyph erosion, torn edges and
   halftone are SVG filters and gradients applied by class. They stay
   retunable, work over art that does not exist yet, and let the asset pipeline
   regenerate a portrait without re-rendering a texture. Print strips them all.
4. **No SaaS furniture.** No drop shadows, no gradients as decoration, no pills,
   no rounded cards, no grid of identical tiles.
5. **Absence composes.** Every block returns markup or nothing. A profile with
   two blocks looks as deliberate as one with eight.

### What is fixed vs per-profile

| Fixed for every profile | Chosen per profile |
|---|---|
| Paper ground, ink, grain | Accent + secondary accent (`theme`) |
| Type scale and families | Layout variant (`layout`) |
| Block order within a variant | Which blocks exist at all |
| The four distress treatments | Which assets exist |
| Which blocks a persona may have | Which blocks it actually authored |

---

## 2. Architecture

```
groups/<id>/personas.json      SOURCE — prose + creative direction (hand-authored)
  │
  ├─ scripts/persona_schema.py     the field contract + fail-loud validation
  └─ scripts/sync_personas.py      projects to the site shape (withholds nothing)
        │
        ▼
docs/data/<id>/personas.json   PUBLISHED — what the browser may see
docs/data/<id>/standings.json  picks, exact arithmetic (unchanged by this system)
docs/data/team_marks.json      team identity  ← scripts/build_team_marks.py
docs/assets/art_slots.json     which picture     (pre-existing indirection)
docs/assets/profiles/heroes.json  art dimensions (pre-existing, now load-bearing)
        │
        ▼
docs/managers.js   composes eight blocks into a variant
docs/profile.css   paints them
```

**`docs/style.css` is not touched by this system.** `profile.css` is linked only
by `managers.html`. That is the guarantee that the standings board, analytics
and the SVP column cannot shift when a profile rule changes — and it is worth
preserving.

**Python computes, JS renders.** Anything that is a *decision* — is this hex
readable on that hex, which register withholds which field, what is this team's
mark — happens in Python and arrives as data.

---

## 3. Schema

All fields live on `groups/<id>/personas.json` under `managers.<manager_id>`.
**Every field below is optional.** 2 of the 24 managers have no persona content
at all and 12 more have only the four original prose fields; the page has to
compose around every absence. What is *not* optional is being well-formed — a
malformed field fails the build and names the offender.

### Published (the page paints these)

| Field | Type | Notes |
|---|---|---|
| `archetype` | string | Eyebrow above the name. Falls back to `epithet`. |
| `thesis` | string | One line under the name. Falls back to `tagline`. |
| `layout` | enum | `sideline` \| `headliner` \| `dossier` \| `program`. Absent = `sideline`. |
| `theme` | object | `accent`, `accent_secondary`, `paper`, `ink` — `#rgb` or `#rrggbb`. |
| `dossier` | object | `role`, `nicknames[]`, `known_for`, `hometown`, `college`, `drafted`, `status`. |
| `modules` | object | Keys: `draft_tendency`, `fatal_flaw`, `running_gag`, `rival`. Each `{label, headline, art}`. |
| `pull_quote` | object | `{text, attribution}`. `text` required if present. |
| `footer` | object | `{left, right}` — **both or neither**. |
| `assets` | object | `hero`, `nameplate`, `signature`, `badge`, `spots[]`. |

### Private (never leaves the repo)

`north_star`, `motifs`, `easter_eggs` — the creative brief that feeds the image
prompt-writer. Same posture as the pre-existing `traits` / `silhouette_cue`:
internal art direction has no surface on the page, so it is not served to a
browser. `scripts/test_persona_schema.py` asserts they never appear in
`docs/data/`.

### The one rule that is easy to get wrong

**`modules` decorates a flat field. It never carries body prose.**

`modules.fatal_flaw` supplies the *label, headline and spot art*; the body text
stays in the flat `fatal_flaw` field. Authoring a module whose flat field is
empty is a build error, because it would render a headline over nothing:

```
FAIL [family/john]: modules.fatal_flaw is authored but the flat `fatal_flaw`
field it decorates is empty. `modules` carries the label/headline/art only --
the body prose stays in the flat field, so this would render a headline with
nothing under it.
```

It is now the only thing standing between an authored module and an empty
body — see §4.

---

## 4. The tone gate (retired 2026-08-25)

**There is no tone gate any more.** Every manager in every group is `roast` and
every authored block publishes. This section is kept because the shape of what
was removed is the argument for how to put it back.

It was three registers, each withholding a set of flat fields, and each
withheld field took its module block with it:

| Register | Withheld | Was used by |
|---|---|---|
| `roast` | nothing | The Panel, The Browns |
| `warm` | `fatal_flaw` | CEC |
| `straight` | `fatal_flaw`, `running_gag`, `rival` | Family — John, Rachel, Vic |

It ran as two independent gates: `sync_personas.py` nulled what a register
withheld *before it left the repo*, and `managers.js` refused to render it even
if it arrived. Anything not one of the three keys fell to `straight`, the most
restrictive, so a typo failed toward the quiet version.

**Why it went.** Family's three straight managers were flipped to roast and
authored the blocks they had been withholding; church's five warm managers went
with them, for the same reason and in the same commit. That left the gate with
no input, and a branch that can never be taken is a branch that has stopped
being tested. `tone` survives as required data — `sync_personas.VALID_TONES` is
`("roast",)` — so the roster cannot drift back into a withholding register by
accident.

**What replaced it is a writing rule, not code:** aim the joke at the board, not
the person. Both retired registers existed because those groups are somebody's
parents, and that fact did not change when the branching did. The eight flaws
authored at the flip are picks-first by construction; a replacement must be too.

**If you need withholding again**, restore a real gate in *both* halves — the
publish path and the page. Do not lean on leaving a field unauthored: an
unauthored block and a withheld one used to be indistinguishable on the page and
are not any more, because nothing is nulled on the way out.

**The acceptance criterion survives the gate and still binds:** a profile with
fewer blocks must not look like a full page with holes. An absent block composes
to the empty string and never enters the markup, so the surviving blocks close
up around it. `family/holly`, roast with no fatal flaw ever authored, is the
worked example.

---

## 5. Layout variants

Same eight blocks, four arrangements. The variant is a data value; there is no
`if (manager === ...)` anywhere in `managers.js`.

| Variant | Composition | Wants | Crops the art? |
|---|---|---|---|
| **SIDELINE** | Portrait left (bleeding into the margin), editorial right. The reference. | A tall portrait, 4:5 or 3:4 | No |
| **HEADLINER** | Full-bleed art, name pulled up over its foot, wide measure below. | Art with headroom, clean lower third | **Yes** — fixed 620px |
| **DOSSIER** | Ruled header, facts as a wide strip, picks promoted to full width. | **No art at all** | **Yes** — 300px letterbox |
| **PROGRAM** | Centred masthead, centred framed plate, dossier beneath it, then picks and scouting in two columns. | A squarer crop | No |

**The crop column is a hard constraint, not a note.** Much of the existing
profile art carries *baked-in lettering* — "I'M A MAN, I'M 40!" across Blaine,
a Colorado scoreboard across Chris, BULLDOGS across David — and that lettering
is the joke. HEADLINER and DOSSIER scale the art to a fixed height and crop it;
assigning either to an asset with baked lettering will cut the joke in half.
**Only give an asset to a cropping variant if you have looked at what the crop
removes.** The Panel alternates sideline/program for exactly this reason.

Choosing a variant is as much about the **rhythm of the scroll** as the
individual: see §0.

**The no-art rule.** SIDELINE, HEADLINER and PROGRAM are all compositions built
around a picture. A manager with no art is forced to DOSSIER — a tier, not a
special case, and currently the state of 10 of the 24 managers. The same
override runs again at runtime if a declared portrait 404s, because a
two-column layout with an empty column is the same failure in a different
costume.

**Adding a variant** means adding one entry to `LAYOUT_TEMPLATES` in
`managers.js`, one block of rules in `profile.css`, and one name to `LAYOUTS`
in `persona_schema.py`. Those three must agree — the schema rejects any value
the templates do not implement.

---

## 6. Responsive

The breakpoint is **900px**. Below it the two column wrappers become
`display: contents`, every block becomes a direct child of the profile grid, and
the reading order is set explicitly with `order`:

> portrait → name → picks → dossier → scouting → modules → quote → band

That is a **recomposition, not a shrink**. Portrait, name and picks come first
because they are what someone opening a profile on a phone came for.

The **picks need no breakpoint of their own.** They were a four-column table
that folded on a *container* query — it had to fold whenever its own column got
narrow, which happened on a phone, in PROGRAM's side column and in HEADLINER's
secondary column, none of which a viewport rule can see. They are now a wrapping
strip of inline units, and wrapping content re-flows into any width by itself,
so the container query is gone and one rule serves all four situations.

That change was made for **scroll cost**, not for tidiness: as a table the block
was 325px per manager — 2,278px across the seven browns profiles, 28% of that
page — to print four facts per pick that the home board and the picks page both
carry in full. On the page whose subject is the person, the picks are a
reference line, not the exhibit. Nothing was lost: the per-pick conference
moved up to the label line as a **deduped spread**, which costs no height at
all and says more than repeating it four times did — this league's rules
require a minimum number of distinct conferences, so `SEC · ACC` is the fact
about somebody's draft. Same source values, same in-season delta, and the call
is still a word in a filled or outlined block so OVER and UNDER are never told
apart by colour alone.

Verified at 1440px and a true 390px across all 24 profiles: no horizontal
overflow anywhere.

---

## 7. Accessibility

- Every accent gets two derived variants so a helmet colour never has to clear
  4.5:1 raw: `--profile-accent-deep` (45% toward black, for small labels on
  paper) and `--profile-accent-on-dark` (50% toward paper, for the dossier card
  and closing band). Both numbers were **measured on the rendered page**, not
  estimated — 32% and 40% were tried first and both still failed.
- Over/Under is carried by **word, shape and colour** — filled block vs
  outlined. Never colour alone.
- Team monogram type colour is computed per team from WCAG luminance
  (`build_team_marks.ink_for`); 21 of 137 primaries need dark type.
- The `<h2>` survives a nameplate image as the accessible name — it is visually
  hidden with `.sr-only`, never `display: none`.
- Portraits are primary content and carry the manager's name as alt text.
  Decorative art (nameplate, signature, badge, spots) is `alt=""`.
- Audited across all 24 profiles at both widths: **zero text under 4.5:1**.

---

## 8. Asset spec

Nothing is generated yet — this pass is architecture only. The page is fully
functional with every asset absent, which is today's state for all 24 managers.

| Slot | Aspect / size | Format | Notes |
|---|---|---|---|
| `hero` | **4:5 or 3:4 portrait**, ≥960px wide | `.webp`, opaque or alpha | PROGRAM prefers ~1:1 |
| `nameplate` | **wide, ~4:1**, transparent | `.webp`/`.png` alpha | Replaces the typographic name |
| `signature` | **wide, ~5:1**, transparent | `.webp`/`.png` alpha | Sits in the pull quote |
| `badge` | **1:1**, ~400px | `.webp` alpha | Centre of the closing band |
| `spots[]` | **1:1 or 4:3**, ~600px | `.webp` alpha | Illustration inside a module |

**Two hard requirements on the hero:**

1. **Reserve a clean flat lower third.** No baked text, numbers or busy detail in
   the bottom ~30%. HTML overlays land there, and HEADLINER pulls the name
   directly over it.
2. **No baked lettering you would ever want mirrored.** The existing 15 `-ripped`
   assets carry text in the pixels ("I'M A MAN, I'M 40!"), which is why the
   layout can never flip them — a `scaleX(-1)` renders it backwards.

**Provenance sidecars.** Each generated asset should land beside a
`<name>.json` recording model, prompt, seed, reference images and date, so a
regenerated asset is reproducible. Not yet implemented.

**Do not mask a torn asset twice.** `art_slots.json` distinguishes the torn cut
(`profile_page_hero`) from the opaque rectangle (`profile_hero`); the synthetic
torn edge is applied only to the latter.

---

## 9. How to add a manager

1. Add them to `groups/<id>/config.json` (`manager_id` is the join key).
2. Add a `managers.<manager_id>` block to `groups/<id>/personas.json`. **`tone`
   is required**; everything else is optional.
3. Run `python scripts/sync_personas.py` and commit the regenerated
   `docs/data/<id>/personas.json`.
4. Run `python -m pytest -q`.

That is the whole procedure. With only `tone` and a `display_name` they get a
real page off their picks alone.

## 10. How to add an asset

1. Drop the file under `docs/assets/profiles/<group>/`.
2. Either name it to match the pattern in `art_slots.json`, or point
   `assets.hero` at it explicitly in the persona (an explicit path wins).
3. Re-run `python scripts/build_profile_heroes.py` so `heroes.json` learns its
   dimensions. **This step is mandatory, not an optimisation.** The manifest is
   now the page's declaration that a file exists: `art_slots.json` resolves a
   *pattern*, so every manager in a group resolves to a path whether or not the
   file was made, and with portraits lazy-loaded the 404 no longer arrives in
   time to correct the layout. Art missing from `heroes.json` is therefore
   treated as no art — and says so with a console warning naming the manager
   and the path.

**Team logos:** drop `docs/assets/logos/<slug>.webp` (`Texas A&M` → `texas-am`)
and re-run `build_team_marks.py`. The pick strip picks them up with no page
change; absent, it draws a team-coloured monogram.

---

## 11. Known gaps

- **Fonts are still CDN.** Every page, including this one, loads Roboto
  Condensed / Inter / JetBrains Mono from Google Fonts. This system adds no new
  font request and every stack ends in a real system fallback, but the profile
  brief calls for self-hosted fonts and that remains **unmet**. Self-hosting is
  a site-wide change affecting all five pages and belongs in its own commit.
- **`docs/design/profile-reference-blaine.png` is missing** (see the top).
- **No local team logos**, so every pick renders a monogram. Note this is a
  deliberate difference from `index.html`, which does hot-link the
  collegefootballdata CDN logos today.
- **No provenance sidecars** on generated assets yet.
- **21 of 24 managers** have no `layout`, `theme` or creative fields and render
  on the fallback tiers. That is the designed state, not a backlog — but
  `docs/profile-creative-template.md` is the form for filling them in.
