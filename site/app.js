/**
 * app.js — Main page renderer.
 * Two-number scoreboard (current + projected), per-pick detail with schedule
 * lookahead, commentary, and latest results for the active group.
 */

// --- Helpers ---

function fmtSigned(n) {
    if (n === null || n === undefined) return '—';
    const r = Math.round(n * 10) / 10;
    return (r > 0 ? '+' : '') + r.toFixed(1);
}

function deltaClass(n) {
    if (n === null || n === undefined) return 'neutral';
    if (n > 0) return 'pos';
    if (n < 0) return 'neg';
    return 'neutral';
}

// Win-probability → color (red = tough, green = likely win).
function probColor(p) {
    const t = Math.max(0, Math.min(1, p));
    const hue = Math.round(t * 120); // 0 = red, 120 = green
    return `hsl(${hue}, 65%, 42%)`;
}

/**
 * Merge current standings with projections into one owner list, keyed by id.
 * Each pick combines current record (standings) + projected detail (projections).
 */
function mergeOwners(standings, projections) {
    const projOwners = {};
    (projections?.owners || []).forEach(o => { projOwners[o.id] = o; });
    const stdOwners = {};
    (standings?.owners || []).forEach(o => { stdOwners[o.id] = o; });

    const ids = [...new Set([...Object.keys(stdOwners), ...Object.keys(projOwners)])];

    const merged = ids.map(id => {
        const s = stdOwners[id] || {};
        const p = projOwners[id] || {};
        const projPickByTeam = {};
        (p.picks || []).forEach(pk => { projPickByTeam[pk.team] = pk; });

        const picks = (s.picks || []).map(sp => {
            const pp = projPickByTeam[sp.team] || {};
            return { ...sp, ...pp };
        });
        // If standings has no picks but projections do, fall back to projection picks.
        const finalPicks = picks.length ? picks : (p.picks || []);

        return {
            id,
            name: s.name || p.name || id,
            short_name: s.short_name || s.name || p.name || id,
            current_score: s.current_score ?? p.current_score ?? 0,
            win_probability: p.win_probability ?? null,
            projected: p.projected_final_score || null,
            picks: finalPicks,
        };
    });

    // Sort by win probability when we have projections, else by current score.
    const hasProj = (projections?.owners || []).length > 0;
    merged.sort((a, b) => hasProj
        ? (b.win_probability ?? -1) - (a.win_probability ?? -1)
        : b.current_score - a.current_score);
    merged.forEach((o, i) => { o.rank = i + 1; });
    return merged;
}

// Shared p10..p90 domain for the projection range bars.
function projectionDomain(owners) {
    const vals = [];
    owners.forEach(o => {
        if (o.projected) { vals.push(o.projected.p10, o.projected.p90); }
        vals.push(o.current_score);
    });
    if (!vals.length) return { min: -10, max: 10 };
    let min = Math.min(...vals), max = Math.max(...vals);
    if (min === max) { min -= 1; max += 1; }
    const pad = (max - min) * 0.08;
    return { min: min - pad, max: max + pad };
}

function rangeBar(proj, current, domain) {
    if (!proj) return '';
    const span = domain.max - domain.min;
    const pos = v => `${((v - domain.min) / span) * 100}%`;
    const left = pos(proj.p10);
    const width = `${((proj.p90 - proj.p10) / span) * 100}%`;
    return `
        <div class="range-bar" title="p10 ${fmtSigned(proj.p10)} · median ${fmtSigned(proj.median)} · p90 ${fmtSigned(proj.p90)}">
            <div class="range-fill" style="left:${left}; width:${width};"></div>
            <div class="range-median" style="left:${pos(proj.median)};"></div>
            <div class="range-current" style="left:${pos(current)};"></div>
        </div>`;
}

// --- Render: Scoreboard ---

function renderScoreboard(owners) {
    const grid = document.getElementById('scoreboard-grid');
    if (!grid) return;

    if (!owners.length) {
        grid.innerHTML = `
            <div class="empty-state">
                <h2>🏈 Draft Day Pending</h2>
                <p>The scoreboard appears once picks are in and the season is underway.</p>
            </div>`;
        return;
    }

    const domain = projectionDomain(owners);

    grid.innerHTML = owners.map(o => {
        const proj = o.projected;
        const winPct = o.win_probability != null ? `${(o.win_probability * 100).toFixed(0)}%` : '—';

        return `
        <div class="score-card ${o.rank === 1 ? 'leader' : ''}">
            <div class="score-head">
                <span class="score-rank">#${o.rank}</span>
                <span class="score-name">${o.name}</span>
                <span class="score-winpct" title="Chance of finishing first">${winPct}</span>
            </div>

            <div class="score-numbers">
                <div class="score-num">
                    <div class="score-num-label">Current</div>
                    <div class="score-num-val ${deltaClass(o.current_score)}">${fmtSigned(o.current_score)}</div>
                </div>
                <div class="score-num">
                    <div class="score-num-label">Projected</div>
                    <div class="score-num-val ${deltaClass(proj?.median)}">${proj ? fmtSigned(proj.median) : '—'}</div>
                </div>
            </div>
            ${rangeBar(proj, o.current_score, domain)}

            <div class="picks">
                ${o.picks.map(renderPick).join('')}
            </div>
        </div>`;
    }).join('');
}

function renderPick(pk) {
    const side = (pk.side || '').toUpperCase();
    const lineStr = Number.isInteger(pk.line) ? pk.line : pk.line;
    const record = `${pk.current_wins ?? 0}-${pk.current_losses ?? 0}`;
    const projWins = pk.projected_final_wins;
    const projDelta = pk.projected_delta;
    const sched = pk.remaining_schedule || [];

    const schedHtml = sched.length ? `
        <div class="sched-strip">
            ${sched.map(g => `
                <span class="sched-dot" style="background:${probColor(g.win_prob)}"
                      title="${g.home ? 'vs' : '@'} ${g.opponent} · ${(g.win_prob * 100).toFixed(0)}% win (wk ${g.week ?? '?'})"></span>
            `).join('')}
        </div>` : `<div class="sched-strip none">no games left</div>`;

    return `
        <div class="pick-row">
            <div class="pick-main">
                <div class="pick-team">
                    <span class="ou-badge ${pk.side}">${side} ${lineStr}</span>
                    <span class="pick-name">${pk.team}</span>
                    <span class="pick-conf">${pk.conference || ''}</span>
                </div>
                <div class="pick-meta">
                    <span class="pick-record">${record}</span>
                    <span class="pick-gr">${pk.games_remaining ?? 0} left</span>
                </div>
            </div>
            <div class="pick-stats">
                <div class="pick-stat">
                    <span class="pick-stat-label">Now</span>
                    <span class="chip ${deltaClass(pk.current_delta)}">${fmtSigned(pk.current_delta)}</span>
                </div>
                <div class="pick-stat">
                    <span class="pick-stat-label">Proj wins</span>
                    <span class="pick-stat-val">${projWins ? `${projWins.median} (${projWins.p10}–${projWins.p90})` : '—'}</span>
                </div>
                <div class="pick-stat">
                    <span class="pick-stat-label">Proj Δ</span>
                    <span class="chip ${deltaClass(projDelta?.median)}">${projDelta ? fmtSigned(projDelta.median) : '—'}</span>
                </div>
            </div>
            ${schedHtml}
        </div>`;
}

// --- Render: Sidebar leaderboard ---

function renderLeaderboard(owners) {
    const container = document.getElementById('sidebar-leaderboard');
    const mobileContainer = document.getElementById('mobile-leaderboard');

    if (!owners.length) {
        if (container) container.innerHTML = `
            <div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">
                No standings yet.<br>Draft day is coming.
            </div>`;
        if (mobileContainer) mobileContainer.innerHTML = '';
        return;
    }

    if (container) {
        container.innerHTML = owners.map(o => {
            const winPct = o.win_probability != null ? `${(o.win_probability * 100).toFixed(0)}%` : '';
            const proj = o.projected ? fmtSigned(o.projected.median) : '—';
            return `
            <div class="lb-row ${o.rank === 1 ? 'first-place' : ''}">
                <div class="lb-rank">${o.rank}</div>
                <img class="lb-portrait" src="assets/portraits/${o.id || 'default'}.png"
                     onerror="this.style.display='none'" alt="">
                <div class="lb-info">
                    <div class="lb-name">${o.name}</div>
                    <div class="lb-meta">cur ${fmtSigned(o.current_score)} · proj ${proj}</div>
                </div>
                <div class="lb-stats">
                    <div class="lb-points">${winPct}</div>
                    <div class="lb-winpct">win</div>
                </div>
            </div>`;
        }).join('');
    }

    if (mobileContainer) {
        mobileContainer.innerHTML = owners.map(o => `
            <div class="mobile-lb-item">
                <div class="rank">#${o.rank}</div>
                <div class="name">${o.short_name || o.name}</div>
                <div class="pts">${o.projected ? fmtSigned(o.projected.median) : fmtSigned(o.current_score)}</div>
            </div>
        `).join('');
    }
}

function renderTicker(owners) {
    const track = document.getElementById('ticker-track');
    if (!track) return;
    if (!owners.length) {
        track.innerHTML = '<span class="ticker-item">Season preview mode — draft day coming soon</span>';
        return;
    }
    const items = owners.map(o => {
        const winPct = o.win_probability != null ? ` · ${(o.win_probability * 100).toFixed(0)}% win` : '';
        return `<span class="ticker-item"><span class="score">#${o.rank} ${o.name}</span> — cur ${fmtSigned(o.current_score)} · proj ${o.projected ? fmtSigned(o.projected.median) : '—'}${winPct}</span>`;
    }).join('<span class="ticker-item separator">|</span>');
    track.innerHTML = items + items;
}

// --- Render: Commentary (unchanged structurally) ---

function renderCommentary(commentary) {
    const anchorEl = document.getElementById('anchor-commentary');
    const analystEl = document.getElementById('analyst-commentary');

    if (!commentary) {
        if (anchorEl) anchorEl.innerHTML = `
            <div class="empty-state">
                <h2>📺 Broadcast Starts at Kickoff</h2>
                <p>Hot takes will generate once the season begins.</p>
            </div>`;
        if (analystEl) analystEl.innerHTML = '';
        return;
    }

    const anchor = commentary.anchor || {};
    if (anchorEl) {
        anchorEl.innerHTML = `
            <div class="commentary-card card-accent">
                <div class="commentary-header">
                    <div class="commentary-avatar">SA</div>
                    <div>
                        <div class="commentary-voice">${anchor.voice || 'The Anchor'}</div>
                        <div class="commentary-label">Weekly Hot Take · Week ${commentary.week || '?'}</div>
                    </div>
                </div>
                <div class="commentary-body">${anchor.content || 'No take available.'}</div>
            </div>`;
    }

    const analyst = commentary.analyst || {};
    if (analystEl) {
        const initials = (analyst.voice || 'AN').split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
        analystEl.innerHTML = `
            <div class="commentary-card">
                <div class="commentary-header">
                    <div class="commentary-avatar" style="background: var(--accent-gold)">${initials}</div>
                    <div>
                        <div class="commentary-voice">${analyst.voice || 'Analyst'}</div>
                        <div class="commentary-label">Analyst Desk</div>
                    </div>
                </div>
                <div class="commentary-body">${analyst.content || 'No analysis available.'}</div>
            </div>`;
    }
}

// --- Render: Latest results (unchanged) ---

function renderResults(results) {
    const grid = document.getElementById('results-grid');
    if (!grid) return;

    const games = (results?.games || []).filter(g => g.completed);
    const completed = games.slice(-12);

    if (!completed.length) {
        grid.innerHTML = `
            <div class="empty-state">
                <h2>📋 No Results Yet</h2>
                <p>Game results will appear here once the season kicks off.</p>
            </div>`;
        return;
    }

    grid.innerHTML = completed.reverse().map(g => {
        const homeWon = (g.home_points || 0) > (g.away_points || 0);
        return `
            <div class="match-card">
                <div class="match-teams">
                    <span class="${!homeWon ? 'match-winner' : ''}">${g.away_team || '?'}</span>
                    <span class="match-score">${g.away_points ?? '?'} - ${g.home_points ?? '?'}</span>
                    <span class="${homeWon ? 'match-winner' : ''}">${g.home_team || '?'}</span>
                </div>
                <div class="match-meta">Week ${g.week || '?'}${g.conference_game ? ' · Conference' : ''}</div>
            </div>`;
    }).join('');
}

// --- Load everything ---

async function loadPage() {
    const [standings, projections, commentary, results] = await Promise.all([
        fetchJSON(getDataPath('owner_standings.json')),
        fetchJSON(getDataPath('projections.json')),
        fetchJSON(getDataPath('commentary.json')),
        fetchJSON(`../data/live_results.json`),
    ]);

    const owners = mergeOwners(standings, projections);

    renderTicker(owners);
    renderLeaderboard(owners);
    renderScoreboard(owners);
    renderCommentary(commentary);
    renderResults(results);
}

document.addEventListener('DOMContentLoaded', loadPage);
// 'league-changed' is the cross-page event contract emitted by nav.js; the name
// is kept (frozen page scripts also listen on it) until the later site rebuild.
window.addEventListener('league-changed', loadPage);
