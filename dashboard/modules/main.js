/* ============================================================
   Cologic Shop Floor Tracker — ES Module Entry Point
   ============================================================ */

import { toast, escHtml, stateToClass, todayStr, fmtDate } from './utils.js';
import * as apiClient from './api-client.js';
import * as store from './state-store.js';
import * as wsClient from './ws-client.js';
import * as overview from './machine-overview.js';
import * as detail from './machine-detail.js';

/* ── Main Init ────────────────────────────────────────────── */

window.addEventListener('DOMContentLoaded', async () => {
    const user = await apiClient.checkAuth();
    if (!user) return;
    store.setUser(user);

    // Show username
    const userEl = document.getElementById('current-user-display');
    if (userEl) userEl.textContent = user.username + ' (' + user.role + ')';

    // Hide admin-only nav items for non-admins
    if (user.role !== 'admin') {
        document.querySelectorAll('.nav-admin-only').forEach(el => el.style.display = 'none');
    }

    initSidebar();
    initClock();
    initWebSocket();
    detail.initAlertBell();
    detail.initReportTabs();
    detail.initMachineDetailPage();
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
    document.getElementById('btn-logout')?.addEventListener('click', () => apiClient.logout());

    // Sessions load button
    document.getElementById('btn-load-sessions')?.addEventListener('click', () => detail.loadSessionsData());

    // Employee register
    document.getElementById('btn-register-emp')?.addEventListener('click', () => detail.registerEmployee());

    // Reports download CSV & PDF
    document.getElementById('btn-download-csv')?.addEventListener('click', () => detail.downloadCSV());
    document.getElementById('btn-export-pdf')?.addEventListener('click', () => detail.exportReportToPDF());

    // Machines empty-state button
    document.getElementById('btn-setup-first-machine')?.addEventListener('click', () => navigateTo('cameras'));
});

/* ── Sidebar Navigation ──────────────────────────────────── */

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

    store.setActivePage(page);
    loadPageData(page);
}

// Expose navigateTo globally for inline event handlers
window.__navigateTo = navigateTo;

// Expose machine detail navigation for overview grid card clicks (Requirement 5.3)
window.__navigateToMachineDetail = (machineId) => {
    detail.openMachineDetailView(machineId);
};
// Legacy alias
window.__openMachineDetail = window.__navigateToMachineDetail;

async function loadPageData(page) {
    if (page === 'home') await detail.loadHomeData();
    else if (page === 'machines') await overview.loadMachinesPage();
    else if (page === 'sessions') await detail.loadSessionsData();
    else if (page === 'reports') await detail.loadReportsData();
    else if (page === 'settings') await loadSettingsData();
    else if (page === 'cameras') initCameraWizardPage();
    // machine-detail is loaded via openMachineDetailView, not through this path
}

/* ── Live Clock ──────────────────────────────────────────── */

function initClock() {
    function tick() {
        const el = document.getElementById('live-clock');
        if (el) el.textContent = new Date().toLocaleTimeString('en-GB');
    }
    tick();
    setInterval(tick, 1000);
}

/* ── WebSocket ────────────────────────────────────────────── */

function initWebSocket() {
    wsClient.onMessage(handleWsMessage);
    wsClient.connect();
}

function handleWsMessage(data) {
    // State broadcast
    if (data.state !== undefined) {
        const mid = data.machine_id || 'M-01';
        store.updateMachineState(mid, data);
        detail.updateHomeMetricsFromWs(data);
        detail.updateDetailFromWs(data);
        overview.updateMachineCardFromWs(data);
    }
    // Event notifications
    if (data.event_type) {
        detail.pushActivityToFeed(data);
        if (data.event_type === 'ALERT') {
            detail.refreshAlertBadge();
            if (data.alert_type === 'machine_red_light' || data.alert_type === 'camera_offline') {
                detail.playAlertSound();
                toast(data.message || 'Critical machine alert!', 'error');
            } else {
                toast(data.message || 'New alert received', 'info');
            }
        }
    }
}

/* ── Settings Page ────────────────────────────────────────── */

function initSettingsTabs() {
    document.querySelectorAll('.settings-tab-item').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.settings-tab-item').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const panel = document.getElementById('settings-tab-' + tab.dataset.tab);
            if (panel) panel.classList.add('active');

            if (tab.dataset.tab === 'employees') {
                detail.loadEmployeesData();
            }
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
        const all = await apiClient.getSettings();
        store.setSettingsCache(all);

        // System
        const sys = all.system || {};
        setInputVal('s-camera-url', sys.camera_url);

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
        store.setShiftsData(sh.named_shifts || []);
        renderShiftsList();
        renderHolidaysList(sh.holidays || []);

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
        const user = store.getUser();
        if (user && user.role === 'admin') {
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

    if (section === 'system') {
        payload = { camera_url: document.getElementById('s-camera-url')?.value };
    } else if (section === 'detection') {
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
            named_shifts: store.getShiftsData(),
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
        await apiClient.saveSettingsSection(section, payload);
        if (banner) { banner.hidden = false; setTimeout(() => { banner.hidden = true; }, 3000); }
    } catch (e) { toast('Save failed: ' + e.message, 'error'); }
};

/* ── Shifts & Holidays ────────────────────────────────────── */

function renderShiftsList() {
    const container = document.getElementById('shifts-list');
    if (!container) return;
    const shiftsData = store.getShiftsData();
    container.innerHTML = shiftsData.map((shift, i) => `
        <div class="shift-row" data-idx="${i}">
            <input type="text" class="input-field" placeholder="Name (e.g. Shift A)" value="${escHtml(shift.name || '')}"
                data-shift-idx="${i}" data-shift-field="name">
            <input type="time" class="input-field" value="${escHtml(shift.start || '')}"
                data-shift-idx="${i}" data-shift-field="start">
            <span>to</span>
            <input type="time" class="input-field" value="${escHtml(shift.end || '')}"
                data-shift-idx="${i}" data-shift-field="end">
            <button class="btn btn-ghost btn-sm btn-danger" data-action="remove-shift" data-idx="${i}">Remove</button>
        </div>`).join('');

    // Bind change listeners
    container.querySelectorAll('[data-shift-idx]').forEach(input => {
        input.addEventListener('change', () => {
            const idx = parseInt(input.dataset.shiftIdx);
            const field = input.dataset.shiftField;
            const data = store.getShiftsData();
            if (data[idx]) data[idx][field] = input.value;
        });
    });
    container.querySelectorAll('[data-action="remove-shift"]').forEach(btn => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.dataset.idx);
            const data = store.getShiftsData();
            data.splice(idx, 1);
            store.setShiftsData(data);
            renderShiftsList();
        });
    });
}

// Expose for global use
window.removeShift = function(i) {
    const data = store.getShiftsData();
    data.splice(i, 1);
    store.setShiftsData(data);
    renderShiftsList();
};

function addShiftRow() {
    const data = store.getShiftsData();
    data.push({ name: '', start: '06:00', end: '14:00' });
    store.setShiftsData(data);
    renderShiftsList();
}

function renderHolidaysList(holidays) {
    const container = document.getElementById('holidays-list');
    if (!container) return;
    container.innerHTML = holidays.map(d => `
        <div class="shift-row holiday-item" data-date="${d}">
            <span>${d}</span>
            <button class="btn btn-ghost btn-sm" data-action="remove-holiday">Remove</button>
        </div>`).join('');
    container.querySelectorAll('[data-action="remove-holiday"]').forEach(btn => {
        btn.addEventListener('click', () => btn.closest('.holiday-item').remove());
    });
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
        <button class="btn btn-ghost btn-sm" data-action="remove-holiday">Remove</button>`;
    div.querySelector('[data-action="remove-holiday"]').addEventListener('click', () => div.remove());
    container.appendChild(div);
    input.value = '';
}

/* ── User Management (Admin) ──────────────────────────────── */

async function loadUsersList() {
    try {
        const users = await apiClient.getUsers();
        const tbody = document.getElementById('users-body');
        const empty = document.getElementById('users-empty');
        if (!tbody) return;
        tbody.innerHTML = '';
        if (!users || !users.length) {
            if (empty) empty.style.display = 'flex';
            return;
        }
        if (empty) empty.style.display = 'none';
        const currentUser = store.getUser();
        tbody.innerHTML = users.map(u => `
            <tr>
                <td>${escHtml(u.username)}</td>
                <td><span class="role-badge role-${u.role}">${u.role}</span></td>
                <td>${u.created_at ? fmtDate(u.created_at) : '—'}</td>
                <td>${u.last_login ? fmtDate(u.last_login) : 'Never'}</td>
                <td>
                    ${u.username !== currentUser.username
                        ? `<button class="btn btn-danger btn-sm" data-action="delete-user" data-username="${escHtml(u.username)}">Delete</button>`
                        : '<span class="text-muted">(you)</span>'}
                </td>
            </tr>`).join('');
        tbody.querySelectorAll('[data-action="delete-user"]').forEach(btn => {
            btn.addEventListener('click', () => deleteUserPrompt(btn.dataset.username));
        });
    } catch (e) { console.error('loadUsersList', e); }
}

async function createUser() {
    const uname = document.getElementById('new-user-username')?.value.trim();
    const pwd = document.getElementById('new-user-password')?.value;
    const role = document.getElementById('new-user-role')?.value;
    const msg = document.getElementById('create-user-msg');
    if (!uname || !pwd) {
        if (msg) { msg.textContent = 'Username and password required.'; msg.style.color = 'var(--danger)'; }
        return;
    }
    try {
        await apiClient.createUser({ username: uname, password: pwd, role });
        if (msg) { msg.textContent = 'User created.'; msg.style.color = 'var(--success)'; }
        document.getElementById('new-user-username').value = '';
        document.getElementById('new-user-password').value = '';
        await loadUsersList();
    } catch (e) {
        if (msg) { msg.textContent = e.message; msg.style.color = 'var(--danger)'; }
    }
}

async function deleteUserPrompt(username) {
    if (!confirm('Delete user ' + username + '?')) return;
    try {
        await apiClient.deleteUser(username);
        toast('User deleted.', 'success');
        loadUsersList();
    } catch (e) { toast(e.message, 'error'); }
}

window.deleteUser = deleteUserPrompt;

/* ── Backup / Restore ─────────────────────────────────────── */

function initRestoreBackup() {
    const fileInput = document.getElementById('restore-file');
    if (!fileInput) return;
    fileInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const statusEl = document.getElementById('restore-status');
        if (statusEl) { statusEl.textContent = 'Uploading backup…'; statusEl.style.color = 'var(--text-secondary)'; }
        try {
            const data = await apiClient.restoreBackup(file);
            if (statusEl) { statusEl.textContent = data.message || 'Restored. Restart server for full effect.'; statusEl.style.color = 'var(--success)'; }
            toast('Database restored successfully.', 'success');
        } catch (err) {
            if (statusEl) { statusEl.textContent = err.message; statusEl.style.color = 'var(--danger)'; }
            toast('Restore failed: ' + err.message, 'error');
        }
        fileInput.value = '';
    });
}

/* ── Camera Wizard ────────────────────────────────────────── */

let wizCanvas = null, wizCtx = null, wizDrawing = false, wizStart = { x: 0, y: 0 };
let lightCanvas = null, lightCtx = null, lightDrawing = false, lightStart = { x: 0, y: 0 };

function initCameraWizardPage() {
    resetWizard();
    loadCamMachineList();
    bindWizardButtons();
}

function resetWizard() {
    store.resetWizardState();
    showCamStep('cam-welcome');
}

function showCamStep(id) {
    ['cam-welcome', 'cam-step-1', 'cam-step-2', 'cam-step-3', 'cam-step-3b', 'cam-step-4', 'cam-detail-view']
        .forEach(s => { const el = document.getElementById(s); if (el) el.hidden = (s !== id); });
}

async function loadCamMachineList() {
    const ul = document.getElementById('cam-machine-items');
    const emptyEl = document.getElementById('cam-machine-empty');
    if (!ul) return;
    try {
        const status = await apiClient.getStatus();
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
            li.innerHTML = `<span class="cm-name">${escHtml(mid)}</span><span class="cm-state ${stateToClass(m.state)}">${escHtml(m.state || 'IDLE')}</span>`;
            li.addEventListener('click', () => openMachineDetail(mid, m));
            ul.appendChild(li);
        });
    } catch (e) { console.error('loadCamMachineList', e); }
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
        <div class="detail-row"><strong>State:</strong> ${escHtml(stateData.state || 'IDLE')}</div>
        <div class="detail-row"><strong>Worker:</strong> ${escHtml(stateData.employee_name || stateData.badge_id || 'None')}</div>
        <div class="detail-row"><strong>Efficiency:</strong> ${stateData.efficiency_percent || 0}%</div>
    `;
    const detailImg = document.getElementById('cam-detail-img');
    if (detailImg) { detailImg.src = '/api/video_feed'; detailImg.style.display = 'block'; }

    const editZoneBtn = document.getElementById('cam-detail-edit-zone');
    if (editZoneBtn) editZoneBtn.onclick = () => {
        store.setWizardState({ machineId: machineId, name: machineId });
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
        store.resetWizardState();
        const nameEl = document.getElementById('wiz-machine-name');
        const grpEl = document.getElementById('wiz-machine-group');
        const hrsEl = document.getElementById('wiz-shift-hours');
        if (nameEl) nameEl.value = '';
        if (grpEl) grpEl.value = '';
        if (hrsEl) hrsEl.value = 8;
        showCamStep('cam-step-1');
    });

    // Step 1 -> 2
    document.getElementById('wiz-next-1')?.addEventListener('click', () => {
        const name = document.getElementById('wiz-machine-name')?.value.trim();
        const hrs = parseFloat(document.getElementById('wiz-shift-hours')?.value) || 0;
        const nameErr = document.getElementById('wiz-name-error');
        const hrsErr = document.getElementById('wiz-hours-error');
        let ok = true;
        if (!name) { if (nameErr) nameErr.textContent = 'Name is required'; ok = false; } else { if (nameErr) nameErr.textContent = ''; }
        if (!hrs || hrs < 1) { if (hrsErr) hrsErr.textContent = 'Enter valid hours'; ok = false; } else { if (hrsErr) hrsErr.textContent = ''; }
        if (!ok) return;
        store.setWizardState({
            name: name,
            group: document.getElementById('wiz-machine-group')?.value.trim() || '',
            shiftHours: hrs
        });
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
        store.setWizardState({ rtspUrl: url });
        if (statusEl) statusEl.hidden = false;
        if (msgEl) msgEl.textContent = 'Connecting to camera…';
        try {
            const img = document.getElementById('wiz-stream-img');
            if (img) { img.src = '/api/video_feed?' + Date.now(); img.onload = () => { if (preview) preview.hidden = false; }; }
            if (msgEl) msgEl.textContent = 'Connected!';
            store.setWizardState({ streamOk: true });
            if (nextBtn) nextBtn.disabled = false;
        } catch (e) {
            if (msgEl) msgEl.textContent = 'Connection failed: ' + e.message;
            store.setWizardState({ streamOk: false });
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
        if (wizCtx && wizCanvas) wizCtx.clearRect(0, 0, wizCanvas.width, wizCanvas.height);
        store.setWizardState({ zone: null });
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
        store.setWizardState({ lightZone: null });
        saveMachineConfig();
    });
    document.getElementById('wiz-light-redraw')?.addEventListener('click', () => {
        if (lightCtx && lightCanvas) lightCtx.clearRect(0, 0, lightCanvas.width, lightCanvas.height);
        store.setWizardState({ lightZone: null });
        const saveBtn = document.getElementById('wiz-light-save');
        if (saveBtn) saveBtn.disabled = true;
        const hint = document.getElementById('wiz-light-zone-hint');
        if (hint) hint.hidden = true;
    });
    document.getElementById('wiz-light-save')?.addEventListener('click', saveMachineConfig);

    // Step 4 actions
    document.getElementById('wiz-add-another')?.addEventListener('click', () => {
        store.resetWizardState();
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
        wizCanvas.width = img.clientWidth || 640;
        wizCanvas.height = img.clientHeight || 360;
    };
    wizCtx = wizCanvas.getContext('2d');

    const getPos = (e) => {
        const r = wizCanvas.getBoundingClientRect();
        return { x: (e.touches ? e.touches[0].clientX : e.clientX) - r.left,
                 y: (e.touches ? e.touches[0].clientY : e.clientY) - r.top };
    };

    wizCanvas.onmousedown = (e) => { wizDrawing = true; wizStart = getPos(e); };
    wizCanvas.onmousemove = (e) => {
        if (!wizDrawing) return;
        const p = getPos(e);
        wizCtx.clearRect(0, 0, wizCanvas.width, wizCanvas.height);
        wizCtx.fillStyle = 'rgba(99,102,241,0.2)';
        wizCtx.strokeStyle = '#6366F1';
        wizCtx.lineWidth = 2;
        wizCtx.fillRect(wizStart.x, wizStart.y, p.x - wizStart.x, p.y - wizStart.y);
        wizCtx.strokeRect(wizStart.x, wizStart.y, p.x - wizStart.x, p.y - wizStart.y);
    };
    wizCanvas.onmouseup = (e) => {
        wizDrawing = false;
        const p = getPos(e);
        const zone = { x1: wizStart.x / wizCanvas.width, y1: wizStart.y / wizCanvas.height,
                       x2: p.x / wizCanvas.width, y2: p.y / wizCanvas.height };
        store.setWizardState({ zone });
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
        lightCanvas.width = img.clientWidth || 640;
        lightCanvas.height = img.clientHeight || 360;
    };
    lightCtx = lightCanvas.getContext('2d');

    const getPos = (e) => {
        const r = lightCanvas.getBoundingClientRect();
        return { x: (e.touches ? e.touches[0].clientX : e.clientX) - r.left,
                 y: (e.touches ? e.touches[0].clientY : e.clientY) - r.top };
    };

    lightCanvas.onmousedown = (e) => { lightDrawing = true; lightStart = getPos(e); };
    lightCanvas.onmousemove = (e) => {
        if (!lightDrawing) return;
        const p = getPos(e);
        lightCtx.clearRect(0, 0, lightCanvas.width, lightCanvas.height);
        lightCtx.fillStyle = 'rgba(245,158,11,0.2)';
        lightCtx.strokeStyle = '#F59E0B';
        lightCtx.lineWidth = 2;
        lightCtx.fillRect(lightStart.x, lightStart.y, p.x - lightStart.x, p.y - lightStart.y);
        lightCtx.strokeRect(lightStart.x, lightStart.y, p.x - lightStart.x, p.y - lightStart.y);
    };
    lightCanvas.onmouseup = (e) => {
        lightDrawing = false;
        const p = getPos(e);
        const zone = { x1: lightStart.x / lightCanvas.width, y1: lightStart.y / lightCanvas.height,
                       x2: p.x / lightCanvas.width, y2: p.y / lightCanvas.height };
        store.setWizardState({ lightZone: zone });
        const saveBtn = document.getElementById('wiz-light-save');
        if (saveBtn) saveBtn.disabled = false;
        const hint = document.getElementById('wiz-light-zone-hint');
        if (hint) hint.hidden = false;
    };
}

async function saveMachineConfig() {
    showCamStep('cam-step-4');
    const savingEl = document.getElementById('wiz-saving');
    const savedEl = document.getElementById('wiz-saved');
    if (savingEl) savingEl.style.display = 'flex';
    if (savedEl) savedEl.hidden = true;

    const wiz = store.getWizardState();
    try {
        await apiClient.saveCameraZones({
            machineName: wiz.name,
            machineGroup: wiz.group,
            shiftHours: wiz.shiftHours,
            rtspUrl: wiz.rtspUrl,
            zone: wiz.zone,
            lightZone: wiz.lightZone,
        });
        if (savingEl) savingEl.style.display = 'none';
        if (savedEl) savedEl.hidden = false;

        const summary = document.getElementById('wiz-summary');
        if (summary) summary.innerHTML = `
            <div class="wiz-sum-row"><strong>Machine:</strong> ${escHtml(wiz.name)}</div>
            ${wiz.group ? '<div class="wiz-sum-row"><strong>Group:</strong> ' + escHtml(wiz.group) + '</div>' : ''}
            <div class="wiz-sum-row"><strong>Shift Hours:</strong> ${wiz.shiftHours}h</div>
            <div class="wiz-sum-row"><strong>Zone:</strong> ${wiz.zone ? 'Configured' : 'None'}</div>
            <div class="wiz-sum-row"><strong>Light Zone:</strong> ${wiz.lightZone ? 'Configured' : 'Skipped'}</div>
        `;
        loadCamMachineList();
    } catch (e) {
        if (savingEl) savingEl.style.display = 'none';
        toast('Save failed: ' + e.message, 'error');
        showCamStep('cam-step-3b');
    }
}

/* ── AI Chat Widget ───────────────────────────────────────── */

const chatMessages = [];

window.toggleChat = function() {
    const panel = document.getElementById('ai-chat-panel');
    const fab = document.getElementById('ai-chat-fab');
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
    const input = document.getElementById('chat-input');
    const history = document.getElementById('chat-history');
    if (!input || !history) return;
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.disabled = true;

    chatMessages.push({ role: 'user', content: text });
    appendChatBubble(history, 'user', text);

    const typing = appendChatBubble(history, 'ai', '…', true);
    history.scrollTop = history.scrollHeight;

    try {
        const data = await apiClient.sendAIChat(chatMessages);
        const reply = data.reply || 'No response.';
        chatMessages.push({ role: 'assistant', content: reply });
        typing.textContent = reply;
        typing.classList.remove('chat-typing');
    } catch (e) {
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

/* ── Dynamic CSS (machine cards, badges, toasts) ──────────── */

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
