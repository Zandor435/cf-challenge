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

const fmtSigned = (n) => (n > 0 ? '+' : n < 0 ? '' : '') + Number(n).toFixed(1);
const signClass = (n) => (n > 0.0001 ? 'pos' : n < -0.0001 ? 'neg' : 'zero');
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
function daysSince(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return null;
  return (Date.now() - d.getTime()) / 86400000;
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
function renderProvenance(meta) {
  const strip = $('provenance');
  const wk = (meta.as_of_week === null || meta.as_of_week === undefined)
    ? 'Live (current week)' : `Week ${meta.as_of_week}`;
  strip.innerHTML =
    `<span class="prov-item"><b>Scored:</b> ${esc(wk)}</span>` +
    `<span class="prov-item"><b>Generated:</b> ${esc(fmtStamp(meta.generated_at))}</span>` +
    `<span class="prov-item"><b>Data pulled:</b> ${esc(fmtStamp(meta.cache_fetched_at))}</span>`;
  show(strip);

  const age = daysSince(meta.cache_fetched_at);
  const banner = $('stale-banner');
  if (age !== null && age > STALE_DAYS) {
    banner.textContent =
      `These numbers are ${Math.floor(age)} days old (data last pulled ` +
      `${fmtStamp(meta.cache_fetched_at)}). They may not reflect recent games.`;
    show(banner);
  } else {
    hide(banner);
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
function rangeBar(floor, ceiling, mark, gMin, gMax) {
  const span = (gMax - gMin) || 1;
  const pos = (v) => Math.max(0, Math.min(100, ((v - gMin) / span) * 100));
  const l = pos(floor), r = pos(ceiling), m = pos(mark);
  const zero = pos(0);
  return `<div class="range">
    <div class="range-track">
      <div class="range-fill" style="left:${l}%;right:${100 - r}%"></div>
      ${gMin < 0 && gMax > 0 ? `<div class="range-zero" style="left:${zero}%"></div>` : ''}
      <div class="range-mark" style="left:${m}%" title="Banked ${fmtSigned(mark)}"></div>
    </div>
    <div class="range-labels">
      <span class="floor-l">Floor ${fmtSigned(floor)}</span>
      <span class="ceil-l">Ceiling ${fmtSigned(ceiling)}</span>
    </div>
  </div>`;
}

function pickRow(p) {
  const cls = p.status === 'CLINCHED' ? 'clinched' : p.status === 'DEAD' ? 'dead' : '';
  const rec = `<span class="rec"><span class="rec-w">${p.banked_wins}W</span>&ndash;` +
              `<span class="rec-l">${p.banked_losses}L</span></span>`;
  const rem = p.games_remaining > 0 ? `<span>${p.games_remaining} left</span>` : `<span>final</span>`;
  return `<div class="pick ${cls}">
    <div class="pick-flag"></div>
    <div class="pick-main">
      <div class="pick-team">${esc(p.team)}<span class="conf">${esc(p.conference || '')}</span></div>
      <div class="pick-sub">
        <span class="ou">${p.direction} ${p.line}</span>
        ${rec} ${rem}
      </div>
    </div>
    <div class="pick-right">
      <span class="pick-delta ${signClass(p.banked_delta)}">${fmtSigned(p.banked_delta)}</span>
      <span class="status-badge status-${p.status}">${p.status}</span>
    </div>
  </div>`;
}

function managerCard(m, gMin, gMax) {
  const picks = m.picks || [];
  const nClinched = picks.filter((p) => p.status === 'CLINCHED').length;
  const nDead = picks.filter((p) => p.status === 'DEAD').length;
  const nLive = picks.filter((p) => p.status === 'LIVE').length;
  const chips = [];
  if (nClinched) chips.push(`<span class="chip chip-clinched">${nClinched} clinched</span>`);
  if (nDead) chips.push(`<span class="chip chip-dead">${nDead} dead</span>`);
  if (nLive) chips.push(`<span class="chip chip-remain">${nLive} live</span>`);

  return `<details class="mgr"${m.rank === 1 ? ' open' : ''}>
    <summary>
      <div class="mgr-row">
        <div class="rank rank-${m.rank}">${m.rank}</div>
        <div class="mgr-id">
          <div class="mgr-name">${esc(m.display_name)}</div>
          ${chips.length ? `<div class="mgr-status-chips">${chips.join('')}</div>` : ''}
        </div>
        <div class="mgr-total">
          <div class="val ${signClass(m.banked_total)}">${fmtSigned(m.banked_total)}</div>
          <div class="lbl">Banked</div>
        </div>
        <div class="caret">&#9654;</div>
      </div>
      ${rangeBar(m.floor, m.ceiling, m.banked_total, gMin, gMax)}
    </summary>
    <div class="picks">${picks.map(pickRow).join('')}</div>
  </details>`;
}

function renderBoard1(standings) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  const gMin = Math.min(0, ...mgrs.map((m) => m.floor));
  const gMax = Math.max(0, ...mgrs.map((m) => m.ceiling));
  $('standings').innerHTML = mgrs.map((m) => managerCard(m, gMin, gMax)).join('');
  show($('board1'));
}

// ---------- Board 2 — Projected Finish ------------------------------------
function projPickRow(p) {
  return `<div class="proj-pick">
    <span class="pp-team">${esc(p.team)}</span>
    <span class="pp-line">${p.direction} ${p.line}</span>
    <span class="pp-nums"><span class="beat">${pct(p.p_beat_line)}</span> beat &middot; ${fmtSigned(p.expected_delta)} exp</span>
  </div>`;
}
function projManager(m) {
  const lo = m.p05, hi = m.p95, med = m.p50;
  const span = (hi - lo) || 1;
  const medPos = Math.max(0, Math.min(100, ((med - lo) / span) * 100));
  return `<div class="proj-mgr">
    <div class="proj-mgr-head">
      <div class="proj-name">${esc(m.display_name)}</div>
      <div class="proj-winpool">
        <div class="val">${pct(m.p_win_pool)}</div>
        <div class="lbl">Win pool</div>
      </div>
    </div>
    <div class="proj-stats">
      <span><span class="k">Exp total</span> ${fmtSigned(m.expected_total)}</span>
      <span><span class="k">Range</span> ${fmtSigned(m.p05)} &hellip; ${fmtSigned(m.p95)}</span>
      <span><span class="k">Median</span> ${fmtSigned(m.p50)}</span>
    </div>
    <div class="proj-range">
      <div class="proj-range-track">
        <div class="proj-range-fill" style="left:0;right:0"></div>
        <div class="proj-range-med" style="left:${medPos}%"></div>
      </div>
      <div class="proj-range-labels"><span>p05 ${fmtSigned(m.p05)}</span><span>p95 ${fmtSigned(m.p95)}</span></div>
    </div>
    <div class="proj-picks">${(m.picks || []).map(projPickRow).join('')}</div>
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
  $('projection').innerHTML = note + mgrs.map(projManager).join('');
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
