/**
 * analytics.js — Analytics page charts and tables.
 */

const OWNER_COLORS = [
    '#cc0000', '#d4a843', '#3498db', '#2ecc71', '#9b59b6', '#e67e22',
    '#1abc9c', '#e74c3c', '#f1c40f', '#8e44ad'
];

let winProbChart = null;
let depChart = null;
let tierChart = null;

async function loadAnalytics() {
    const leagueId = getCurrentLeague();

    const [timeline, standings, projections, narrative] = await Promise.all([
        fetchJSON(getDataPath('timeline.json')),
        fetchJSON(getDataPath('owner_standings.json')),
        fetchJSON(getDataPath('projections.json')),
        fetchJSON(getDataPath('narrative_state.json')),
    ]);

    renderWinProbChart(timeline, standings);
    renderWinProbTable(projections);
    renderDraftReport(standings);
    renderDependencyChart(narrative);
    renderTierChart(standings);
}


function renderWinProbChart(timeline, standings) {
    const canvas = document.getElementById('win-prob-chart');
    if (!canvas) return;

    const entries = timeline?.entries || [];
    const owners = standings?.owners || [];

    if (!entries.length || !owners.length) {
        canvas.parentElement.innerHTML = `
            <div class="empty-state">
                <h2>📈 Win Probability Timeline</h2>
                <p>Chart builds as the season progresses and simulations run.</p>
            </div>`;
        return;
    }

    // Build datasets
    const labels = entries.map((_, i) => `Week ${i + 1}`);
    const datasets = owners.map((o, i) => {
        const data = entries.map(entry => {
            const proj = (entry.projections || []).find(p => p.owner_id === o.id);
            return proj ? Math.round(proj.win_probability * 100) : null;
        });

        return {
            label: o.name || o.id,
            data,
            borderColor: OWNER_COLORS[i % OWNER_COLORS.length],
            backgroundColor: 'transparent',
            tension: 0.3,
            pointRadius: 3,
        };
    });

    if (winProbChart) winProbChart.destroy();
    winProbChart = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            plugins: {
                legend: { labels: { color: '#f0f0f0', font: { family: 'Oswald' } } },
            },
            scales: {
                x: { ticks: { color: '#a0a0a0' }, grid: { color: '#2a2a2a' } },
                y: {
                    ticks: { color: '#a0a0a0', callback: v => v + '%' },
                    grid: { color: '#2a2a2a' },
                    min: 0, max: 100,
                },
            },
        },
    });
}


function renderWinProbTable(projections) {
    const container = document.getElementById('win-prob-table');
    if (!container) return;

    const projs = projections?.projections || [];
    if (!projs.length) return;

    container.innerHTML = `
        <div class="card" style="overflow-x: auto;">
            <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
                <thead>
                    <tr style="border-bottom: 2px solid var(--accent-red); text-align:left;">
                        <th style="padding:8px; font-family:var(--font-display); text-transform:uppercase;">Owner</th>
                        <th style="padding:8px; text-align:right;">Win %</th>
                        <th style="padding:8px; text-align:right;">Current</th>
                        <th style="padding:8px; text-align:right;">P10</th>
                        <th style="padding:8px; text-align:right;">Median</th>
                        <th style="padding:8px; text-align:right;">P90</th>
                    </tr>
                </thead>
                <tbody>
                    ${projs.map(p => `
                        <tr style="border-bottom: 1px solid var(--border-subtle);">
                            <td style="padding:8px; font-weight:600;">${p.owner_name || p.owner_id}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--accent-gold);">${(p.win_probability * 100).toFixed(1)}%</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${p.current_points}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${p.projected_p10}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${p.projected_median}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${p.projected_p90}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}


function renderDraftReport(standings) {
    const container = document.getElementById('draft-report');
    if (!container) return;

    const owners = standings?.owners || [];
    if (!owners.length) {
        container.innerHTML = `<div class="card" style="text-align:center; color:var(--text-muted); padding:30px;">Draft report card available after draft day.</div>`;
        return;
    }

    container.innerHTML = owners.map(o => {
        const teams = o.teams || [];
        const tiers = {};
        teams.forEach(t => {
            tiers[t.tier] = (tiers[t.tier] || 0) + t.points;
        });

        return `
            <div class="card" style="margin-bottom:12px;">
                <div style="font-family:var(--font-display); font-weight:600; margin-bottom:8px;">
                    #${o.rank} ${o.name || o.id} — ${o.total_points} pts
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    ${Object.entries(tiers).map(([tier, pts]) => `
                        <span class="tier-badge ${tier.toLowerCase()}" style="padding:4px 10px; font-size:0.8rem;">
                            ${tier}: ${pts} pts
                        </span>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}


function renderDependencyChart(narrative) {
    const canvas = document.getElementById('dependency-chart');
    if (!canvas) return;

    const snapshots = narrative?.owner_snapshots || [];
    if (!snapshots.length) {
        canvas.parentElement.innerHTML = `<div class="empty-state"><p>Dependency data available after games are scored.</p></div>`;
        return;
    }

    const labels = snapshots.map(s => s.owner_name || s.owner_id);
    const data = snapshots.map(s => Math.round((s.dependency_index || 0) * 100));

    if (depChart) depChart.destroy();
    depChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'T1 Dependency %',
                data,
                backgroundColor: data.map(d => d >= 50 ? '#e74c3c' : d >= 30 ? '#f39c12' : '#2ecc71'),
                borderRadius: 4,
            }],
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    ticks: { color: '#a0a0a0', callback: v => v + '%' },
                    grid: { color: '#2a2a2a' },
                    max: 100,
                },
                y: { ticks: { color: '#f0f0f0', font: { family: 'Oswald' } }, grid: { display: false } },
            },
        },
    });
}


function renderTierChart(standings) {
    const canvas = document.getElementById('tier-chart');
    if (!canvas) return;

    const owners = standings?.owners || [];
    if (!owners.length) {
        canvas.parentElement.innerHTML = `<div class="empty-state"><p>Tier breakdown available after scoring begins.</p></div>`;
        return;
    }

    const tierColors = { T1: '#d4a843', T2: '#3498db', T3: '#7f8c8d', T4: '#555555' };
    const allTiers = [...new Set(owners.flatMap(o => (o.teams || []).map(t => t.tier)))].sort();

    const datasets = allTiers.map(tier => ({
        label: tier,
        data: owners.map(o => (o.teams || []).filter(t => t.tier === tier).reduce((sum, t) => sum + (t.points || 0), 0)),
        backgroundColor: tierColors[tier] || '#888',
        borderRadius: 2,
    }));

    if (tierChart) tierChart.destroy();
    tierChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: owners.map(o => o.name || o.id),
            datasets,
        },
        options: {
            responsive: true,
            plugins: {
                legend: { labels: { color: '#f0f0f0', font: { family: 'Oswald' } } },
            },
            scales: {
                x: { ticks: { color: '#f0f0f0', font: { family: 'Oswald' } }, grid: { display: false }, stacked: true },
                y: { ticks: { color: '#a0a0a0' }, grid: { color: '#2a2a2a' }, stacked: true },
            },
        },
    });
}


// --- Init ---
document.addEventListener('DOMContentLoaded', loadAnalytics);
window.addEventListener('league-changed', loadAnalytics);
