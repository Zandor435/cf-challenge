/* A renderer: Python supplies odds, route conditions, ordering and evidence.
   Only display formatting and chart widths happen here. */
'use strict';
const oddsText = n => n == null ? 'Unavailable' : `${(Number(n) * 100).toFixed(1)}%`;
const dataDate = value => value ? new Date(value).toLocaleDateString('en-US', {
  month: 'short', day: 'numeric', year: 'numeric', timeZone: 'America/New_York'
}) : 'date unavailable';
const barWidth = n => (Math.max(0, Math.min(1, Number(n))) * 100).toFixed(2);
const noData = message => `<p class="an-empty">${message}</p>`;

function renderNav(groupId) {
  const { raw: q, attr: qa } = navQuery(groupId);
  $('page-nav').innerHTML = PAGE_NAV.filter(p => p.kind !== 'soon').map(p =>
    p.href === 'analytics.html' ? `<span class="nav-btn" aria-current="page">${esc(p.label)}</span>`
      : `<a class="nav-btn" href="${esc(p.href)}${qa}">${esc(p.label)}</a>`).join('');
  $('brand-link').setAttribute('href', `index.html${q}`);
}

function renderOdds(odds, pre) {
  const managers = (odds || {}).managers || [];
  return `<div class="an-chances"><div class="an-chances-head"><div><p class="an-eyebrow">The big picture</p><h2>Who brings it home?</h2></div><span class="an-model-label">Estimated chance to win</span></div>
    ${!odds || !odds.available ? noData('The forecast is unavailable for this update.') :
      `<div class="an-odds-bars">${managers.map(m => `<div class="an-odds-row"><a href="#an-rooting" data-choose-manager="${esc(m.manager_id)}">${esc(m.display_name)}</a><div class="an-odds-track${m.p_win_pool == null ? ' is-unknown' : ''}" aria-hidden="true"><span style="width:${barWidth(m.p_win_pool)}%"></span></div><strong>${oddsText(m.p_win_pool)}</strong></div>`).join('') || noData('No managers in this league yet.')}</div>`}
    <p class="an-fine">Longer bar, better shot at the title. ${pre ? 'Preseason forecast; no games have been played yet.' : 'A forecast of the rest of the season, not a promise.'}</p></div>`;
}

function matchup(team, game) {
  return game.neutral ? `${esc(team)} vs. ${esc(game.opponent)}` : game.home_away === 'home'
    ? `${esc(game.opponent)} at ${esc(team)}` : `${esc(team)} at ${esc(game.opponent)}`;
}

function routeStory(manager, route, index) {
  return `<div class="an-route-story" data-route-index="${index}" ${index ? 'hidden' : ''}>
    <div class="an-route-lead"><div><span class="an-route-kicker">One way forward</span><h3>${esc(route.headline)}</h3><p>${esc(route.story)}</p></div><div class="an-rooting-sign" aria-label="${esc(manager.display_name)} wants ${esc(route.team)} to ${route.direction === 'U' ? 'lose' : 'win'}"><span>You want</span><b>${route.direction === 'U' ? 'LOSSES' : 'WINS'}</b><span>for ${esc(route.team)}</span></div></div>
    <div class="an-route-main"><div class="an-route-watches"><p class="an-eyebrow">${esc(route.watch_heading)}</p>${(route.games_to_watch || []).map(g => `<div class="an-circle-game"><span class="an-week">W${esc(String(g.week ?? '?'))}</span><div><h4>${matchup(route.team, g)}</h4><p>${esc(g.rooting_note)}</p></div></div>`).join('')}</div><aside class="an-route-aside"><p class="an-eyebrow">The catch</p><h4>${esc(route.difficulty)}</h4><p>${route.needed === route.next_games && route.next_games > 1 ? 'Every game in that stretch has to go the right way. ' : ''}This would improve ${esc(manager.display_name)}&rsquo;s chances. The other picks still have to do their part.</p>${route.rival_note ? `<p class="an-tug"><b>A little tug-of-war</b>${esc(route.rival_note)}</p>` : ''}</aside></div>
    ${route.narrative ? `<div class="an-commentary"><div><p class="an-eyebrow">The Saturday subplot</p><p>${esc(route.narrative)}</p><p>${esc(route.if_it_happens || '')}</p></div><aside class="an-banter"><p class="an-eyebrow">Group chat ammunition</p><p>${esc(route.banter || '')}</p><span>Friendly fire, courtesy of the rooting guide.</span></aside></div>` : ''}
    <details class="an-proof"><summary>Why this would help</summary><div class="an-proof-content"><p>${esc(route.why)}</p><p>In simulated seasons where ${esc(route.condition)}, ${esc(manager.display_name)}&rsquo;s title chance is about <b>${oddsText(route.p_title_if)}</b>, compared with <b>${oddsText(route.p_title_now)}</b> today.</p><p>${esc(route.likelihood_text)} Other outcomes and team ratings stay within the same forecast.</p><p class="an-eyebrow">The full stretch</p><div class="an-stretch">${(route.stretch || []).map(g => `<div><span>Week ${esc(String(g.week ?? '?'))}</span><b>${matchup(route.team, g)}</b><small>${oddsText(g.p_win)} chance ${esc(route.team)} wins</small></div>`).join('')}</div><p class="an-fine">One helpful scenario, with other routes still possible. Tied titles are split equally in the forecast.</p></div></details>
  </div>`;
}

function renderRooting(routes) {
  if (!routes || !routes.available) return noData('The rooting guide is unavailable for this update.');
  const managers = routes.managers || [];
  if (!managers.length) return noData('Once this league has picks, everyone will have a rooting guide.');
  return `<div class="an-manager-picker" role="group" aria-label="Choose a manager">${managers.map((m, i) => `<button type="button" data-manager-button="${esc(m.manager_id)}" aria-pressed="${i === 0}" aria-controls="route-${esc(m.manager_id)}">${esc(m.display_name)}</button>`).join('')}</div>
    ${managers.map((m, i) => `<article class="an-manager-guide" id="route-${esc(m.manager_id)}" data-manager-panel="${esc(m.manager_id)}" aria-label="${esc(m.display_name)}'s rooting guide" ${i ? 'hidden' : ''}><div class="an-guide-top"><p><strong>${esc(m.display_name)}&rsquo;s rooting guide</strong><span>${esc(m.position)}</span></p>${(m.routes || []).length > 1 ? `<div class="an-pick-picker" role="group" aria-label="Explore ${esc(m.display_name)}'s picks"><span>Explore a pick</span>${m.routes.map((r, ri) => `<button type="button" data-route-button="${ri}" aria-pressed="${ri === 0}">${esc(r.team)}</button>`).join('')}</div>` : ''}</div>${(m.routes || []).length ? m.routes.map((r, ri) => routeStory(m, r, ri)).join('') : `<p class="an-empty">${esc(m.intro)}</p>`}</article>`).join('')}
    <details class="an-rules"><summary>New to the pool? Here&rsquo;s how the picks work.</summary><p>Some picks need a team to win <b>more</b> than its preseason target. Others need it to win <b>less</b>. That is why a team losing can be great news for one manager and bad news for another. The best combined score across all of a manager&rsquo;s picks wins.</p></details>`;
}

function watchCard(game) {
  const chat = game.conversation ? `<p class="an-watch-chat"><b>The group chat angle</b>${esc(game.conversation)}</p>` : '';
  return `<article class="an-watch-card"><p class="an-eyebrow">Week ${esc(String(game.week))}</p><p class="an-watch-matchup">${matchup(game.team, game)}</p><h3>${esc(game.headline || 'A game to keep an eye on.')}</h3><p class="an-watch-story">${esc(game.story || '')}</p>${chat}<details class="an-proof"><summary>See how much it matters</summary><div class="an-proof-content"><p class="an-fine">Each manager&rsquo;s title chance if one side or the other wins. The rest of the forecast stays the same.</p><div class="an-scenario-head"><span>Manager</span><span>${esc(game.team)} wins</span><span>${esc(game.opponent)} wins</span></div>${(game.managers || []).map(m => `<div class="an-scenario-row"><b>${esc(m.display_name)}</b><span>${oddsText(m.p_if_win)}</span><span>${oddsText(m.p_if_loss)}</span></div>`).join('')}</div></details></article>`;
}

function renderWatch(leverage) {
  if (!leverage || !leverage.available) return noData('The upcoming game guide is unavailable for this update.');
  const games = leverage.games || [];
  if (!games.length) return noData('No upcoming games on the current slate.');
  return `<div class="an-watch-grid">${games.slice(0, 3).map(watchCard).join('')}</div>${games.length > 3 ? `<details class="an-fold"><summary>More games to keep an eye on</summary><div class="an-watch-grid">${games.slice(3).map(watchCard).join('')}</div></details>` : ''}`;
}

function scheduleStory(t) {
  if (t.direction === 'unavailable') return `We don&rsquo;t have comparable ratings for ${esc(t.team)}&rsquo;s remaining opponents.`;
  if (t.direction === 'unchanged') return `${esc(t.team)}&rsquo;s remaining road looks about as tough as it did at the draft.`;
  const help = t.owners.filter(o => o.effect === 'helps').map(o => esc(o.display_name));
  const hurt = t.owners.filter(o => o.effect === 'hurts').map(o => esc(o.display_name));
  return `${esc(t.team)}&rsquo;s road has become ${t.direction === 'harder' ? 'bumpier' : 'a little smoother'}. ${help.length ? `That helps ${help.join(', ')}.` : ''} ${hurt.length ? `It works against ${hurt.join(', ')}.` : ''}`;
}

function renderRoad(schedule) {
  if (!schedule || !schedule.available) return `<div class="an-road-report"><p class="an-eyebrow">The road report</p><h2>Still waiting on the comparison.</h2><p>Draft-day ratings aren&rsquo;t available for this update.</p></div>`;
  const changed = schedule.teams.filter(t => t.direction === 'easier' || t.direction === 'harder');
  return `<div class="an-road-report"><div class="an-road-mark" aria-hidden="true"><i></i><i></i><i></i></div><div><p class="an-eyebrow">The road report</p><h2>${changed.length ? 'A few twists since draft day.' : schedule.compared_teams ? 'Same road. No new twists yet.' : 'No road left to compare.'}</h2>${changed.length ? changed.slice(0, 3).map(t => `<p>${scheduleStory(t)}</p>`).join('') : `<p>${schedule.compared_teams ? 'So far, the ratings haven&rsquo;t meaningfully changed how tough the remaining schedules look.' : 'There are no remaining games with ratings in both snapshots.'}</p>`}<details class="an-proof"><summary>Check the road for a team</summary><div class="an-proof-content"><label class="an-team-label" for="an-road-team">Choose a team <select id="an-road-team">${schedule.teams.map((t, i) => `<option value="${i}">${esc(t.team)}</option>`).join('')}</select></label>${schedule.teams.map((t, i) => `<div class="an-road-team" data-road-team="${i}" ${i ? 'hidden' : ''}><p>${scheduleStory(t)}</p><p class="an-fine">${t.compared_games} of ${t.remaining_games} remaining games compared. ${t.unrated_games ? 'Unrated opponents are left out.' : ''} We hold the picked team&rsquo;s strength fixed and compare its opponents with their draft-day ratings.</p></div>`).join('')}</div></details></div></div>`;
}

function renderChanges(story, odds) {
  if (!story || !story.available) return '';
  const baseline = (odds || {}).draft_baseline || {};
  return `<details class="an-fold"><summary>How has the race changed?</summary><div class="an-change-list">${baseline.reason ? `<p class="an-fine">${esc(baseline.reason)}</p>` : ''}${(story.managers || []).map(m => `<div><h3>${esc(m.display_name)}</h3><p>${esc(m.movement_summary || 'No comparable earlier forecast.')}</p><p class="an-fine">${m.basis === 'week' ? `Compared with week ${esc(String(story.prior_week))}.` : 'Compared with draft day.'}</p></div>`).join('')}<p class="an-fine">The forecast can change because of results, team ratings, or model updates.</p></div></details>`;
}

function renderScores(race, portfolio) {
  if (!race) return '';
  return `<details class="an-fold"><summary>The scorekeeping, explained</summary><div class="an-score-help"><h3>What do the plus and minus mean?</h3><p>They show how a pick compares with its preseason win target. An Over on 8.5 wins finishes at <b>+0.5</b> if the team wins nine games, or <b>&minus;0.5</b> if it wins eight. An Under flips those scores.</p><p>During the season, that running score is unfinished business. Over picks climb as wins arrive; Under picks start high and come down with each win. That is why the title forecast is more useful than the running score on its own.</p><div class="an-score-rows"><div class="an-score-head"><span>Manager</span><span>Running score</span></div>${(race.managers || []).map(m => `<div><b>${esc(m.display_name)}</b><span>${fmtSigned(m.banked_total)}</span></div>`).join('')}</div><details class="an-proof"><summary>See each pick&rsquo;s running score</summary><div class="an-proof-content">${((portfolio || {}).managers || []).map(m => `<div class="an-pick-scores"><h4>${esc(m.display_name)}</h4>${(m.picks || []).map(p => `<p><span>${esc(p.team)} &middot; ${p.direction === 'O' ? 'Over' : 'Under'} ${fmtLine(p.line)}</span><b>${fmtSigned(p.banked_delta)}</b></p>`).join('')}</div>`).join('')}</div></details></div></details>`;
}

function chooseManager(id) {
  document.querySelectorAll('[data-manager-button]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.managerButton === id)));
  document.querySelectorAll('[data-manager-panel]').forEach(panel => { panel.hidden = panel.dataset.managerPanel !== id; });
}

function bindGuide() {
  document.querySelectorAll('[data-manager-button]').forEach(button => button.addEventListener('click', () => chooseManager(button.dataset.managerButton)));
  document.querySelectorAll('[data-choose-manager]').forEach(link => link.addEventListener('click', () => chooseManager(link.dataset.chooseManager)));
  document.querySelectorAll('[data-route-button]').forEach(button => button.addEventListener('click', () => {
    const panel = button.closest('[data-manager-panel]');
    panel.querySelectorAll('[data-route-button]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    panel.querySelectorAll('[data-route-index]').forEach(route => { route.hidden = route.dataset.routeIndex !== button.dataset.routeButton; });
  }));
  const team = $('an-road-team');
  if (team) team.addEventListener('change', () => document.querySelectorAll('[data-road-team]').forEach(panel => { panel.hidden = panel.dataset.roadTeam !== team.value; }));
}

function fail(title, message) {
  hide($('loading'));
  $('load-error').innerHTML = `<h2>${title}</h2><p>${message}</p>`;
  show($('load-error'));
}

async function main() {
  const groupId = currentGroupId();
  if (groupId === null) { fail(`Unknown league &quot;${esc(groupParam())}&quot;.`, '<a href="index.html">Back to all leagues</a>'); return; }
  renderNav(groupId);
  $('group-label').textContent = groupLabel(groupId);
  document.title = `${groupLabel(groupId)} — The rooting guide`;
  const [a, standings] = await Promise.all([
    fetchJSON(`data/${groupId}/analytics.json`).catch(() => null),
    fetchJSON(`data/${groupId}/standings.json`).catch(() => null),
  ]);
  if (!a) { fail('The race report could not be loaded.', 'Try again in a moment.'); return; }
  renderSampleBanner(standings && standings.meta);
  const pre = standings ? isPreseasonStandings(standings) : isPreseasonAnalytics(a);
  $('an-intro').innerHTML = `<p class="an-eyebrow">${esc(groupLabel(groupId))} &nbsp; / &nbsp; The race, explained</p><h1>Your rooting <span>guide.</span></h1><p class="an-intro-deck">One trophy. A different wish list for everyone.</p><div class="an-intro-bottom"><nav aria-label="On this page"><a href="#an-projection">Who wins it</a><a href="#an-rooting">Find your path</a><a href="#an-stakes">What to watch</a></nav><span>${pre ? 'Preseason · ' : ''}Data through ${esc(dataDate((a.meta || {}).cache_fetched_at))}</span></div>`;
  $('an-odds').innerHTML = renderOdds(a.championship_odds, pre);
  $('an-rooting-content').innerHTML = renderRooting(a.title_routes);
  $('an-stakes-content').innerHTML = renderWatch(a.leverage);
  $('an-schedule-content').innerHTML = renderRoad(a.schedule_watch);
  $('an-story-content').innerHTML = renderChanges(a.race_story, a.championship_odds);
  $('an-details-content').innerHTML = renderScores(a.race, a.portfolio);
  ['an-intro', 'an-projection', 'an-rooting', 'an-stakes', 'an-schedule', 'an-story', 'an-details'].forEach(id => show($(id)));
  bindGuide();
  hide($('loading'));
}
main();
