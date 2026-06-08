/**
 * nav.js — Shared navigation for all pages.
 * Handles: hamburger menu, league switcher, sidebar toggle, active page.
 */

// --- League configuration ---
const LEAGUES = [
    { id: 'league-1', label: 'L1', owners: 3 },
    { id: 'league-2', label: 'L2', owners: 5 },
    { id: 'league-3', label: 'L3', owners: 6 },
];

// --- State ---
let currentLeague = localStorage.getItem('cfb_league') || LEAGUES[0].id;

function setLeague(leagueId) {
    currentLeague = leagueId;
    localStorage.setItem('cfb_league', leagueId);

    // Update switcher buttons
    document.querySelectorAll('.league-switcher button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.league === leagueId);
    });

    // Dispatch event for page scripts to listen to
    window.dispatchEvent(new CustomEvent('league-changed', { detail: { leagueId } }));
}

function getCurrentLeague() {
    return currentLeague;
}

function getDataPath(filename) {
    return `data/${currentLeague}/${filename}`;
}

// --- Fetch helper ---
async function fetchJSON(path) {
    try {
        const resp = await fetch(path);
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        console.warn(`Failed to load ${path}:`, e.message);
        return null;
    }
}

// --- Build league switcher ---
function buildLeagueSwitcher() {
    const container = document.getElementById('league-switcher');
    if (!container) return;

    container.innerHTML = '';
    LEAGUES.forEach(league => {
        const btn = document.createElement('button');
        btn.textContent = league.label;
        btn.dataset.league = league.id;
        btn.title = `${league.id} (${league.owners} owners)`;
        if (league.id === currentLeague) btn.classList.add('active');
        btn.addEventListener('click', () => setLeague(league.id));
        container.appendChild(btn);
    });
}

// --- Hamburger menu ---
function initHamburger() {
    const hamburger = document.getElementById('hamburger');
    const navLinks = document.getElementById('nav-links');
    const sidebar = document.getElementById('sidebar');

    if (hamburger) {
        hamburger.addEventListener('click', () => {
            navLinks?.classList.toggle('open');
            sidebar?.classList.toggle('open');
        });
    }

    // Close on outside click
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.nav') && !e.target.closest('.sidebar')) {
            navLinks?.classList.remove('open');
            sidebar?.classList.remove('open');
        }
    });
}

// --- Active page highlight ---
function highlightActivePage() {
    const path = window.location.pathname.split('/').pop() || 'index.html';
    document.querySelectorAll('.nav-links a').forEach(a => {
        const href = a.getAttribute('href');
        a.classList.toggle('active', href === path);
    });
}

// --- Init ---
document.addEventListener('DOMContentLoaded', () => {
    buildLeagueSwitcher();
    initHamburger();
    highlightActivePage();
});
