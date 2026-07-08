/* ── Centralized State Store ──────────────────────────────── */

/**
 * Holds state for the application and all machines.
 * Provides a pub/sub interface for UI components to react to changes.
 */

const state = {
    user: null,
    activePage: 'home',
    connectionStatus: 'disconnected', // connected | reconnecting | disconnected
    machines: {},                      // { [machine_id]: { state, badge_id, employee_name, ... } }
    activityFeed: [],
    alertCache: [],
    trendChart: null,
    wizardState: {
        machineId: null,
        name: '', group: '', shiftHours: 8,
        rtspUrl: '', streamOk: false,
        zone: null, lightZone: null
    },
    shiftsData: [],
    settingsCache: {},
};

const listeners = new Set();

/**
 * Subscribe to state changes. Returns an unsubscribe function.
 */
export function subscribe(callback) {
    listeners.add(callback);
    return () => listeners.delete(callback);
}

/**
 * Notify all listeners of a state change.
 */
function notify(key, value) {
    for (const cb of listeners) {
        try { cb(key, value, state); } catch (_) {}
    }
}

/* ── Getters ─────────────────────────────────────────────── */

export function getState() {
    return state;
}

export function getUser() {
    return state.user;
}

export function getActivePage() {
    return state.activePage;
}

export function getConnectionStatus() {
    return state.connectionStatus;
}

export function getMachineState(machineId) {
    return state.machines[machineId] || null;
}

export function getAllMachines() {
    return state.machines;
}

/* ── Setters ─────────────────────────────────────────────── */

export function setUser(user) {
    state.user = user;
    notify('user', user);
}

export function setActivePage(page) {
    state.activePage = page;
    notify('activePage', page);
}

export function setConnectionStatus(status) {
    state.connectionStatus = status;
    notify('connectionStatus', status);
}

export function updateMachineState(machineId, data) {
    state.machines[machineId] = Object.assign(state.machines[machineId] || {}, data, { machine_id: machineId, _stale: false });
    notify('machine', state.machines[machineId]);
}

/**
 * Mark all machines as stale (used during WebSocket disconnection).
 */
export function markAllMachinesStale() {
    for (const id of Object.keys(state.machines)) {
        state.machines[id]._stale = true;
    }
    notify('machinesStale', true);
}

/**
 * Clear stale flag from all machines (used after reconnection reconciliation).
 */
export function clearAllMachinesStale() {
    for (const id of Object.keys(state.machines)) {
        state.machines[id]._stale = false;
    }
    notify('machinesStale', false);
}

/**
 * Reconcile local state with a full state snapshot received from the server.
 * Preserves locally cached session history while updating machine statuses.
 */
export function reconcileState(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return;

    // Update each machine from the snapshot while preserving session history
    const machines = snapshot.machines || snapshot;
    if (Array.isArray(machines)) {
        for (const machineData of machines) {
            const mid = machineData.machine_id;
            if (mid) {
                const existing = state.machines[mid] || {};
                // Preserve cached session history during reconciliation
                state.machines[mid] = Object.assign({}, existing, machineData, {
                    machine_id: mid,
                    _stale: false,
                    _sessionHistory: existing._sessionHistory || []
                });
            }
        }
    } else if (typeof machines === 'object') {
        for (const [mid, machineData] of Object.entries(machines)) {
            const existing = state.machines[mid] || {};
            state.machines[mid] = Object.assign({}, existing, machineData, {
                machine_id: mid,
                _stale: false,
                _sessionHistory: existing._sessionHistory || []
            });
        }
    }

    notify('stateReconciled', state.machines);
}

export function pushActivity(entry) {
    state.activityFeed.unshift(entry);
    if (state.activityFeed.length > 50) state.activityFeed.pop();
    notify('activityFeed', entry);
}

export function setTrendChart(chart) {
    state.trendChart = chart;
}

export function getTrendChart() {
    return state.trendChart;
}

export function setWizardState(partial) {
    Object.assign(state.wizardState, partial);
}

export function getWizardState() {
    return state.wizardState;
}

export function resetWizardState() {
    state.wizardState = {
        machineId: null,
        name: '', group: '', shiftHours: 8,
        rtspUrl: '', streamOk: false,
        zone: null, lightZone: null
    };
}

export function setShiftsData(data) {
    state.shiftsData = data;
}

export function getShiftsData() {
    return state.shiftsData;
}

export function setSettingsCache(data) {
    state.settingsCache = data;
}

export function getSettingsCache() {
    return state.settingsCache;
}
