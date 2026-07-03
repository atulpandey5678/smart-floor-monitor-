/* ============================================================
   Cologic Shop Floor Tracker — dashboard/app.js
   Full rewrite — all features restored
   ============================================================ */

/* ── 1. GLOBALS & AUTH ─────────────────────────────────────── */
const App = {
    user: null,          // {username, role}
    activePage: 'home',
    ws: null,
    wsRetryTimer: null,
    wsRetries: 0,
    activityFeed: [],
    alertCache: [],      // latest unread alerts for bell
    trendChart: null,
    wizardState: {
        machineId: null,   // set when editing existing
        name: '', group: '', shiftHours: 8,
        rtspUrl: '', streamOk: false,
        zone: null, lightZone: null
    },
    shiftsData: [],      // named shifts array for settings
    settingsCache: {},   // last-loaded settings by section
};

/* Check session via /auth/me — cookie-based auth */
async function checkAuth() {
    try {
        const res = await fetch('/auth/me', { credentials: 'include' });
        if (!res.ok) throw new Error('Not authenticated');
        App.user = await res.json();
    } catch (_) {
        window.location.href = '/login.html';
        return false;
    }
    return true;
}

/* ── 2. MAIN INIT ──────────────────────────────────────────── */
window.addEventListener('DOMContentLoaded', async () => {
    const ok = await checkAuth();
    if (!ok) return;

    // Show username
    const userEl = document.getElementById('current-user-display');
    if (userEl) userEl.textContent = App.user.username + ' (' + App.user.role + ')';

    // Hide admin-only nav items for non-admins
    if (App.user.role !== 'admin') {
        document.querySelectorAll('.nav-admin-only').forEach(el => el.style.display = 'none');
    }

    initSidebar();
    initClock();
    initWebSocket();
    initAlertBell();
    initReportTabs();
    initSettingsTabs();
    initRestoreBackup();

    // Load first page
    await loadPageData('home');

    // Remove startup overlay
    setTimeout(() => {
        const ov = document.getElementById('startup-overlay');
        if (ov) ov.style.opacity = '0';
        setTimeout(() => { if (ov) ov.style.display = 'none'; }, 400);
    }, 600);

    // Logout
    document.getElementById('btn-logout')?.addEventListener('click', async () => {
        await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
        window.location.href = '/login.html';
    });

    // Sessions load button
    document.getElementById('btn-load-sessions')?.addEventListener('click', loadSessionsData);

    // Employee register
    document.getElementById('btn-register-emp')?.addEventListener('click', registerEmployee);

    // Reports download CSV
    document.getElementById('btn-download-csv')?.addEventListener('click', downloadCSV);

    // Machines empty-state button
    document.getElementById('btn-setup-first-machine')?.addEventListener('click', () => navigateTo('cameras'));
});

/* ── 3. SIDEBAR NAVIGATION ─────────────────────────────────── */
function initSidebar() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            navigateTo(page);
        });
    });
}

function navigateTo(page) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (navItem) navItem.classList.add('active');

    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const pageEl = document.getElementById('page-' + page);
    if (pageEl) pageEl.classList.add('active');

    // Update topbar title
    const titles = {
        home: ['Dashboard', 'Overview'],
        cameras: ['Camera Setup', 'Machine wizard'],
        machines: ['Live Machines', 'Real-time monitoring'],
        sessions: ['Sessions', 'Work session log'],
        employees: ['Employees', 'Workforce registry'],
        reports: ['Reports', 'Analytics & exports'],
        settings: ['Settings', 'System configuration'],
    };
    const [title, crumb] = titles[page] || [page, ''];
    const titleEl = document.querySelector('.topbar-title');
    const crumbEl = document.querySelector('.topbar-breadcrumb');
    if (titleEl) titleEl.textContent = title;
    if (crumbEl) crumbEl.textContent = crumb;

    App.activePage = page;
    loadPageData(page);
}

async function loadPageData(page) {
    if (page === 'home')      await loadHomeData();
    else if (page === 'machines')   await loadMachinesPage();
    else if (page === 'sessions')   await loadSessionsData();
    else if (page === 'employees')  await loadEmployeesData();
    else if (page === 'reports')    await loadReportsData();
    else if (page === 'settings')   await loadSettingsData();
    else if (page === 'cameras')    initCameraWizardPage();
}

/* ── 4. API UTILITY ────────────────────────────────────────── */
async function api(path, opts = {}) {
    const defaults = { credentials: 'include' };
    const merged = Object.assign({}, defaults, opts);
    if (merged.json !== undefined) {
        merged.headers = Object.assign({ 'Content-Type': 'application/json' }, merged.headers);
        merged.body = JSON.stringify(merged.json);
        delete merged.json;
        if (!merged.method) merged.method = 'POST';
    }
    const res = await fetch(path, merged);
    if (res.status === 401) {
        window.location.href = '/login.html';
        throw new Error('Not authenticated');
    }
    const ct = res.headers.get('content-type') || '';
    const data = ct.includes('application/json') ? await res.json() : await res.text();
    if (!res.ok) {
        const msg = (data && data.detail) ? data.detail : ('API error ' + res.status);
        throw new Error(msg);
    }
    return data;
}

/* ── 5. TOAST NOTIFICATIONS ───────────────────────────────── */
function toast(msg, type = 'info') {
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

/* ── 6. LIVE CLOCK ─────────────────────────────────────────── */
function initClock() {
    function tick() {
        const el = document.getElementById('live-clock');
        if (el) el.textContent = new Date().toLocaleTimeString('en-GB');
    }
    tick();
    setInterval(tick, 1000);
}

/* ── 7. ALERT BELL ─────────────────────────────────────────── */
function initAlertBell() {
    const wrap = document.getElementById('bell-icon-wrap');
    const dropdown = document.getElementById('alert-dropdown');
    if (!wrap || !dropdown) return;

    wrap.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('open');
        if (dropdown.classList.contains('open')) loadAlertDropdown();
    });

    document.addEventListener('click', () => dropdown.classList.remove('open'));
    dropdown.addEventListener('click', e => e.stopPropagation());

    document.getElementById('btn-clear-alerts')?.addEventListener('click', () => {
        setBadge(0);
        const list = document.getElementById('alert-dropdown-list');
        if (list) list.innerHTML = '<div class="alert-dropdown-empty">All caught up!</div>';
    });

    // Poll unread count every 30 seconds
    refreshAlertBadge();
    setInterval(refreshAlertBadge, 30000);
}

async function refreshAlertBadge() {
    try {
        const data = await api('/api/alerts/unread-count');
        setBadge(data.count || 0);
    } catch (_) {}
}

function setBadge(count) {
    const badge = document.getElementById('bell-badge');
    const metricEl = document.getElementById('metric-alerts');
    if (badge) {
        badge.textContent = count;
        badge.style.display = count > 0 ? 'flex' : 'none';
    }
    if (metricEl) metricEl.textContent = count;
}

async function loadAlertDropdown() {
    const list = document.getElementById('alert-dropdown-list');
    if (!list) return;
    list.innerHTML = '<div class="alert-dropdown-empty">Loading…</div>';
    try {
        const alerts = await api('/api/alerts/history?limit=10');
        if (!alerts.length) {
            list.innerHTML = '<div class="alert-dropdown-empty">No recent alerts</div>';
            return;
        }
        list.innerHTML = alerts.map(a => `
            <div class="alert-dropdown-item ${a.resolved ? 'resolved' : 'open'}">
                <div class="adi-type">${escHtml(a.alert_type)}</div>
                <div class="adi-msg">${escHtml(a.message || '')}</div>
                <div class="adi-time">${relTime(a.created_at)}</div>
            </div>
        `).join('');
    } catch (e) {
        list.innerHTML = '<div class="alert-dropdown-empty">Failed to load</div>';
    }
}

/* ── 8. WEBSOCKET ─────────────────────────────────────────── */
function initWebSocket() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = proto + '//' + location.host + '/ws';

    App.ws = new WebSocket(url);

    App.ws.onopen = () => {
        App.wsRetries = 0;
        if (App.wsRetryTimer) { clearTimeout(App.wsRetryTimer); App.wsRetryTimer = null; }
        setWsStatus('connected');
        document.getElementById('connection-banner').hidden = true;
    };

    App.ws.onmessage = (ev) => {
        try { handleWsMessage(JSON.parse(ev.data)); } catch (_) {}
    };

    App.ws.onclose = () => {
        setWsStatus('disconnected');
        document.getElementById('connection-banner').hidden = false;
        const delay = Math.min(30000, 2000 * Math.pow(1.5, App.wsRetries));
        App.wsRetries++;
        App.wsRetryTimer = setTimeout(initWebSocket, delay);
    };

    App.ws.onerror = () => App.ws.close();
}

function setWsStatus(s) {
    const el = document.getElementById('ws-status');
    if (!el) return;
    el.dataset.status = s;
    el.innerHTML = `<span class="status-dot-indicator"></span>${s === 'connected' ? 'Live' : 'Disconnected'}`;
}

function handleWsMessage(data) {
    // State broadcast from server: {state, badge_id, employee_name, active_duration_seconds,
    //   efficiency_percent, machine_id, light_color, light_confidence, …}
    if (data.state !== undefined) {
        updateHomeMetricsFromWs(data);
        updateMachineCardsFromWs(data);
    }
    // Event notifications
    if (data.event_type) {
        pushActivity(data);
        if (data.event_type === 'ALERT') refreshAlertBadge();
    }
}

/* ── 9. ACTIVITY FEED ─────────────────────────────────────── */
function pushActivity(data) {
    const feed = document.getElementById('activity-feed');
    const empty = document.getElementById('activity-feed-empty');
    if (!feed) return;

    const label = activityLabel(data);
    const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

    const li = document.createElement('li');
    li.className = 'activity-item type-' + (data.event_type || 'info').toLowerCase();
    li.innerHTML = `
        <span class="activity-dot"></span>
        <span class="activity-time">${now}</span>
        <span class="activity-text">${escHtml(label)}</span>
    `;
    feed.prepend(li);
    if (empty) empty.style.display = 'none';

    // Keep max 50 entries
    const children = feed.querySelectorAll('li');
    if (children.length > 50) children[children.length - 1].remove();

    // Store in cache
    App.activityFeed.unshift({ time: now, label, type: data.event_type });
    if (App.activityFeed.length > 50) App.activityFeed.pop();
}

function activityLabel(data) {
    const e = data.event_type || '';
    if (e === 'SESSION_START') return `Session started — ${data.employee_name || data.badge_id || 'Unknown'} on ${data.machine_id || 'M-01'}`;
    if (e === 'SESSION_END')   return `Session ended — ${data.employee_name || data.badge_id || 'Unknown'} on ${data.machine_id || 'M-01'}`;
    if (e === 'ALERT')         return `Alert: ${data.alert_type || ''} — ${data.message || ''}`;
    if (e === 'LIGHT')         return `Light changed to ${data.light_color || '?'} on ${data.machine_id || 'M-01'}`;
    return data.message || e;
}

/* ── 10. HOME PAGE ─────────────────────────────────────────── */
async function loadHomeData() {
    await Promise.allSettled([loadHomeAlerts(), loadHomeMachineStatus()]);
}

async function loadHomeAlerts() {
    try {
        const alerts = await api('/api/alerts');
        renderHomeAlertsTable(alerts);
        setBadge(alerts.length);
    } catch (e) { console.error('loadHomeAlerts', e); }
}

function renderHomeAlertsTable(alerts) {
    const tbody = document.getElementById('home-alerts-body');
    const empty = document.getElementById('home-alerts-empty');
    const badge = document.getElementById('alerts-count-badge');
    if (!tbody) return;

    if (badge) badge.textContent = alerts.length;

    if (!alerts.length) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';

    tbody.innerHTML = alerts.slice(0, 20).map(a => `
        <tr>
            <td><span class="alert-pill alert-${(a.alert_type || '').toLowerCase().replace(/[^a-z]/g, '-')}">${escHtml(a.alert_type)}</span></td>
            <td>${escHtml(a.machine_id || a.badge_id || '—')}</td>
            <td>${escHtml(a.employee_name || a.badge_id || '—')}</td>
            <td>${relTime(a.created_at)}</td>
            <td>
                ${a.resolved ? '<span class="badge-resolved">Resolved</span>' :
                `<button class="btn btn-ghost btn-sm" onclick="resolveAlertPrompt(${a.id})">Resolve</button>`}
            </td>
        </tr>
    `).join('');
}

async function loadHomeMachineStatus() {
    try {
        const state = await api('/api/status');
        renderHomeMachineList([state]);
        updateMetricCards(state);
    } catch (e) { console.error('loadHomeMachineStatus', e); }
}

function updateMetricCards(state) {
    const workers = document.getElementById('metric-workers');
    const hrs = document.getElementById('metric-machine-hrs');
    const eff = document.getElementById('metric-efficiency');
    if (workers) workers.textContent = state.state === 'ACTIVE' ? 1 : 0;
    if (hrs && state.active_duration_seconds !== undefined)
        hrs.textContent = (state.active_duration_seconds / 3600).toFixed(1);
    if (eff && state.efficiency_percent !== undefined)
        eff.textContent = state.efficiency_percent > 0 ? state.efficiency_percent.toFixed(0) + '%' : '—';
}

function updateHomeMetricsFromWs(data) {
    if (App.activePage !== 'home') return;
    updateMetricCards(data);
}

function renderHomeMachineList(machines) {
    const ul = document.getElementById('home-machine-list');
    const empty = document.getElementById('home-machines-empty');
    if (!ul) return;

    if (!machines || !machines.length) {
        ul.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';

    ul.innerHTML = machines.map(m => {
        const stateClass = stateToClass(m.state);
        const dur = m.active_duration_seconds ? formatDuration(m.active_duration_seconds) : '—';
        return `
        <li class="ms-item">
            <div class="ms-row">
                <span class="ms-id">${escHtml(m.machine_id || 'M-01')}</span>
                <span class="ms-badge ${stateClass}">${escHtml(m.state || 'IDLE')}</span>
            </div>
            <div class="ms-sub">
                ${m.employee_name ? escHtml(m.employee_name) : (m.badge_id ? 'Badge: ' + escHtml(m.badge_id) : 'No worker')}
                ${dur !== '—' ? ' · ' + dur : ''}
            </div>
            ${m.light_color ? `<div class="ms-light light-${(m.light_color || '').toLowerCase()}">⬤ ${escHtml(m.light_color)}</div>` : ''}
        </li>`;
    }).join('');
}

/* ── 11. RESOLVE ALERT ─────────────────────────────────────── */
window.resolveAlertPrompt = async function(id) {
    const note = prompt('Enter root cause / note (optional):');
    if (note === null) return;
    try {
        await api(`/api/alerts/${id}/resolve`, { method: 'POST', json: { note } });
        toast('Alert resolved', 'success');
        loadHomeAlerts();
        refreshAlertBadge();
    } catch (e) { toast(e.message, 'error'); }
};


/* ── 12. MACHINES PAGE ─────────────────────────────────────── */
async function loadMachinesPage() {
    const container = document.getElementById('machines-container');
    const emptyState = document.getElementById('machines-empty-state');
    if (!container) return;
    try {
        const state = await api('/api/status');
        emptyState.hidden = true;
        renderMachineCards([state]);
    } catch (e) {
        container.innerHTML = '';
        emptyState.hidden = false;
    }
}

function renderMachineCards(machines) {
    const container = document.getElementById('machines-container');
    if (!container) return;
    container.innerHTML = machines.map(m => {
        const sc = stateToClass(m.state);
        const dur = m.active_duration_seconds ? formatDuration(m.active_duration_seconds) : '—';
        const eff = m.efficiency_percent !== undefined ? m.efficiency_percent.toFixed(0) + '%' : '—';
        const lightBadge = m.light_color
            ? `<span class="mc-light light-${(m.light_color||'').toLowerCase()}">${escHtml(m.light_color)}</span>` : '';
        const mid = escHtml(m.machine_id || 'M-01');
        return `
        <div class="machine-card" id="mc-${mid}">
            <div class="mc-header">
                <div class="mc-title-row">
                    <h3 class="mc-name">${mid}</h3>
                    <span class="mc-state-badge ${sc}">${escHtml(m.state||'IDLE')}</span>
                    ${lightBadge}
                </div>
                <div class="mc-meta">
                    <span class="mc-worker">${m.employee_name ? escHtml(m.employee_name) : (m.badge_id ? 'Badge: '+escHtml(m.badge_id) : 'No worker')}</span>
                    <span class="mc-duration">${dur}</span>
                    <span class="mc-efficiency">${eff} efficiency</span>
                </div>
            </div>
            <div class="mc-feed-wrap">
                <img src="/api/video_feed" class="mc-feed" alt="Live feed">
                <div class="mc-feed-overlay"><span class="mc-status-dot ${sc}"></span></div>
            </div>
            <div class="mc-footer">
                <button class="btn btn-ghost btn-sm" onclick="navigateTo('cameras')">Edit Zone</button>
                <button class="btn btn-ghost btn-sm" onclick="loadMachinesPage()">Refresh</button>
            </div>
        </div>`;
    }).join('');
}

function updateMachineCardsFromWs(data) {
    if (App.activePage !== 'machines') return;
    const mid = data.machine_id || 'M-01';
    const card = document.getElementById('mc-' + mid);
    if (!card) return;
    const sc = stateToClass(data.state);
    const stBadge = card.querySelector('.mc-state-badge');
    if (stBadge) { stBadge.className = 'mc-state-badge ' + sc; stBadge.textContent = data.state || 'IDLE'; }
    const dot = card.querySelector('.mc-status-dot');
    if (dot) dot.className = 'mc-status-dot ' + sc;
    const worker = card.querySelector('.mc-worker');
    if (worker) worker.textContent = data.employee_name || (data.badge_id ? 'Badge: '+data.badge_id : 'No worker');
    const dur = card.querySelector('.mc-duration');
    if (dur) dur.textContent = data.active_duration_seconds ? formatDuration(data.active_duration_seconds) : '—';
}

/* ── 13. SESSIONS PAGE ─────────────────────────────────────── */
async function loadSessionsData() {
    const dateEl = document.getElementById('sessions-date');
    const tbody = document.getElementById('sessions-body');
    const empty = document.getElementById('sessions-empty');
    if (!tbody) return;
    const dateVal = dateEl ? dateEl.value : '';
    const url = dateVal ? '/api/sessions/today?date=' + dateVal : '/api/sessions/today';
    try {
        const sessions = await api(url);
        tbody.innerHTML = '';
        if (!sessions || !sessions.length) {
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';
        tbody.innerHTML = sessions.map(s => {
            const start = fmtTime(s.start_time);
            const end = s.end_time ? fmtTime(s.end_time) : '<em>Active</em>';
            const dur = s.duration_seconds ? formatDuration(s.duration_seconds) : '—';
            const status = s.end_time ? 'Completed' : 'Active';
            return `<tr>
                <td>${escHtml(s.employee_name || s.badge_id || '—')}</td>
                <td>${escHtml(s.machine_id || 'M-01')}</td>
                <td>${start}</td>
                <td>${end}</td>
                <td>${dur}</td>
                <td><span class="sess-status ${s.end_time ? 'completed' : 'active'}">${status}</span></td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error('loadSessionsData', e);
        toast('Failed to load sessions', 'error');
    }
}

/* ── 14. EMPLOYEES PAGE ─────────────────────────────────────── */
async function loadEmployeesData() {
    try {
        const emps = await api('/api/employees');
        const tbody = document.getElementById('employees-body');
        const empty = document.getElementById('employees-empty');
        if (!tbody) return;
        tbody.innerHTML = '';
        if (!emps || !emps.length) {
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';
        tbody.innerHTML = emps.map(e => {
            const canDel = App.user.role === 'admin';
            const delBtn = canDel
                ? '<button class=\"btn btn-danger btn-sm\" onclick=\"deleteEmployee(\'' + escHtml(e.badge_id) + '\',\'' + escHtml(e.name) + '\')\">Delete</button>'
                : '';
            return `<tr>
                <td><span class="mono">${escHtml(e.badge_id)}</span></td>
                <td>${escHtml(e.name)}</td>
                <td>${delBtn}</td>
            </tr>`;
        }).join('');
    } catch (e) { console.error('loadEmployeesData', e); }
}

async function registerEmployee() {
    const badge = document.getElementById('emp-badge-id') && document.getElementById('emp-badge-id').value.trim();
    const name  = document.getElementById('emp-name') && document.getElementById('emp-name').value.trim();
    const msg   = document.getElementById('emp-message');
    if (!badge || !name) {
        if (msg) { msg.textContent = 'Please fill in all fields.'; msg.className = 'form-message error'; }
        return;
    }
    try {
        await api('/api/employees', { json: { badge_id: badge, name: name } });
        if (msg) { msg.textContent = 'Employee registered successfully.'; msg.className = 'form-message success'; }
        document.getElementById('emp-badge-id').value = '';
        document.getElementById('emp-name').value = '';
        await loadEmployeesData();
    } catch (e) {
        if (msg) { msg.textContent = e.message; msg.className = 'form-message error'; }
    }
}

window.deleteEmployee = async function(badgeId, name) {
    if (!confirm('Delete employee ' + name + ' (' + badgeId + ')?')) return;
    try {
        await api('/api/employees/' + badgeId, { method: 'DELETE' });
        toast('Employee deleted.', 'success');
        loadEmployeesData();
    } catch (e) { toast(e.message, 'error'); }
};



/* ── 15. REPORTS PAGE ─────────────────────────────────────── */
let currentReportType = 'daily';

function initReportTabs() {
    document.querySelectorAll('.report-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.report-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentReportType = btn.dataset.reportType;
            loadReportsData();
        });
    });
    // Set default date to today
    const dateEl = document.getElementById('reports-date');
    if (dateEl && !dateEl.value) dateEl.value = todayStr();
}

async function loadReportsData() {
    const dateEl = document.getElementById('reports-date');
    const dateVal = dateEl ? dateEl.value : todayStr();

    try {
        let data;
        const trend = document.getElementById('report-trend-banner');

        if (currentReportType === 'weekly') {
            data = await api('/api/reports/weekly?week_start=' + dateVal);
            if (trend) {
                trend.hidden = false;
                const th = document.getElementById('trend-hours');
                const tu = document.getElementById('trend-utilization');
                const te = document.getElementById('trend-efficiency');
                if (th) th.textContent = 'Hours: ' + (data.total_machine_hours || 0).toFixed(1);
                if (tu) tu.textContent = 'Utilization: ' + (data.avg_utilization_percent || 0).toFixed(0) + '%';
                if (te) te.textContent = 'Efficiency: ' + (data.avg_efficiency_percent || 0).toFixed(0) + '%';
            }
        } else {
            data = await api('/api/reports/daily?date=' + dateVal);
            if (trend) trend.hidden = true;
        }

        // Summary cards
        const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        setVal('report-total-sessions', data.total_sessions || 0);
        setVal('report-total-hours', (data.total_machine_hours || 0).toFixed(1));
        setVal('report-avg-efficiency', data.avg_efficiency_percent > 0 ? data.avg_efficiency_percent.toFixed(0) + '%' : '—');
        setVal('report-avg-utilization', data.avg_utilization_percent > 0 ? data.avg_utilization_percent.toFixed(0) + '%' : '—');

        // Worker breakdown
        renderWorkerTable(data.worker_stats || {});
        // Alert summary
        renderAlertSummaryTable(data.alert_counts || {});
        // Trend chart
        if (data.daily_trend) renderTrendChart(data.daily_trend);

    } catch (e) {
        console.error('loadReportsData', e);
        toast('Failed to load report', 'error');
    }
}

function renderWorkerTable(workerStats) {
    const tbody = document.getElementById('report-workers-body');
    const empty = document.getElementById('report-workers-empty');
    if (!tbody) return;
    const keys = Object.keys(workerStats || {});
    if (!keys.length) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';
    tbody.innerHTML = keys.map(k => {
        const w = workerStats[k];
        return `<tr>
            <td><span class="mono">${escHtml(k)}</span></td>
            <td>${escHtml(w.name || '—')}</td>
            <td>${(w.total_hours || 0).toFixed(1)}</td>
            <td>${w.efficiency_percent > 0 ? w.efficiency_percent.toFixed(0) + '%' : '—'}</td>
        </tr>`;
    }).join('');
}

function renderAlertSummaryTable(alertCounts) {
    const tbody = document.getElementById('report-alerts-body');
    const empty = document.getElementById('report-alerts-empty');
    if (!tbody) return;
    const keys = Object.keys(alertCounts || {});
    if (!keys.length) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';
    tbody.innerHTML = keys.map(k => `
        <tr>
            <td>${escHtml(k)}</td>
            <td>${alertCounts[k]}</td>
        </tr>`).join('');
}

function renderTrendChart(trendData) {
    const canvas = document.getElementById('trend-chart');
    const panel = document.getElementById('reports-chart-panel');
    if (!canvas || !window.Chart) return;
    if (panel) panel.style.display = '';

    const labels = trendData.map(d => d.date);
    const values = trendData.map(d => d.total_hours || 0);

    if (App.trendChart) App.trendChart.destroy();
    App.trendChart = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Active Hours',
                data: values,
                borderColor: '#6366F1',
                backgroundColor: 'rgba(99,102,241,0.15)',
                borderWidth: 2,
                pointBackgroundColor: '#6366F1',
                pointRadius: 4,
                tension: 0.35,
                fill: true,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: { mode: 'index', intersect: false }
            },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9CA3AF' } },
                y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9CA3AF' }, beginAtZero: true }
            }
        }
    });
}

async function downloadCSV() {
    const dateEl = document.getElementById('reports-date');
    const dateVal = dateEl ? dateEl.value : todayStr();
    const url = currentReportType === 'weekly'
        ? '/api/reports/weekly?week_start=' + dateVal + '&format=csv'
        : '/api/reports/daily?date=' + dateVal + '&format=csv';
    try {
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw new Error('Download failed');
        const blob = await res.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        const cd = res.headers.get('content-disposition') || '';
        const m = cd.match(/filename="([^"]+)"/);
        a.download = m ? m[1] : 'report.csv';
        a.click();
        URL.revokeObjectURL(a.href);
    } catch (e) { toast('CSV download failed: ' + e.message, 'error'); }
}

/* ── 16. SETTINGS PAGE ────────────────────────────────────── */
function initSettingsTabs() {
    document.querySelectorAll('.settings-tab-item').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.settings-tab-item').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const panel = document.getElementById('settings-tab-' + tab.dataset.tab);
            if (panel) panel.classList.add('active');
        });
    });

    // Slider live value display
    document.querySelectorAll('input[type=range]').forEach(slider => {
        const valEl = document.getElementById(slider.id + '-val');
        if (valEl) {
            valEl.textContent = parseFloat(slider.value).toFixed(2);
            slider.addEventListener('input', () => {
                valEl.textContent = parseFloat(slider.value).toFixed(2);
            });
        }
    });

    // Color pickers sync
    const colorPicker = document.getElementById('s-primary-color');
    const colorHex = document.getElementById('s-primary-color-hex');
    if (colorPicker && colorHex) {
        colorPicker.addEventListener('input', () => { colorHex.value = colorPicker.value; });
        colorHex.addEventListener('input', () => {
            if (/^#[0-9a-f]{6}$/i.test(colorHex.value)) colorPicker.value = colorHex.value;
        });
    }

    // Logo preview
    const logoUrl = document.getElementById('s-logo-url');
    const logoPreview = document.getElementById('s-logo-preview');
    if (logoUrl && logoPreview) {
        logoUrl.addEventListener('input', () => { logoPreview.src = logoUrl.value; });
    }

    // Add shift button
    document.getElementById('btn-add-shift')?.addEventListener('click', addShiftRow);
    // Add holiday button
    document.getElementById('btn-add-holiday')?.addEventListener('click', addHoliday);

    // User management
    document.getElementById('btn-create-user')?.addEventListener('click', createUser);
}

async function loadSettingsData() {
    try {
        const all = await api('/api/settings');
        App.settingsCache = all;

        // Detection
        const d = all.detection || {};
        setInputVal('s-person-conf', d.person_confidence_threshold);
        setInputVal('s-grace', d.grace_period_seconds);
        setInputVal('s-stable-frames', d.stable_frames_required);
        setInputVal('s-flow-thresh', d.optical_flow_threshold);
        setInputVal('s-static-timeout', d.static_worker_timeout);
        syncSliderVal('s-person-conf');
        syncSliderVal('s-flow-thresh');

        // Light
        const l = all.light || {};
        setChecked('s-light-enabled', l.enabled !== false);
        setChecked('s-light-alert-red', l.alert_on_red !== false);
        setInputVal('s-sat-min', l.saturation_min);
        setInputVal('s-bright-min', l.brightness_min);
        setInputVal('s-min-px', l.min_pixels);
        setInputVal('s-analysis-size', l.analysis_size);
        setInputVal('s-green-min', l.green_hue_min);
        setInputVal('s-green-max', l.green_hue_max);
        setInputVal('s-amber-min', l.amber_hue_min);
        setInputVal('s-amber-max', l.amber_hue_max);
        setInputVal('s-red-low-min', l.red_low_min);
        setInputVal('s-red-low-max', l.red_low_max);
        setInputVal('s-red-high-min', l.red_high_min);
        setInputVal('s-red-high-max', l.red_high_max);
        syncSliderVal('s-sat-min');
        syncSliderVal('s-bright-min');

        // Shifts
        const sh = all.shifts || {};
        setInputVal('s-shift-hours', sh.default_shift_hours);
        App.shiftsData = sh.named_shifts || [];
        renderShiftsList();
        const holidays = sh.holidays || [];
        renderHolidaysList(holidays);

        // Notifications
        const n = all.notifications || {};
        setChecked('s-email-enabled', !!n.email_enabled);
        setInputVal('s-smtp-host', n.smtp_host);
        setInputVal('s-smtp-port', n.smtp_port);
        setInputVal('s-smtp-user', n.smtp_username);
        setChecked('s-notify-red', n.notify_red_light !== false);
        setChecked('s-notify-static', n.notify_static_worker !== false);
        setChecked('s-notify-camera', !!n.notify_camera_offline);
        setInputVal('s-recipients', (n.recipients || []).join('\n'));

        // Branding
        const b = all.branding || {};
        setInputVal('s-company-name', b.company_name);
        setInputVal('s-logo-url', b.logo_url);
        const logoPreview = document.getElementById('s-logo-preview');
        if (logoPreview) logoPreview.src = b.logo_url || '';
        if (b.primary_color) {
            setInputVal('s-primary-color', b.primary_color);
            setInputVal('s-primary-color-hex', b.primary_color);
        }

        // Retention
        const r = all.retention || {};
        setInputVal('s-retention-days', r.retention_days);
        setChecked('s-archive-enabled', r.auto_archive_enabled !== false);
        setInputVal('s-archive-time', r.archive_time);

        // Users list (admin only)
        if (App.user.role === 'admin') {
            const settingsUsersTab = document.getElementById('settings-users-tab');
            if (settingsUsersTab) settingsUsersTab.style.display = '';
            await loadUsersList();
        } else {
            const settingsUsersTab = document.getElementById('settings-users-tab');
            if (settingsUsersTab) settingsUsersTab.style.display = 'none';
        }

    } catch (e) {
        console.error('loadSettingsData', e);
    }
}

function setInputVal(id, val) {
    const el = document.getElementById(id);
    if (!el || val === undefined || val === null) return;
    el.value = val;
}

function setChecked(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = !!val;
}

function syncSliderVal(id) {
    const slider = document.getElementById(id);
    const valEl = document.getElementById(id + '-val');
    if (slider && valEl) valEl.textContent = parseFloat(slider.value).toFixed(2);
}

window.saveSettings = async function(section) {
    const banner = document.getElementById('settings-save-banner');
    let payload = {};

    if (section === 'detection') {
        payload = {
            person_confidence_threshold: parseFloat(document.getElementById('s-person-conf')?.value || 0.6),
            grace_period_seconds: parseInt(document.getElementById('s-grace')?.value || 180),
            stable_frames_required: parseInt(document.getElementById('s-stable-frames')?.value || 4),
            optical_flow_threshold: parseFloat(document.getElementById('s-flow-thresh')?.value || 2.0),
            static_worker_timeout: parseInt(document.getElementById('s-static-timeout')?.value || 180),
        };
    } else if (section === 'light') {
        payload = {
            enabled: document.getElementById('s-light-enabled')?.checked,
            alert_on_red: document.getElementById('s-light-alert-red')?.checked,
            saturation_min: parseInt(document.getElementById('s-sat-min')?.value || 30),
            brightness_min: parseInt(document.getElementById('s-bright-min')?.value || 40),
            min_pixels: parseInt(document.getElementById('s-min-px')?.value || 15),
            analysis_size: parseInt(document.getElementById('s-analysis-size')?.value || 160),
            green_hue_min: parseInt(document.getElementById('s-green-min')?.value || 25),
            green_hue_max: parseInt(document.getElementById('s-green-max')?.value || 95),
            amber_hue_min: parseInt(document.getElementById('s-amber-min')?.value || 10),
            amber_hue_max: parseInt(document.getElementById('s-amber-max')?.value || 25),
            red_low_min: parseInt(document.getElementById('s-red-low-min')?.value || 0),
            red_low_max: parseInt(document.getElementById('s-red-low-max')?.value || 10),
            red_high_min: parseInt(document.getElementById('s-red-high-min')?.value || 160),
            red_high_max: parseInt(document.getElementById('s-red-high-max')?.value || 180),
        };
    } else if (section === 'shifts') {
        payload = {
            default_shift_hours: parseFloat(document.getElementById('s-shift-hours')?.value || 8),
            named_shifts: App.shiftsData,
            holidays: Array.from(document.querySelectorAll('.holiday-item')).map(el => el.dataset.date).filter(Boolean),
        };
    } else if (section === 'notifications') {
        const recips = document.getElementById('s-recipients')?.value || '';
        payload = {
            email_enabled: document.getElementById('s-email-enabled')?.checked,
            smtp_host: document.getElementById('s-smtp-host')?.value,
            smtp_port: parseInt(document.getElementById('s-smtp-port')?.value || 587),
            smtp_username: document.getElementById('s-smtp-user')?.value,
            recipients: recips.split('\n').map(s => s.trim()).filter(Boolean),
            notify_red_light: document.getElementById('s-notify-red')?.checked,
            notify_static_worker: document.getElementById('s-notify-static')?.checked,
            notify_camera_offline: document.getElementById('s-notify-camera')?.checked,
        };
    } else if (section === 'branding') {
        payload = {
            company_name: document.getElementById('s-company-name')?.value,
            logo_url: document.getElementById('s-logo-url')?.value,
            primary_color: document.getElementById('s-primary-color-hex')?.value,
        };
    } else if (section === 'retention') {
        payload = {
            retention_days: parseInt(document.getElementById('s-retention-days')?.value || 90),
            auto_archive_enabled: document.getElementById('s-archive-enabled')?.checked,
            archive_time: document.getElementById('s-archive-time')?.value,
        };
    }

    try {
        await api('/api/settings/' + section, { method: 'PUT', json: payload });
        if (banner) { banner.hidden = false; setTimeout(() => { banner.hidden = true; }, 3000); }
    } catch (e) { toast('Save failed: ' + e.message, 'error'); }
};

/* ── 17. SHIFTS & HOLIDAYS WITHIN SETTINGS ─────────────────── */
function renderShiftsList() {
    const container = document.getElementById('shifts-list');
    if (!container) return;
    container.innerHTML = App.shiftsData.map((shift, i) => `
        <div class="shift-row" data-idx="${i}">
            <input type="text" class="input-field" placeholder="Name (e.g. Shift A)" value="${escHtml(shift.name||'')}"
                onchange="App.shiftsData[${i}].name=this.value">
            <input type="time" class="input-field" value="${escHtml(shift.start||'')}"
                onchange="App.shiftsData[${i}].start=this.value">
            <span>to</span>
            <input type="time" class="input-field" value="${escHtml(shift.end||'')}"
                onchange="App.shiftsData[${i}].end=this.value">
            <button class="btn btn-ghost btn-sm btn-danger" onclick="removeShift(${i})">Remove</button>
        </div>`).join('');
}

function addShiftRow() {
    App.shiftsData.push({ name: '', start: '06:00', end: '14:00' });
    renderShiftsList();
}

window.removeShift = function(i) {
    App.shiftsData.splice(i, 1);
    renderShiftsList();
};

function renderHolidaysList(holidays) {
    const container = document.getElementById('holidays-list');
    if (!container) return;
    container.innerHTML = holidays.map(d => `
        <div class="shift-row holiday-item" data-date="${d}">
            <span>${d}</span>
            <button class="btn btn-ghost btn-sm" onclick="this.closest('.holiday-item').remove()">Remove</button>
        </div>`).join('');
}

function addHoliday() {
    const input = document.getElementById('holiday-date-input');
    if (!input || !input.value) return;
    const container = document.getElementById('holidays-list');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'shift-row holiday-item';
    div.dataset.date = input.value;
    div.innerHTML = `<span>${input.value}</span>
        <button class="btn btn-ghost btn-sm" onclick="this.closest('.holiday-item').remove()">Remove</button>`;
    container.appendChild(div);
    input.value = '';
}

/* ── 18. USER MANAGEMENT (ADMIN) ──────────────────────────── */
async function loadUsersList() {
    try {
        const users = await api('/auth/users');
        const tbody = document.getElementById('users-body');
        const empty = document.getElementById('users-empty');
        if (!tbody) return;
        tbody.innerHTML = '';
        if (!users || !users.length) {
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';
        tbody.innerHTML = users.map(u => `
            <tr>
                <td>${escHtml(u.username)}</td>
                <td><span class="role-badge role-${u.role}">${u.role}</span></td>
                <td>${u.created_at ? fmtDate(u.created_at) : '—'}</td>
                <td>${u.last_login ? fmtDate(u.last_login) : 'Never'}</td>
                <td>
                    ${u.username !== App.user.username
                        ? '<button class="btn btn-danger btn-sm" onclick="deleteUser(\'' + escHtml(u.username) + '\')">Delete</button>'
                        : '<span class="text-muted">(you)</span>'}
                </td>
            </tr>`).join('');
    } catch (e) { console.error('loadUsersList', e); }
}

async function createUser() {
    const uname = document.getElementById('new-user-username')?.value.trim();
    const pwd   = document.getElementById('new-user-password')?.value;
    const role  = document.getElementById('new-user-role')?.value;
    const msg   = document.getElementById('create-user-msg');
    if (!uname || !pwd) {
        if (msg) { msg.textContent = 'Username and password required.'; msg.style.color = 'var(--danger)'; }
        return;
    }
    try {
        await api('/auth/users', { json: { username: uname, password: pwd, role } });
        if (msg) { msg.textContent = 'User created.'; msg.style.color = 'var(--success)'; }
        document.getElementById('new-user-username').value = '';
        document.getElementById('new-user-password').value = '';
        await loadUsersList();
    } catch (e) {
        if (msg) { msg.textContent = e.message; msg.style.color = 'var(--danger)'; }
    }
}

window.deleteUser = async function(username) {
    if (!confirm('Delete user ' + username + '?')) return;
    try {
        await api('/auth/users/' + username, { method: 'DELETE' });
        toast('User deleted.', 'success');
        loadUsersList();
    } catch (e) { toast(e.message, 'error'); }
};

/* ── 19. BACKUP / RESTORE ─────────────────────────────────── */
function initRestoreBackup() {
    const fileInput = document.getElementById('restore-file');
    if (!fileInput) return;
    fileInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const statusEl = document.getElementById('restore-status');
        if (statusEl) { statusEl.textContent = 'Uploading backup…'; statusEl.style.color = 'var(--text-secondary)'; }
        const fd = new FormData();
        fd.append('file', file);
        try {
            const res = await fetch('/api/settings/backup/restore', {
                method: 'POST',
                credentials: 'include',
                body: fd,
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                if (statusEl) { statusEl.textContent = data.message || 'Restored. Restart server for full effect.'; statusEl.style.color = 'var(--success)'; }
                toast('Database restored successfully.', 'success');
            } else {
                throw new Error(data.detail || 'Restore failed');
            }
        } catch (err) {
            if (statusEl) { statusEl.textContent = err.message; statusEl.style.color = 'var(--danger)'; }
            toast('Restore failed: ' + err.message, 'error');
        }
        fileInput.value = '';
    });
}



/* ── 20. CAMERA WIZARD ────────────────────────────────────── */
let wizCanvas = null, wizCtx = null, wizDrawing = false, wizStart = {x:0,y:0};
let lightCanvas = null, lightCtx = null, lightDrawing = false, lightStart = {x:0,y:0};

function initCameraWizardPage() {
    resetWizard();
    loadCamMachineList();
    bindWizardButtons();
}

function resetWizard() {
    App.wizardState = { machineId: null, name:'', group:'', shiftHours:8, rtspUrl:'', streamOk:false, zone:null, lightZone:null };
    showCamStep('cam-welcome');
}

function showCamStep(id) {
    ['cam-welcome','cam-step-1','cam-step-2','cam-step-3','cam-step-3b','cam-step-4','cam-detail-view']
        .forEach(s => { const el=document.getElementById(s); if(el) el.hidden = (s !== id); });
}

async function loadCamMachineList() {
    const ul = document.getElementById('cam-machine-items');
    const emptyEl = document.getElementById('cam-machine-empty');
    if (!ul) return;
    try {
        const status = await api('/api/status');
        ul.innerHTML = '';
        const machines = status && status.state !== undefined ? [status] : [];
        if (!machines.length) {
            if (emptyEl) emptyEl.style.display = 'block';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        machines.forEach(m => {
            const li = document.createElement('li');
            li.className = 'cam-machine-item';
            const mid = m.machine_id || 'M-01';
            li.innerHTML = `<span class="cm-name">${escHtml(mid)}</span><span class="cm-state ${stateToClass(m.state)}">${escHtml(m.state||'IDLE')}</span>`;
            li.addEventListener('click', () => openMachineDetail(mid, m));
            ul.appendChild(li);
        });
    } catch(e) { console.error('loadCamMachineList', e); }
}

function openMachineDetail(machineId, stateData) {
    document.querySelectorAll('.cam-machine-item').forEach(el => el.classList.remove('selected'));
    const selected = Array.from(document.querySelectorAll('.cam-machine-item'))
        .find(el => el.querySelector('.cm-name')?.textContent === machineId);
    if (selected) selected.classList.add('selected');

    const title = document.getElementById('cam-detail-title');
    if (title) title.textContent = machineId + ' — Details';
    const info = document.getElementById('cam-detail-info');
    if (info) info.innerHTML = `
        <div class="detail-row"><strong>State:</strong> ${escHtml(stateData.state||'IDLE')}</div>
        <div class="detail-row"><strong>Worker:</strong> ${escHtml(stateData.employee_name || stateData.badge_id || 'None')}</div>
        <div class="detail-row"><strong>Efficiency:</strong> ${stateData.efficiency_percent||0}%</div>
    `;
    const detailImg = document.getElementById('cam-detail-img');
    if (detailImg) { detailImg.src = '/api/video_feed'; detailImg.style.display = 'block'; }

    const editZoneBtn = document.getElementById('cam-detail-edit-zone');
    if (editZoneBtn) editZoneBtn.onclick = () => {
        App.wizardState.machineId = machineId;
        App.wizardState.name = machineId;
        showCamStep('cam-step-1');
        const nameEl = document.getElementById('wiz-machine-name');
        if (nameEl) nameEl.value = machineId;
    };
    const deleteBtn = document.getElementById('cam-detail-delete');
    if (deleteBtn) deleteBtn.onclick = () => {
        if (confirm('Remove machine ' + machineId + ' configuration?')) {
            toast('Machine configuration cleared.', 'success');
            showCamStep('cam-welcome');
            loadCamMachineList();
        }
    };
    showCamStep('cam-detail-view');
}

function bindWizardButtons() {
    document.getElementById('btn-add-machine')?.addEventListener('click', () => {
        App.wizardState = { machineId: null, name:'', group:'', shiftHours:8, rtspUrl:'', streamOk:false, zone:null, lightZone:null };
        const nameEl = document.getElementById('wiz-machine-name');
        const grpEl  = document.getElementById('wiz-machine-group');
        const hrsEl  = document.getElementById('wiz-shift-hours');
        if (nameEl) nameEl.value = '';
        if (grpEl) grpEl.value = '';
        if (hrsEl) hrsEl.value = 8;
        showCamStep('cam-step-1');
    });

    // Step 1 -> 2
    document.getElementById('wiz-next-1')?.addEventListener('click', () => {
        const name = document.getElementById('wiz-machine-name')?.value.trim();
        const hrs  = parseFloat(document.getElementById('wiz-shift-hours')?.value) || 0;
        const nameErr = document.getElementById('wiz-name-error');
        const hrsErr  = document.getElementById('wiz-hours-error');
        let ok = true;
        if (!name) { if (nameErr) nameErr.textContent='Name is required'; ok=false; } else { if (nameErr) nameErr.textContent=''; }
        if (!hrs || hrs < 1) { if (hrsErr) hrsErr.textContent='Enter valid hours'; ok=false; } else { if (hrsErr) hrsErr.textContent=''; }
        if (!ok) return;
        App.wizardState.name = name;
        App.wizardState.group = document.getElementById('wiz-machine-group')?.value.trim() || '';
        App.wizardState.shiftHours = hrs;
        showCamStep('cam-step-2');
    });

    // Step 2 connect
    document.getElementById('wiz-connect-btn')?.addEventListener('click', async () => {
        const url = document.getElementById('wiz-rtsp-url')?.value.trim();
        const statusEl = document.getElementById('wiz-stream-status');
        const msgEl = document.getElementById('wiz-stream-msg');
        const preview = document.getElementById('wiz-stream-preview');
        const nextBtn = document.getElementById('wiz-next-2');
        if (!url) { toast('Enter an RTSP URL', 'error'); return; }
        App.wizardState.rtspUrl = url;
        if (statusEl) statusEl.hidden = false;
        if (msgEl) msgEl.textContent = 'Connecting to camera…';
        try {
            const img = document.getElementById('wiz-stream-img');
            if (img) { img.src = '/api/video_feed?' + Date.now(); img.onload = () => { if (preview) preview.hidden = false; }; }
            if (msgEl) msgEl.textContent = 'Connected!';
            App.wizardState.streamOk = true;
            if (nextBtn) nextBtn.disabled = false;
        } catch(e) {
            if (msgEl) msgEl.textContent = 'Connection failed: ' + e.message;
            App.wizardState.streamOk = false;
        }
    });

    // Step 2 back/next
    document.getElementById('wiz-back-2')?.addEventListener('click', () => showCamStep('cam-step-1'));
    document.getElementById('wiz-next-2')?.addEventListener('click', () => {
        showCamStep('cam-step-3');
        initDrawCanvas();
    });

    // Step 3 back/redraw/save-zone
    document.getElementById('wiz-back-3')?.addEventListener('click', () => showCamStep('cam-step-2'));
    document.getElementById('wiz-redraw')?.addEventListener('click', () => {
        if (wizCtx && wizCanvas) wizCtx.clearRect(0,0,wizCanvas.width,wizCanvas.height);
        App.wizardState.zone = null;
        const saveBtn = document.getElementById('wiz-save-zone');
        if (saveBtn) saveBtn.disabled = true;
        const hint = document.getElementById('wiz-zone-hint');
        if (hint) hint.hidden = true;
    });
    document.getElementById('wiz-save-zone')?.addEventListener('click', () => {
        showCamStep('cam-step-3b');
        initLightCanvas();
    });

    // Step 3b light zone
    document.getElementById('wiz-light-back')?.addEventListener('click', () => showCamStep('cam-step-3'));
    document.getElementById('wiz-light-skip')?.addEventListener('click', () => {
        App.wizardState.lightZone = null;
        saveMachineConfig();
    });
    document.getElementById('wiz-light-redraw')?.addEventListener('click', () => {
        if (lightCtx && lightCanvas) lightCtx.clearRect(0,0,lightCanvas.width,lightCanvas.height);
        App.wizardState.lightZone = null;
        const saveBtn = document.getElementById('wiz-light-save');
        if (saveBtn) saveBtn.disabled = true;
        const hint = document.getElementById('wiz-light-zone-hint');
        if (hint) hint.hidden = true;
    });
    document.getElementById('wiz-light-save')?.addEventListener('click', saveMachineConfig);

    // Step 4 actions
    document.getElementById('wiz-add-another')?.addEventListener('click', () => {
        App.wizardState = {};
        showCamStep('cam-step-1');
        const nameEl = document.getElementById('wiz-machine-name');
        if (nameEl) nameEl.value = '';
    });
    document.getElementById('wiz-go-machines')?.addEventListener('click', () => navigateTo('machines'));
}

function initDrawCanvas() {
    const img = document.getElementById('wiz-draw-img');
    wizCanvas = document.getElementById('wiz-draw-canvas');
    if (!wizCanvas) return;
    img.src = '/api/video_feed?' + Date.now();
    img.onload = () => {
        wizCanvas.width  = img.clientWidth || 640;
        wizCanvas.height = img.clientHeight || 360;
    };
    wizCtx = wizCanvas.getContext('2d');

    const getPos = (e) => {
        const r = wizCanvas.getBoundingClientRect();
        return { x: (e.touches ? e.touches[0].clientX : e.clientX) - r.left,
                 y: (e.touches ? e.touches[0].clientY : e.clientY) - r.top };
    };

    wizCanvas.onmousedown = (e) => { wizDrawing=true; wizStart=getPos(e); };
    wizCanvas.onmousemove = (e) => {
        if (!wizDrawing) return;
        const p = getPos(e);
        wizCtx.clearRect(0,0,wizCanvas.width,wizCanvas.height);
        wizCtx.fillStyle = 'rgba(99,102,241,0.2)';
        wizCtx.strokeStyle = '#6366F1';
        wizCtx.lineWidth = 2;
        wizCtx.fillRect(wizStart.x, wizStart.y, p.x-wizStart.x, p.y-wizStart.y);
        wizCtx.strokeRect(wizStart.x, wizStart.y, p.x-wizStart.x, p.y-wizStart.y);
    };
    wizCanvas.onmouseup = (e) => {
        wizDrawing = false;
        const p = getPos(e);
        const zone = { x1: wizStart.x/wizCanvas.width, y1: wizStart.y/wizCanvas.height,
                       x2: p.x/wizCanvas.width,       y2: p.y/wizCanvas.height };
        App.wizardState.zone = zone;
        const saveBtn = document.getElementById('wiz-save-zone');
        if (saveBtn) saveBtn.disabled = false;
        const hint = document.getElementById('wiz-zone-hint');
        if (hint) hint.hidden = false;
    };
}

function initLightCanvas() {
    const img = document.getElementById('wiz-light-draw-img');
    lightCanvas = document.getElementById('wiz-light-draw-canvas');
    if (!lightCanvas) return;
    img.src = '/api/video_feed?' + Date.now();
    img.onload = () => {
        lightCanvas.width  = img.clientWidth || 640;
        lightCanvas.height = img.clientHeight || 360;
    };
    lightCtx = lightCanvas.getContext('2d');

    const getPos = (e) => {
        const r = lightCanvas.getBoundingClientRect();
        return { x: (e.touches ? e.touches[0].clientX : e.clientX) - r.left,
                 y: (e.touches ? e.touches[0].clientY : e.clientY) - r.top };
    };

    lightCanvas.onmousedown = (e) => { lightDrawing=true; lightStart=getPos(e); };
    lightCanvas.onmousemove = (e) => {
        if (!lightDrawing) return;
        const p = getPos(e);
        lightCtx.clearRect(0,0,lightCanvas.width,lightCanvas.height);
        lightCtx.fillStyle = 'rgba(245,158,11,0.2)';
        lightCtx.strokeStyle = '#F59E0B';
        lightCtx.lineWidth = 2;
        lightCtx.fillRect(lightStart.x, lightStart.y, p.x-lightStart.x, p.y-lightStart.y);
        lightCtx.strokeRect(lightStart.x, lightStart.y, p.x-lightStart.x, p.y-lightStart.y);
    };
    lightCanvas.onmouseup = (e) => {
        lightDrawing = false;
        const p = getPos(e);
        const zone = { x1: lightStart.x/lightCanvas.width, y1: lightStart.y/lightCanvas.height,
                       x2: p.x/lightCanvas.width,           y2: p.y/lightCanvas.height };
        App.wizardState.lightZone = zone;
        const saveBtn = document.getElementById('wiz-light-save');
        if (saveBtn) saveBtn.disabled = false;
        const hint = document.getElementById('wiz-light-zone-hint');
        if (hint) hint.hidden = false;
    };
}

async function saveMachineConfig() {
    showCamStep('cam-step-4');
    const savingEl = document.getElementById('wiz-saving');
    const savedEl  = document.getElementById('wiz-saved');
    if (savingEl) savingEl.style.display = 'flex';
    if (savedEl)  savedEl.hidden = true;

    try {
        await api('/api/camera/zones', {
            json: {
                machineName: App.wizardState.name,
                machineGroup: App.wizardState.group,
                shiftHours: App.wizardState.shiftHours,
                rtspUrl: App.wizardState.rtspUrl,
                zone: App.wizardState.zone,
                lightZone: App.wizardState.lightZone,
            }
        });
        if (savingEl) savingEl.style.display = 'none';
        if (savedEl)  savedEl.hidden = false;

        const summary = document.getElementById('wiz-summary');
        if (summary) summary.innerHTML = `
            <div class="wiz-sum-row"><strong>Machine:</strong> ${escHtml(App.wizardState.name)}</div>
            ${App.wizardState.group ? '<div class="wiz-sum-row"><strong>Group:</strong> ' + escHtml(App.wizardState.group) + '</div>' : ''}
            <div class="wiz-sum-row"><strong>Shift Hours:</strong> ${App.wizardState.shiftHours}h</div>
            <div class="wiz-sum-row"><strong>Zone:</strong> ${App.wizardState.zone ? 'Configured' : 'None'}</div>
            <div class="wiz-sum-row"><strong>Light Zone:</strong> ${App.wizardState.lightZone ? 'Configured' : 'Skipped'}</div>
        `;
        loadCamMachineList();
    } catch(e) {
        if (savingEl) savingEl.style.display = 'none';
        toast('Save failed: ' + e.message, 'error');
        showCamStep('cam-step-3b');
    }
}



/* ── 21. AI CHAT WIDGET ───────────────────────────────────── */
const chatMessages = [];  // {role:'user'|'assistant', content:string}

window.toggleChat = function() {
    const panel = document.getElementById('ai-chat-panel');
    const fab   = document.getElementById('ai-chat-fab');
    if (!panel) return;
    const isOpen = panel.classList.toggle('active');
    if (isOpen) {
        setTimeout(() => document.getElementById('chat-input')?.focus(), 100);
        if (fab) fab.style.transform = 'scale(0.9) rotate(45deg)';
    } else {
        if (fab) fab.style.transform = '';
    }
};

window.handleChatEnter = function(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
};

window.sendChatMessage = async function() {
    const input   = document.getElementById('chat-input');
    const history = document.getElementById('chat-history');
    if (!input || !history) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.disabled = true;

    // Add user bubble
    chatMessages.push({ role: 'user', content: text });
    appendChatBubble(history, 'user', text);

    // Typing indicator
    const typing = appendChatBubble(history, 'ai', '…', true);
    history.scrollTop = history.scrollHeight;

    try {
        const res = await fetch('/api/ai/chat', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: chatMessages }),
        });
        if (!res.ok) throw new Error('Server error ' + res.status);
        const data = await res.json();
        const reply = data.reply || 'No response.';

        chatMessages.push({ role: 'assistant', content: reply });
        typing.textContent = reply;
        typing.classList.remove('chat-typing');
    } catch(e) {
        typing.textContent = 'Error: ' + e.message;
        typing.style.color = 'var(--danger)';
        typing.classList.remove('chat-typing');
    } finally {
        input.disabled = false;
        input.focus();
        history.scrollTop = history.scrollHeight;
    }
};

function appendChatBubble(history, role, text, isTyping = false) {
    const div = document.createElement('div');
    div.className = 'chat-msg ' + role + (isTyping ? ' chat-typing' : '');
    div.textContent = text;
    history.appendChild(div);
    return div;
}

/* ── 22. UTILITY FUNCTIONS ────────────────────────────────── */
function escHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function stateToClass(state) {
    const s = (state || '').toUpperCase();
    if (s === 'ACTIVE')     return 'state-active';
    if (s === 'IDLE')       return 'state-idle';
    if (s === 'GRACE')      return 'state-grace';
    if (s === 'ABANDONED')  return 'state-abandoned';
    if (s === 'OFFLINE')    return 'state-offline';
    return 'state-idle';
}

function formatDuration(seconds) {
    if (!seconds || seconds < 0) return '0m';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + s + 's';
    return s + 's';
}

function fmtTime(isoStr) {
    if (!isoStr) return '—';
    try { return new Date(isoStr).toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit', second:'2-digit'}); }
    catch(_) { return isoStr; }
}

function fmtDate(isoStr) {
    if (!isoStr) return '—';
    try { return new Date(isoStr).toLocaleDateString('en-GB'); }
    catch(_) { return isoStr; }
}

function relTime(isoStr) {
    if (!isoStr) return '—';
    try {
        const diff = Date.now() - new Date(isoStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'Just now';
        if (mins < 60) return mins + 'm ago';
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return hrs + 'h ago';
        return Math.floor(hrs / 24) + 'd ago';
    } catch(_) { return isoStr; }
}

function todayStr() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth()+1).padStart(2,'0');
    const day = String(d.getDate()).padStart(2,'0');
    return y + '-' + m + '-' + day;
}

/* ── 23. EXTRA CSS (machine cards, badges, toasts) ─────────── */
(function injectStyles() {
    const css = `
        .toast { position:fixed; bottom:80px; right:24px; padding:12px 20px; border-radius:10px;
            font-size:13px; font-weight:500; opacity:0; transition:opacity 0.3s; z-index:9999;
            max-width:340px; box-shadow:0 4px 16px rgba(0,0,0,0.4); }
        .toast-visible { opacity:1; }
        .toast-success { background:#10B981; color:#fff; }
        .toast-error   { background:#EF4444; color:#fff; }
        .toast-info    { background:#3B82F6; color:#fff; }

        .state-active   { background:rgba(16,185,129,0.15); color:#10B981; }
        .state-idle     { background:rgba(107,114,128,0.15); color:#9CA3AF; }
        .state-grace    { background:rgba(245,158,11,0.15); color:#F59E0B; }
        .state-abandoned{ background:rgba(239,68,68,0.15); color:#EF4444; }
        .state-offline  { background:rgba(239,68,68,0.15); color:#EF4444; }

        .machine-card { background:var(--bg-card); border:1px solid var(--border-subtle);
            border-radius:var(--radius-lg); overflow:hidden; display:flex; flex-direction:column; }
        .mc-header { padding:16px 20px; }
        .mc-title-row { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
        .mc-name { font-size:16px; font-weight:700; color:var(--text-primary); }
        .mc-state-badge { padding:3px 10px; border-radius:20px; font-size:11px; font-weight:600; }
        .mc-meta { display:flex; gap:16px; font-size:12px; color:var(--text-secondary); }
        .mc-feed-wrap { position:relative; background:#000; flex:1; }
        .mc-feed { width:100%; height:240px; object-fit:cover; display:block; }
        .mc-feed-overlay { position:absolute; top:8px; right:8px; }
        .mc-status-dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
        .mc-status-dot.state-active { background:#10B981; box-shadow:0 0 6px #10B981; }
        .mc-status-dot.state-idle { background:#6B7280; }
        .mc-footer { padding:12px 16px; display:flex; gap:8px; border-top:1px solid var(--border-subtle); }
        .mc-light { padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600; }
        .light-green  { background:rgba(16,185,129,0.15); color:#10B981; }
        .light-red    { background:rgba(239,68,68,0.15);  color:#EF4444; }
        .light-amber  { background:rgba(245,158,11,0.15); color:#F59E0B; }
        .light-off    { background:rgba(107,114,128,0.15);color:#9CA3AF; }

        .machines-container { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr));
            gap:20px; padding:24px; overflow-y:auto; }

        .alert-pill { display:inline-block; padding:2px 8px; border-radius:12px; font-size:11px;
            font-weight:600; background:rgba(239,68,68,0.15); color:#EF4444; }
        .badge-resolved { color:var(--success); font-size:12px; }
        .badge-active { color:var(--success); font-style:normal; }
        .sess-status { font-size:11px; font-weight:600; }
        .sess-status.active { color:var(--success); }
        .sess-status.completed { color:var(--text-muted); }
        .role-badge { padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600; }
        .role-admin { background:rgba(99,102,241,0.15); color:#6366F1; }
        .role-supervisor { background:rgba(245,158,11,0.15); color:#F59E0B; }
        .role-viewer { background:rgba(107,114,128,0.15); color:#9CA3AF; }
        .mono { font-family:'JetBrains Mono',monospace; font-size:12px; }
        .ms-item { padding:12px 0; border-bottom:1px solid var(--border-subtle); }
        .ms-row { display:flex; align-items:center; justify-content:space-between; margin-bottom:4px; }
        .ms-id { font-weight:600; }
        .ms-badge { padding:2px 10px; border-radius:20px; font-size:11px; font-weight:600; }
        .ms-sub { font-size:12px; color:var(--text-secondary); }
        .ms-light { font-size:12px; margin-top:4px; }
        .activity-item { display:flex; align-items:center; gap:10px; padding:8px 0;
            border-bottom:1px solid var(--border-subtle); }
        .activity-dot { width:6px; height:6px; border-radius:50%; background:var(--accent); flex-shrink:0; }
        .activity-time { font-size:11px; color:var(--text-muted); width:48px; flex-shrink:0; }
        .activity-text { font-size:12px; color:var(--text-secondary); }
        .shift-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
        .shift-row input { flex:1; }
        .detail-row { padding:6px 0; font-size:13px; border-bottom:1px solid var(--border-subtle); }
        .wiz-sum-row { padding:6px 0; font-size:13px; color:var(--text-secondary);
            border-bottom:1px solid var(--border-subtle); }
        .alert-dropdown-item { padding:10px 14px; border-bottom:1px solid var(--border-subtle); cursor:default; }
        .alert-dropdown-item.open .adi-type { color:var(--danger); }
        .alert-dropdown-item.resolved .adi-type { color:var(--text-muted); }
        .adi-type { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; }
        .adi-msg { font-size:12px; color:var(--text-secondary); margin:2px 0; }
        .adi-time { font-size:11px; color:var(--text-muted); }
        .alert-dropdown-empty { padding:16px; text-align:center; color:var(--text-muted); font-size:13px; }
        .text-muted { color:var(--text-muted); font-size:12px; }
        .form-message { margin-top:8px; font-size:12px; font-weight:500; }
        .form-message.success { color:var(--success); }
        .form-message.error   { color:var(--danger); }
        .cam-machine-item { display:flex; align-items:center; justify-content:space-between;
            padding:10px 14px; cursor:pointer; border-radius:var(--radius-sm); transition:background 0.15s; }
        .cam-machine-item:hover, .cam-machine-item.selected { background:var(--accent-bg); }
        .cm-name { font-weight:600; font-size:13px; }
        .cm-state { font-size:11px; padding:2px 8px; border-radius:12px; font-weight:600; }

        #ai-chat-panel { display:none; flex-direction:column; position:fixed; bottom:90px; right:24px;
            width:360px; height:500px; background:var(--bg-card); border:1px solid var(--border-default);
            border-radius:var(--radius-xl); box-shadow:var(--shadow-lg); z-index:1000; overflow:hidden; }
        #ai-chat-panel.chat-open { display:flex; }
        .chat-header { display:flex; align-items:center; justify-content:space-between;
            padding:14px 18px; border-bottom:1px solid var(--border-subtle);
            background:linear-gradient(135deg, #4338CA, #6366F1); color:#fff; font-weight:600; }
        .chat-close { background:none; border:none; color:#fff; cursor:pointer; font-size:18px; padding:0; line-height:1; }
        .chat-history { flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px; }
        .chat-msg { max-width:85%; padding:10px 14px; border-radius:14px; font-size:13px; line-height:1.5; }
        .chat-msg.user { background:var(--accent); color:#fff; align-self:flex-end; border-bottom-right-radius:4px; }
        .chat-msg.ai   { background:var(--bg-elevated); color:var(--text-primary); align-self:flex-start; border-bottom-left-radius:4px; }
        .chat-msg.chat-typing { animation:pulse 1.2s ease-in-out infinite; }
        @keyframes pulse { 0%,100%{opacity:0.4} 50%{opacity:1} }
        .chat-input-area { display:flex; gap:8px; padding:12px; border-top:1px solid var(--border-subtle); }
        .chat-input-area input { flex:1; background:var(--bg-primary); border:1px solid var(--border-default);
            border-radius:var(--radius-full); padding:8px 14px; color:var(--text-primary); font-size:13px; outline:none; }
        .chat-input-area button { background:var(--accent); border:none; border-radius:50%;
            width:36px; height:36px; cursor:pointer; display:flex; align-items:center; justify-content:center; flex-shrink:0; }
        .chat-input-area button svg { width:16px; height:16px; color:#fff; }
        #ai-chat-fab { position:fixed; bottom:24px; right:24px; width:56px; height:56px;
            background:linear-gradient(135deg,#4338CA,#6366F1); border-radius:50%;
            display:flex; align-items:center; justify-content:center; cursor:pointer;
            box-shadow:0 4px 20px rgba(99,102,241,0.5); z-index:999; transition:transform 0.2s; }
        #ai-chat-fab:hover { transform:scale(1.08); }
        #ai-chat-fab svg { width:26px; height:26px; color:#fff; }
        .machines-empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center;
            padding:60px; color:var(--text-muted); text-align:center; gap:16px; }
        .settings-save-banner { background:var(--success-bg); color:var(--success);
            border:1px solid rgba(16,185,129,0.3); border-radius:var(--radius-md);
            padding:10px 16px; font-size:13px; font-weight:500; margin-bottom:16px; }
    `;
    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
})();

