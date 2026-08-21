/* ==========================================================================
   analytics.js — Board 3 (analytics.html).

   THIS PAGE COMPUTES NOTHING. That is the contract, not a style preference:
   docs/output-contract.md states it twice, once generally and once again under
   analytics.json because this is the file where it is easiest to break. Every
   ratio, every delta, every rank and every sort order arrives already computed
   from scripts/analytics.py. If the page wants a number, the engine gets a key.

   The single exception is bar geometry — the clamped `(p * 100).toFixed(1)`
   that sets a CSS width, exactly as app.js does for the win-probability bar.
   That is a length in a style attribute, not a fact about the league.

   GROUP-GENERIC BY CONSTRUCTION. No roster, no manager id, no group path.

   Reads, in one Promise.all:
     data/<group>/analytics.json    REQUIRED — every module on this page
     data/<group>/standings.json    optional — meta.draft_status, for the
                                    sample-data banner ONLY. analytics.json
                                    deliberately carries no draft_status.
     data/<group>/projection.json   optional — meta.ratings_source, for the
                                    projection disclaimer's wording ONLY. The
                                    odds themselves come from analytics.json,
                                    already plumbed from the projector.

   Only analytics.json is required. The other two are allowed to 404: the
   banner is simply not shown, and the disclaimer names the contract's default
   ratings source. Neither absence removes a number from the page.

   BOARD SEPARATION is the one rule in here that is not cosmetic. Four modules
   are Board 3 exact arithmetic; championship_odds is a Board 2 projection.
   They render into two sibling sections that never interleave, and the
   projection carries its label and its disclaimer wherever it goes.

   fmtSigned / fmtLine / pct / fmtSignedPct live in site.js and are in scope
   here by name — these are classic scripts sharing one global lexical scope,
   and re-declaring one would be a SyntaxError that kills the whole page.
   ========================================================================== */
'use strict';

// The only formatter this page needs that no other page does, so per site.js's
// own rule it stays local. race[].week_move is signed PLACES — an integer, not
// a quantity — so fmtSigned's fixed decimal would print "+2.0 places climbed".
// fmtSignedPct (site.js) handles championship_odds[].week_move, which is a
// signed change in probability and a different kind of number entirely.
const fmtPlaces = (n) => (Number(n) > 0 ? '+' : '') + String(n);

// Missing is an em dash, never 0 and never blank: on this page a blank cell
// and a zero are both claims, and "no prior snapshot to measure against" is
// neither of them.
const DASH = '&mdash;';
const orDash = (v, fmt) => (v === null || v === undefined ? DASH : fmt(v));

// Sign class shared by every delta cell, matching managers.js.
const signCls = (n) => (Number(n) > 0 ? ' pos' : Number(n) < 0 ? ' neg' : '');

// ---------- preseason detection -------------------------------------------
// WHY THIS EXISTS. With zero games played, banked_delta collapses to ±line for
// every pick, and the consequences run through four of the five modules:
// `race` effectively ranks managers by how many Unders they drafted,
// `best_worst` ties up to seven ways at identical deltas, every manager lands
// in controls_destiny or needs_help, and `portfolio` measures concentration
// against lines rather than results. Each of those is arithmetically correct
// and semantically empty, and a reader who is not told will read a restatement
// of the draft as a result.
//
// DETECTED FROM THE DATA, NEVER FROM A DATE, so it clears itself the moment
// week 1 is scored with no edit here. Two independent signals, either of which
// is sufficient:
//   1. meta.as_of_week is null — no week has been scored at all.
//   2. every pick in every portfolio still shows |banked_delta| == |line|,
//      which is exactly banked_wins == 0 everywhere. This is the fallback for
//      a run that carries a week but has not yet banked a game inside it.
function isPreseason(a) {
  const meta = a.meta || {};
  if (meta.as_of_week === null || meta.as_of_week === undefined) return true;
  const mgrs = (a.portfolio && a.portfolio.managers) || [];
  if (!mgrs.length) return false;
  let seen = 0;
  const untouched = mgrs.every((m) => (m.picks || []).every((p) => {
    seen += 1;
    return Math.abs(Number(p.banked_delta)) === Math.abs(Number(p.line));
  }));
  return seen > 0 && untouched;
}

// The honesty line under a degenerate module's .card-sub. Rendered as a
// distinct element rather than appended to the sub, so it cannot be skimmed as
// part of the module's ordinary description.
const degenerate = (on, text) =>
  (on ? `<p class="an-degenerate">${text}</p>` : '');

// ---------- card shell -----------------------------------------------------
// One head for every module, carrying its board's tag. "exact" gets the amber
// .tag-exact; "projection" gets the grey .tag-proj. A module that somehow
// shipped without a board field gets no tag at all rather than a guessed one —
// an untagged number is a bug to notice, an mislabelled one is a lie.
function cardHead(title, board, sub, extra) {
  const tag = board === 'exact'
    ? '<span class="card-tag tag-exact">Exact</span>'
    : board === 'projection'
      ? '<span class="card-tag tag-proj">Projection</span>' : '';
  return `<div class="card-head">
      <h2 class="card-title">${title}</h2>
      ${tag}
    </div>
    ${sub ? `<p class="card-sub">${sub}</p>` : ''}
    ${extra || ''}`;
}

// ---------- 1. race — board: exact ----------------------------------------
// Rendered in the order the engine emitted (standings order, by rank). No sort
// here; the ordering is a computed value like any other.
function renderRace(race, pre) {
  if (!race) return '';
  const mgrs = race.managers || [];
  const sub = 'Standings order. Gap is the leader&rsquo;s banked total minus yours; ' +
    'ceiling left is what is still physically on the table; move is places gained ' +
    'since the previous scored week.';
  const note = degenerate(pre,
    'Preseason &mdash; nothing has been played, so a banked total is just that ' +
    'manager&rsquo;s lines added up in their picks&rsquo; directions. This ranks how ' +
    'many Unders someone drafted, not how they are doing.');
  const head = `<div class="an-row is-head">
    <span class="an-c-rank">#</span>
    <span class="an-name">Manager</span>
    <span class="an-c-banked an-num">Banked</span>
    <span class="an-c-gap an-num">Gap</span>
    <span class="an-c-ceil an-num">Ceiling left</span>
    <span class="an-c-move an-num">Move</span>
  </div>`;
  const body = mgrs.length ? mgrs.map((m) => {
    const isLeader = m.manager_id === race.leader_id;
    return `<div class="an-row">
      <span class="an-c-rank">${orDash(m.rank, (v) => esc(String(v)))}</span>
      <span class="an-name">${esc(m.display_name)}${
        isLeader ? '<span class="an-c-leader">Leader</span>' : ''}</span>
      <span class="an-c-banked an-num${signCls(m.banked_total)}">${
        orDash(m.banked_total, fmtSigned)}</span>
      <span class="an-c-gap an-num">${orDash(m.gap_to_leader, fmtLine)}</span>
      <span class="an-c-ceil an-num">${orDash(m.ceiling_remaining, fmtLine)}</span>
      <span class="an-c-move an-num">${orDash(m.week_move, fmtPlaces)}</span>
    </div>`;
  }).join('') : '<p class="an-empty">No managers in this league yet.</p>';
  // prior_week is null whenever week_move could not be measured. Say which
  // week the moves are against rather than leaving a column of dashes
  // unexplained.
  const against = race.prior_week === null || race.prior_week === undefined
    ? '<p class="card-sub">Move is unavailable: there is no earlier scored week to ' +
      'measure against.</p>'
    : `<p class="card-sub">Move is measured against week ${esc(String(race.prior_week))}.</p>`;
  return `<section class="card an-race">
    ${cardHead('The race', race.board, sub, note)}
    <div class="an-table">${head}${body}</div>
    ${against}
  </section>`;
}

// ---------- 2. championship odds — board: PROJECTION ----------------------
// The only modelled module on the page. It carries the grey Projection tag,
// sits inside the projection band, and never shares a row with an exact one.
//
// Its order is the engine's (p_win_pool desc, nulls last, then manager_id) and
// is deliberately NOT standings order — sorting it here would be computation.
function renderOdds(odds, pre) {
  if (!odds) return '';
  const sub = 'Probability this manager finishes with the group&rsquo;s highest total, ' +
    'from the shared-draw simulation. Its own ranking &mdash; not standings order.';
  const note = degenerate(pre,
    'Preseason &mdash; this is a full-season projection with no games banked ' +
    'behind it, so it is the model&rsquo;s read on the draft alone.');
  // available:false means the projector degraded and every p_win_pool is null.
  // Say the projection is unavailable rather than ruling a column of blanks.
  if (odds.available === false) {
    return `<section class="card an-odds">
      ${cardHead('Championship odds', odds.board, sub, note)}
      <div class="an-unavailable">
        <div class="an-u-title">Projection unavailable</div>
        <p class="an-u-sub">The weekly projection did not run this cycle, so there
          are no championship odds to show. Every exact number above is unaffected.</p>
      </div>
    </section>`;
  }
  const mgrs = odds.managers || [];
  const head = `<div class="an-row is-head">
    <span class="an-c-seed"></span>
    <span class="an-name">Manager</span>
    <span class="an-c-odds an-num">P(win pool)</span>
    <span class="an-c-omove an-num">Week move</span>
  </div>`;
  const body = mgrs.length ? mgrs.map((m, i) => `<div class="an-row">
    <span class="an-c-seed">${i + 1}</span>
    <span class="an-name">${esc(m.display_name)}</span>
    <span class="an-c-odds an-num">${orDash(m.p_win_pool, pct)}</span>
    <span class="an-c-omove an-num${signCls(m.week_move)}">${
      orDash(m.week_move, fmtSignedPct)}</span>
  </div>`).join('') : '<p class="an-empty">No managers in this league yet.</p>';
  const against = odds.prior_week === null || odds.prior_week === undefined
    ? '<p class="card-sub">Week move is unavailable: there is no earlier scored week ' +
      'to measure against.</p>'
    : `<p class="card-sub">Week move is the change since week ${
      esc(String(odds.prior_week))}, in probability &mdash; not places.</p>`;
  return `<section class="card an-odds">
    ${cardHead('Championship odds', odds.board, sub, note)}
    <div class="an-table">${head}${body}</div>
    ${against}
  </section>`;
}

// ---------- 3. best / worst — board: exact --------------------------------
// steal, bust, mvp and anchor are ALWAYS arrays. Every one of them can be
// empty (steal and bust are sign-gated: a group where nobody is above their
// line has no steal) and every one of them can hold several entries, because
// ties emit every tied pick. Family's steal is seven-way tied today.
function pickLine(p) {
  const dir = p.direction === 'O' ? 'Over' : 'Under';
  return `${esc(p.team)} ${dir} ${fmtLine(p.line)}`;
}

function bwGroupItems(list, emptyText) {
  if (!list || !list.length) return `<p class="an-bw-none">${emptyText}</p>`;
  return list.map((p) => `<div class="an-bw-item">
    <span class="an-bw-txt"><span class="an-bw-who">${esc(p.display_name)}</span>
      &middot; <span class="an-bw-pick">${pickLine(p)}</span>
      <span class="an-bw-conf">${esc(p.conference || '—')}</span></span>
    <span class="an-bw-delta${signCls(p.delta)}">${fmtSigned(p.delta)}</span>
  </div>`).join('');
}

function bwSlot(label, list) {
  if (!list || !list.length) {
    return `<div class="an-bw-slot"><span class="an-bw-slot-lbl">${label}</span>
      <span class="an-bw-none">${DASH}</span></div>`;
  }
  return list.map((p, i) => `<div class="an-bw-slot">
    <span class="an-bw-slot-lbl">${i === 0 ? label : '&nbsp;'}</span>
    <span class="an-bw-pick">${pickLine(p)}</span>
    <span class="an-bw-delta${signCls(p.delta)}">${fmtSigned(p.delta)}</span>
  </div>`).join('');
}

function renderBestWorst(bw, pre) {
  if (!bw) return '';
  const sub = 'Steal and bust are group-wide and sign-gated. MVP and anchor are each ' +
    'manager&rsquo;s own best and worst pick &mdash; relative, not sign-gated, so a best ' +
    'pick can still be under water. Every tied pick is listed.';
  const note = degenerate(pre,
    'Preseason &mdash; every delta is still exactly ±the line, so &ldquo;best&rdquo; and ' +
    '&ldquo;worst&rdquo; only name the biggest and smallest numbers a manager drafted. ' +
    'Ties are the rule rather than the exception here.');
  const mgrs = bw.managers || [];
  const perMgr = mgrs.length ? mgrs.map((m) => `<div class="an-bw-mgr">
    <div class="an-bw-mgr-name">${esc(m.display_name)}</div>
    ${bwSlot('MVP', m.mvp)}
    ${bwSlot('Anchor', m.anchor)}
  </div>`).join('') : '<p class="an-empty">No managers in this league yet.</p>';
  return `<section class="card an-bw">
    ${cardHead('Best and worst', bw.board, sub, note)}
    <div class="an-bw-group">
      <span class="an-bw-lbl">Steal of the draft</span>
      ${bwGroupItems(bw.steal, 'Nobody in this league is above their line yet.')}
    </div>
    <div class="an-bw-group">
      <span class="an-bw-lbl">Biggest bust</span>
      ${bwGroupItems(bw.bust, 'Nobody in this league is below their line yet.')}
    </div>
    <div class="an-bw-group">
      <span class="an-bw-lbl">Every manager&rsquo;s own</span>
      ${perMgr}
    </div>
  </section>`;
}

// ---------- 4. paths — board: exact ---------------------------------------
// `comparison` ships OPERANDS, not prose, so that the page shows the reasoning
// rather than an unexplained label. Composing the sentence is this renderer's
// job — and it is composition, not computation: nothing here derives a number,
// it only reads the four the engine handed over in the order it named them.
const PATH_STATES = {
  eliminated:       { label: 'Eliminated',       cls: ' st-eliminated' },
  clinched:         { label: 'Clinched',         cls: ' st-clinched' },
  controls_destiny: { label: 'Controls destiny', cls: ' st-controls' },
  needs_help:       { label: 'Needs help',       cls: '' },
};

// The four literal operators the contract uses, in words. An operator this map
// does not know is printed verbatim instead of being paraphrased into
// something it might not mean.
const OPERATOR_WORDS = {
  '<':  'is below',
  '<=': 'is at or below',
  '>':  'is above',
  '>=': 'reaches',
};

function pathWhy(c) {
  if (!c) return '';
  // A sole manager is clinched vacuously: every other_* field and the operator
  // are null, because there is nobody on the other side of the comparison.
  if (c.basis === 'sole_manager') {
    return 'The only manager in this league &mdash; there is nobody to be compared ' +
      'against, so the title is theirs by default.';
  }
  if (c.other_display_name === null || c.other_display_name === undefined ||
      c.operator === null || c.operator === undefined) {
    return '';
  }
  const word = Object.prototype.hasOwnProperty.call(OPERATOR_WORDS, c.operator)
    ? OPERATOR_WORDS[c.operator]
    : `is <span class="mono">${esc(String(c.operator))}</span>`;
  return `Their ${esc(String(c.my_field))} ` +
    `<span class="mono">${orDash(c.my_value, fmtSigned)}</span> ${word} ` +
    `${esc(c.other_display_name)}&rsquo;s ${esc(String(c.other_field))} ` +
    `<span class="mono">${orDash(c.other_value, fmtSigned)}</span>.`;
}

function renderPaths(paths, pre) {
  if (!paths) return '';
  const sub = 'Read straight off the floor&ndash;ceiling envelope in the standings, in ' +
    'the contract&rsquo;s fixed order: eliminated, clinched, controls destiny, then ' +
    'needs help as the fallback.';
  const note = degenerate(pre,
    'Preseason &mdash; with nothing banked, every ceiling still clears every floor, ' +
    'so everybody lands in controls-destiny or needs-help. That is correct and it ' +
    'says nothing yet; the states start separating once results arrive.');
  const mgrs = paths.managers || [];
  if (!mgrs.length) {
    return `<section class="card an-paths">
      ${cardHead('Paths to the title', paths.board, sub, note)}
      <p class="an-empty">No managers in this league yet.</p>
    </section>`;
  }
  const rows = mgrs.map((m) => {
    const st = Object.prototype.hasOwnProperty.call(PATH_STATES, m.state)
      ? PATH_STATES[m.state] : { label: String(m.state || '—'), cls: '' };
    const why = pathWhy(m.comparison);
    return `<div class="an-path">
      <div class="an-path-top">
        <span class="an-path-name">${esc(m.display_name)}${
          m.manager_id === paths.leader_id ? '<span class="an-c-leader">Leader</span>' : ''}</span>
        <span class="an-state${st.cls}">${esc(st.label)}</span>
      </div>
      ${why ? `<p class="an-path-why">${why}</p>` : ''}
    </div>`;
  }).join('');
  return `<section class="card an-paths">
    ${cardHead('Paths to the title', paths.board, sub, note)}
    <div class="an-table">${rows}</div>
  </section>`;
}

// ---------- 5. portfolio — board: exact -----------------------------------
// share_of_delta is the concentration number: |this pick| over |everything|.
// It is NULL — never 0 — when absolute_total is 0, which is exactly the
// nothing-played case. A zero-width bar next to "0%" would say "this pick
// accounts for none of it"; the honest answer is that there is nothing to take
// a share of, so the bar is hatched and the cell is an em dash.
function renderPortfolio(pf, pre) {
  if (!pf) return '';
  const sub = 'How concentrated a manager&rsquo;s swing is. Share is this pick&rsquo;s ' +
    'absolute delta over the absolute total, so a manager&rsquo;s shares sum to 100%.';
  const note = degenerate(pre,
    'Preseason &mdash; the totals below are sums of lines, not results, so these ' +
    'shares describe how a manager drafted rather than how their season is going.');
  const mgrs = pf.managers || [];
  if (!mgrs.length) {
    return `<section class="card an-pf">
      ${cardHead('Portfolio concentration', pf.board, sub, note)}
      <p class="an-empty">No managers in this league yet.</p>
    </section>`;
  }
  const head = `<div class="an-row is-head">
    <span class="an-c-team">Team</span>
    <span class="an-c-conf">Conf</span>
    <span class="an-c-line an-num">Line</span>
    <span class="an-c-dir">O/U</span>
    <span class="an-c-delta an-num">&Delta;</span>
    <span class="an-c-bar"></span>
    <span class="an-c-share an-num">Share</span>
  </div>`;
  const blocks = mgrs.map((m) => {
    const picks = m.picks || [];
    const rows = picks.length ? picks.map((p) => {
      const over = p.direction === 'O';
      const known = p.share_of_delta !== null && p.share_of_delta !== undefined;
      // THE ONE PIECE OF ARITHMETIC ON THIS PAGE, and it is a CSS length:
      // the same clamped (p * 100).toFixed(1) app.js uses for the win-prob
      // bar. The share itself is printed by pct() from the engine's value.
      const w = known
        ? (Math.max(0, Math.min(1, Number(p.share_of_delta))) * 100).toFixed(1)
        : null;
      return `<div class="an-row${p.status === 'DEAD' ? ' is-dead' : ''}">
        <span class="an-c-team">${esc(p.team)}</span>
        <span class="an-c-conf">${esc(p.conference || '—')}</span>
        <span class="an-c-line an-num">${fmtLine(p.line)}</span>
        <span class="an-c-dir"><span class="an-dir ${over ? 'over' : 'under'}">${
          over ? 'Over' : 'Under'}</span></span>
        <span class="an-c-delta an-num${signCls(p.banked_delta)}">${
          orDash(p.banked_delta, fmtSigned)}</span>
        <span class="an-c-bar"><span class="an-bar${known ? '' : ' is-unknown'}">${
          known ? `<span class="an-bar-fill" style="width:${w}%"></span>` : ''
        }</span></span>
        <span class="an-c-share an-num">${orDash(p.share_of_delta, pct)}</span>
      </div>`;
    }).join('') : '<p class="an-empty">No picks &mdash; this league hasn&rsquo;t drafted.</p>';
    return `<div class="an-pf-mgr">
      <div class="an-pf-head">
        <span class="an-pf-name">${esc(m.display_name)}</span>
        <span class="an-pf-tot">Banked <b class="${signCls(m.banked_total).trim()}">${
          orDash(m.banked_total, fmtSigned)}</b> &middot; swing <b>${
          orDash(m.absolute_total, fmtLine)}</b></span>
      </div>
      <div class="an-table">${head}${rows}</div>
    </div>`;
  }).join('');
  return `<section class="card an-pf">
    ${cardHead('Portfolio concentration', pf.board, sub, note)}
    ${blocks}
  </section>`;
}

// ---------- 6. leverage — reserved ----------------------------------------
// BRANCH ON `leverage === null`, NEVER on whether the key is present. The key
// exists today precisely so the shape is stable; the module lands in its own
// commit against the contract, and until then this is an honest placeholder
// rather than an invented one.
function renderLeverage(a) {
  if (a.leverage === null || a.leverage === undefined) {
    return `<section class="card an-leverage">
      <div class="coming-soon">
        <div class="cs-label">Games that matter &mdash; coming soon</div>
        <p class="cs-sub">The only genuinely new arithmetic on this page. It is
          reserved in the output contract and ships with its own commit, so the
          slot is held rather than filled with something else.</p>
      </div>
    </section>`;
  }
  // The key stopped being null, which means a later commit shipped the module.
  // This renderer predates its shape, so it says so instead of guessing.
  return `<section class="card an-leverage">
    ${cardHead('Games that matter', a.leverage.board,
      'This module now ships data, but this page was written before its shape was ' +
      'defined. It renders with the commit that lands the module.', '')}
  </section>`;
}

// ---------- nav ------------------------------------------------------------
// Same PAGE_NAV as every other page. ANALYTICS is this page: it becomes a dead
// <span>, exactly the posture managers.js takes on the roster page. Entries
// still marked unbuilt are omitted rather than linked at nothing — this page
// has no COMING SOON panel to swap in.
function renderNav(groupId) {
  const { raw: q, attr: qAttr } = navQuery(groupId);
  $('page-nav').innerHTML = PAGE_NAV.filter((p) => p.kind !== 'soon').map((p) => {
    if (p.href === 'analytics.html') {
      return `<span class="nav-btn" aria-current="true">${esc(p.label)}</span>`;
    }
    return `<a class="nav-btn" href="${esc(p.href)}${qAttr}">${esc(p.label)}</a>`;
  }).join('');
  $('brand-link').setAttribute('href', `index.html${q}`);
}

function fail(title, body) {
  hide($('loading'));
  $('load-error').innerHTML = `<h2>${title}</h2><p>${body}</p>`;
  show($('load-error'));
}

// ---------- boot -----------------------------------------------------------
async function main() {
  const groupId = currentGroupId();

  // Unknown league: name it and stop, exactly as app.js, managers.js and
  // managers.js do. A wrong ?group= must never quietly render another league's
  // numbers under this URL.
  if (groupId === null) {
    fail(`Unknown league &quot;${esc(groupParam())}&quot;.`,
      'No analytics were loaded. <a href="index.html">Back to all leagues &rarr;</a>');
    return;
  }

  renderNav(groupId);
  $('group-label').textContent = groupLabel(groupId);
  document.title = `${groupLabel(groupId)} — Analytics`;

  const [analyticsRes, standingsRes, projRes] = await Promise.all([
    fetchJSON(`data/${groupId}/analytics.json`).catch((e) => ({ $error: e })),
    fetchJSON(`data/${groupId}/standings.json`).catch(() => null),
    fetchJSON(`data/${groupId}/projection.json`).catch(() => null),
  ]);

  if (!analyticsRes || analyticsRes.$error) {
    const msg = analyticsRes && analyticsRes.$error
      ? analyticsRes.$error.message : 'unknown error';
    fail(`Can&rsquo;t load ${esc(groupLabel(groupId))}`,
      `analytics.json is missing or unreadable (${esc(msg)}). If this league exists, ` +
      `its Board 3 output may not have been generated yet.`);
    return;
  }

  const a = analyticsRes;
  const meta = a.meta || {};

  // Sample-data banner — the same band and the same sentence index.html shows,
  // read off standings.json because analytics.json deliberately carries no
  // draft_status. All four leagues are "dummy" today. Only the literal string
  // triggers it; a missing standings.json shows nothing rather than guessing.
  const sMeta = (standingsRes && standingsRes.meta) || {};
  if (sMeta.draft_status === 'dummy') {
    $('sample-banner').textContent =
      `Sample data — the draft has not been entered. These picks are placeholders ` +
      `to preview the board, not real selections.`;
    show($('sample-banner'));
  }

  const pre = isPreseason(a);
  const wk = (meta.as_of_week === null || meta.as_of_week === undefined)
    ? 'Preseason' : `Week ${meta.as_of_week}`;
  const n = ((a.race && a.race.managers) || []).length;
  $('an-intro').innerHTML =
    `<p class="an-intro-kicker">Analytics</p>
     <h1 class="an-intro-title">${esc(groupLabel(groupId))}</h1>
     <p class="an-intro-sub">${n} manager${n === 1 ? '' : 's'} &middot; ${esc(wk)} &middot; ` +
    `Board 3 &mdash; a reshape of the standings, the projection and the timeline</p>`;
  show($('an-intro'));

  // --- exact band ---
  $('an-race').innerHTML = renderRace(a.race, pre);
  $('an-grid-a').innerHTML = renderBestWorst(a.best_worst, pre) + renderPaths(a.paths, pre);
  $('an-grid-b').innerHTML = renderPortfolio(a.portfolio, pre) + renderLeverage(a);
  show($('an-exact'));

  // --- projection band ---
  // The disclaimer is built from projection.json's OWN metadata, so this page
  // cannot characterize the model differently from the home page, the roster
  // page or a profile. The contract pins ratings_source to "SP+"; a missing
  // projection.json falls back to it exactly as those pages do when the key
  // itself is absent.
  const src = (projRes && projRes.meta && projRes.meta.ratings_source) || 'SP+';
  $('an-proj-note').textContent =
    `Model estimate from ${src} ratings — it updates weekly and can be wrong. ` +
    `Board 1 is exact arithmetic.`;
  $('an-odds').innerHTML = renderOdds(a.championship_odds, pre);
  show($('an-projection'));

  hide($('loading'));
}

main();
