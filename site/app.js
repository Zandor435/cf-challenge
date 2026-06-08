/**
 * app.js — Main page renderer.
 * Loads standings, portfolios, commentary, results for the active league.
 */

// --- Render functions ---

function renderLeaderboard(standings) {
    const container = document.getElementById('sidebar-leaderboard');
    const mobileContainer = document.getElementById('mobile-leaderboard');
    if (!container) return;

    const owners = standings?.owners || [];

    if (!owners.length) {
        container.innerHTML = `
            <div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">
                No standings yet.<br>Draft day is coming.
            </div>`;
        if (mobileContainer) mobileContainer.innerHTML = '';
        return;
    }

    // Desktop sidebar
    container.innerHTML = owners.map((o, i) => `
        <div class="lb-row ${i === 0 ? 'first-place' : ''}">
            <div class="lb-rank">${o.rank || i + 1}</div>
            <img class="lb-portrait" src="assets/portraits/${o.id || 'default'}.png"
                 onerror="this.style.display='none'" alt="">
            <div class="lb-info">
                <div class="lb-name">${o.name || o.id}</div>
                <div class="lb-meta">${o.teams?.length || 0} teams</div>
            </div>
            <div class="lb-stats">
                <div class="lb-points">${o.total_points || 0}</div>
                <div class="lb-winpct"></div>
            </div>
        </div>
    `).join('');

    // Mobile horizontal bar
    if (mobileContainer) {
        mobileContainer.innerHTML = owners.map((o, i) => `
            <div class="mobile-lb-item">
                <div class="rank">#${o.rank || i + 1}</div>
                <div class="name">${o.short_name || o.name || o.id}</div>
                <div class="pts">${o.total_points || 0}</div>
            </div>
        `).join('');
    }
}


function renderPortfolios(standings) {
    const grid = document.getElementById('portfolio-grid');
    if (!grid) return;

    const owners = standings?.owners || [];

    if (!owners.length) {
        grid.innerHTML = `
            <div class="empty-state">
                <h2>🏈 Draft Day Pending</h2>
                <p>Portfolios will appear after the draft board is filled in.</p>
            </div>`;
        return;
    }

    grid.innerHTML = owners.map(o => {
        const teams = (o.teams || []).sort((a, b) => {
            const tierOrder = { T1: 0, T2: 1, T3: 2, T4: 3 };
            return (tierOrder[a.tier] || 9) - (tierOrder[b.tier] || 9);
        });

        return `
            <div class="portfolio-card">
                <div class="portfolio-owner">
                    <div class="portfolio-owner-name">${o.name || o.id}</div>
                    <div class="portfolio-owner-rank">#${o.rank || '?'} · ${o.total_points || 0} pts</div>
                </div>
                ${teams.map(t => `
                    <div class="portfolio-team">
                        <div class="portfolio-team-name">
                            <span class="tier-badge ${(t.tier || '').toLowerCase()}">${t.tier || '?'}</span>
                            ${t.team}
                        </div>
                        <div class="portfolio-team-pts">${t.points || 0} <span style="color:var(--text-muted);font-size:0.75rem">${t.record || ''}</span></div>
                    </div>
                `).join('')}
            </div>
        `;
    }).join('');
}


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

    // Anchor (Stephen A.)
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
            </div>
        `;
    }

    // Rotating analyst
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
            </div>
        `;
    }
}


function renderResults(results) {
    const grid = document.getElementById('results-grid');
    if (!grid) return;

    const games = results?.games || [];
    const completed = games.filter(g => g.completed).slice(-12); // Last 12

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
            </div>
        `;
    }).join('');
}


function renderTicker(standings) {
    const track = document.getElementById('ticker-track');
    if (!track) return;

    const owners = standings?.owners || [];
    if (!owners.length) {
        track.innerHTML = '<span class="ticker-item">Season preview mode — draft day coming soon</span>';
        return;
    }

    // Build ticker items (repeat for seamless scroll)
    const items = owners.map(o =>
        `<span class="ticker-item"><span class="score">#${o.rank} ${o.name || o.id}</span> — ${o.total_points || 0} pts</span>`
    ).join('<span class="ticker-item separator">|</span>');

    track.innerHTML = items + items; // Duplicate for seamless loop
}


// --- Load everything ---

async function loadPage() {
    const leagueId = getCurrentLeague();

    // Load all data in parallel
    const [standings, commentary, results] = await Promise.all([
        fetchJSON(getDataPath('owner_standings.json')),
        fetchJSON(getDataPath('commentary.json')),
        fetchJSON(`../data/live_results.json`),
    ]);

    renderTicker(standings);
    renderLeaderboard(standings);
    renderPortfolios(standings);
    renderCommentary(commentary);
    renderResults(results);
}


// --- Init ---
document.addEventListener('DOMContentLoaded', loadPage);
window.addEventListener('league-changed', loadPage);
