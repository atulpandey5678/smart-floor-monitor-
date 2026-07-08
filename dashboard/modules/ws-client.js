/* ── WebSocket Client with Reconnection ──────────────────── */

import * as store from './state-store.js';

let ws = null;
let retryTimer = null;
let retries = 0;
let messageHandler = null;
let disconnectedAt = null;

const INITIAL_DELAY = 1000;  // 1 second
const MAX_DELAY = 30000;     // 30 seconds
const BACKOFF_BASE = 2;      // Exponential base (1s, 2s, 4s, 8s, 16s, 30s cap)

/**
 * Set the callback for incoming messages (parsed JSON).
 */
export function onMessage(handler) {
    messageHandler = handler;
}

/**
 * Initialize WebSocket connection to /ws.
 */
export function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = proto + '//' + location.host + '/ws';

    ws = new WebSocket(url);

    ws.onopen = () => {
        const wasDisconnected = retries > 0;
        retries = 0;
        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }

        store.setConnectionStatus('connected');
        updateBanner(false);
        updateStatusPill('connected');

        // On reconnect, request full state snapshot and reconcile
        if (wasDisconnected) {
            requestStateSnapshot();
            store.clearAllMachinesStale();
        }

        disconnectedAt = null;
    };

    ws.onmessage = (ev) => {
        try {
            const data = JSON.parse(ev.data);

            // Handle state reconciliation/snapshot response from server
            if (data.type === 'snapshot') {
                const payload = data.payload || {};
                const machines = payload.machines || {};
                store.reconcileState(machines);
                return;
            }

            if (messageHandler) messageHandler(data);
        } catch (_) {}
    };

    ws.onclose = () => {
        if (!disconnectedAt) {
            disconnectedAt = Date.now();
        }

        store.setConnectionStatus('reconnecting');

        // Mark all machine statuses as stale while disconnected
        store.markAllMachinesStale();

        updateBanner(true);
        updateStatusPill('reconnecting');
        scheduleReconnect();
    };

    ws.onerror = () => {
        ws.close();
    };
}

/**
 * Send a message through the WebSocket (JSON serialized).
 */
export function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
    }
}

/**
 * Subscribe to updates for a specific machine.
 */
export function subscribeMachine(machineId) {
    send({ type: 'subscribe', machine_ids: [machineId] });
}

/**
 * Unsubscribe from updates for a specific machine.
 */
export function unsubscribeMachine(machineId) {
    send({ type: 'unsubscribe', machine_ids: [machineId] });
}

/**
 * Subscribe to updates for all machines.
 */
export function subscribeAll() {
    send({ type: 'subscribe', machine_ids: ['*'] });
}

/**
 * Get whether we're currently disconnected.
 */
export function isDisconnected() {
    return store.getConnectionStatus() !== 'connected';
}

/**
 * Get how long we've been disconnected (ms), or 0 if connected.
 */
export function getDisconnectedDuration() {
    return disconnectedAt ? Date.now() - disconnectedAt : 0;
}

/* ── Reconnection Logic ──────────────────────────────────── */

function scheduleReconnect() {
    // Exponential backoff: 1s * 2^retries, capped at 30s
    const delay = Math.min(MAX_DELAY, INITIAL_DELAY * Math.pow(BACKOFF_BASE, retries));
    retries++;
    store.setConnectionStatus('reconnecting');
    updateBannerCountdown(delay);
    retryTimer = setTimeout(connect, delay);
}

/**
 * Request a full state snapshot from the server after reconnection.
 * The server responds with a 'state_snapshot' message containing all machine states.
 */
function requestStateSnapshot() {
    send({ type: 'request_snapshot' });
}

/* ── UI Helpers ──────────────────────────────────────────── */

function updateBanner(show) {
    const banner = document.getElementById('connection-banner');
    if (banner) {
        banner.hidden = !show;
        if (show) {
            banner.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/>
                    <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                <span id="connection-banner-text">Connection lost — reconnecting…</span>
                <span class="connection-banner-spinner"></span>
            `;
        }
    }
}

function updateBannerCountdown(delay) {
    const textEl = document.getElementById('connection-banner-text');
    if (textEl) {
        const secs = Math.round(delay / 1000);
        textEl.textContent = `Connection lost — retrying in ${secs}s…`;
    }
}

function updateStatusPill(status) {
    const el = document.getElementById('ws-status');
    if (!el) return;
    el.dataset.status = status;

    const labels = {
        connected: 'Live',
        reconnecting: 'Reconnecting',
        disconnected: 'Disconnected'
    };

    el.innerHTML = `<span class="status-dot-indicator"></span>${labels[status] || 'Disconnected'}`;
}
