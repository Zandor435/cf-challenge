/**
 * nav.js — Shared navigation for all pages.
 * Handles: hamburger menu, group switcher, sidebar toggle, active page.
 */

// --- Group configuration ---
const GROUPS = [
    { id: 'group_a', label: 'A', managers: 3 },
    { id: 'group_b', label: 'B', managers: 5 },
    { id: 'group_c', label: 'C', managers: 6 },
];

// --- State ---
let currentGroup = localStorage.getItem('cfb_group') || GROUPS[0].id;

function setGroup(groupId) {
    currentGroup = groupId;
    localStorage.setItem('cfb_group', groupId);

    // Update switcher buttons
    // NOTE: '.league-switcher' is the DOM/CSS contract shared with the (frozen)
    // page HTML + style.css; kept until the later site rebuild unifies it.
    document.querySelectorAll('.league-switcher button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.group === groupId);
    });

    // Dispatch event for page scripts to listen to.
    // NOTE: 'league-changed' is the event-name contract shared with the (frozen)
    // page scripts (app.js, teams/bios/analytics); kept until the site rebuild.
    window.dispatchEvent(new CustomEvent('league-changed', { detail: { groupId } }));
}

// NOTE: public API named getCurrentLeague() is called by frozen bios.html;
// kept (returns the current group) until the later site rebuild renames callers.
function getCurrentLeague() {
    return currentGroup;
}

function getDataPath(filename) {
    return `data/${currentGroup}/${filename}`;
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

// --- Build group switcher ---
function buildGroupSwitcher() {
    const container = document.getElementById('league-switcher');
    if (!container) return;

    container.innerHTML = '';
    GROUPS.forEach(group => {
        const btn = document.createElement('button');
        btn.textContent = group.label;
        btn.dataset.group = group.id;
        btn.title = `${group.id} (${group.managers} managers)`;
        if (group.id === currentGroup) btn.classList.add('active');
        btn.addEventListener('click', () => setGroup(group.id));
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
    buildGroupSwitcher();
    initHamburger();
    highlightActivePage();
});
