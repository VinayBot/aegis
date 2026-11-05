// Shapes served by the gateway's control plane (backend/aegis/api/control.py).

export type Status = "up" | "down" | "degraded" | "disabled" | "starting" | "unknown";
export type BreakerState = "closed" | "open" | "half_open";

export interface GatewayEvent {
  seq: number;
  ts: number;
  type: string;
  request_id: string | null;
  [key: string]: unknown;
}

export interface HealthComponent {
  status: Status;
  ok: boolean;
  detail: string;
  latency_ms: number;
  checked_at: number;
  breaker?: BreakerState;
  in_flight?: number;
  capacity?: number;
}

export interface Health {
  status: "up" | "degraded" | "down";
  uptime_s: number;
  redis_ok: boolean;
  chaos_enabled: boolean;
  components: Record<string, HealthComponent>;
}

export interface ArchNode {
  id: string;
  label: string;
  group: string;
  kind: "client" | "stage" | "side" | "store" | "backend";
  status: Status;
  about: string;
  source: string;
  stats: Record<string, number | null>;
  config?: Record<string, unknown>;
  breaker?: { state: BreakerState; retry_after_s: number; open_seconds: number; trips: number; window_failure_rate?: number | null; failure_rate_threshold?: number };
  /** what the serving engine reports about itself (vLLM /metrics) */
  engine?: { running: number; waiting: number; kv_cache_usage: number | null; prefix_cache_hit_rate: number | null } | null;
  saturated?: boolean;
  slots?: { capacity: number; in_flight: number } | null;
}

export interface ArchEdge {
  id: string;
  source: string;
  target: string;
  kind: "flow" | "hedge" | "cache" | "coalesce" | "response" | "side";
  status: "ok" | "broken";
}

export interface Architecture {
  nodes: ArchNode[];
  edges: ArchEdge[];
  groups: string[];
  config_reloads: number;
}

export interface Kpi {
  rps: number;
  ttft_p95_ms: number | null;
  hit_rate: number | null;
  hit_rate_exact: number | null;
  hit_rate_semantic: number | null;
  saved_est_usd: number;
  requests: number;
}

export interface Totals {
  requests: number;
  served: number;
  cache_exact: number;
  cache_semantic: number;
  coalesced: number;
  hedged: number;
  failed_over: number;
  rate_limited: number;
  tokens_in: number;
  tokens_out: number;
  cost_est_usd: number;
  saved_est_usd: number;
  breaker_trips: number;
}

export interface Metrics {
  window_s: number;
  step_s: number;
  backends: string[];
  stages: string[];
  series: Record<string, (number | null)[]>;
  kpis: { window_s: number; current: Kpi; previous: Kpi };
  breaker_history: { ts: number; backend: string; state: BreakerState; reason: string }[];
  breakers: Record<string, { state: BreakerState; retry_after_s: number }>;
  totals: Totals;
  tokens_per_min_by_key: Record<string, number>;
  limits: Record<string, { tenant: string; tpm: number }>;
  server_time: number;
}

export interface RequestRow {
  request_id: string;
  ts: number;
  tenant: string;
  model: string;
  status: string;
  http_status: number;
  cache: string;
  backend: string | null;
  coalesced: boolean | number;
  hedged: boolean | number;
  failovers: number;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  ttft_ms: number | null;
  saved_est_usd: number;
  cost_est_usd: number;
  trace_id: string;
  error: string | null;
  similarity?: number | null;
  usage_estimated?: boolean | number;
  redactions?: Record<string, number> | null;
}

export interface Summary extends Omit<RequestRow, "ts"> {
  hedge_won: boolean;
  hedge_winner: string | null;
  failover_detail: { backend: string; reason: string; detail: string }[];
  cost_label: string;
  stages: { stage: string; ms: number; outcome: string }[];
  coalesced_with: string | null;
  client: string;
}

export interface Tenant {
  tenant: string;
  key_id: string;
  feedback_up?: number;
  feedback_down?: number;
  tpm: number;
  budget: { soft: number; hard: number };
  spent_est_usd: number;
  requests: number;
  tokens: number;
  rate_limited: number;
  saved_est_usd: number;
  cost_est_usd: number;
}

export interface TraceSpan {
  span_id: string;
  parent_id: string | null;
  name: string;
  start_ms: number;
  duration_ms: number;
  error: boolean;
  tags: Record<string, unknown>;
}

export interface TraceView {
  trace_id: string;
  start: number;
  duration_ms: number;
  root: string;
  request_id?: string;
  status?: string;
  cache?: string;
  backend?: string;
  tenant?: string;
  hedged?: boolean;
  failovers?: number;
  span_count: number;
  spans: TraceSpan[];
  jaeger_url?: string;
}
