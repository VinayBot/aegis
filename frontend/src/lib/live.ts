// The site's single source of live data.
//
// live:   WebSocket /ws/events for every lifecycle event, plus polling of /api/health,
//         /api/architecture and /api/metrics.
// replay: the backend is unreachable. A recorded fixture of real traffic (public/replay/
//         fixture.json, captured from this gateway by scripts/record_fixture.py) plays back with
//         its original timing, and the UI shows a "REPLAY: backend offline" banner.
// Nothing here ever synthesises a number. If neither source is available, values stay empty.

import { create } from "zustand";
import { api } from "./api";
import type { Architecture, GatewayEvent, Health, Metrics, RequestRow, Tenant } from "./types";

export type Mode = "connecting" | "live" | "replay";

interface Fixture {
  recorded_at: number;
  duration_s: number;
  snapshots: {
    health: Health;
    architecture: Architecture;
    metrics: Metrics;
    requests: RequestRow[];
    tenants: Tenant[];
  };
  events: GatewayEvent[];
}

interface LiveState {
  mode: Mode;
  wsConnected: boolean;
  lastEventAt: number;
  health: Health | null;
  architecture: Architecture | null;
  metrics: Metrics | null;
  recent: GatewayEvent[];
  fixtureRecordedAt: number | null;
}

export const useLive = create<LiveState>(() => ({
  mode: "connecting",
  wsConnected: false,
  lastEventAt: 0,
  health: null,
  architecture: null,
  metrics: null,
  recent: [],
  fixtureRecordedAt: null,
}));

type Listener = (e: GatewayEvent) => void;
const listeners = new Set<Listener>();

/** Animation consumers subscribe here so a burst of events never re-renders React trees. */
export function onEvent(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

let recentBuffer: GatewayEvent[] = [];
let flushScheduled = false;

function emit(e: GatewayEvent) {
  for (const fn of listeners) fn(e);
  recentBuffer.push(e);
  if (recentBuffer.length > 400) recentBuffer = recentBuffer.slice(-300);
  if (!flushScheduled) {
    flushScheduled = true;
    setTimeout(() => {
      flushScheduled = false;
      useLive.setState({ recent: recentBuffer.slice(-300), lastEventAt: Date.now() });
    }, 250); // lists refresh 4x a second; animations get every event immediately via onEvent
  }
}

// ---------------------------------------------------------------------------- live mode

let ws: WebSocket | null = null;
let pollers: number[] = [];
let started = false;

async function poll<K extends "health" | "architecture" | "metrics">(key: K, path: string) {
  if (document.hidden || useLive.getState().mode !== "live") return;
  try {
    const value = await api<LiveState[K]>(path);
    useLive.setState({ [key]: value } as Partial<LiveState>);
  } catch {
    /* the health poller decides when to fall back to replay */
  }
}

function connectSocket() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/events`);
  let beat = 0;
  const watchdog = window.setInterval(() => {
    if (Date.now() - beat > 9000 && useLive.getState().wsConnected) {
      useLive.setState({ wsConnected: false });
      ws?.close();
    }
  }, 3000);
  ws.onopen = () => {
    beat = Date.now();
    useLive.setState({ wsConnected: true });
  };
  ws.onmessage = (msg) => {
    beat = Date.now();
    const data = JSON.parse(msg.data);
    if (data.type === "heartbeat") return;
    if (data.type === "hello") {
      recentBuffer = data.recent ?? [];
      useLive.setState({ recent: recentBuffer.slice(-300) });
      return;
    }
    emit(data as GatewayEvent);
  };
  ws.onclose = () => {
    window.clearInterval(watchdog);
    useLive.setState({ wsConnected: false });
    ws = null;
    if (useLive.getState().mode === "live") window.setTimeout(connectSocket, 1500);
  };
}

async function goLive() {
  stopReplay();
  useLive.setState({ mode: "live", fixtureRecordedAt: null });
  connectSocket();
  pollers = [
    window.setInterval(() => poll("architecture", "/api/architecture"), 1500),
    window.setInterval(() => poll("metrics", "/api/metrics?window=300&step=5"), 3000),
  ];
  await Promise.all([poll("architecture", "/api/architecture"), poll("metrics", "/api/metrics?window=300&step=5")]);
}

function stopLive() {
  pollers.forEach((p) => window.clearInterval(p));
  pollers = [];
  ws?.close();
  ws = null;
}

// ---------------------------------------------------------------------------- replay mode

let replayTimer: number | null = null;

async function goReplay() {
  stopLive();
  let fixture: Fixture;
  try {
    fixture = await api<Fixture>("/replay/fixture.json");
  } catch {
    useLive.setState({ mode: "replay", wsConnected: false });
    return;
  }
  const s = fixture.snapshots;
  useLive.setState({
    mode: "replay",
    wsConnected: false,
    health: s.health,
    architecture: s.architecture,
    metrics: s.metrics,
    fixtureRecordedAt: fixture.recorded_at,
  });
  const events = fixture.events;
  if (!events.length) return;
  const t0 = events[0].ts;
  const loop = () => {
    const start = performance.now();
    let i = 0;
    const tick = () => {
      const elapsed = (performance.now() - start) / 1000;
      while (i < events.length && events[i].ts - t0 <= elapsed) {
        emit({ ...events[i], ts: Date.now() / 1000, replay: true });
        i++;
      }
      replayTimer = i < events.length ? window.setTimeout(tick, 50) : window.setTimeout(loop, 1500);
    };
    tick();
  };
  loop();
}

function stopReplay() {
  if (replayTimer !== null) window.clearTimeout(replayTimer);
  replayTimer = null;
}

// ---------------------------------------------------------------------------- lifecycle

async function checkHealth() {
  try {
    const health = await api<Health>("/api/health");
    useLive.setState({ health });
    if (useLive.getState().mode !== "live") await goLive();
  } catch {
    if (useLive.getState().mode !== "replay") await goReplay();
  }
}

export function startLive() {
  if (started) return;
  started = true;
  void checkHealth();
  window.setInterval(() => {
    if (!document.hidden) void checkHealth();
  }, 2000);
}

/** For tests: push an event as if it came from the socket. */
export function __emitForTest(e: GatewayEvent) {
  emit(e);
}

export async function fetchRequests(limit = 50): Promise<RequestRow[]> {
  if (useLive.getState().mode === "replay") {
    const f = await api<Fixture>("/replay/fixture.json");
    return f.snapshots.requests.slice(0, limit);
  }
  return api<RequestRow[]>(`/api/requests?limit=${limit}`);
}

export async function fetchTenants(): Promise<Tenant[]> {
  if (useLive.getState().mode === "replay") {
    const f = await api<Fixture>("/replay/fixture.json");
    return f.snapshots.tenants;
  }
  return api<Tenant[]>("/api/tenants");
}
