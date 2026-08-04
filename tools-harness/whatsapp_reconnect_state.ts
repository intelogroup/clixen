// Pure reconnect-storm state machine for the WhatsApp bridge.
//
// A session rejected at the login handshake (e.g. 405 "Connection Failure")
// never recovers by retrying hard — WhatsApp treats persistent failures as
// needing a re-link. After a few consecutive failed reconnects the bridge
// enters "stuck" mode: back off to a slow probe instead of hammering, and
// surface a repair signal (QR or reset hint) so the operator can re-pair.
//
// Kept side-effect free so it can be exercised without a live socket.

export const STORM_THRESHOLD = 6;
export const STORM_PROBE_MS = 600_000; // 10 min slow probe while stuck
export const NORMAL_MAX_BACKOFF_MS = 120_000;

export interface ReconnectState {
  reconnectAttempts: number;
  consecutiveFailures: number;
  stuck: boolean;
  stuckSince: number | null;
  stuckReason: string;
}

export function initialState(): ReconnectState {
  return {
    reconnectAttempts: 0,
    consecutiveFailures: 0,
    stuck: false,
    stuckSince: null,
    stuckReason: '',
  };
}

export function reconnectDelay(state: ReconnectState): number {
  const base = Math.min(3000 * Math.pow(2, state.reconnectAttempts), NORMAL_MAX_BACKOFF_MS);
  const delay = state.stuck ? Math.max(base, STORM_PROBE_MS) : base;
  state.reconnectAttempts++;
  return delay;
}

export function recordReconnectFailure(state: ReconnectState, reasonText: string, errorMsg: string): boolean {
  const alreadyStuck = state.stuck;
  state.consecutiveFailures++;
  if (state.consecutiveFailures >= STORM_THRESHOLD && !state.stuck) {
    state.stuck = true;
    state.stuckSince = Date.now();
    state.stuckReason = `${reasonText}: ${errorMsg}`.slice(0, 300);
  }
  // true when this call newly entered stuck mode (caller should fire the
  // best-effort fresh-socket probe + loud log).
  return !alreadyStuck && state.stuck;
}

export function resetReconnect(state: ReconnectState): void {
  state.reconnectAttempts = 0;
  state.consecutiveFailures = 0;
}

export function clearStuck(state: ReconnectState): void {
  state.stuck = false;
  state.stuckSince = null;
  state.stuckReason = '';
}
