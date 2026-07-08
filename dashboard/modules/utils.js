/* ── Shared Utility Functions ─────────────────────────────── */

export function escHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function stateToClass(state) {
    const s = (state || '').toUpperCase();
    if (s === 'ACTIVE') return 'state-active';
    if (s === 'IDLE') return 'state-idle';
    if (s === 'GRACE') return 'state-grace';
    if (s === 'ABANDONED') return 'state-abandoned';
    if (s === 'OFFLINE') return 'state-offline';
    return 'state-idle';
}

export function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '0m';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + s + 's';
    return s + 's';
}

export function fmtTime(isoStr) {
    if (!isoStr) return '—';
    try { return new Date(isoStr).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
    catch (_) { return isoStr; }
}

export function fmtDate(isoStr) {
    if (!isoStr) return '—';
    try { return new Date(isoStr).toLocaleDateString('en-GB'); }
    catch (_) { return isoStr; }
}

export function relTime(isoStr) {
    if (!isoStr) return '—';
    try {
        const diff = Date.now() - new Date(isoStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'Just now';
        if (mins < 60) return mins + 'm ago';
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return hrs + 'h ago';
        return Math.floor(hrs / 24) + 'd ago';
    } catch (_) { return isoStr; }
}

export function todayStr() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

/** Toast notification */
export function toast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = msg;
    container.appendChild(el);
    requestAnimationFrame(() => el.classList.add('toast-visible'));
    setTimeout(() => {
        el.classList.remove('toast-visible');
        setTimeout(() => el.remove(), 400);
    }, 3500);
}
