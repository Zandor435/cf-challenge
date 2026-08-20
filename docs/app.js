/* ==========================================================================
   app.js — CFB Pick'Em renderer.
   Reads ONLY site/data/<group>/{standings,projection,timeline}.json, exactly as
   docs/output-contract.md defines them. The engine computes; the site displays.
   No field is read that the contract does not define.

   Sections rendered here (all from the three contract files above):
     hero        — leader + every manager's banked total (standings.json)
     board1      — exact standings, incl. the floor/ceiling range bar
     scoreboard  — client-side re-pivot of standings.json by TEAM, not manager
     board2      — win probabilities from projection.json (degrades alone)
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
const STALE_DAYS = 8;               // STEP 5: cache older than this = visible warning

// Second-tier page nav. HOME and STANDINGS are both this page (it already holds
// both boards); WEEKLY RECAP is svp.html; the rest are honest placeholders.
const PAGE_NAV = [
  { label: 'HOME',         kind: 'home' },
  { label: 'STANDINGS',    kind: 'detail' },
  { label: 'WEEKLY RECAP', kind: 'link', href: 'svp.html' },
  { label: 'PROFILES',     kind: 'soon' },
  { label: 'ANALYTICS',    kind: 'soon' },
];
// The three swappable views. Exactly one is visible at a time.
const VIEWS = { home: 'home-content', detail: 'standings-detail', soon: 'coming-soon' };

// Manager colors are assigned per manager, generically — NOT hardcoded to any
// one group's roster. Managers are sorted by the stable `manager_id` join key
// and indexed into this palette, so a given person keeps the same color across
// renders and every group (4, 5, or 8 managers) gets distinct colors.
const MANAGER_PALETTE = [
  '#FF8C00', '#2FAE39', '#2563EB', '#FF2020',
  '#7C3AED', '#0891B2', '#DB2777', '#B45309',
];

// ---------- helpers --------------------------------------------------------
const $ = (id) => document.getElementById(id);
const show = (el) => { if (el) el.hidden = false; };
const hide = (el) => { if (el) el.hidden = true; };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Every displayed number is rounded to one decimal before render — raw float
// math never leaks into the page. Percentages render as whole percent.
const fmtSigned = (n) => (n > 0 ? '+' : n < 0 ? '' : '') + Number(n).toFixed(1);
const fmtLine = (n) => Number(n).toFixed(1);
const pct = (p) => (Number(p) * 100).toFixed(0) + '%';
const round1 = (n) => Math.round(Number(n) * 10) / 10;

function fmtStamp(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}
function fmtShort(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
function daysSince(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return null;
  return (Date.now() - d.getTime()) / 86400000;
}

// The real-world CFB season for a given date. A season is labeled by the calendar
// year it kicks off (late Aug) and runs through the following January's bowls, so
// Jan–Jul belongs to the PRIOR year's season and Aug onward to the new one. Used
// ONLY as a presentation cue — to detect that we're replaying a season the real
// world has moved past. The engine never reads the clock; this mirrors the
// existing clock-based staleness check.
const SEASON_ROLLOVER_MONTH = 7; // 0-indexed: August
function realWorldSeason(d = new Date()) {
  return d.getMonth() >= SEASON_ROLLOVER_MONTH ? d.getFullYear() : d.getFullYear() - 1;
}

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

// ---------- manager identity (color + initials) ----------------------------
// Built once per render off whatever manager list standings.json returns.
// ---------- contrast fitting ----------------------------------------------
// Team colors are picked for helmets, not for legibility on a white card.
// Measured: Colorado #cfb87c and Wake Forest #ceb888 both sit at 1.94:1 against
// white and effectively disappear; Texas Tech #c30020 is only 2.65:1 on the dark
// hero. So each identity carries TWO fitted variants -- one per ground -- rather
// than the raw hex, keeping the team's hue while clearing WCAG's 3:1 non-text
// threshold. The raw value is kept for large fills, where contrast is moot.
const LIGHT_GROUND = '#ffffff';
const DARK_GROUND = '#1e1e1e';
const MIN_CONTRAST = 3;

function hexToRgb(h) {
  const t = String(h || '').replace('#', '').trim();
  if (!/^[0-9a-fA-F]{6}$/.test(t)) return null;
  return [0, 2, 4].map((i) => parseInt(t.slice(i, i + 2), 16));
}
function rgbToHex(rgb) {
  return '#' + rgb.map((v) => {
    const n = Math.max(0, Math.min(255, Math.round(v)));
    return n.toString(16).padStart(2, '0');
  }).join('');
}
function relLum(rgb) {
  const f = (v) => {
    const x = v / 255;
    return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
}
function contrastRatio(a, b) {
  const la = relLum(a), lb = relLum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
// Walk the color toward black (on a light ground) or white (on a dark one) in
// fixed steps until it clears `target`, then stop. Returns the input untouched
// if it already passes or can't be parsed.
function fitContrast(hex, groundHex, target) {
  const c = hexToRgb(hex), ground = hexToRgb(groundHex);
  if (!c || !ground) return hex;
  const toward = relLum(ground) > 0.5 ? [0, 0, 0] : [255, 255, 255];
  const STEPS = 20;
  let out = c;
  for (let i = 1; i <= STEPS && contrastRatio(out, ground) < target; i += 1) {
    const t = i / STEPS;
    out = c.map((v, k) => v + (toward[k] - v) * t);
  }
  return rgbToHex(out);
}

function buildManagerIdentity(managers, groupId, week) {
  const ids = managers.map((m) => m.manager_id).slice().sort();
  // Manifest is {manager_id: {team, color}}; tolerate the earlier
  // {group: [id, ...]} array shape so a stale file still renders.
  const raw = PORTRAITS[groupId];
  const art = Array.isArray(raw)
    ? Object.fromEntries(raw.map((id) => [id, {}]))
    : (raw || {});
  const map = {};
  managers.forEach((m) => {
    // Poster path is fixed by scripts/prepare_portraits.py, which writes
    // <manager_id>.webp per group.
    const entry = Object.prototype.hasOwnProperty.call(art, m.manager_id)
      ? (art[m.manager_id] || {}) : null;
    const base = entry ? `assets/portraits/${groupId}/${m.manager_id}` : null;
    // Avatar crops come from scripts/build_avatars.py instead:
    // img/avatars/<manager_id>_{56,112}.webp, cut from the kept persona
    // recolor and sized to the 56px .avatar-lg rather than the 256px face
    // crop the poster pipeline emits. The namespace is flat by manager_id --
    // one portrait per person wherever they play. `entry` is still the gate,
    // so a group absent from the manifest (family, church) resolves to null
    // and emits no <img> at all: no request, no 404, no console error.
    // The manager_avatar slot picks WHICH base, `entry` still decides WHETHER
    // there is one. Keeping the gate ahead of the slot is the whole reason a
    // group with no art still costs zero requests: a declared candidate here
    // would otherwise emit an <img> for every manager on every board.
    // Slot candidates are size-suffix bases, not full paths, so the 1x/2x
    // srcset pair below is built the same way it always was.
    const av = entry
      ? (resolveArt(groupId, 'manager_avatar', week, { id: m.manager_id })
        || `img/avatars/${m.manager_id}`)
      : null;
    // Team color when the manager has art, else the derived palette. Still
    // never hardcoded per roster -- the palette remains the fallback path.
    const seed = (entry && entry.color)
      || MANAGER_PALETTE[ids.indexOf(m.manager_id) % MANAGER_PALETTE.length];
    map[m.manager_id] = {
      color: fitContrast(seed, LIGHT_GROUND, MIN_CONTRAST),
      colorDark: fitContrast(seed, DARK_GROUND, MIN_CONTRAST),
      teamColor: seed,
      team: (entry && entry.team) || null,
      initials: initialsOf(m.display_name),
      name: m.display_name,
      poster: base ? `${base}.webp` : null,
      face: av ? `${av}_56.webp` : null,
      face2x: av ? `${av}_112.webp` : null,
    };
  });
  return map;
}
// "Jonathan" -> JO, "Texas A&M John" -> TJ. Two letters keeps every roster's
// initials distinct enough to scan (Gunner/Gayden -> GU/GA).
function initialsOf(name) {
  const words = String(name).trim().split(/\s+/).filter(Boolean);
  if (!words.length) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}
// FALLBACK ONLY. Real abbreviations come from teams_canonical.json; this is
// what a chip shows when a team is missing from that file (or the file itself
// failed to load): first four letters of the first word ("Ohio State" -> OHIO,
// "Ole Miss" -> OLE). Deterministic, and never invents a team field.
function teamAbbr(name) {
  const first = String(name).trim().split(/\s+/)[0] || '?';
  return first.replace(/[^A-Za-z0-9]/g, '').slice(0, 4).toUpperCase() || '?';
}

// ---------- team identity (abbreviation + logo) ----------------------------
// Keyed by `school`, the canonical string pick.team already carries. Empty
// when teams_canonical.json is missing — every consumer falls back to a text
// chip, so this is cosmetic and never blocks a board (same posture as Board 2).
let TEAM_INFO = {};
// { group_id: [manager_id, ...] } from assets/portraits/index.json — which
// managers have approved persona art. Same contract as team logos: we only
// emit an <img> for art we KNOW exists, so a group with none costs no 404s.
let PORTRAITS = {};

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
// (avatar()/renderHero) rather than being duplicated here.
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

// CFBD returns SIXTEEN logo URLs per team — 8 sizes x light/dark, interleaved
// light-then-dark descending 500..16 — so logos[0] is the 500px asset, far too
// big for a 24px chip. Take a small LIGHT variant: 64px covers 2x displays.
function pickLogo(logos) {
  if (!Array.isArray(logos)) return null;
  const urls = logos.filter((u) => typeof u === 'string' && u);
  const light = urls.filter((u) => !u.includes('/logos-dark/'));
  const pool = light.length ? light : urls;
  if (!pool.length) return null;
  return pool.find((u) => u.includes('/64/')) || pool.find((u) => u.includes('/128/')) || pool[0];
}

function buildTeamIndex(canonical) {
  const out = {};
  ((canonical && canonical.teams) || []).forEach((t) => {
    if (t && t.school) {
      out[t.school] = { abbreviation: t.abbreviation || null, logo: pickLogo(t.logos) };
    }
  });
  return out;
}

// One team chip: the real logo where we have one, otherwise the colored
// abbreviation box. Both are emitted; .has-logo hides the text until/unless
// the image fails, which is how the onerror path degrades with no reflow.
function teamMark(team, variant) {
  const info = TEAM_INFO[team];
  const abbr = (info && info.abbreviation) || teamAbbr(team);
  const logo = info && info.logo;
  const text = variant === 'sb'
    ? `<span class="sb-chip">${esc(abbr)}</span>`
    : `<span class="chip">${esc(abbr)}</span>`;
  if (!logo) return `<span class="team-mark mark-${variant}">${text}</span>`;
  return `<span class="team-mark mark-${variant} has-logo">` +
    `<img class="team-logo" src="${esc(logo)}" alt="${esc(abbr)}" loading="lazy">` +
    `${text}</span>`;
}

// Third fallback tier: the URL was present but the image didn't load. Drop the
// img and let the text chip underneath show.
function markLogoFailed(img) {
  const mark = img.closest('.team-mark');
  if (mark) mark.classList.remove('has-logo');
  img.remove();
}
// Same third-tier fallback for a portrait: drop the img, restore the initials.
function markPortraitFailed(img) {
  const av = img.closest('.avatar');
  if (av) av.classList.remove('has-portrait');
  img.remove();
}

function wireImageFallbacks(root) {
  if (!root) return;
  root.querySelectorAll('img.team-logo, img.avatar-img').forEach((img) => {
    const fail = img.classList.contains('avatar-img')
      ? markPortraitFailed : markLogoFailed;
    // Catch images that already failed before this ran (cached 404s).
    if (img.complete && img.naturalWidth === 0) { fail(img); return; }
    img.addEventListener('error', () => fail(img), { once: true });
  });
}

// Size comes from a CSS class (avatar-sm/md/lg), not an inline pixel value, so
// the responsive rules can shrink avatars without fighting inline styles.
function avatar(ident, sizeClass, cls) {
  const vars = `--mc:${ident.color};--mcd:${ident.colorDark || ident.color}`;
  const shell = `class="avatar ${sizeClass} ${cls || ''}`;
  const initials = `<span>${esc(ident.initials)}</span>`;
  if (!ident.face) {
    return `<span ${shell}" style="${vars}">${initials}</span>`;
  }
  // Both are emitted; .has-portrait hides the initials until/unless the image
  // fails (mirrors .has-logo on team marks) so the fallback costs no reflow.
  // alt is empty on purpose: the manager's name is always adjacent as text.
  // The 2x file is only offered when we have one -- an srcset naming a missing
  // asset would put a 404 back on retina displays, which is the whole thing
  // the manifest gate exists to avoid.
  const srcset = ident.face2x
    ? ` srcset="${esc(ident.face)} 1x, ${esc(ident.face2x)} 2x"` : '';
  return `<span ${shell} has-portrait" style="${vars}">` +
    `${initials}<img class="avatar-img" src="${esc(ident.face)}"${srcset} alt="" ` +
    `loading="lazy"></span>`;
}

// ---------- poster lightbox ------------------------------------------------
// Opened from a manager card's rail. One shared overlay, wired once in main();
// per-card buttons only carry the src, so re-renders can't leak listeners.
function openPoster(src, name) {
  const img = $('lightbox-img');
  img.src = src;
  img.alt = `${name} persona art`;
  show($('lightbox'));
  $('lightbox-close').focus();
}

function closePoster() {
  hide($('lightbox'));
  $('lightbox-img').removeAttribute('src');
}

function wirePosters(root) {
  if (!root) return;
  root.querySelectorAll('.poster-btn').forEach((btn) => {
    btn.addEventListener('click', () => openPoster(btn.dataset.poster, btn.dataset.name));
  });
}

function wireLightbox() {
  const lb = $('lightbox');
  if (!lb) return;
  $('lightbox-close').addEventListener('click', closePoster);
  // Backdrop click closes; a click on the image itself must not.
  lb.addEventListener('click', (e) => { if (e.target === lb) closePoster(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !lb.hidden) closePoster();
  });
}

// ---------- masthead / switcher -------------------------------------------
function renderSwitcher(activeId) {
  const nav = $('group-switch');
  const buttons = GROUPS.map((g) => ({ ...g, demo: false }));
  if (activeId === DEMO.id) buttons.push({ ...DEMO, demo: true });
  nav.innerHTML = buttons.map((g) =>
    `<button class="group-btn${g.demo ? ' is-demo' : ''}" data-group="${g.id}" ` +
    `aria-current="${g.id === activeId}">${esc(g.label)}</button>`
  ).join('');
  nav.querySelectorAll('.group-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const url = new URL(location.href);
      url.searchParams.set('group', btn.dataset.group);
      location.href = url.toString();
    });
  });
}

function setView(view) {
  Object.entries(VIEWS).forEach(([k, id]) => (k === view ? show($(id)) : hide($(id))));
  window.scrollTo({ top: 0 });
}

// Page nav. STANDINGS swaps in the detailed per-pick board; PROFILES /
// ANALYTICS swap in a COMING SOON panel. All three are view swaps against
// already-fetched data — no new pages, no routing, no second fetch, and the
// group query param is preserved on links.
function renderPageNav(groupId) {
  const nav = $('page-nav');
  // Scoped mode has to survive every internal hop, or the first click lands
  // back on the master view. `q` goes to setAttribute (raw &); `qAttr` goes
  // into markup (&amp;) — setAttribute would take the entity literally.
  const q = `?group=${encodeURIComponent(groupId)}${isScoped() ? '&scoped=1' : ''}`;
  const qAttr = q.replace(/&/g, '&amp;');
  nav.innerHTML = PAGE_NAV.map((p, i) => {
    if (p.kind === 'link') {
      return `<a class="nav-btn" href="${esc(p.href)}${qAttr}" data-i="${i}">${esc(p.label)}</a>`;
    }
    return `<button class="nav-btn" data-i="${i}"` +
      `${p.kind === 'home' && i === 0 ? ' aria-current="true"' : ''}>${esc(p.label)}</button>`;
  }).join('');

  nav.querySelectorAll('button.nav-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const spec = PAGE_NAV[Number(btn.dataset.i)];
      nav.querySelectorAll('.nav-btn').forEach((b) => b.removeAttribute('aria-current'));
      btn.setAttribute('aria-current', 'true');
      if (spec.kind === 'soon') {
        $('coming-soon').innerHTML =
          `<div class="cs-label">${esc(spec.label)} — Coming soon</div>` +
          `<p class="cs-sub">This section hasn&rsquo;t been built yet.</p>`;
      } else if (spec.kind === 'detail' && !$('standings-detail-list').childElementCount) {
        // Reachable before/without a successful load (pre-draft, load error).
        // Say so rather than showing an empty board.
        $('standings-detail-list').innerHTML =
          `<p class="detail-empty">No standings to show yet — see the notice on the Home tab.</p>`;
      }
      setView(spec.kind);
    });
  });

  // Keep the group selection when navigating to the column / brand home.
  $('brand-link').setAttribute('href', `index.html${q}`);
  const edLink = $('editorial-link');
  if (edLink) edLink.setAttribute('href', `svp.html${q}`);
}

// ---------- provenance strip (STEP 5) -------------------------------------
// One quiet muted line. The season value flows from season.json via the
// engine (meta.season) — nothing is hardcoded here. Replay and staleness are
// inline, small and muted: the accent is reserved for live/leading states.
function renderProvenance(meta) {
  const strip = $('provenance');
  const wk = (meta.as_of_week === null || meta.as_of_week === undefined)
    ? 'live' : `week ${meta.as_of_week}`;
  const dataSeason = Number(meta.season);
  const seasonLabel = Number.isFinite(dataSeason) ? String(dataSeason) : '—';

  const rw = realWorldSeason();
  const age = daysSince(meta.cache_fetched_at);
  const isReplay = Number.isFinite(dataSeason) && dataSeason < rw;
  const isStale = age !== null && age > STALE_DAYS;

  const parts = [
    `<b>${esc(seasonLabel)}${isReplay ? ' replay' : ''}</b>`,
    `scored ${esc(wk)}`,
    `gen ${esc(fmtShort(meta.generated_at))}`,
    `data ${esc(fmtShort(meta.cache_fetched_at))}`,
  ];
  if (isStale) {
    parts.push(`<span class="prov-stale" title="Data last pulled ` +
      `${esc(fmtStamp(meta.cache_fetched_at))} — may not reflect recent games.">` +
      `⚠ ${Math.floor(age)}d old</span>`);
  }
  strip.innerHTML = parts.map((p) => `<span class="prov-item">${p}</span>`)
    .join('<span class="prov-sep">&middot;</span>');
  show(strip);

  // Sample-data banner stays a visible band: dummy picks previewing the board
  // must never read as a real draft.
  const sample = $('sample-banner');
  if (meta.draft_status === 'dummy') {
    sample.textContent =
      `Sample data — the draft has not been entered. These picks are placeholders ` +
      `to preview the board, not real selections.`;
    show(sample);
  } else {
    hide(sample);
  }
}

// ---------- pre-draft state (STEP 3) --------------------------------------
function hasRealPicks(standings) {
  const mgrs = standings.managers || [];
  return mgrs.some((m) => Array.isArray(m.picks) && m.picks.length > 0);
}
function renderPreDraft(groupId, meta) {
  const el = $('predraft');
  el.innerHTML =
    `<div class="predraft-badge">Draft not yet entered</div>` +
    `<h1><span class="predraft-group">${esc(groupLabel(groupId))}</span><br>hasn&rsquo;t drafted yet</h1>` +
    `<p>Rosters are empty until draft day. Once picks are entered, this page fills ` +
    `with the exact standings and the weekly projection.</p>` +
    `<p class="predraft-meta">Want to see the populated layout? ` +
    `<a href="?group=${DEMO.id}">Open the demo fixture &rarr;</a></p>`;
  show(el);
}

// ---------- week-over-week move (timeline.json) ----------------------------
// The contract has no "this week" delta on standings.json, so the move is
// derived from timeline.json — the append-only per-week history — by summing
// each snapshot's picks' banked_delta (which is exactly banked_total). If the
// latest snapshot already equals the live standings, the baseline is the one
// before it. Returns null when there's no honest comparison to draw, and the
// column then renders as an em dash rather than a fabricated zero.
function snapshotTotals(snap) {
  const out = {};
  (snap.managers || []).forEach((m) => {
    out[m.manager_id] = round1((m.picks || [])
      .reduce((s, p) => s + Number(p.banked_delta), 0));
  });
  return out;
}
function computeMoves(standings, timeline) {
  const snaps = (timeline && timeline.snapshots) || [];
  if (snaps.length < 2) return null;

  const current = {};
  (standings.managers || []).forEach((m) => { current[m.manager_id] = round1(m.banked_total); });

  const latest = snapshotTotals(snaps[snaps.length - 1]);
  const ids = Object.keys(current);
  const latestMatchesNow = ids.every((id) => Math.abs((latest[id] ?? NaN) - current[id]) < 1e-9);
  const base = snaps[snaps.length - (latestMatchesNow ? 2 : 1)];
  if (!base) return null;

  const baseTotals = snapshotTotals(base);
  const moves = {};
  ids.forEach((id) => {
    if (id in baseTotals) moves[id] = round1(current[id] - baseTotals[id]);
  });
  if (!Object.keys(moves).length) return null;
  return { week: base.as_of_week, moves };
}

// ---------- Hero banner ----------------------------------------------------
// Leader + every manager's banked total. Only shown once real picks exist —
// the same gate that governs Board 1 / Board 2 vs. the pre-draft state.
// Which groups have a published kickoff banner. Declared in the manifest under
// a reserved key rather than probed by URL, for the same reason portraits are:
// a group without one must cost zero failed requests. BANNER_KEY starts with '$'
// so it can never collide with a group slug (slugs are path/URL segments).
const BANNER_KEY = '$banners';

// Slot first, $banners second. The manifest list stays as the fallback so a
// missing or 404ing art_slots.json leaves banner selection exactly as it was.
function bannerFor(groupId, week) {
  const slot = resolveArt(groupId, 'hero_banner', week);
  if (slot) return slot;
  const list = (PORTRAITS && PORTRAITS[BANNER_KEY]) || [];
  return list.indexOf(groupId) >= 0 ? `assets/banners/${groupId}.webp` : null;
}

function renderHero(standings, ident) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  if (!mgrs.length) return;
  const meta = standings.meta || {};
  const leader = mgrs[0];
  const wk = (meta.as_of_week === null || meta.as_of_week === undefined)
    ? 'Live' : `Week ${meta.as_of_week}`;
  const season = Number.isFinite(Number(meta.season)) ? String(meta.season) : '';

  // The sub-line is derived, never invented: the gap to second place, or a tie.
  let sub;
  if (mgrs.length > 1) {
    const gap = round1(leader.banked_total - mgrs[1].banked_total);
    sub = gap === 0
      ? `Tied with <b>${esc(mgrs[1].display_name)}</b> at <span class="mono">${fmtSigned(leader.banked_total)}</span> banked.`
      : `Banked <span class="mono hero-pos">${fmtSigned(leader.banked_total)}</span> &middot; ` +
        `<span class="mono">${fmtLine(gap)}</span> clear of <b>${esc(mgrs[1].display_name)}</b>.`;
  } else {
    sub = `Banked <span class="mono hero-pos">${fmtSigned(leader.banked_total)}</span>.`;
  }

  const banner = bannerFor(meta.group_id || currentGroupId(), meta.as_of_week);
  // Decorative: the headline below carries the same information as text, so the
  // banner is alt="" rather than duplicating it for a screen reader.
  const bannerHTML = banner
    ? `<div class="hero-banner"><img src="${esc(banner)}" alt="" loading="eager"></div>`
    : '';

  $('hero').innerHTML = bannerHTML +
    `<div class="hero-main">
      <div class="hero-kicker">${esc(season)} CFB Over/Under Challenge &middot; ` +
        `${esc(groupLabel(meta.group_id || currentGroupId()))} &middot; ${esc(wk)}</div>
      <h1 class="hero-title">Current leader: <span>${esc(leader.display_name)}</span></h1>
      <p class="hero-sub">${sub}</p>
    </div>
    <div class="hero-mgrs">` +
      mgrs.map((m) => {
        const id = ident[m.manager_id];
        const neg = m.banked_total < 0;
        return `<div class="hero-mgr">
          ${avatar(id, 'avatar-lg', 'on-dark')}
          <div class="hero-mgr-name">${esc(m.display_name)}</div>
          <div class="hero-mgr-total mono${neg ? ' neg' : ''}">${fmtSigned(m.banked_total)}</div>
        </div>`;
      }).join('') +
    `</div>`;
  // Third-tier fallback, same contract as logos and portraits: if the banner
  // 404s or fails to decode, drop the whole block rather than leaving a gap.
  const bimg = $('hero').querySelector('.hero-banner img');
  if (bimg) {
    const drop = () => { const w = bimg.closest('.hero-banner'); if (w) w.remove(); };
    if (bimg.complete && bimg.naturalWidth === 0) drop();
    else bimg.addEventListener('error', drop, { once: true });
  }
  show($('hero'));
}

// ---------- Board 1 — Standings -------------------------------------------
// Per-pick floor–ceiling bar: muted track, filled floor→ceiling span, marker
// at the current banked position. All pick bars in a group share one scale so
// they read like columns in a box score. This bar is the credibility feature
// of Board 1 — exact, reproducible arithmetic made visible. Keep it.
function rangeBar(floor, ceiling, mark, gMin, gMax, color) {
  const span = (gMax - gMin) || 1;
  const pos = (v) => Math.max(0, Math.min(100, ((v - gMin) / span) * 100));
  const l = pos(floor), r = pos(ceiling), m = pos(mark);
  const zero = pos(0);
  // The delta-0 tick IS the line: banked lands there exactly when the team
  // sits at its win total. Drawn whenever 0 is inside the group scale.
  return `<div class="range" title="Floor ${fmtSigned(floor)} · Banked ${fmtSigned(mark)} · Ceiling ${fmtSigned(ceiling)}">
    <div class="range-track">
      <div class="range-fill" style="left:${l}%;width:${Math.max(r - l, 0.5)}%;background:${color}40"></div>
      ${gMin <= 0 && gMax >= 0 ? `<div class="range-zero" style="left:${zero}%"></div>` : ''}
      <div class="range-mark" style="left:${m}%"></div>
    </div>
  </div>`;
}

function pickRow(p, gMin, gMax, color) {
  const over = p.direction === 'O';
  const cls = p.status === 'DEAD' ? ' dead' : '';
  const rem = p.games_remaining > 0 ? `${p.games_remaining} left` : 'final';
  const stCls = p.status === 'LIVE' ? 'st-live' : p.status === 'CLINCHED' ? 'st-clinched' : 'st-dead';
  const dCls = p.banked_delta > 0 ? ' pos' : p.banked_delta < 0 ? ' neg' : '';
  return `<div class="pick${cls}">
    <div class="pick-line1">
      <div class="pick-team">${esc(p.team)}<span class="conf">${esc(p.conference || '')}</span></div>
      <span class="dir-badge ${over ? 'over' : 'under'}">${over ? 'Over' : 'Under'} ${fmtLine(p.line)}</span>
      <span class="pick-delta mono${dCls}">${fmtSigned(p.banked_delta)}</span>
    </div>
    <div class="pick-sub">
      <span>${p.banked_wins}&ndash;${p.banked_losses}</span>
      <span>${rem}</span>
      <span class="${stCls}">${p.status}</span>
    </div>
    ${rangeBar(p.floor, p.ceiling, p.banked_delta, gMin, gMax, color)}
  </div>`;
}

// Week-over-week move cell — shared by both densities so the two boards can
// never disagree. An em dash when computeMoves() found no honest baseline.
function moveCell(managerId, moves) {
  if (!moves || !(managerId in moves.moves)) return `<div class="mgr-move none mono">—</div>`;
  const v = moves.moves[managerId];
  const cls = v > 0 ? 'pos' : v < 0 ? 'neg' : 'flat';
  const arrow = v > 0 ? '↑' : v < 0 ? '↓' : '·';
  return `<div class="mgr-move ${cls} mono">${arrow} ${fmtSigned(v)}</div>`;
}
function moveHead(moves) {
  return moves ? `Since wk ${esc(String(moves.week))}` : 'Move';
}
function totalCell(m) {
  return `<div class="mgr-total">
    <div class="val mono${m.banked_total < 0 ? ' neg' : ''}">${fmtSigned(m.banked_total)}</div>
    <div class="lbl">Banked</div>
  </div>`;
}
function identityCell(m, id, picks) {
  return `<div class="rank">${m.rank}</div>
    ${avatar(id, 'avatar-md')}
    <div class="mgr-id">
      <div class="mgr-name">${esc(m.display_name)}</div>
      <div class="mgr-sub">${picks.length} team${picks.length === 1 ? '' : 's'}</div>
    </div>`;
}

// The poster art is the joke — its baked-in lettering is the whole point — so
// the rail never crops it, and clicking opens it full-size where the text is
// actually readable. Managers with no art get no rail and no empty gutter.
function posterRail(id) {
  if (!id.poster) return '';
  return `<button type="button" class="poster-btn" data-poster="${esc(id.poster)}"
      data-name="${esc(id.name)}" aria-label="View ${esc(id.name)} full size">
    <img class="poster-img" src="${esc(id.poster)}" alt="${esc(id.name)} persona art"
      loading="lazy">
  </button>`;
}

function managerCard(m, ident, gMin, gMax, moves) {
  const picks = m.picks || [];
  const id = ident[m.manager_id];
  const rail = posterRail(id);
  return `<article class="mgr${rail ? ' has-poster' : ''}" style="--mc:${id.color}">
    ${rail}
    <div class="mgr-main">
      <div class="mgr-row">
        ${identityCell(m, id, picks)}
        ${totalCell(m)}
        ${moveCell(m.manager_id, moves)}
      </div>
      <div class="picks">${picks.map((p) => pickRow(p, gMin, gMax, id.color)).join('')}</div>
    </div>
  </article>`;
}

// ---------- Board 1, compact (Home tab) ------------------------------------
// One row per manager: identity, banked total, week move, and the portfolio as
// abbreviation chips with a status dot. No range bar, record, or O/U line —
// that detail is the STANDINGS tab's job.
const STATUS_DOT = { CLINCHED: 'dot-clinched', DEAD: 'dot-dead', LIVE: 'dot-live' };

function pickChip(p) {
  const dot = STATUS_DOT[p.status] || 'dot-live';
  return `<span class="chip-cell" title="${esc(p.team)} — ${esc(p.status)}">
    ${teamMark(p.team, 'chip')}
    <span class="dot ${dot}"></span>
  </span>`;
}

function compactCard(m, ident, moves) {
  const picks = m.picks || [];
  const id = ident[m.manager_id];
  return `<article class="mgr-c" style="--mc:${id.color}">
    ${identityCell(m, id, picks)}
    ${totalCell(m)}
    ${moveCell(m.manager_id, moves)}
    <div class="chips">${picks.map(pickChip).join('')}</div>
  </article>`;
}

function renderBoard1(standings, ident, moves) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  const meta = standings.meta || {};
  const wk = (meta.as_of_week === null || meta.as_of_week === undefined)
    ? 'Live' : `Week ${meta.as_of_week}`;
  $('board1-week').textContent = wk;

  const head = `<div class="mgr-head mgr-head-c">
    <span>Rank</span><span></span><span>Manager</span><span>Banked</span>
    <span>${moveHead(moves)}</span><span>Portfolio</span>
  </div>`;
  $('standings').innerHTML = head + mgrs.map((m) => compactCard(m, ident, moves)).join('');
  wireImageFallbacks($('standings'));
  show($('board1'));
}

// ---------- Board 1, detailed (STANDINGS tab) ------------------------------
// Rendered once from the same standings.json main() already fetched; the nav
// only toggles its visibility.
function renderStandingsDetail(standings, ident, moves) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  const meta = standings.meta || {};
  $('detail-week').textContent = (meta.as_of_week === null || meta.as_of_week === undefined)
    ? 'Live' : `Week ${meta.as_of_week}`;

  // Shared scale across every pick in the group so bars are comparable.
  const allPicks = mgrs.flatMap((m) => m.picks || []);
  const gMin = Math.min(0, ...allPicks.map((p) => p.floor));
  const gMax = Math.max(0, ...allPicks.map((p) => p.ceiling));

  const head = `<div class="mgr-head">
    <span>Rank</span><span></span><span>Manager</span><span>Banked</span>
    <span>${moveHead(moves)}</span>
  </div>`;
  $('standings-detail-list').innerHTML =
    head + mgrs.map((m) => managerCard(m, ident, gMin, gMax, moves)).join('');
  wireImageFallbacks($('standings-detail-list'));
  wirePosters($('standings-detail-list'));
}

// ---------- Scoreboard — standings.json re-pivoted by team -----------------
// Pure client-side transform: every manager's picks flattened into one row per
// TEAM. Where two managers hold the same team (the draft allows opposite sides)
// the row carries both owners and both sides.
function buildTeamRows(standings) {
  const rows = new Map();
  (standings.managers || []).forEach((m) => {
    (m.picks || []).forEach((p) => {
      if (!rows.has(p.team)) {
        rows.set(p.team, {
          team: p.team,
          conference: p.conference || '',
          banked_wins: p.banked_wins,
          banked_losses: p.banked_losses,
          games_remaining: p.games_remaining,
          owners: [],
          sides: [],
        });
      }
      const row = rows.get(p.team);
      row.owners.push({ id: m.manager_id, name: m.display_name });
      // Collapse identical sides (same line + direction) so two managers on the
      // same side show one O/U value, while opposite sides show both.
      const key = `${p.line}${p.direction}`;
      let side = row.sides.find((s) => s.key === key);
      if (!side) {
        side = { key, line: p.line, direction: p.direction, banked_delta: p.banked_delta, status: p.status };
        row.sides.push(side);
      }
    });
  });
  return [...rows.values()].sort((a, b) => a.team.localeCompare(b.team));
}

function teamRowHTML(r, ident) {
  const owners = r.owners.map((o) =>
    `<span class="sb-owner" style="--mc:${ident[o.id].color}">${esc(o.name)}</span>`).join('<span class="sb-comma">,</span> ');
  // A team held on both sides gets both lines and both deltas, stacked — one
  // row per team, never two.
  const multi = r.sides.length > 1 ? ' is-multi' : '';
  const ou = r.sides.map((s) =>
    `<span>${fmtLine(s.line)}&nbsp;${s.direction === 'O' ? 'O' : 'U'}</span>`).join('');
  const delta = r.sides.map((s) => {
    const cls = s.banked_delta > 0 ? 'pos' : s.banked_delta < 0 ? 'neg' : 'flat';
    return `<span class="${cls}">${fmtSigned(s.banked_delta)}</span>`;
  }).join('');
  const rem = r.games_remaining > 0 ? `${r.games_remaining} left` : 'final';

  // .sb-meta is display:contents on wide screens (its children become grid
  // cells) and a flex row on phones, where the table folds to two lines.
  return `<div class="sb-row">
    ${teamMark(r.team, 'sb')}
    <span class="sb-team">${esc(r.team)}<span class="sb-conf">${esc(r.conference)}</span></span>
    <span class="sb-meta">
      <span class="sb-owners">${owners}</span>
      <span class="sb-ou mono${multi}">${ou}</span>
      <span class="sb-cur mono" title="${esc(rem)}">${r.banked_wins}&ndash;${r.banked_losses}</span>
    </span>
    <span class="sb-delta mono${multi}">${delta}</span>
  </div>`;
}

function renderScoreboard(standings, ident) {
  const rows = buildTeamRows(standings);
  if (!rows.length) return;
  const mid = Math.ceil(rows.length / 2);
  const header = `<div class="sb-row sb-head">
    <span></span><span>Team</span>
    <span class="sb-meta"><span>Owner</span><span>O/U</span><span>Cur</span></span>
    <span>&Delta;</span>
  </div>`;
  const col = (list) => `<div class="sb-col">${header}${list.map((r) => teamRowHTML(r, ident)).join('')}</div>`;
  $('sb-cols').innerHTML = col(rows.slice(0, mid)) + col(rows.slice(mid));
  wireImageFallbacks($('sb-cols'));
  show($('scoreboard'));
}

// ---------- Board 2 — Win probabilities ------------------------------------
// Deliberately minimal: P(win pool) as a bar, plus the projected total. The
// per-pick breakdown is Board 1's job; this board never mimics Board 1's
// certainty. It degrades independently of Board 1.
function projManager(m, ident) {
  const id = ident[m.manager_id];
  const p = Math.max(0, Math.min(1, Number(m.p_win_pool)));
  return `<div class="proj-mgr" style="--mc:${id ? id.color : '#666'}">
    <div class="proj-top">
      <div class="proj-who">
        ${id ? avatar(id, 'avatar-sm') : ''}
        <span class="proj-name">${esc(m.display_name)}</span>
      </div>
      <div class="proj-nums">
        <span class="proj-total mono">${fmtSigned(m.expected_total)}</span>
        <span class="proj-winpool mono">${pct(m.p_win_pool)}</span>
      </div>
    </div>
    <div class="proj-bar"><div class="proj-bar-fill" style="width:${(p * 100).toFixed(1)}%"></div></div>
  </div>`;
}
function renderBoard2(projection, standings, ident) {
  const disc = $('proj-disclaimer');
  const src = (projection.meta && projection.meta.ratings_source) || 'SP+';
  disc.textContent =
    `Model estimate from ${src} ratings — it updates weekly and can be wrong. ` +
    `Board 1 is exact arithmetic.`;

  const mgrs = (projection.managers || []).slice();
  // Order to mirror Board 1 where possible (p_win_pool desc as the contract sorts).
  mgrs.sort((a, b) => (b.p_win_pool - a.p_win_pool) || (b.expected_total - a.expected_total));

  // Staleness: projection generated off an older cache than standings.
  let note = '';
  const pStamp = projection.meta && projection.meta.cache_fetched_at;
  const sStamp = standings.meta && standings.meta.cache_fetched_at;
  if (pStamp && sStamp && new Date(pStamp) < new Date(sStamp)) {
    note = `<div class="proj-stale-note">This projection was built from an earlier data pull ` +
      `(${fmtStamp(pStamp)}) than the standings (${fmtStamp(sStamp)}). ` +
      `It may lag the latest results.</div>`;
  }
  const head = `<div class="proj-head-row">
    <span>Owner</span><span>Proj total &middot; P(win pool)</span>
  </div>`;
  $('projection').innerHTML = note + head + mgrs.map((m) => projManager(m, ident)).join('') +
    `<p class="proj-foot">Percent chance to finish with the group&rsquo;s highest total, ` +
    `from the shared-draw simulation.</p>`;
  wireImageFallbacks($('projection'));
  show($('board2'));
}
function renderBoard2Unavailable(reason) {
  $('board2').querySelector('.proj-disclaimer').textContent =
    'The weekly projection could not be loaded.';
  $('projection').innerHTML = `<div class="board2-unavailable">
    <div class="u-title">Projection unavailable</div>
    <div class="u-sub">${esc(reason)} The projection can fail without affecting the standings ` +
    `— those are exact and always render. Check back after the next update.</div>
  </div>`;
  show($('board2'));
}

// ---------- boot -----------------------------------------------------------
async function main() {
  // Scoped mode is chrome-only: hide the switcher so a per-group link stays on
  // that league. Everything below renders identically either way.
  if (isScoped()) hide($('group-switch'));

  const groupId = currentGroupId();
  $('wordmark-season').textContent = '';

  // Unknown league: name it and stop. No fetch, no board — a wrong ?group=
  // must not quietly serve the first group's numbers.
  if (groupId === null) {
    hide($('loading'));
    $('load-error').innerHTML =
      `<h2>Unknown league &quot;${esc(groupParam())}&quot;.</h2>` +
      `<p>No data was loaded. <a href="index.html">Back to all leagues &rarr;</a></p>`;
    show($('load-error'));
    return;
  }

  renderSwitcher(groupId);
  renderPageNav(groupId);

  // Team identity is shared across groups, so it is fetched alongside (not
  // after) standings — one round trip, and a failure here only costs logos.
  const [standingsRes, teamsRes, portraitsRes, slotsRes] = await Promise.allSettled([
    fetchJSON(`data/${groupId}/standings.json`),
    fetchJSON('data/teams_canonical.json'),
    fetchJSON('assets/portraits/index.json'),
    fetchJSON('assets/art_slots.json'),
  ]);
  TEAM_INFO = teamsRes.status === 'fulfilled' ? buildTeamIndex(teamsRes.value) : {};
  // A missing manifest is normal (no group has art yet) and costs only the
  // portraits — every avatar falls back to initials, nothing else notices.
  PORTRAITS = portraitsRes.status === 'fulfilled' ? (portraitsRes.value || {}) : {};
  // Likewise for art slots: absent means every slot resolves to null, which is
  // the pre-slots behavior verbatim. Nothing on the page requires this file.
  ART_SLOTS = slotsRes.status === 'fulfilled' ? (slotsRes.value || {}) : {};

  let standings;
  try {
    if (standingsRes.status === 'rejected') throw standingsRes.reason;
    standings = standingsRes.value;
  } catch (e) {
    hide($('loading'));
    $('load-error').innerHTML =
      `<h2>Can&rsquo;t load ${esc(groupLabel(groupId))}</h2>` +
      `<p>standings.json is missing or unreadable (${esc(e.message)}). ` +
      `If this group exists, the data may not have been generated yet.</p>`;
    show($('load-error'));
    return;
  }

  const meta = standings.meta || {};
  if (meta.season) $('wordmark-season').textContent = meta.season;
  document.title = `${groupLabel(groupId)} — CFB Pick'Em`;
  renderProvenance(meta);
  hide($('loading'));

  // Pre-draft: first-class state, not a fallback (STEP 3).
  if (!hasRealPicks(standings)) {
    renderPreDraft(groupId, meta);
    return;
  }

  const ident = buildManagerIdentity(standings.managers || [], groupId, meta.as_of_week);

  // Week-over-week move is a nice-to-have: a missing timeline.json must not
  // affect anything else on the page.
  let moves = null;
  try {
    moves = computeMoves(standings, await fetchJSON(`data/${groupId}/timeline.json`));
  } catch (e) {
    moves = null;
  }

  wireLightbox();
  renderHero(standings, ident);
  wireImageFallbacks($('hero'));
  renderBoard1(standings, ident, moves);          // Home tab — compact
  renderStandingsDetail(standings, ident, moves); // Standings tab — full detail
  renderScoreboard(standings, ident);
  show($('editorial'));

  // Board 2 degrades independently of Board 1 (STEP 4).
  try {
    const projection = await fetchJSON(`data/${groupId}/projection.json`);
    renderBoard2(projection, standings, ident);
  } catch (e) {
    renderBoard2Unavailable(`It was not found for this group (${esc(e.message)}).`);
  }
}

main();
