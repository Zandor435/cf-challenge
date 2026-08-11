/* ==========================================================================
   app.js — CFB Pick'Em two-board renderer.
   Reads ONLY site/data/<group>/{standings,projection}.json, exactly as
   docs/output-contract.md defines them. The engine computes; the site displays.
   No field is read that the contract does not define.
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

function currentGroupId() {
  const q = new URLSearchParams(location.search).get('group');
  if (q === DEMO.id) return DEMO.id;
  if (GROUPS.some((g) => g.id === q)) return q;
  return GROUPS[0].id;
}
function groupLabel(id) {
  if (id === DEMO.id) return DEMO.label;
  const g = GROUPS.find((x) => x.id === id);
  return g ? g.label : id;
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

// ---------- provenance strip (STEP 5) -------------------------------------
// One quiet muted line. The season value flows from season.json via the
// engine (meta.season) — nothing is hardcoded here. Replay and staleness are
// inline, small, slate: wire-red is reserved for OVER badges and live states.
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

  // Sample-data banner stays a visible band (gold wash, not accent-burning):
  // dummy picks previewing the board must never read as a real draft.
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

// ---------- Board 1 — Standings -------------------------------------------
// Per-pick floor–ceiling bar: muted track, filled floor→ceiling span, marker
// at the current banked position. All pick bars in a group share one scale so
// they read like columns in a box score.
function rangeBar(floor, ceiling, mark, gMin, gMax) {
  const span = (gMax - gMin) || 1;
  const pos = (v) => Math.max(0, Math.min(100, ((v - gMin) / span) * 100));
  const l = pos(floor), r = pos(ceiling), m = pos(mark);
  const zero = pos(0);
  // The delta-0 tick IS the line: banked lands there exactly when the team
  // sits at its win total. Drawn whenever 0 is inside the group scale.
  return `<div class="range" title="Floor ${fmtSigned(floor)} · Banked ${fmtSigned(mark)} · Ceiling ${fmtSigned(ceiling)}">
    <div class="range-track">
      <div class="range-fill" style="left:${l}%;width:${Math.max(r - l, 0.5)}%"></div>
      ${gMin <= 0 && gMax >= 0 ? `<div class="range-zero" style="left:${zero}%"></div>` : ''}
      <div class="range-mark" style="left:${m}%"></div>
    </div>
  </div>`;
}

function pickRow(p, gMin, gMax) {
  const over = p.direction === 'O';
  const cls = p.status === 'DEAD' ? ' dead' : '';
  const rem = p.games_remaining > 0 ? `${p.games_remaining} left` : 'final';
  const stCls = p.status === 'LIVE' ? 'st-live' : p.status === 'CLINCHED' ? 'st-clinched' : 'st-dead';
  return `<div class="pick${cls}">
    <div class="pick-line1">
      <div class="pick-team">${esc(p.team)}<span class="conf">${esc(p.conference || '')}</span></div>
      <span class="dir-badge ${over ? 'over' : 'under'}">${over ? 'Over' : 'Under'} ${fmtLine(p.line)}</span>
      <span class="pick-delta">${fmtSigned(p.banked_delta)}</span>
    </div>
    <div class="pick-sub">
      <span>${p.banked_wins}&ndash;${p.banked_losses}</span>
      <span>${rem}</span>
      <span class="${stCls}">${p.status}</span>
    </div>
    ${rangeBar(p.floor, p.ceiling, p.banked_delta, gMin, gMax)}
  </div>`;
}

function managerCard(m, groupName, gMin, gMax) {
  const picks = m.picks || [];
  return `<article class="mgr">
    <div class="mgr-row">
      <div class="rank rank-${m.rank}">${m.rank}</div>
      <div class="mgr-id">
        <div class="mgr-name">${esc(m.display_name)}</div>
        <div class="mgr-group-label">${esc(groupName)}</div>
      </div>
      <div class="mgr-total">
        <div class="val">${fmtSigned(m.banked_total)}</div>
        <div class="lbl">Banked</div>
      </div>
    </div>
    <div class="picks">${picks.map((p) => pickRow(p, gMin, gMax)).join('')}</div>
  </article>`;
}

function renderBoard1(standings) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  const groupName = groupLabel((standings.meta || {}).group_id || currentGroupId());
  // Shared scale across every pick in the group so bars are comparable.
  const allPicks = mgrs.flatMap((m) => m.picks || []);
  const gMin = Math.min(0, ...allPicks.map((p) => p.floor));
  const gMax = Math.max(0, ...allPicks.map((p) => p.ceiling));
  $('standings').innerHTML = mgrs.map((m) => managerCard(m, groupName, gMin, gMax)).join('');
  show($('board1'));
}

// ---------- Board 2 — Projected finish ------------------------------------
// Deliberately minimal: projected total delta + P(win pool) only. The per-pick
// breakdown is Board 1's job; this board never mimics Board 1's certainty.
function projManager(m) {
  return `<div class="proj-mgr">
    <div class="proj-name">${esc(m.display_name)}</div>
    <span class="proj-total">${fmtSigned(m.expected_total)}</span>
    <span class="proj-winpool">${pct(m.p_win_pool)}</span>
  </div>`;
}
function renderBoard2(projection, standings) {
  const disc = $('proj-disclaimer');
  const src = (projection.meta && projection.meta.ratings_source) || 'SP+';
  disc.textContent =
    `Model estimate from ${src} ratings — it updates weekly and can be wrong. ` +
    `Board 1 above is exact arithmetic.`;

  const mgrs = (projection.managers || []).slice();
  // Order to mirror Board 1 where possible (p_win_pool desc as the contract sorts).
  mgrs.sort((a, b) => (b.p_win_pool - a.p_win_pool) || (b.expected_total - a.expected_total));

  // Staleness: projection generated off an older cache than standings.
  let note = '';
  const pStamp = projection.meta && projection.meta.cache_fetched_at;
  const sStamp = standings.meta && standings.meta.cache_fetched_at;
  if (pStamp && sStamp && new Date(pStamp) < new Date(sStamp)) {
    note = `<div class="proj-stale-note">This projection was built from an earlier data pull ` +
      `(${fmtStamp(pStamp)}) than the standings above (${fmtStamp(sStamp)}). ` +
      `It may lag the latest results.</div>`;
  }
  const head = `<div class="proj-head-row">
    <span>Owner</span><span>Proj total</span><span>P(win pool)</span>
  </div>`;
  $('projection').innerHTML = note + head + mgrs.map(projManager).join('');
  show($('board2'));
}
function renderBoard2Unavailable(reason) {
  $('board2').querySelector('.proj-disclaimer').textContent =
    'The weekly projection could not be loaded.';
  $('projection').innerHTML = `<div class="board2-unavailable">
    <div class="u-title">Projection unavailable</div>
    <div class="u-sub">${esc(reason)} The projection can fail without affecting the standings ` +
    `above — those are exact and always render. Check back after the next update.</div>
  </div>`;
  show($('board2'));
}

// ---------- boot -----------------------------------------------------------
async function main() {
  const groupId = currentGroupId();
  $('wordmark-season').textContent = '';
  renderSwitcher(groupId);

  let standings;
  try {
    standings = await fetchJSON(`data/${groupId}/standings.json`);
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

  renderBoard1(standings);

  // Board 2 degrades independently of Board 1 (STEP 4).
  try {
    const projection = await fetchJSON(`data/${groupId}/projection.json`);
    renderBoard2(projection, standings);
  } catch (e) {
    renderBoard2Unavailable(`It was not found for this group (${esc(e.message)}).`);
  }
}

main();
