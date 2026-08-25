/* ==========================================================================
   site.js — the primitives every page shares. Loaded BEFORE the page script.

   Why this file exists: managers.html needs the group list, the ?group= /
   ?scoped= routing rules, and resolveArt() — all of which already existed in
   app.js. Copying them would have created a second, silently-drifting truth
   for which leagues are real and which URL is legal, which is exactly the
   failure the loud-unknown-group check below was written to prevent. So they
   moved here and app.js reads them from the same place managers.js does.

   No build step, no modules. These are classic scripts: a top-level const in
   one lands in the shared global lexical scope, so app.js and managers.js see
   these bindings by name, unqualified, exactly as when they were local.

   NOTHING page-specific belongs here. If only one page uses it, it stays in
   that page's script.
   ========================================================================== */
'use strict';

// Frontend presentation config (labels only — NOT a JSON field). The three real
// groups; `test` is the demo fixture, reachable via ?group=test.
// `church` keeps its group_id -- it is the URL and the output path, and those
// are permanent -- but the label everyone actually uses is CEC.
const GROUPS = [
  { id: 'panel',  label: 'The Panel' },
  { id: 'family', label: 'Family League' },
  { id: 'church', label: 'CEC' },
  { id: 'browns', label: 'The Browns' },
];
const DEMO = { id: 'test', label: 'Demo Fixture' };

// Second-tier page nav, shared by every page so the tab strip cannot disagree
// with itself. `kind` says how a page renders the entry, not what it means:
//   home / detail  view swaps that only exist on index.html
//   link           a real page — rendered as an <a> everywhere
//   soon           unbuilt; index.html swaps in its COMING SOON panel, and
//                  pages that have no such panel omit the entry entirely.
//
// No entry is `soon` any more — ANALYTICS was the last one and analytics.html
// is built. The kind is kept in the model rather than deleted with its last
// user: the next unbuilt tab reuses it, and index.html's COMING SOON swap is
// the only behavior that depends on it.
const PAGE_NAV = [
  { label: 'HOME',         kind: 'home',   href: 'index.html' },
  { label: 'STANDINGS',    kind: 'detail', href: 'index.html' },
  { label: 'WEEKLY RECAP', kind: 'link',   href: 'svp.html' },
  { label: 'PROFILES',     kind: 'link',   href: 'managers.html' },
  { label: 'ANALYTICS',    kind: 'link',   href: 'analytics.html' },
];

// ---------- helpers --------------------------------------------------------
const $ = (id) => document.getElementById(id);
const show = (el) => { if (el) el.hidden = false; };
const hide = (el) => { if (el) el.hidden = true; };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function fetchJSON(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

// ---------- persona primitives ---------------------------------------------
// These lived in managers.js until a second view needed the same rules. That
// view (profile.html, the single-manager page) has since been retired and
// managers.html is the only profile surface, but the formatters below are
// still read by three pages, and a delta that reads +2.5 on the standings
// page must not read 2.50 on a profile.

// Number formatting matches the boards exactly — a delta that reads +2.5 on
// the standings page must not read 2.50 on a profile.
const fmtSigned = (n) => (n > 0 ? '+' : n < 0 ? '' : '') + Number(n).toFixed(1);
const fmtLine = (n) => Number(n).toFixed(1);
const pct = (p) => (Number(p) * 100).toFixed(0) + '%';

// A signed percentage, for a CHANGE in probability rather than a level.
// analytics.json's championship_odds[].week_move is the only one on the site
// today: it is p_win_pool minus the prior snapshot's, and a change without its
// sign is unreadable. pct() above is a level, so it carries none.
//
// One decimal, not pct()'s zero: these moves are fractions of a point and
// rounding them to whole percent would print "0%" for most real weeks.
// The sign is decided AFTER rounding — otherwise a move of -0.0004 formats as
// "-0.0%", which claims a direction the rounded number no longer shows.
const fmtSignedPct = (n) => {
  const s = (Number(n) * 100).toFixed(1);
  const v = Number(s);
  return (v > 0 ? '+' : v < 0 ? '' : '') + (v === 0 ? '0.0' : s) + '%';
};

// THE TONE GATE IS GONE, removed 2026-08-25. It withheld fatal_flaw,
// running_gag and rival per register — `straight` withheld all three, `warm`
// withheld the fatal flaw — and it existed because those registers held
// somebody's parents. Every manager in every group is `roast` now, so the gate
// had no input left to act on: family's three straight managers were flipped
// and church's five warm ones with them, all eight authored the blocks they
// had been withholding, and the branching became dead code in the same commit.
// `tone` survives as a DATA field on every persona and is still required by
// sync_personas.py; nothing reads it to decide what renders.
//
// What the registers protected is now a WRITING rule with no code behind it:
// aim the joke at the board, not the person. If a future manager needs blocks
// withheld rather than written carefully, restore a real gate — do not lean on
// leaving the field unauthored, because an unauthored block and a withheld one
// are no longer the same thing.

// One test for "is there anything here". null, "", and whitespace are all the
// same answer, so the render sites never have to ask three questions. The sync
// script guarantees every key EXISTS (null when unauthored, never absent and
// never a "TODO" string) so this only ever has to judge emptiness.
function has(v) {
  return typeof v === 'string' ? v.trim().length > 0 : v !== null && v !== undefined;
}

// ---------- board state: is this before kickoff, and what week is it? -------
// Three pages read the same fact off the same file and answered differently:
// index.html and analytics.html called a preseason board "Preseason" while
// managers.html called identical data "Live". The answer lives here now, so a
// disagreement has to be introduced on purpose rather than by forgetting a page.
//
// ONE PREDICATE, TWO EXPRESSIONS OF IT, AND NEVER A DATE. With zero games
// played, scoring.py's signed_delta collapses to +line for every Under and
// -line for every Over, so every board built on it restates the draft instead
// of reporting a season. The question "has anything been played" is therefore
// asked of the PICKS, never of a calendar and never of meta.as_of_week, and it
// answers itself the moment the first game is banked.
//
// WHY NOT meta.as_of_week. It is tempting and it is wrong for every file on
// this site. output-contract.md's meta block — shared by standings.json,
// projection.json and analytics.json — states that as_of_week "mirrors the
// --as-of-week N flag and is null on a live run", and scoring.py:111 and
// analytics.py:490 both write that argument straight through. A null week means
// "nobody passed --as-of-week", not "nothing has been played", and the two come
// apart on every live run from week 1 onward. Only the timeline snapshot's
// as_of_week is the effective week, and no page reads that for a label.
//
// The two functions below are the SAME TEST against two different shapes, which
// is why they are two functions rather than one with a branch: standings.json
// carries banked_wins / banked_losses per pick and can be asked directly;
// analytics.json carries banked_delta and line, whose equality in absolute
// value is exactly banked_wins == banked_losses == 0. Prefer the standings form
// wherever standings.json is in hand — it is the direct reading — and keep the
// analytics form for the one page that may not have it.
function isPreseasonStandings(standings) {
  const mgrs = (standings && standings.managers) || [];
  let seen = 0;
  const untouched = mgrs.every((m) => (m.picks || []).every((p) => {
    seen += 1;
    return !Number(p.banked_wins) && !Number(p.banked_losses);
  }));
  return seen > 0 && untouched;
}

function isPreseasonAnalytics(a) {
  const mgrs = (a && a.portfolio && a.portfolio.managers) || [];
  let seen = 0;
  const untouched = mgrs.every((m) => (m.picks || []).every((p) => {
    seen += 1;
    return Math.abs(Number(p.banked_delta)) === Math.abs(Number(p.line));
  }));
  return seen > 0 && untouched;
}

// The label every page prints for "when is this". Three states, and the order
// of the tests is the whole content of the function:
//
//   Preseason  nothing has been banked, whatever the flag says.
//   Week N     a replay: somebody passed --as-of-week N, so the number is real
//              and is the most specific thing that can be said.
//   Live       games are banked but no week was pinned. This is the ordinary
//              cron run, and "Live" is the honest answer rather than a week
//              number, because standings.json and analytics.json do not carry
//              the effective week — inventing one here would be this page
//              computing a fact the engine did not emit.
//
// Number.isInteger, not a null check: the contract types as_of_week as an int
// or null, and a non-integer that slipped through should fall to "Live" rather
// than print "Week null".
function weekLabel(week, pre) {
  if (pre) return 'Preseason';
  return Number.isInteger(week) ? `Week ${week}` : 'Live';
}

// ---------- sample-data banner ---------------------------------------------
// The band that says these picks are not a real draft. It was emitted from two
// pages in two places with one identical sentence typed twice, and the third
// page — managers.html, the one that shows every pick a manager holds — had no
// band at all. All four groups are draft_status "dummy" today, so Profiles was
// presenting placeholder selections as somebody's actual roster.
//
// READ OFF standings.json AND NOWHERE ELSE. draft_status lives on that file by
// contract (analytics.json's meta block is spec'd with no extra keys, which is
// why analytics.html fetches standings.json for this one field). Only the
// literal string "dummy" triggers the band: a missing or unrecognised
// draft_status shows nothing rather than guessing, because the failure mode of
// guessing wrong in the loud direction is crying wolf over a real draft.
//
// ANSWERS A DIFFERENT QUESTION FROM weekLabel(), and the two must never be
// merged. This one asks "are these picks real" and clears when the draft is
// entered; preseason asks "has anything been played" and clears when week 1 is
// scored. Two facts on two clocks — one sentence covering both would clear at
// the wrong moment for one of them.
//
// Idempotent, and hides on the negative path rather than only skipping the
// show: a page that re-renders must be able to take the band back down.
function renderSampleBanner(standingsMeta) {
  const el = $('sample-banner');
  if (!el) return false;
  const dummy = !!(standingsMeta && standingsMeta.draft_status === 'dummy');
  if (dummy) {
    el.textContent =
      'Sample data — the draft has not been entered. These picks are placeholders ' +
      'to preview the board, not real selections.';
    show(el);
  } else {
    hide(el);
  }
  return dummy;
}

// ---------- URL state ------------------------------------------------------
// Two optional params. `group` picks the board; `scoped=1` is group-scoped
// mode — the per-group entry URLs (/panel/, /family/, /church/) redirect here
// with it set, which hides the switcher so a shared link opens one league and
// stays there. The root URL carries neither and is the master view.
function groupParam() {
  return new URLSearchParams(location.search).get('group');
}
function isScoped() {
  return new URLSearchParams(location.search).get('scoped') === '1';
}
// Canonical group id, or null when a group param was supplied but names no
// real league. Null is an error the caller must handle: an unknown league used
// to fall back to GROUPS[0], silently rendering The Panel's board under
// someone else's URL. Stopping loudly is the lesser evil. An absent (or empty)
// param still defaults to the first group.
function currentGroupId() {
  const q = groupParam();
  if (!q) return GROUPS[0].id;
  if (q === DEMO.id) return DEMO.id;
  if (GROUPS.some((g) => g.id === q)) return q;
  return null;
}
function groupLabel(id) {
  if (id === DEMO.id) return DEMO.label;
  const g = GROUPS.find((x) => x.id === id);
  return g ? g.label : id;
}

// The query string every internal link must carry, or the first click drops
// the reader out of a scoped league and back onto the master view. `raw` is for
// setAttribute; `attr` is for markup, where a bare & would be parsed as an
// entity. Both, always — getting this wrong is invisible until someone shares
// a link.
function navQuery(groupId) {
  const raw = `?group=${encodeURIComponent(groupId)}${isScoped() ? '&scoped=1' : ''}`;
  return { raw, attr: raw.replace(/&/g, '&amp;') };
}

// ---------- art slots ------------------------------------------------------
// One indirection between "this surface wants a picture" and "here is the
// file", loaded from assets/art_slots.json (schema documented in that file's
// $note). Same posture as the portraits manifest: a 404 leaves this {} and
// every caller falls through to exactly what it did before slots existed.
let ART_SLOTS = {};

// Candidate pools for `rotate` slots, keyed "<group>/<slot>". A rotate slot
// does not list its own candidates: it NAMES a manifest, that manifest is
// fetched at boot, and whoever fetched it hands the resulting paths here. The
// split is deliberate -- resolveArt() stays a pure synchronous "pick one", and
// the knowledge of what a particular manifest looks like stays with the page
// that owns the slot (app.js owns hero_banner and banners.json). Empty is the
// normal state: unfetched, missing, 404, malformed and "declared no art" all
// land on the same empty pool, and therefore on the same fallback.
let ART_POOLS = {};

function poolKey(groupId, slot) {
  return `${groupId}/${slot}`;
}

// Paths must already be docs-relative and fully expanded. Anything that is not
// a non-empty string is dropped rather than published as a broken <img> src.
function setArtPool(groupId, slot, paths) {
  const clean = (Array.isArray(paths) ? paths : [])
    .filter((s) => typeof s === 'string' && s);
  if (clean.length) ART_POOLS[poolKey(groupId, slot)] = clean;
}

// {group} always expands; per-subject slots also pass {id}. Substitution runs
// AFTER selection so which candidate rotates in never depends on the subject.
// An unknown token is left verbatim rather than blanked -- a visibly wrong
// path fails loudly at the img.onerror tier instead of silently resolving to
// some shorter path that happens to exist.
function expandArt(path, groupId, tokens) {
  const t = Object.assign({ group: groupId }, tokens || {});
  return String(path).replace(/\{(\w+)\}/g, (m, k) => (
    Object.prototype.hasOwnProperty.call(t, k) ? String(t[k]) : m));
}

// Resolve ONE slot to ONE path, or null when the group declares no art for it.
// null is the normal case -- family and church have none -- and is what pushes
// the caller onto its existing fallback tiers. This never invents a path and
// never returns a placeholder, so the fallback logic stays in one place
// (avatar()/renderHero/the profile art panel) rather than being duplicated.
//
// THE WEEK RULE, which is the whole reason this takes `week` at all: it is
// standings meta.as_of_week, which is null before the season's first scored
// week. That is TODAY for all three groups, and it is true again at the start
// of every future season. Only an integer can index the list; null, undefined,
// NaN and non-integers all collapse to candidates[0] -- identical to `fixed`.
// A `weekly` slot therefore renders correctly on day one instead of asking for
// candidates[NaN] and blanking the surface it was supposed to fill.
//
// PER-SUBJECT OVERRIDE (`by_id`), and the exact bug it exists to prevent:
// selection runs BEFORE {id} is substituted, which is what keeps rotation from
// depending on who is being rendered. The consequence is that one group-level
// candidate list has to fit every manager in the group -- so a list of three
// {id}_NN variants, written for the one manager who has three, hands the
// manager who has one a _03 that does not exist. Browns is exactly that shape:
// bryan has three images, todd two, everyone else one. `by_id` lets a single
// subject carry their own {mode, candidates} spec; anyone not listed falls
// through to the group's. Rotation still happens strictly within whichever
// spec was chosen, so the ordering rule above is unchanged.
function resolveArt(groupId, slot, week, tokens) {
  const groups = (ART_SLOTS && ART_SLOTS.groups) || {};
  let spec = (groups[groupId] || {})[slot];
  if (!spec) return null;
  const subject = tokens && tokens.id;
  if (subject && spec.by_id &&
      Object.prototype.hasOwnProperty.call(spec.by_id, subject)) {
    spec = spec.by_id[subject];
    if (!spec) return null;
  }
  // `rotate` before the candidates check, because a rotate slot HAS no
  // candidates of its own -- its pool was fetched from the manifest the slot
  // names and handed here via setArtPool(). An empty pool is the same answer
  // as an empty candidate list: null, and the caller falls to its next tier.
  if (spec.mode === 'rotate') {
    const pool = ART_POOLS[poolKey(groupId, slot)];
    if (!Array.isArray(pool) || !pool.length) return null;
    return expandArt(pool[Math.floor(Math.random() * pool.length)],
                     groupId, tokens);
  }
  const list = (Array.isArray(spec.candidates) ? spec.candidates : [])
    .filter((s) => typeof s === 'string' && s);
  // Declared-but-empty is deliberately the same answer as never declared.
  if (!list.length) return null;
  const n = Number(week);
  let pick = list[0];
  if (spec.mode === 'weekly' && Number.isInteger(n)) {
    // Modulo twice: a negative week (replay, backfill) must still land in range
    // -- JS % keeps the sign, and list[-1] is undefined, not the last element.
    pick = list[((n % list.length) + list.length) % list.length];
  } else if (spec.mode === 'random') {
    // Independent of week by definition, so no null-week branch is needed here.
    pick = list[Math.floor(Math.random() * list.length)];
  }
  return expandArt(pick, groupId, tokens);
}
