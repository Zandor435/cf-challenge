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
const GROUPS = [
  { id: 'panel',  label: 'The Panel' },
  { id: 'family', label: 'Family League' },
  { id: 'church', label: 'Church League' },
];
const DEMO = { id: 'test', label: 'Demo Fixture' };

// Second-tier page nav, shared by every page so the tab strip cannot disagree
// with itself. `kind` says how a page renders the entry, not what it means:
//   home / detail  view swaps that only exist on index.html
//   link           a real page — rendered as an <a> everywhere
//   soon           unbuilt; index.html swaps in its COMING SOON panel, and
//                  pages that have no such panel omit the entry entirely.
const PAGE_NAV = [
  { label: 'HOME',         kind: 'home',   href: 'index.html' },
  { label: 'STANDINGS',    kind: 'detail', href: 'index.html' },
  { label: 'WEEKLY RECAP', kind: 'link',   href: 'svp.html' },
  { label: 'PROFILES',     kind: 'link',   href: 'managers.html' },
  { label: 'ANALYTICS',    kind: 'soon' },
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
function resolveArt(groupId, slot, week, tokens) {
  const groups = (ART_SLOTS && ART_SLOTS.groups) || {};
  const spec = (groups[groupId] || {})[slot];
  if (!spec) return null;
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
