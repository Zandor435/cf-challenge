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

// GROUPS, DEMO and PAGE_NAV moved to site.js — managers.html needs the same
// league list and the same tab strip, and two copies of "which leagues are
// real" is how a URL starts rendering the wrong board.
const STALE_DAYS = 8;               // STEP 5: cache older than this = visible warning

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
// $ / show / hide / esc / fetchJSON live in site.js, and so do fmtSigned /
// fmtLine / pct. They were duplicated here and in managers.js until a third
// page needed them; the comment that used to sit here called them "this page's
// box-score conventions, not primitives", which stopped being true the moment
// a second page had to match them exactly. They
// ARE the site's rounding convention — every displayed number is rounded to
// one decimal before render, raw float math never leaks into a page, and
// percentages render as whole percent — so they belong in one place.
//
// These are classic scripts sharing one global lexical scope: re-declaring a
// `const` that site.js already declares is not shadowing, it is a
// SyntaxError that kills the whole page. Do not add a local copy back.
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

// fetchJSON and the whole ?group= / ?scoped=1 URL-state block (groupParam,
// isScoped, currentGroupId, groupLabel, navQuery) moved to site.js — every
// page has to agree on what a legal URL means, including the loud null for an
// unknown league.

// ---------- manager identity (color + initials) ----------------------------
// Built once per render off whatever manager list standings.json returns.
// ---------- contrast fitting ----------------------------------------------
// Team colors are picked for helmets, not for legibility on a white card.
// Measured: Colorado #cfb87c and Wake Forest #ceb888 both sit at 1.94:1 against
// white and effectively disappear. So an identity carries a fitted variant
// rather than the raw hex, keeping the team's hue while clearing WCAG's 3:1
// non-text threshold. The raw value is kept for large fills, where contrast is
// moot.
//
// A second variant, fitted against the #1e1e1e ground of the hero band's
// manager strip, used to be built alongside it: Texas Tech #c30020 only reaches
// 2.65:1 there, so white-fitted values were not safe to reuse. That strip is
// gone and every avatar now sits on a white card. fitContrast() still takes the
// ground as an argument, so a dark surface that needs avatars again fits its
// own value; it must not borrow the white-fitted one.
const LIGHT_GROUND = '#ffffff';
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
      teamColor: seed,
      team: (entry && entry.team) || null,
      initials: initialsOf(m.display_name),
      name: m.display_name,
      poster: base ? `${base}.webp` : null,
      face: av ? `${av}_56.webp` : null,
      face2x: av ? `${av}_112.webp` : null,
      // Deep link into the profile page. Built here, once, so both board
      // densities link the same place and the scoped-mode query survives the
      // hop. Every manager gets one -- managers.html renders a real card off
      // standings/projection even for someone with no persona content at all,
      // so there is no such thing as a manager not worth linking to.
      profile: `managers.html${navQuery(groupId).attr}#${encodeURIComponent(m.manager_id)}`,
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
// ART_SLOTS, expandArt() and resolveArt() moved to site.js. managers.html
// resolves profile_hero through the same function this page resolves
// hero_banner and manager_avatar with — one slot resolver, every surface.

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
  const vars = `--mc:${ident.color}`;
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
  // back on the master view. `raw` goes to setAttribute; `attr` goes into
  // markup (&amp;) — setAttribute would take the entity literally. Shared with
  // managers.html via site.js so the two tab strips can't drift apart.
  const { raw: q, attr: qAttr } = navQuery(groupId);
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
  // DELIBERATELY NOT weekLabel(). This strip reports the run's INVOCATION --
  // "scored live" means nobody passed --as-of-week -- while weekLabel() answers
  // "when is this season", which is a different question with a different
  // predicate. Routing this through it would print "scored Preseason", which
  // describes the league rather than the command that produced the file.
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

// ---------- preseason posture ----------------------------------------------
// WHY THIS PAGE CARES. With zero games played, scoring.py's signed_delta
// collapses to +line for every Under and -line for every Over, so a manager's
// banked total is nothing but their lines added up in their picks' directions.
// Board 1 then ranks the group by how many Unders someone drafted; the hero
// names that manager the leader and prints a gap to second; every range marker
// is pinned at one end of its own range (the ceiling for an Under, the floor for
// an Over), so an all-Under manager has zero ceiling left; and the scoreboard's
// delta column is every team's line restated. All of it is arithmetically
// correct and none of it means anything. A reader arriving before kickoff and
// told none of this concludes the engine is broken -- or, worse, believes the
// ordering.
//
// The DETECTION moved to site.js as isPreseasonStandings(), along with
// weekLabel(), the day a third page had to agree with this one about the same
// standings.json. The old comment here claimed analytics.json "carries the
// EFFECTIVE week" and that this page therefore had to differ from it; that was
// wrong -- both files write the --as-of-week argument through verbatim -- and
// the correction is documented at the definitions. Read them there.

// The honesty line under a degenerate card's .card-sub. A distinct element with
// its own rule rather than text appended to the sub, so it cannot be skimmed as
// part of the card's ordinary description -- the same treatment, and the same
// reasoning, as analytics.css's .an-degenerate.
function preseasonNote(id, on, html) {
  const el = $(id);
  if (!el) return;
  if (!on) { hide(el); return; }
  el.innerHTML = html;
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

// ONLY INTEGER-WEEK SNAPSHOTS ARE ELIGIBLE BASELINES. timeline.json also carries
// the preseason snapshot, whose as_of_week is null, and run_groups sorts that row
// LAST (_week_sort_key: "weeks ascending, the unresolvable-week row last"). So the
// moment week 1 is scored the live file reads [week 1, null] and choosing a
// baseline BY POSITION lands on the preseason board: every manager's "move" would
// come out as their entire banked total measured against a restatement of their
// draft, under a column header reading "Since wk null". Filtering to integer weeks
// is the rule analytics.select_prior and build_week_packet already apply on the
// Python side; this was the one reader of the three that did not, so it is the
// only place the null row could still be mistaken for a scored week.
//
// With no earlier scored week the answer is no answer — return null and let the
// column render an em dash. Honest null beats a confident zero, and it beats a
// confident wrong number for exactly the same reason.
function computeMoves(standings, timeline) {
  const snaps = ((timeline && timeline.snapshots) || [])
    .filter((s) => Number.isInteger(s.as_of_week))
    .sort((a, b) => a.as_of_week - b.as_of_week);
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

// Per-file geometry from the rotate manifest, keyed by the same docs-relative
// path that was handed to setArtPool(). It lives here rather than in the pool
// because resolveArt() returns a PATH and deliberately nothing else -- it is
// one resolver for every slot on every page and must not learn what a banner
// is -- so the manifest's width/height/focal are parked beside the pool and
// looked up again once a path has been picked. A path with no entry is the
// normal case, not an error: every `fixed` group has one, and it renders the
// single fixed band .hero-banner img has always described.
let BANNER_META = {};

// An object-position value and nothing else, checked because this string is
// written into a style attribute. Two components, x then y, each a percentage
// or a CSS keyword -- the same shape build_banners.py enforces on focal.json,
// asserted again here because the page must not trust a manifest it fetched.
const FOCAL_RE =
  /^(?:left|center|right|top|bottom|\d{1,3}(?:\.\d+)?%) (?:left|center|right|top|bottom|\d{1,3}(?:\.\d+)?%)$/;

// Below this width:height ratio a banner is poster-shaped rather than
// band-shaped and gets .tall. 2.2 sits in the empty gap between the two
// shapes actually published (2.0 collages, 2.36 and wider everything else),
// so it is a boundary, not a knife edge through the middle of the set.
const TALL_RATIO = 2.2;

// Slot first, $banners second. The manifest list stays as the fallback so a
// missing or 404ing art_slots.json leaves banner selection exactly as it was.
//
// THE FULL CHAIN, and every rung of it is load-bearing:
//   1. the hero_banner slot. For panel that is mode `rotate`, one of the
//      sixteen published banners picked uniformly at random per page load;
//      for family, church and browns it is mode `fixed`, i.e. exactly the one
//      path they resolved to before rotation existed.
//   2. the $banners list in the portraits manifest -> assets/banners/<group>.webp.
//      This is where a rotate group lands when banners.json is missing, empty,
//      404s or does not parse -- panel keeps its single kickoff banner and the
//      masthead is indistinguishable from what shipped before this branch.
//   3. null, and renderHero() emits no banner block at all.
//   4. and if the chosen file 404s or fails to decode, the img.onerror handler
//      below drops the block rather than leaving a gap.
// A rotate group with no manifest therefore degrades one rung, not to nothing.
function bannerFor(groupId, week) {
  const slot = resolveArt(groupId, 'hero_banner', week);
  if (slot) return slot;
  const list = (PORTRAITS && PORTRAITS[BANNER_KEY]) || [];
  return list.indexOf(groupId) >= 0 ? `assets/banners/${groupId}.webp` : null;
}

// Fetch the manifest a `rotate` hero_banner slot names, and hand its paths to
// the slot resolver. Called once at boot, before anything renders.
//
// ONLY groups that declare `rotate` pay for it: family, church and browns are
// `fixed` and this returns without a request, which is the zero-failed-requests
// contract art_slots.json exists to keep. Every failure here is silent ON
// PURPOSE -- a missing or broken manifest must cost the reader the rotation,
// never the masthead, so the pool stays empty and bannerFor() falls to tier 2.
async function loadBannerPool(groupId) {
  const spec = (((ART_SLOTS && ART_SLOTS.groups) || {})[groupId] || {}).hero_banner;
  if (!spec || spec.mode !== 'rotate') return;
  const src = expandArt(String(spec.source || ''), groupId);
  if (!src) return;
  let doc;
  try {
    doc = await fetchJSON(src);
  } catch (e) {
    return;
  }
  // The manifest's own `dir` is the publish path, so the page never hardcodes
  // where banners live -- build_banners.py writes both halves and they cannot
  // drift apart. Trailing slashes trimmed so "a/" + "/b" cannot produce "a//b".
  const dir = String((doc && doc.dir) || '').replace(/\/+$/, '');
  const entries = ((doc && doc.banners) || [])
    .filter((b) => b && typeof b.file === 'string' && b.file);
  if (!dir || !entries.length) return;
  // Geometry is read per entry and each half is independently optional: a
  // banner with unusable dimensions still rotates, it just renders in the old
  // fixed band instead of its own shape. Degrading one image's framing is the
  // right cost here; dropping it from the pool over a bad number would be the
  // manifest quietly shortening the rotation.
  const paths = entries.map((b) => {
    const path = `${dir}/${b.file}`;
    const meta = {};
    const w = Number(b.width), h = Number(b.height);
    // Both, positive, finite. A zero or a missing height would resolve to
    // `aspect-ratio: 1584 / 0` and collapse the box to nothing.
    if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
      meta.ratio = `${w} / ${h}`;
    }
    // Squarer than TALL_RATIO gets the taller band, and the threshold is the
    // whole rule: a poster-format piece stacks its subjects down the frame
    // instead of across it, so a band sized for landscape art cuts one of
    // them off whatever the focal says. Derived from the ratio rather than
    // listed by filename so the next 2:1 piece needs no code change. The two
    // heights live in style.css, where the rest of the band's geometry does.
    if (meta.ratio && w / h < TALL_RATIO) meta.tall = true;
    const focal = typeof b.focal === 'string' ? b.focal.trim() : '';
    if (FOCAL_RE.test(focal)) meta.focal = focal;
    if (meta.ratio || meta.focal) BANNER_META[path] = meta;
    return path;
  });
  setArtPool(groupId, 'hero_banner', paths);
}

// `pre` reframes the headline, and only the headline. Nothing here is
// recomputed or re-ranked for it: re-sorting a preseason board would be this
// page inventing an ordering to replace the one it was given, which is a worse
// lie than the one being fixed. What changes is the CLAIM -- a card-sub note
// cannot defuse an h1 that reads "Current leader", so before kickoff the h1
// stops saying it.
//
// The hero carried a per-manager avatar strip (name + banked total) until it
// was cut: every figure in it was already in the standings board directly
// below, so it was a second, smaller copy of the same table. The band is now
// banner + headline + one derived sub-line and nothing else.
function renderHero(standings, pre) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  if (!mgrs.length) return;
  const meta = standings.meta || {};
  const leader = mgrs[0];
  const wk = weekLabel(meta.as_of_week, pre);
  const season = Number.isFinite(Number(meta.season)) ? String(meta.season) : '';

  // The sub-line is derived, never invented: the gap to second place, or a tie.
  //
  // Preseason emits NO sub-line at all. With nothing played there is no leader,
  // no gap and no tie, so every version of this line describes the draft rather
  // than the season -- and the two things it could say are already said, once
  // each: the h1 states the state, and Board 1's preseason note states the
  // arithmetic. A third telling is not more honest, only longer.
  //
  // Absent rather than blank, the same way bannerHTML is absent when a group
  // has no art. An emitted-but-empty <p class="hero-sub"> would still spend its
  // line-height on the band, which is the gap this is removing.
  let subHTML = '';
  if (!pre) {
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
    subHTML = `<p class="hero-sub">${sub}</p>`;
  }

  const banner = bannerFor(meta.group_id || currentGroupId(), meta.as_of_week);
  // Decorative: the headline below carries the same information as text, so the
  // banner is alt="" rather than duplicating it for a screen reader.
  // Shape and framing ride as custom properties rather than as a width/height
  // pair on the img, because the box is what has to be reserved: see the
  // .hero-banner.sized comment in style.css. `sized` is keyed on the RATIO,
  // not on the meta entry -- a focal with no dimensions has nothing to crop
  // against, so it stays off the class and off the style.
  const bmeta = (banner && BANNER_META[banner]) || null;
  const ratio = bmeta && bmeta.ratio;
  const vars = ratio
    ? `--banner-ratio:${ratio};` + (bmeta.focal ? `--banner-focal:${bmeta.focal};` : '')
    : '';
  const cls = 'hero-banner' + (ratio ? ' sized' : '') + (bmeta && bmeta.tall ? ' tall' : '');
  const bannerHTML = banner
    ? `<div class="${cls}"${vars ? ` style="${esc(vars)}"` : ''}>` +
      `<img src="${esc(banner)}" alt="" loading="eager"></div>`
    : '';

  $('hero').innerHTML = bannerHTML +
    `<div class="hero-main">
      <div class="hero-kicker">${esc(season)} CFB Over/Under Challenge &middot; ` +
        `${esc(groupLabel(meta.group_id || currentGroupId()))} &middot; ${esc(wk)}</div>
      <h1 class="hero-title">${pre
        ? 'Drafted &mdash; <span>no games played</span>'
        : `Current leader: <span>${esc(leader.display_name)}</span>`}</h1>
      ${subHTML}
    </div>`;
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
  // The name is the link to the profile page — the row's own affordance, so
  // no extra chevron or "view" column is needed on either board density.
  return `<div class="rank">${m.rank}</div>
    ${avatar(id, 'avatar-md')}
    <div class="mgr-id">
      <div class="mgr-name"><a class="mgr-profile-link" href="${id.profile}">${esc(m.display_name)}</a></div>
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

function renderBoard1(standings, ident, moves, pre) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  const meta = standings.meta || {};
  $('board1-week').textContent = weekLabel(meta.as_of_week, pre);
  // One line, not two. The static .card-sub describes a board with results in
  // it, which before kickoff there are none of, so it steps aside rather than
  // stacking above a correction that contradicts it. In season the note hides
  // and the sub comes back — never both at once.
  (pre ? hide : show)($('board1-sub'));
  preseasonNote('board1-preseason', pre,
    'Preseason totals are just each manager&rsquo;s picks added up in their chosen ' +
    'direction &mdash; arithmetic, not results. Real scoring starts Week 1.');

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
function renderStandingsDetail(standings, ident, moves, pre) {
  const mgrs = (standings.managers || []).slice().sort((a, b) => a.rank - b.rank);
  const meta = standings.meta || {};
  $('detail-week').textContent = weekLabel(meta.as_of_week, pre);
  preseasonNote('detail-preseason', pre,
    'Preseason &mdash; nothing has been played, so each pick&rsquo;s banked figure is ' +
    'its line restated in the direction it was taken, and every marker sits pinned at ' +
    'one end of its own range: the ceiling for an Under, the floor for an Over. An ' +
    'all-Under manager therefore shows no ceiling left. The ranking is the draft, ' +
    'read back.');

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

function renderScoreboard(standings, ident, pre) {
  const rows = buildTeamRows(standings);
  if (!rows.length) return;
  preseasonNote('sb-preseason', pre,
    'Preseason &mdash; every record below is 0&ndash;0, so the &Delta; column is each ' +
    'team&rsquo;s line restated in its owner&rsquo;s direction rather than anything that ' +
    'has happened.');
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
// Board 2 gets NO .preseason-note. It is the one board on this page that is not
// degenerate before kickoff -- a forward simulation off SP+ ratings says the
// same kind of thing in August as it does in November, and here it already
// disagrees with Board 1's ordering. Hanging the same accent bar on it that the
// broken boards carry would read as a warning about the projection, which would
// be false. It gets one extra sentence in its own muted disclaimer instead.
function renderBoard2(projection, standings, ident, pre) {
  const disc = $('proj-disclaimer');
  const src = (projection.meta && projection.meta.ratings_source) || 'SP+';
  disc.textContent =
    `Model estimate from ${src} ratings — it updates weekly and can be wrong. ` +
    `Board 1 is exact arithmetic.` +
    (pre ? ` With nothing played yet, this is the one board here carrying something `
      + `the draft did not already contain.` : '');

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

// ---------- Editorial card — the current column, on the home view ----------
// Reads the SAME archive svp.html reads (data/<group>/columns/), so the front
// page and the Weekly Recap page cannot disagree about which column is current.
// Two round trips, both after the boards are on screen: the manifest, then the
// newest column's own file.
//
// FAIL-SOFT, AND THE EMPTY STATE IS THE HONEST ONE. No manifest, no entries, or
// a newest file that will not load all land on the same card: the same sentence
// svp.html's teaser carries, and a link to the desk. It never says a column is
// coming, because that is a promise this page cannot keep, and it never leaves
// the slot blank, because the card is already shown by the time this resolves.
//
// LEAD-IN, NOT THE FULL COLUMN — see the note on the card in index.html.
async function renderEditorial(groupId) {
  const headline = $('ed-headline');
  const body = $('ed-body');
  const link = $('editorial-link');
  if (!headline || !body || !link) return;

  // The absent state, written first so every early return below lands on it
  // rather than on an empty card.
  const absent = () => {
    headline.textContent = 'Nothing filed yet';
    body.innerHTML = '<p class="ed-body">The desk is dark until the games start. The ' +
      'first column drops after Week&nbsp;0 &mdash; until then, the board is the whole ' +
      'story.</p>';
    link.textContent = 'Visit the column desk \u2192';
  };

  let index = null;
  try {
    index = await fetchJSON(`data/${groupId}/columns/index.json`);
  } catch (e) {
    index = null;                      // a 404 is the ordinary pre-Week-0 state
  }
  const entries = (index && Array.isArray(index.columns)) ? index.columns : [];
  if (!entries.length) { absent(); return; }

  const entry = entries[0];
  let doc = null;
  try {
    doc = await fetchJSON(`data/${groupId}/columns/${entry.file}`);
  } catch (e) {
    doc = null;
  }
  const paras = ((doc && doc.column && doc.column.paragraphs) || [])
    .filter((x) => typeof x === 'string' && x.trim());
  // A manifest that names a file the site cannot serve is a broken publish, not
  // a reason to invent a column. Say the desk is dark and link to the page that
  // can still list the weeks that do load.
  if (!paras.length) { absent(); return; }

  const meta = doc.meta || {};
  // The locked column format name, the same one svp.html prints. Not a title
  // for THIS week -- no filed column carries one.
  headline.textContent = 'One big thing';

  const bits = [meta.preseason === true ? 'Preseason'
    : (Number.isInteger(meta.week) ? `Week ${meta.week < 10 ? '0' : ''}${meta.week}` : 'Live')];
  const filed = String(meta.generated_at || '').slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(filed)) bits.push(filed);
  $('ed-byline-meta').textContent = bits.join(' \u00b7 ');
  show($('ed-byline'));

  // Same slot, same resolver, same graceful absence as the byline on svp.html.
  const src = resolveArt(groupId, 'svp_column_art', meta.week);
  if (src) {
    const img = $('ed-byline-avatar');
    img.onerror = () => img.remove();
    img.alt = 'Fat Van Pelt';
    img.src = src;
    img.hidden = false;
  }

  body.innerHTML = `<p class="ed-body">${esc(paras[0])}</p>`;
  link.textContent = 'Read the full column \u2192';

  // Only when there IS a back catalogue. "and 0 earlier columns" is the kind of
  // sentence that gets written by a template and read by nobody.
  const earlier = entries.length - 1;
  if (earlier > 0) {
    const el = $('ed-archive');
    el.innerHTML = `${earlier} earlier column${earlier === 1 ? '' : 's'} in the archive.`;
    show(el);
  }
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
  // Sequential, not folded into the round trip above, because WHICH manifest
  // to fetch (or whether to fetch one at all) is stated by the file that just
  // landed. Costs one small request for panel and none for anyone else.
  await loadBannerPool(groupId);

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
  // Out of renderProvenance and into main(), where managers.js and analytics.js
  // call it too: three pages, one helper, one call site apiece.
  renderSampleBanner(meta);
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

  // Preseason is a posture, not a filter: nothing below is recomputed, re-ranked
  // or suppressed because of it. What changes is what the page CLAIMS about the
  // numbers it was handed.
  const pre = isPreseasonStandings(standings);

  wireLightbox();
  // No wireImageFallbacks() here: the hero holds no .avatar-img or .team-logo
  // now that the manager strip is gone, and its banner carries its own error
  // handler inside renderHero.
  renderHero(standings, pre);
  renderBoard1(standings, ident, moves, pre);          // Home tab — compact
  renderStandingsDetail(standings, ident, moves, pre); // Standings tab — full detail
  renderScoreboard(standings, ident, pre);
  show($('editorial'));
  // Not awaited: the column card is the least important thing on this page and
  // must not hold the projection behind two more round trips. It fills itself
  // in when it lands, and lands on its own empty state if it does not.
  renderEditorial(groupId);

  // Board 2 degrades independently of Board 1 (STEP 4).
  try {
    const projection = await fetchJSON(`data/${groupId}/projection.json`);
    renderBoard2(projection, standings, ident, pre);
  } catch (e) {
    renderBoard2Unavailable(`It was not found for this group (${esc(e.message)}).`);
  }
}

main();
