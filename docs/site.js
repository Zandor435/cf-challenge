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
// These four lived in managers.js until a second view needed the same rules.
// That view (profile.html, the single-manager page) has since been retired and
// managers.html is the only profile surface, but these stay here for the reason
// at the top of this file: a second copy of the tone gate is a second,
// silently-drifting answer to "may this person be captioned with a fatal
// flaw", and that is the one question on this site where drift is not a
// cosmetic bug. The formatters below are still read by three pages.

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

// THE TONE GATE. tone decides whether a person gets a "fatal flaw", a running
// gag, and a named rival printed under their name. Three registers:
//
//   roast     everything present renders. Panel, Browns.
//   warm      running gag and rival render; a fatal flaw NEVER does. CEC —
//             the jokes aim at picks, not people, but the affectionate gag
//             each manager has and the one real rivalry in the group are the
//             point of the page and are not "flaws".
//   straight  the neutral blocks + draft_tendency ONLY. Family's John,
//             Rachel and Vic.
//
// Withheld blocks are not rendered, not hidden with CSS — they never enter
// the markup.
//
// ANY tone that is not one of the three keys below is treated as `straight`,
// the most restrictive. That direction is deliberate: a typo, a missing
// field, or a persona file written before tone existed must fail toward the
// quiet version, because the failure mode in the other direction is somebody's
// father captioned with a fatal flaw on a page his family reads.
// scripts/sync_personas.py already nulls each register's withheld fields
// before publishing, so this is the second of two independent gates.
const TONE_BLOCKS = {
  roast:    { fatal_flaw: true,  running_gag: true,  rival: true },
  warm:     { fatal_flaw: false, running_gag: true,  rival: true },
  straight: { fatal_flaw: false, running_gag: false, rival: false },
};

function toneOf(p) {
  const t = p && p.tone;
  return Object.prototype.hasOwnProperty.call(TONE_BLOCKS, t)
    ? TONE_BLOCKS[t] : TONE_BLOCKS.straight;
}

// One test for "is there anything here". null, "", and whitespace are all the
// same answer, so the render sites never have to ask three questions. The sync
// script guarantees every key EXISTS (null when unauthored, never absent and
// never a "TODO" string) so this only ever has to judge emptiness.
function has(v) {
  return typeof v === 'string' ? v.trim().length > 0 : v !== null && v !== undefined;
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
