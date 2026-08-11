# Design lab — overnight exploration notes

**Session:** overnight 2026-08-10 → 08-11 · **Scope:** styling only — no Python, no
JSON contract, no engine changes. Everything here lives under `docs/design-lab/`;
the live site got only the Phase 1 chrome fixes.

**Where to look:** [`/design-lab/`](index.html) on the Pages site — six cards, each
opens a full variant page. All variants are self-contained static HTML (inline CSS,
Google Fonts only) — no shared stylesheet, so directions can't bleed into each other.

---

## Phase 1 — what got fixed on the live template

One commit: `fix: clean up dashboard chrome before design exploration`.

1. **Season label — investigated, deliberately NOT flipped to 2026.** The header
   is not hardcoded: it flows `season.json` → engine → `meta.season` → wordmark,
   and *everything* says 2025 (`season.json` has `season: 2025` and
   `cfbd_default_season: 2025`; every `docs/data/*/standings.json` meta agrees).
   The only 2026 anywhere is the win-totals reference file's name and the
   calendar dates in `generated_at`. Showing "2026" would misstate what's on the
   board (2025 replay results), and flipping `season.json` is a pipeline action
   the §6 guard would reject against the 2025 cache tag. What I did instead: the
   label now renders **"2025 replay"** inline, so the value reads deliberate.
   When you flip `season.json` for kickoff, the header follows on the next data
   regen with zero frontend edits. (Also note: GitHub Pages deploys only `docs/`
   — root `season.json` isn't served, so the frontend physically can't read it
   directly without creating a second copy, i.e. a parallel truth.)
2. **Metadata bloat** — the four stacked provenance lines collapsed to one quiet
   mono line: `2025 replay · scored live · gen Aug 10 · data Jul 22 · ⚠ 19d old`.
3. **Staleness** — no longer a full-width wire-red alert box; it's the small
   muted `⚠ Nd old` fragment at the end of that line (full detail in a tooltip).
   The replay banner folded into the season label; the sample-data band stays a
   visible gold-wash strip because dummy picks must never pass as a real draft.
4. **Display font** — verified loading, not assumed: `document.fonts.check()`
   confirms Fraunces 600/900, Plex Sans 400/500, Plex Mono 400/500/700 all
   arrive; computed styles show Fraunces on the wordmark and owner names. This
   one was a non-bug.
5. **Badges** — audited every radius; all chrome already sits at the 2px cap
   (the SaaS-pill radii died in the restyle commit). The round range-bar dot was
   the last soft shape — replaced with a square ink tick.
6. **Range bar** — now an actual range: visible floor→ceiling filled segment on
   a muted track, square ink tick at banked, thin slate tick at delta-0 (which
   *is* the line — banked sits there exactly when a team is at its win total).
   Decided picks (floor = ceiling) keep a minimum 3px fill so they read as a
   settled point, not a missing bar.

## The fixture (why the numbers differ from the live board)

Blaine's real dummy data is fully decided — every pick CLINCHED, floor = ceiling
— which collapses every range bar to a dot and shows zero badge/status variety.
All six variants therefore stage the **same invented week-11 state**: Ohio State
O9.5 8–1 LIVE, Texas A&M O8.5 10–1 CLINCHED, Utah **U**8.5 6–3 LIVE (direction
flipped from his real O to get an under badge on the page), Virginia O7.5 4–6
DEAD. Banked 0.0, floor −4.0, ceiling +5.0, proj +1.5, P(win) 41%. V4 stages the
other three panel managers the same way (real teams/lines, invented states).

## The six variants

**V1 · Warm Broadsheet** (locked baseline) — Fraunces 900/600 + IBM Plex Sans +
IBM Plex Mono on `#F1EDE3`. The direction we sketched in chat, rebuilt as a clean
specimen with the fixed range bars so it can be compared fairly. Its identity is
warmth + restraint: two accents, hairline discipline, solid-vs-washed boards.
*Distinct because:* it's the only one that treats warmth itself as the brand.

**V2 · Editorial Three-Tier** (locked) — Oswald caps (identity) + Source Serif 4
(voice) + IBM Plex Mono (game layer) on starker `#F8F5EE`, 4px section rules,
squared chips, gauge-style range with a red needle. The most conventionally
sports-page direction, on purpose — the control group for "does convention win?"
*Distinct because:* hierarchy comes from rule weight and caps, not color or wash.

**V3 · Manager Dossier** (locked, structural) — Barlow Condensed 900 nameplate +
Newsreader + Plex Mono on deep `#EFE9DC`. The owner is the page: 5:7 portrait
block, 84px name, record-card form strip (projection cells dashed + ≈-prefixed),
picks as a numbered squad, SVP cover plate overlapping the art. Football Manager
× Players' Tribune. *Distinct because:* it changes the information architecture,
not just the skin — and it's the variant that gives the persona art a real job.

**V4 · Overnight Wire** (free, the dense one) — Bitter 900 + JetBrains Mono on
stark `#FAF7EF`. Teletype dispatch: dateline lede, heavy rules, dotted row
separators, the whole panel group — 16 picks — on one screen. Ranges are
bracketed typography `[-1.5 → -0.5 → +1.5]`, DEAD is a rotated rubber stamp,
projection is an italic "advisory" wire. *Distinct because:* it's typography-only
data design — no bars, no chips, no surfaces.

**V5 · Three Mastheads** (free, the SB Nation move) — Libre Franklin 900 +
Spectral + Space Mono on deep cream `#EAE2D0`. One kit, three club identities:
panel = oxblood/solid rule, family = pine/double rule, church = navy/dotted rule.
Two-line vertical O/U (direction over big line number, pure type), Board 2 as a
sidebar rail. *Distinct because:* it designs the *system* — the deliverable is
the identity grammar, not one page.

**V6 · The Ledger** (free, premium-analytical) — Roboto Slab + Roboto Serif +
Roboto Mono on cool `#EEF0EA`. One unified card: floor/bank/ceil as pure numeric
columns, O/U as ruled text (`o 9.5` double-underlined crimson / `u 8.5` single
ink rule), and the projection living *inside* the table as an italic, dashed-off
column plus an italic footer. Certainty is a typographic register: roman = fact,
italic = model. *Distinct because:* it merges the two boards and still keeps the
exact/estimate wall — via type, not layout.

## Tried and thrown away

- **Reading `season.json` directly from the frontend** — Pages only serves
  `docs/`, so that requires committing a second copy of the file; a parallel
  truth (playbook rule 13) to fix a label that isn't actually wrong. Killed it.
- **Wire-red for negative deltas** (classic "in the red") — collided with red
  meaning OVER two inches away; sign characters carry the meaning fine. Killed
  in the restyle and stayed dead through all six variants.
- **V3's SVP cover at 4:5 full-width** — a wall of gold placeholder dominated
  the page; cut to 4:3 (hero portrait keeps the 5:7 card ratio, which is where
  the trading-card proportion actually matters).
- **V4 brackets on phones** — three-value brackets + team + record + delta don't
  fit 390px on one line; below 480px the brackets hide and the delta column
  carries it. That's the real cost of the wire direction, recorded on its card.
- **A shared design-lab stylesheet** — DRY but wrong here; variants must not be
  able to leak into each other, so each page is fully self-contained.

## Things for Z to decide

1. **The strongest seventh direction is probably V3 × V2:** the dossier's
   owner-as-page structure wearing the three-tier type system (Oswald/serif/
   mono), with V1's newsprint palette. Dossier for the owner page, a dense
   V4-style table as the league overview, SVP cover as-is — that's a full site
   grammar: scan (wire table) → profile (dossier) → read (column).
2. **How hard should the exact/projection wall be?** V6 proves italic-vs-roman
   can carry it inside one card; the architecture doc's position (separate,
   washed, dashed board) is V1/V2/V3/V5. Pick a side before any rollout.
3. **Utah's direction was flipped in the fixture** (O→U) purely to get an under
   badge on every page. If that bugs you, the alternative was using Zach as the
   specimen owner (real unders) — say the word and I'll re-cut the fixtures.
4. **V5's per-group accents** break "two accents site-wide." If club identities
   are wanted, the restraint rule needs rewriting as "two accents *per section*."
5. The **persona pipeline stays the art source** — every variant reserves either
   a 5:7 portrait (V3 hero, V1/V2/V4/V5/V6 covers use 5:7 or 4:3 blocks) in
   `--cf-muted-gold`; nothing invents a competing illustration style.
