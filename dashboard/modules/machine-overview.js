/* ── Machine Overview Grid Rendering ─────────────────────── */

import { escHtml, formatDuration } from './utils.js';
import * as apiClient from './api-client.js';
import * as store from './state-store.js';

// Listen for stale state changes to update machine card visuals
store.subscribe((key) => {
    if (key === 'machinesStale') {
        applyStaleIndicators();
    }
});

/**
 * Apply or remove stale indicators on all machine cards.
 */
function applyStaleIndicators() {
    const machines = store.getAllMachines();
    for (const [mid, machineData] of Object.entries(machines)) {
        const card = document.getElementById('mc-' + mid);
        if (card) {
            if (machineData._stale) {
                card.classList.add('stale');
            } else {
                card.classList.remove('stale');
            }
        }
    }
}

/**
 * Map state string to a normalized status for display and CSS.
 * Returns: idle, active, grace, error, offline
 */
function normalizeState(state) {
    const s = (state || '').toUpperCase();
    if (s === 'ACTIVE') return 'active';
    if (s === 'GRACE' || s === 'GRACE_PERIOD') return 'grace';
    if (s === 'ERROR' || s === 'ALERT' || s === 'EXCEPTION' || s === 'ABANDONED' || s === 'FAILED') return 'error';
    if (s === 'OFFLINE' || s === 'DISCONNECTED') return 'offline';
    return 'idle';
}

/**
 * Get color class for state indicator dot.
 */
function stateColorClass(normalizedState) {
    switch (normalizedState) {
        case 'active': return 'status-green';
        case 'grace': return 'status-yellow';
        case 'error': return 'status-red';
        case 'offline': return 'status-grey';
        default: return 'status-grey';
    }
}

/**
 * Get pipeline health label and class from machine data.
 */
function getPipelineHealth(m) {
    const pipelineStatus = (m.pipeline_status || m.pipeline_state || '').toLowerCase();
    if (pipelineStatus === 'running' || pipelineStatus === 'active') return { label: 'Healthy', cls: 'health-good' };
    if (pipelineStatus === 'reconnecting') return { label: 'Reconnecting', cls: 'health-warn' };
    if (pipelineStatus === 'error' || pipelineStatus === 'failed') return { label: 'Error', cls: 'health-bad' };
    if (pipelineStatus === 'stopped') return { label: 'Stopped', cls: 'health-off' };
    // Infer from machine state if pipeline_status not available
    const st = normalizeState(m.state);
    if (st === 'active' || st === 'grace') return { label: 'Healthy', cls: 'health-good' };
    if (st === 'error') return { label: 'Error', cls: 'health-bad' };
    if (st === 'offline') return { label: 'Offline', cls: 'health-off' };
    return { label: 'Idle', cls: 'health-idle' };
}

/**
 * Map a liveness string (LIVE/STALE/UNKNOWN) to a CSS class and label.
 */
function livenessDisplay(liveness) {
    const l = (liveness || 'UNKNOWN').toUpperCase();
    if (l === 'LIVE')    return { label: 'LIVE',    cls: 'liveness-live' };
    if (l === 'STALE')   return { label: 'STALE',   cls: 'liveness-stale' };
    return                      { label: 'UNKNOWN', cls: 'liveness-unknown' };
}

/**
 * Map a camera_health string to a display label and CSS class.
 */
function cameraHealthDisplay(health) {
    const h = (health || '').toUpperCase();
    if (h === 'HEALTHY')   return { label: 'Cam: Healthy',   cls: 'cam-healthy' };
    if (h === 'DEGRADED')  return { label: 'Cam: Degraded',  cls: 'cam-degraded' };
    if (h === 'FAILED')    return { label: 'Cam: Failed',    cls: 'cam-failed' };
    return null;
}

/**
 * Load and render the machines page (grid of cards).
 */
export async function loadMachinesPage() {
    const grid = document.getElementById('machines-grid');
    const emptyState = document.getElementById('machines-empty-state');
    const container = document.getElementById('machines-container');
    const countBadge = document.getElementById('machines-count-badge');

    if (grid) grid.innerHTML = Array(3).fill('<div class="machine-card skeleton" style="height:180px;border-radius:12px;"></div>').join('');

    try {
        const [machines, liveState] = await Promise.all([
            apiClient.getMachines().catch(() => []),
            apiClient.getStatus().catch(() => ({})),
        ]);

        let list = machines;
        if (!list || !list.length) {
            // Fallback: use live status as a single machine if no machine list
            if (liveState && liveState.machine_id) {
                list = [liveState];
            } else {
                list = [{ machine_id: liveState.machine_id || 'M-01', state: 'IDLE' }];
            }
        }

        // Merge live state into matching machine
        list = list.map(m => {
            const mid = m.machine_id || m.id || m.machineName || m.name || 'M-01';
            if (mid === (liveState.machine_id || 'M-01')) {
                return Object.assign({}, m, liveState, { machine_id: mid });
            }
            return Object.assign({ machine_id: mid }, m);
        });

        // Update store with all machine data
        list.forEach(m => {
            const mid = m.machine_id || 'M-01';
            store.updateMachineState(mid, m);
        });

        if (countBadge) countBadge.textContent = list.length + ' machine' + (list.length !== 1 ? 's' : '');

        if (!list.length) {
            if (emptyState) emptyState.hidden = false;
            if (container) container.hidden = true;
            return;
        }

        if (emptyState) emptyState.hidden = true;
        if (container) container.hidden = false;

        renderMachineCards(list);
    } catch (e) {
        console.error('loadMachinesPage', e);
    }
}

/**
 * Render machine cards into the grid container.
 */
export function renderMachineCards(machines) {
    const grid = document.getElementById('machines-grid');
    if (!grid) return;

    grid.innerHTML = machines.map(m => {
        const mid = escHtml(m.machine_id || 'M-01');
        const displayName = m.display_name || m.name || mid;
        const normState = normalizeState(m.state);
        const colorCls = stateColorClass(normState);
        const stateLabel = (m.state || 'IDLE').toUpperCase();
        const dataState = stateLabel;

        // Worker info
        const workerName = m.employee_name || (m.badge_id ? 'Badge: ' + m.badge_id : 'No worker');

        // Session duration
        const dur = m.active_duration_seconds ? formatDuration(m.active_duration_seconds) : '—';

        // Pipeline health
        const health = getPipelineHealth(m);

        // Live-state fields from Live_State_Cache (Req 6.8, 10.1)
        const liveness = livenessDisplay(m.liveness);
        const camHealth = cameraHealthDisplay(m.camera_health);
        const machineLight = m.machine_light || m.light_color || null;

        return `
        <div class="machine-card" id="mc-${mid}" data-state="${escHtml(dataState)}" data-machine-id="${mid}" role="button" tabindex="0" aria-label="View details for ${escHtml(displayName)}">
            <div class="machine-card-header">
                <div class="mc-title-row">
                    <span class="machine-name">${escHtml(displayName)}</span>
                    <span class="state-badge">${escHtml(stateLabel)}</span>
                </div>
                <span class="mc-machine-id">${mid}</span>
            </div>
            <div class="mc-snapshot-wrap">
                <img class="mc-snapshot" id="mc-snap-${mid}"
                     src="/api/machines/${mid}/snapshot"
                     alt="Snapshot for ${escHtml(displayName)}"
                     loading="lazy"
                     style="display:none"
                     onerror="this.style.display='none'"
                     onload="this.style.display='block'"
                />
            </div>
            <div class="mc-body">
                <div class="mc-info-row">
                    <div class="mc-info-item">
                        <svg class="mc-info-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
                        <span class="mc-worker">${escHtml(workerName)}</span>
                    </div>
                    <div class="mc-info-item">
                        <svg class="mc-info-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                        <span class="mc-duration">${dur}</span>
                    </div>
                </div>
                <div class="mc-health-row">
                    <span class="mc-status-indicator ${colorCls}"></span>
                    <span class="mc-health-badge ${health.cls}">${health.label}</span>
                    <span class="mc-liveness-badge ${liveness.cls}">${liveness.label}</span>
                    ${camHealth ? `<span class="mc-cam-health ${camHealth.cls}">${camHealth.label}</span>` : ''}
                    ${machineLight ? `<span class="mc-machine-light light-${escHtml(machineLight.toLowerCase())}">${escHtml(machineLight)}</span>` : ''}
                </div>
            </div>
        </div>`;
    }).join('');

    // Bind click events for card navigation to detail view
    grid.querySelectorAll('.machine-card[data-machine-id]').forEach(card => {
        card.addEventListener('click', () => {
            const machineId = card.dataset.machineId;
            navigateToMachineDetail(machineId);
        });
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                const machineId = card.dataset.machineId;
                navigateToMachineDetail(machineId);
            }
        });
    });

    // Bind refresh button
    document.getElementById('btn-refresh-machines')?.addEventListener('click', () => loadMachinesPage());
}

/**
 * Navigate to machine detail view when a card is clicked.
 */
function navigateToMachineDetail(machineId) {
    // Use the detail module's navigation if available
    if (window.__navigateToMachineDetail) {
        window.__navigateToMachineDetail(machineId);
    } else {
        // Fallback: navigate to cameras page with machine selected
        if (window.__navigateTo) window.__navigateTo('cameras');
    }
}

/**
 * Update a single machine card from a WebSocket state update.
 * Real-time updates without page refresh (Requirement 5.4).
 */
export function updateMachineCardFromWs(data) {
    if (store.getActivePage() !== 'machines') return;
    const mid = data.machine_id || 'M-01';
    const card = document.getElementById('mc-' + mid);

    if (!card) {
        // New machine appeared — reload the grid
        loadMachinesPage();
        return;
    }

    const stateLabel = (data.state || 'IDLE').toUpperCase();
    const normState = normalizeState(data.state);
    const colorCls = stateColorClass(normState);
    const health = getPipelineHealth(data);

    // Update data-state attribute for CSS styling
    card.dataset.state = stateLabel;

    // Update state badge
    const stBadge = card.querySelector('.state-badge');
    if (stBadge) stBadge.textContent = stateLabel;

    // Update worker
    const worker = card.querySelector('.mc-worker');
    if (worker) {
        worker.textContent = data.employee_name || (data.badge_id ? 'Badge: ' + data.badge_id : 'No worker');
    }

    // Update duration
    const dur = card.querySelector('.mc-duration');
    if (dur) dur.textContent = data.active_duration_seconds ? formatDuration(data.active_duration_seconds) : '—';

    // Update status indicator color
    const indicator = card.querySelector('.mc-status-indicator');
    if (indicator) {
        indicator.className = 'mc-status-indicator ' + colorCls;
    }

    // Update pipeline health badge
    const healthBadge = card.querySelector('.mc-health-badge');
    if (healthBadge) {
        healthBadge.className = 'mc-health-badge ' + health.cls;
        healthBadge.textContent = health.label;
    }

    // Update liveness badge (LIVE/STALE/UNKNOWN) from Live_State_Cache (Req 6.7, 6.8, 10.5)
    const livenessBadge = card.querySelector('.mc-liveness-badge');
    if (livenessBadge && data.liveness !== undefined) {
        const lv = livenessDisplay(data.liveness);
        livenessBadge.className = 'mc-liveness-badge ' + lv.cls;
        livenessBadge.textContent = lv.label;
    }

    // Update camera health label (Req 6.3)
    const camBadge = card.querySelector('.mc-cam-health');
    const camHealth = cameraHealthDisplay(data.camera_health);
    if (camBadge && camHealth) {
        camBadge.className = 'mc-cam-health ' + camHealth.cls;
        camBadge.textContent = camHealth.label;
    }

    // Update machine light
    const lightBadge = card.querySelector('.mc-machine-light');
    if (lightBadge && (data.machine_light || data.light_color)) {
        const light = data.machine_light || data.light_color;
        lightBadge.className = 'mc-machine-light light-' + light.toLowerCase();
        lightBadge.textContent = light;
    }

    // Refresh snapshot thumbnail (best-effort, silently hide on 404) (Req 9.4, 10.1)
    const snap = card.querySelector('.mc-snapshot');
    if (snap) {
        const newSrc = '/api/machines/' + encodeURIComponent(mid) + '/snapshot?t=' + Date.now();
        snap.style.display = 'none';
        snap.src = newSrc;
    }
}
