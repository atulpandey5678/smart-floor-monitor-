/* ── Machine Detail View Rendering ────────────────────────── */

import { escHtml, stateToClass, formatDuration, relTime, fmtTime, fmtDate, todayStr, toast } from './utils.js';
import * as apiClient from './api-client.js';
import * as store from './state-store.js';

/* ── Home Page ────────────────────────────────────────────── */

export async function loadHomeData() {
    await Promise.allSettled([loadHomeAlerts(), loadHomeMachineStatus()]);
}

async function loadHomeAlerts() {
    const tbody = document.getElementById('home-alerts-body');
    if (tbody) tbody.innerHTML = Array(3).fill('<tr><td colspan="5"><div class="skeleton" style="height:28px"></div></td></tr>').join('');
    try {
        const alerts = await apiClient.getAlerts();
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
                `<button class="btn btn-ghost btn-sm" data-action="resolve-alert" data-id="${a.id}">Resolve</button>`}
            </td>
        </tr>
    `).join('');

    // Bind resolve buttons
    tbody.querySelectorAll('[data-action="resolve-alert"]').forEach(btn => {
        btn.addEventListener('click', () => resolveAlertPrompt(parseInt(btn.dataset.id)));
    });
}

async function loadHomeMachineStatus() {
    try {
        const state = await apiClient.getStatus();
        renderHomeMachineList([state]);
        updateMetricCards(state);
    } catch (e) { console.error('loadHomeMachineStatus', e); }
}

export function updateMetricCards(state) {
    const workers = document.getElementById('metric-workers');
    const hrs = document.getElementById('metric-machine-hrs');
    const eff = document.getElementById('metric-efficiency');
    if (workers) workers.textContent = state.state === 'ACTIVE' ? 1 : 0;
    if (hrs && state.active_duration_seconds !== undefined)
        hrs.textContent = (state.active_duration_seconds / 3600).toFixed(1);
    if (eff && state.efficiency_percent !== undefined)
        eff.textContent = state.efficiency_percent > 0 ? state.efficiency_percent.toFixed(0) + '%' : '—';
}

export function updateHomeMetricsFromWs(data) {
    if (store.getActivePage() !== 'home') return;
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
        const sc = stateToClass(m.state);
        const dur = m.active_duration_seconds ? formatDuration(m.active_duration_seconds) : '—';
        return `
        <li class="ms-item">
            <div class="ms-row">
                <span class="ms-id">${escHtml(m.machine_id || 'M-01')}</span>
                <span class="ms-badge ${sc}">${escHtml(m.state || 'IDLE')}</span>
            </div>
            <div class="ms-sub">
                ${m.employee_name ? escHtml(m.employee_name) : (m.badge_id ? 'Badge: ' + escHtml(m.badge_id) : 'No worker')}
                ${dur !== '—' ? ' · ' + dur : ''}
            </div>
            ${m.light_color ? `<div class="ms-light light-${(m.light_color || '').toLowerCase()}">⬤ ${escHtml(m.light_color)}</div>` : ''}
        </li>`;
    }).join('');
}

/* ── Resolve Alert ────────────────────────────────────────── */

async function resolveAlertPrompt(id) {
    const note = prompt('Enter root cause / note (optional):');
    if (note === null) return;
    try {
        await apiClient.resolveAlert(id, note);
        toast('Alert resolved', 'success');
        loadHomeAlerts();
        refreshAlertBadge();
    } catch (e) { toast(e.message, 'error'); }
}

// Expose globally for onclick handlers in legacy HTML
window.resolveAlertPrompt = resolveAlertPrompt;

/* ── Alert Bell ───────────────────────────────────────────── */

export function initAlertBell() {
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

    refreshAlertBadge();
    setInterval(refreshAlertBadge, 30000);
}

export async function refreshAlertBadge() {
    try {
        const data = await apiClient.getUnreadAlertCount();
        setBadge(data.count || 0);
    } catch (_) {}
}

export function setBadge(count) {
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
        const alerts = await apiClient.getAlertHistory(10);
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

/* ── Sessions Page ────────────────────────────────────────── */

export async function loadSessionsData() {
    const tbody = document.getElementById('sessions-body');
    if (tbody) tbody.innerHTML = Array(5).fill('<tr><td colspan="6"><div class="skeleton" style="height:28px"></div></td></tr>').join('');
    try {
        const dateEl = document.getElementById('sessions-date');
        const d = dateEl ? dateEl.value : todayStr();
        const sessions = await apiClient.getSessions(d);
        renderSessions(sessions);
    } catch (e) { toast('Failed to load sessions', 'error'); }
}

function renderSessions(sessions) {
    const tbody = document.getElementById('sessions-body');
    const empty = document.getElementById('sessions-empty');
    if (!tbody) return;
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
}

/* ── Employees Page ───────────────────────────────────────── */

export async function loadEmployeesData() {
    const tbody = document.getElementById('employees-body');
    const empty = document.getElementById('employees-empty');
    if (!tbody) return;

    tbody.innerHTML = Array(4).fill('<tr><td colspan="3"><div class="skeleton" style="height:28px"></div></td></tr>').join('');

    try {
        const emps = await apiClient.getEmployees();
        tbody.innerHTML = '';
        if (!emps || !emps.length) {
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';
        const user = store.getUser();
        tbody.innerHTML = emps.map(e => {
            const canDel = user && user.role === 'admin';
            const delBtn = canDel
                ? `<button class="btn btn-danger btn-sm" data-action="delete-emp" data-badge="${escHtml(e.badge_id)}" data-name="${escHtml(e.name)}">Delete</button>`
                : '';
            return `<tr>
                <td><span class="mono">${escHtml(e.badge_id)}</span></td>
                <td>${escHtml(e.name)}</td>
                <td>${delBtn}</td>
            </tr>`;
        }).join('');

        // Bind delete buttons
        tbody.querySelectorAll('[data-action="delete-emp"]').forEach(btn => {
            btn.addEventListener('click', () => deleteEmployeePrompt(btn.dataset.badge, btn.dataset.name));
        });
    } catch (e) { console.error('loadEmployeesData', e); }
}

async function deleteEmployeePrompt(badgeId, name) {
    if (!confirm('Delete employee ' + name + ' (' + badgeId + ')?')) return;
    try {
        await apiClient.deleteEmployee(badgeId);
        toast('Employee deleted.', 'success');
        loadEmployeesData();
    } catch (e) { toast(e.message, 'error'); }
}

// Expose globally for settings tab
window.deleteEmployee = deleteEmployeePrompt;

export async function registerEmployee() {
    const badge = document.getElementById('emp-badge-id') && document.getElementById('emp-badge-id').value.trim();
    const name = document.getElementById('emp-name') && document.getElementById('emp-name').value.trim();
    const msg = document.getElementById('emp-message');
    if (!badge || !name) {
        if (msg) { msg.textContent = 'Please fill in all fields.'; msg.className = 'form-message error'; }
        return;
    }
    try {
        await apiClient.createEmployee({ badge_id: badge, name: name });
        if (msg) { msg.textContent = 'Employee registered successfully.'; msg.className = 'form-message success'; }
        document.getElementById('emp-badge-id').value = '';
        document.getElementById('emp-name').value = '';
        await loadEmployeesData();
    } catch (e) {
        if (msg) { msg.textContent = e.message; msg.className = 'form-message error'; }
    }
}

/* ── Reports Page ─────────────────────────────────────────── */

let currentReportType = 'daily';

export function initReportTabs() {
    document.querySelectorAll('.report-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.report-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentReportType = btn.dataset.reportType;
            loadReportsData();
        });
    });
    const dateEl = document.getElementById('reports-date');
    if (dateEl && !dateEl.value) dateEl.value = todayStr();
}

export async function loadReportsData() {
    const dateEl = document.getElementById('reports-date');
    const dateVal = dateEl ? dateEl.value : todayStr();

    try {
        let data;
        const trend = document.getElementById('report-trend-banner');

        if (currentReportType === 'weekly') {
            data = await apiClient.getWeeklyReport(dateVal);
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
            data = await apiClient.getDailyReport(dateVal);
            if (trend) trend.hidden = true;
        }

        const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        setVal('report-total-sessions', data.total_sessions || 0);
        setVal('report-total-hours', (data.total_machine_hours || 0).toFixed(1));
        setVal('report-avg-efficiency', data.avg_efficiency_percent > 0 ? data.avg_efficiency_percent.toFixed(0) + '%' : '—');
        setVal('report-avg-utilization', data.avg_utilization_percent > 0 ? data.avg_utilization_percent.toFixed(0) + '%' : '—');

        renderWorkerTable(data.worker_stats || {});
        renderAlertSummaryTable(data.alert_counts || {});
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

    const existing = store.getTrendChart();
    if (existing) existing.destroy();

    const chart = new Chart(canvas, {
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
    store.setTrendChart(chart);
}

export async function downloadCSV() {
    const dateEl = document.getElementById('reports-date');
    const dateVal = dateEl ? dateEl.value : todayStr();
    const url = currentReportType === 'weekly'
        ? '/api/reports/weekly?week_start=' + dateVal + '&format=csv'
        : '/api/reports/daily?date=' + dateVal + '&format=csv';
    try {
        await apiClient.downloadReportCSV(url);
    } catch (e) { toast('CSV download failed: ' + e.message, 'error'); }
}

export async function exportReportToPDF() {
    if (typeof html2canvas === 'undefined' || typeof window.jspdf === 'undefined') {
        toast('PDF libraries not loaded yet.', 'warning');
        return;
    }
    const reportContent = document.querySelector('.reports-content') || document.getElementById('page-reports');
    if (!reportContent) return;

    const btn = document.getElementById('btn-export-pdf');
    const oldHtml = btn.innerHTML;
    btn.innerHTML = 'Generating...';
    btn.disabled = true;

    try {
        const canvas = await html2canvas(reportContent, {
            scale: 2,
            backgroundColor: getComputedStyle(document.body).getPropertyValue('--bg-primary').trim() || '#121212'
        });

        const imgData = canvas.toDataURL('image/png');
        const pdf = new window.jspdf.jsPDF('p', 'mm', 'a4');
        const pdfWidth = pdf.internal.pageSize.getWidth();
        const pdfHeight = (canvas.height * pdfWidth) / canvas.width;

        pdf.addImage(imgData, 'PNG', 0, 10, pdfWidth, pdfHeight);

        const dateEl = document.getElementById('reports-date');
        const dateVal = dateEl ? dateEl.value : todayStr();
        pdf.save(`Cologic_Report_${currentReportType}_${dateVal}.pdf`);

        toast('PDF exported successfully!');
    } catch (e) {
        console.error('PDF Generation failed', e);
        toast('PDF Generation failed.', 'error');
    } finally {
        btn.innerHTML = oldHtml;
        btn.disabled = false;
    }
}

/* ── Activity Feed ────────────────────────────────────────── */

export function pushActivityToFeed(data) {
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

    const children = feed.querySelectorAll('li');
    if (children.length > 50) children[children.length - 1].remove();

    store.pushActivity({ time: now, label, type: data.event_type });
}

function activityLabel(data) {
    const e = data.event_type || '';
    if (e === 'SESSION_START') return `Session started — ${data.employee_name || data.badge_id || 'Unknown'} on ${data.machine_id || 'M-01'}`;
    if (e === 'SESSION_END') return `Session ended — ${data.employee_name || data.badge_id || 'Unknown'} on ${data.machine_id || 'M-01'}`;
    if (e === 'ALERT') return `Alert: ${data.alert_type || ''} — ${data.message || ''}`;
    if (e === 'LIGHT') return `Light changed to ${data.light_color || '?'} on ${data.machine_id || 'M-01'}`;
    return data.message || e;
}

/* ── Sound Notification ───────────────────────────────────── */

let audioCtx = null;

export function playAlertSound() {
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }

        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();

        oscillator.type = 'sine';
        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);

        oscillator.frequency.setValueAtTime(880, audioCtx.currentTime);
        oscillator.frequency.setValueAtTime(659.25, audioCtx.currentTime + 0.15);

        gainNode.gain.setValueAtTime(0, audioCtx.currentTime);
        gainNode.gain.linearRampToValueAtTime(0.3, audioCtx.currentTime + 0.05);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);

        oscillator.start(audioCtx.currentTime);
        oscillator.stop(audioCtx.currentTime + 0.5);
    } catch (e) {
        console.warn('Audio play failed (interaction required or not supported):', e);
    }
}


/* ── Machine Detail View ──────────────────────────────────── */

let currentDetailMachineId = null;
let detailSessionPage = 1;
let detailSessionTotal = 0;
const DETAIL_PAGE_SIZE = 20;
let detailUnsubscribe = null;

/**
 * Open the machine detail view for a specific machine.
 */
export async function openMachineDetailView(machineId) {
    currentDetailMachineId = machineId;
    detailSessionPage = 1;

    // Show the machine detail page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const detailPage = document.getElementById('page-machine-detail');
    if (detailPage) detailPage.classList.add('active');

    // Update topbar
    const titleEl = document.querySelector('.topbar-title');
    const crumbEl = document.querySelector('.topbar-breadcrumb');
    if (titleEl) titleEl.textContent = 'Machine Detail';
    if (crumbEl) crumbEl.textContent = machineId;

    // Update detail title
    const detailTitle = document.getElementById('machine-detail-title');
    if (detailTitle) detailTitle.textContent = machineId;

    store.setActivePage('machine-detail');

    // Subscribe only to this machine's WebSocket updates
    const { subscribeMachine } = await import('./ws-client.js');
    subscribeMachine(machineId);

    // Listen for state store updates for this machine
    if (detailUnsubscribe) detailUnsubscribe();
    detailUnsubscribe = store.subscribe((key, value) => {
        if (key === 'machine' && value && value.machine_id === currentDetailMachineId) {
            renderDetailStatusCard(value);
        }
    });

    // Render current state from store
    const machineState = store.getMachineState(machineId);
    if (machineState) {
        renderDetailStatusCard(machineState);
    } else {
        renderDetailStatusCard({ machine_id: machineId, state: 'IDLE' });
    }

    // Set default date filter values
    const startInput = document.getElementById('md-history-start');
    const endInput = document.getElementById('md-history-end');
    if (startInput && !startInput.value) {
        const weekAgo = new Date();
        weekAgo.setDate(weekAgo.getDate() - 7);
        startInput.value = weekAgo.toISOString().slice(0, 10);
    }
    if (endInput && !endInput.value) {
        endInput.value = todayStr();
    }

    // Load data
    await Promise.allSettled([
        loadDetailTimeline(machineId),
        loadDetailAlerts(machineId),
        loadDetailConfig(machineId),
        loadDetailSessionHistory(machineId),
    ]);
}

/**
 * Close detail view and return to machines overview.
 */
export function closeMachineDetailView() {
    const machineId = currentDetailMachineId;
    if (machineId) {
        // Unsubscribe from targeted machine updates, re-subscribe to all
        import('./ws-client.js').then(wsClient => {
            wsClient.unsubscribeMachine(machineId);
            wsClient.subscribeAll();
        });
    }
    if (detailUnsubscribe) {
        detailUnsubscribe();
        detailUnsubscribe = null;
    }
    currentDetailMachineId = null;

    // Navigate back to machines page
    if (window.__navigateTo) window.__navigateTo('machines');
}

/**
 * Get the currently viewed machine ID (used by ws handler for targeted updates).
 */
export function getDetailMachineId() {
    return currentDetailMachineId;
}

/**
 * Render the live status card.
 */
function renderDetailStatusCard(data) {
    const machineIdEl = document.getElementById('md-machine-id');
    const badgeEl = document.getElementById('md-state-badge');
    const workerEl = document.getElementById('md-worker');
    const durEl = document.getElementById('md-session-dur');
    const effEl = document.getElementById('md-efficiency');
    const pipelineEl = document.getElementById('md-pipeline-status');
    const lightEl = document.getElementById('md-light-status');

    if (machineIdEl) machineIdEl.textContent = data.machine_id || '—';
    if (badgeEl) {
        const sc = stateToClass(data.state);
        badgeEl.className = 'md-state-badge ' + sc;
        badgeEl.textContent = data.state || 'IDLE';
    }
    if (workerEl) workerEl.textContent = data.employee_name || (data.badge_id ? 'Badge: ' + data.badge_id : '—');
    if (durEl) durEl.textContent = data.active_duration_seconds ? formatDuration(data.active_duration_seconds) : '—';
    if (effEl) effEl.textContent = data.efficiency_percent !== undefined ? data.efficiency_percent.toFixed(0) + '%' : '—';
    if (pipelineEl) pipelineEl.textContent = data.pipeline_status || data.pipeline_state || 'running';
    if (lightEl) {
        const light = data.machine_light_status || data.light_color || '—';
        lightEl.textContent = light;
        lightEl.className = 'md-stat-value' + (light !== '—' ? ' light-' + light.toLowerCase() : '');
    }
}

/**
 * Update the detail view from a WebSocket message (called from main message handler).
 */
export function updateDetailFromWs(data) {
    if (store.getActivePage() !== 'machine-detail') return;
    if (!currentDetailMachineId || data.machine_id !== currentDetailMachineId) return;
    renderDetailStatusCard(data);
}

/* ── Detail: Session Timeline (Today) ─────────────────────── */

async function loadDetailTimeline(machineId) {
    const container = document.getElementById('md-timeline');
    const empty = document.getElementById('md-timeline-empty');
    const dateLabel = document.getElementById('md-timeline-date');
    if (!container) return;

    if (dateLabel) dateLabel.textContent = 'Today — ' + todayStr();
    container.innerHTML = '<div class="skeleton" style="height:60px"></div>';

    try {
        const sessions = await apiClient.getMachineSessionsToday(machineId);
        const list = Array.isArray(sessions) ? sessions : (sessions.items || sessions.data || []);

        if (!list.length) {
            container.innerHTML = '';
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';

        container.innerHTML = list.map(s => {
            const start = fmtTime(s.start_time);
            const end = s.end_time ? fmtTime(s.end_time) : 'Active';
            const dur = s.duration_seconds ? formatDuration(s.duration_seconds) : '—';
            const statusClass = s.end_time ? 'completed' : 'active';
            return `
            <div class="md-timeline-item ${statusClass}">
                <div class="md-tl-dot"></div>
                <div class="md-tl-content">
                    <div class="md-tl-header">
                        <span class="md-tl-worker">${escHtml(s.employee_name || s.badge_id || 'Unknown')}</span>
                        <span class="md-tl-duration">${dur}</span>
                    </div>
                    <div class="md-tl-time">${start} — ${end}</div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        container.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        console.error('loadDetailTimeline', e);
    }
}

/* ── Detail: Alert Panel ──────────────────────────────────── */

async function loadDetailAlerts(machineId) {
    const container = document.getElementById('md-alerts-list');
    const empty = document.getElementById('md-alerts-empty');
    const countEl = document.getElementById('md-alerts-count');
    if (!container) return;

    container.innerHTML = '<div class="skeleton" style="height:40px"></div>';

    try {
        const result = await apiClient.getMachineAlerts(machineId);
        const alerts = Array.isArray(result) ? result : (result.items || result.data || []);

        if (countEl) countEl.textContent = alerts.length;

        if (!alerts.length) {
            container.innerHTML = '';
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';

        container.innerHTML = alerts.slice(0, 10).map(a => `
            <div class="md-alert-item ${a.resolved ? 'resolved' : 'open'}">
                <div class="md-alert-type">
                    <span class="alert-pill alert-${(a.alert_type || '').toLowerCase().replace(/[^a-z]/g, '-')}">${escHtml(a.alert_type)}</span>
                </div>
                <div class="md-alert-msg">${escHtml(a.message || '')}</div>
                <div class="md-alert-time">${relTime(a.created_at)}</div>
                ${a.resolved ? '<span class="badge-resolved">Resolved</span>' : ''}
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        console.error('loadDetailAlerts', e);
    }
}

/* ── Detail: Machine Config Summary ───────────────────────── */

async function loadDetailConfig(machineId) {
    const nameEl = document.getElementById('md-cfg-name');
    const rtspEl = document.getElementById('md-cfg-rtsp');
    const confEl = document.getElementById('md-cfg-confidence');
    const statusEl = document.getElementById('md-cfg-status');

    try {
        const config = await apiClient.getMachineConfig(machineId);
        if (nameEl) nameEl.textContent = config.display_name || config.name || machineId;
        if (rtspEl) rtspEl.textContent = config.rtsp_url ? '••••••••' : '—';
        if (confEl) confEl.textContent = config.confidence_threshold !== undefined
            ? config.confidence_threshold.toFixed(2)
            : '—';
        if (statusEl) statusEl.textContent = config.status || 'active';
    } catch (e) {
        // Machine config endpoint may not exist for legacy single-machine setups
        if (nameEl) nameEl.textContent = machineId;
        if (rtspEl) rtspEl.textContent = '—';
        if (confEl) confEl.textContent = '—';
        if (statusEl) statusEl.textContent = '—';
    }
}

/* ── Detail: Session History with Pagination ──────────────── */

async function loadDetailSessionHistory(machineId) {
    const tbody = document.getElementById('md-sessions-body');
    const empty = document.getElementById('md-sessions-empty');
    if (!tbody) return;

    tbody.innerHTML = Array(3).fill('<tr><td colspan="5"><div class="skeleton" style="height:28px"></div></td></tr>').join('');

    const startInput = document.getElementById('md-history-start');
    const endInput = document.getElementById('md-history-end');
    const startDate = startInput ? startInput.value : undefined;
    const endDate = endInput ? endInput.value : undefined;

    try {
        const result = await apiClient.getMachineSessions(machineId, {
            page: detailSessionPage,
            pageSize: DETAIL_PAGE_SIZE,
            startDate,
            endDate
        });

        const sessions = Array.isArray(result) ? result : (result.items || result.data || []);
        detailSessionTotal = result.total_count || result.total || sessions.length;

        if (!sessions.length) {
            tbody.innerHTML = '';
            if (empty) empty.style.display = 'flex';
            updatePaginationControls(0);
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
                <td>${start}</td>
                <td>${end}</td>
                <td>${dur}</td>
                <td><span class="sess-status ${s.end_time ? 'completed' : 'active'}">${status}</span></td>
            </tr>`;
        }).join('');

        updatePaginationControls(detailSessionTotal);
    } catch (e) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        updatePaginationControls(0);
        console.error('loadDetailSessionHistory', e);
    }
}

function updatePaginationControls(total) {
    const prevBtn = document.getElementById('md-page-prev');
    const nextBtn = document.getElementById('md-page-next');
    const infoEl = document.getElementById('md-page-info');

    const totalPages = Math.max(1, Math.ceil(total / DETAIL_PAGE_SIZE));

    if (infoEl) infoEl.textContent = `Page ${detailSessionPage} of ${totalPages}`;
    if (prevBtn) prevBtn.disabled = detailSessionPage <= 1;
    if (nextBtn) nextBtn.disabled = detailSessionPage >= totalPages;
}

/**
 * Initialize the machine detail page event listeners.
 * Called once at startup from main.js.
 */
export function initMachineDetailPage() {
    // Back button
    document.getElementById('btn-back-to-machines')?.addEventListener('click', () => {
        closeMachineDetailView();
    });

    // Pagination
    document.getElementById('md-page-prev')?.addEventListener('click', () => {
        if (detailSessionPage > 1) {
            detailSessionPage--;
            if (currentDetailMachineId) loadDetailSessionHistory(currentDetailMachineId);
        }
    });

    document.getElementById('md-page-next')?.addEventListener('click', () => {
        const totalPages = Math.ceil(detailSessionTotal / DETAIL_PAGE_SIZE);
        if (detailSessionPage < totalPages) {
            detailSessionPage++;
            if (currentDetailMachineId) loadDetailSessionHistory(currentDetailMachineId);
        }
    });

    // Date range filter
    document.getElementById('btn-md-filter-sessions')?.addEventListener('click', () => {
        detailSessionPage = 1;
        if (currentDetailMachineId) loadDetailSessionHistory(currentDetailMachineId);
    });
}
