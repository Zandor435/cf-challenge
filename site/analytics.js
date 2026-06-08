/**
 * analytics.js — Analytics page charts and tables (delta model).
 */

const OWNER_COLORS = [
    '#cc0000', '#d4a843', '#3498db', '#2ecc71', '#9b59b6', '#e67e22',
    '#1abc9c', '#e74c3c', '#f1c40f', '#8e44ad'
];

let winProbChart = null;
let projChart = null;
let deltaChart = null;

function fmtSigned(n) {
    if (n === null || n === undefined) return '—';
    const r = Math.round(n * 10) / 10;
    return (r > 0 ? '+' : '') + r.toFixed(1);
}

async function loadAnalytics() {
    const [timeline, standings, projections] = await Promise.all([
        fetchJSON(getDataPath('timeline.json')),
        fetchJSON(getDataPath('owner_standings.json')),
        fetchJSON(getDataPath('projections.json')),
    ]);

    renderWinProbChart(timeline);
    renderWinProbTable(projections);
    renderProjVsCurrent(projections, standings);
    renderDeltaContribution(standings);
}


function renderWinProbChart(timeline) {
    const canvas = document.getElementById('win-prob-chart');
    if (!canvas) return;

    const entries = timeline?.entries || [];
    if (!entries.length) {
        canvas.parentElement.innerHTML = `
            <div class="empty-state">
                <h2>📈 Win Probability Timeline</h2>
                <p>Chart builds as the season progresses and simulations run.</p>
            </div>`;
        return;
    }

    // Owner ids appearing anywhere in the timeline.
    const ownerMap = {};
    entries.forEach(e => (e.owners || []).forEach(o => { ownerMap[o.id] = o.name || o.id; }));
    const ownerIds = Object.keys(ownerMap);

    const labels = entries.map(e => `Wk ${e.week ?? '?'}`);
    const datasets = ownerIds.map((id, i) => ({
        label: ownerMap[id],
        data: entries.map(e => {
            const o = (e.owners || []).find(x => x.id === id);
            return o ? Math.round(o.win_probability * 100) : null;
        }),
        borderColor: OWNER_COLORS[i % OWNER_COLORS.length],
        backgroundColor: 'transparent',
        tension: 0.3,
        pointRadius: 3,
        spanGaps: true,
    }));

    if (winProbChart) winProbChart.destroy();
    winProbChart = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: '#f0f0f0', font: { family: 'Oswald' } } } },
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

    const owners = projections?.owners || [];
    if (!owners.length) { container.innerHTML = ''; return; }

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
                    ${owners.map(p => {
                        const s = p.projected_final_score || {};
                        return `
                        <tr style="border-bottom: 1px solid var(--border-subtle);">
                            <td style="padding:8px; font-weight:600;">${p.name || p.id}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--accent-gold);">${(p.win_probability * 100).toFixed(1)}%</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${fmtSigned(p.current_score)}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${fmtSigned(s.p10)}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono);">${fmtSigned(s.median)}</td>
                            <td style="padding:8px; text-align:right; font-family:var(--font-mono); color:var(--text-muted);">${fmtSigned(s.p90)}</td>
                        </tr>`;
                    }).join('')}
                </tbody>
            </table>
        </div>`;
}


function renderProjVsCurrent(projections, standings) {
    const canvas = document.getElementById('proj-vs-current-chart');
    if (!canvas) return;

    const owners = projections?.owners || [];
    if (!owners.length) {
        canvas.parentElement.innerHTML = `<div class="empty-state"><p>Projection comparison appears once simulations run.</p></div>`;
        return;
    }

    const labels = owners.map(o => o.name || o.id);
    const current = owners.map(o => o.current_score ?? 0);
    const projected = owners.map(o => (o.projected_final_score || {}).median ?? 0);

    if (projChart) projChart.destroy();
    projChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                { label: 'Current', data: current, backgroundColor: '#7f8c8d', borderRadius: 3 },
                { label: 'Projected (median)', data: projected, backgroundColor: '#d4a843', borderRadius: 3 },
            ],
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: '#f0f0f0', font: { family: 'Oswald' } } } },
            scales: {
                x: { ticks: { color: '#f0f0f0', font: { family: 'Oswald' } }, grid: { display: false } },
                y: { ticks: { color: '#a0a0a0' }, grid: { color: '#2a2a2a' } },
            },
        },
    });
}


function renderDeltaContribution(standings) {
    const canvas = document.getElementById('delta-chart');
    if (!canvas) return;

    const owners = standings?.owners || [];
    if (!owners.length) {
        canvas.parentElement.innerHTML = `<div class="empty-state"><p>Pick contribution appears after scoring begins.</p></div>`;
        return;
    }

    // Each owner's picks sorted by delta (biggest carrier first). Grouped bars,
    // one dataset per pick slot; team names surfaced in tooltips.
    const sorted = owners.map(o => ({
        name: o.name || o.id,
        picks: [...(o.picks || [])].sort((a, b) => (b.current_delta ?? 0) - (a.current_delta ?? 0)),
    }));
    const maxPicks = Math.max(1, ...sorted.map(o => o.picks.length));

    const datasets = [];
    for (let slot = 0; slot < maxPicks; slot++) {
        const data = sorted.map(o => o.picks[slot]?.current_delta ?? 0);
        const teams = sorted.map(o => o.picks[slot]?.team ?? '');
        datasets.push({
            label: `Pick ${slot + 1}`,
            data,
            teams,
            backgroundColor: data.map(d => d >= 0 ? '#2ecc71' : '#e74c3c'),
            borderRadius: 3,
        });
    }

    if (deltaChart) deltaChart.destroy();
    deltaChart = new Chart(canvas, {
        type: 'bar',
        data: { labels: sorted.map(o => o.name), datasets },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const team = ctx.dataset.teams[ctx.dataIndex] || 'Pick';
                            return `${team}: ${fmtSigned(ctx.raw)}`;
                        },
                    },
                },
            },
            scales: {
                x: { ticks: { color: '#f0f0f0', font: { family: 'Oswald' } }, grid: { display: false } },
                y: { ticks: { color: '#a0a0a0' }, grid: { color: '#2a2a2a' }, title: { display: true, text: 'Current Δ', color: '#a0a0a0' } },
            },
        },
    });
}


document.addEventListener('DOMContentLoaded', loadAnalytics);
window.addEventListener('league-changed', loadAnalytics);
