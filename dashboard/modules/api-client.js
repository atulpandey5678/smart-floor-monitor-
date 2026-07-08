/* ── REST API Client ──────────────────────────────────────── */

/**
 * Core API utility. Handles JSON serialization, auth redirects, and error extraction.
 */
export async function api(path, opts = {}) {
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

/* ── Auth ─────────────────────────────────────────────────── */

export async function checkAuth() {
    try {
        const res = await fetch('/auth/me', { credentials: 'include' });
        if (!res.ok) throw new Error('Not authenticated');
        return await res.json();
    } catch (_) {
        window.location.href = '/login.html';
        return null;
    }
}

export async function logout() {
    await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
    window.location.href = '/login.html';
}

/* ── Status & Machines ────────────────────────────────────── */

export function getStatus() {
    return api('/api/status');
}

export function getMachines() {
    return api('/api/machines');
}

export function getMachineConfig(machineId) {
    return api('/api/v1/machines/' + encodeURIComponent(machineId));
}

/* ── Sessions ─────────────────────────────────────────────── */

export function getSessions(date) {
    return api('/api/sessions?date=' + date);
}

export function getMachineSessions(machineId, { page = 1, pageSize = 20, startDate, endDate } = {}) {
    let url = `/api/v1/sessions?machine_id=${encodeURIComponent(machineId)}&page=${page}&page_size=${pageSize}`;
    if (startDate) url += '&start_date=' + startDate;
    if (endDate) url += '&end_date=' + endDate;
    return api(url);
}

export function getMachineSessionsToday(machineId) {
    const today = new Date().toISOString().slice(0, 10);
    return api(`/api/v1/sessions?machine_id=${encodeURIComponent(machineId)}&date=${today}`);
}

/* ── Alerts ───────────────────────────────────────────────── */

export function getAlerts() {
    return api('/api/alerts');
}

export function getMachineAlerts(machineId, { limit = 20 } = {}) {
    return api(`/api/v1/alerts?machine_id=${encodeURIComponent(machineId)}&page_size=${limit}`);
}

export function getAlertHistory(limit = 10) {
    return api('/api/alerts/history?limit=' + limit);
}

export function getUnreadAlertCount() {
    return api('/api/alerts/unread-count');
}

export function resolveAlert(id, note) {
    return api(`/api/alerts/${id}/resolve`, { method: 'POST', json: { note } });
}

/* ── Employees ────────────────────────────────────────────── */

export function getEmployees() {
    return api('/api/employees');
}

export function createEmployee(data) {
    return api('/api/employees', { json: data });
}

export function deleteEmployee(badgeId) {
    return api('/api/employees/' + badgeId, { method: 'DELETE' });
}

/* ── Reports ──────────────────────────────────────────────── */

export function getDailyReport(date) {
    return api('/api/reports/daily?date=' + date);
}

export function getWeeklyReport(weekStart) {
    return api('/api/reports/weekly?week_start=' + weekStart);
}

export async function downloadReportCSV(url) {
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
}

/* ── Settings ─────────────────────────────────────────────── */

export function getSettings() {
    return api('/api/settings');
}

export function saveSettingsSection(section, payload) {
    return api('/api/settings/' + section, { method: 'PUT', json: payload });
}

/* ── Users ────────────────────────────────────────────────── */

export function getUsers() {
    return api('/auth/users');
}

export function createUser(data) {
    return api('/auth/users', { json: data });
}

export function deleteUser(username) {
    return api('/auth/users/' + username, { method: 'DELETE' });
}

/* ── Camera / Machines ────────────────────────────────────── */

export function saveCameraZones(payload) {
    return api('/api/camera/zones', { json: payload });
}

/* ── AI Chat ──────────────────────────────────────────────── */

export function sendAIChat(messages) {
    return api('/api/ai/chat', { json: { messages } });
}

/* ── Backup ───────────────────────────────────────────────── */

export async function restoreBackup(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/settings/backup/restore', {
        method: 'POST',
        credentials: 'include',
        body: fd,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Restore failed');
    return data;
}
