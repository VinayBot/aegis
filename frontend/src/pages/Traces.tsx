// Traces from Jaeger's query API, as an in-app list with a waterfall per request.
import { BlurWords } from "../components/fx";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { backendLabel, fmtAgo, fmtMs } from "../lib/format";
import { useLive } from "../lib/live";
import type { TraceView } from "../lib/types";

const FILTERS = [
  ["", "All"],
  ["ok", "OK"],
  ["exact", "Exact hit"],
  ["semantic", "Semantic hit"],
  ["coalesced", "Coalesced"],
  ["rejected", "Rejected"],
  ["blocked", "Blocked"],
  ["midstream_error", "Mid-stream error"],
] as const;

function Waterfall({ trace }: { trace: TraceView }) {
  const total = Math.max(1, trace.duration_ms);
  const depth = new Map<string, number>();
  for (const s of trace.spans) depth.set(s.span_id, s.parent_id ? (depth.get(s.parent_id) ?? 0) + 1 : 0);
  return (
    <div className="mt-4">
      <div className="grid grid-cols-[minmax(160px,240px)_1fr_70px] gap-2 text-xs text-ink-mute">
        <span>span</span><span>0 → {fmtMs(total)}</span><span className="text-right">duration</span>
      </div>
      <ol className="mt-2 space-y-1">
        {trace.spans.map((s) => {
          const genai = s.name.startsWith("chat ");
          const colour = s.error ? "var(--red)" : genai ? "var(--amber)" : s.name.includes("cache") ? "var(--violet)" : "var(--cyan)";
          return (
            <li key={s.span_id} className="grid grid-cols-[minmax(160px,240px)_1fr_70px] items-center gap-2 text-[0.8rem]">
              <span className="truncate text-ink-soft" style={{ paddingLeft: (depth.get(s.span_id) ?? 0) * 12 }} title={s.name}>{s.name}</span>
              <span className="relative h-3 rounded" style={{ background: "var(--space-1)" }}>
                <span className="absolute top-0 h-3 rounded" style={{ left: `${(s.start_ms / total) * 100}%`, width: `${Math.max(0.6, (s.duration_ms / total) * 100)}%`, background: colour }} />
              </span>
              <span className="text-right font-tabular text-ink-mute">{fmtMs(s.duration_ms)}</span>
            </li>
          );
        })}
      </ol>
      <details className="mt-4">
        <summary className="cursor-pointer text-sm text-ink-mute">GenAI attributes</summary>
        <pre className="mt-2 overflow-auto rounded-lg p-3 text-[0.75rem] text-ink-soft" style={{ background: "var(--space-1)" }}>
          {JSON.stringify(Object.fromEntries(trace.spans.flatMap((s) => Object.entries(s.tags).filter(([k]) => k.startsWith("gen_ai.")))), null, 2)}
        </pre>
      </details>
    </div>
  );
}

export default function Traces() {
  const mode = useLive((s) => s.mode);
  const [filter, setFilter] = useState("");
  const [traces, setTraces] = useState<TraceView[]>([]);
  const [jaeger, setJaeger] = useState("http://127.0.0.1:16686");
  const [selected, setSelected] = useState<TraceView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (mode !== "live") return;
    setLoading(true);
    try {
      const r = await api<{ traces: TraceView[]; jaeger_ui: string }>(`/api/traces?limit=40${filter ? `&outcome=${filter}` : ""}`);
      setTraces(r.traces);
      setJaeger(r.jaeger_ui);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [filter, mode]);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="mx-auto max-w-[1440px] px-[var(--gutter)] py-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-4xl font-semibold tracking-tight md:text-6xl"><BlurWords text="Traces" shimmer /></h1>
          <p className="mt-2 text-ink-soft">One OpenTelemetry trace per request, one span per stage, stored in Jaeger. GenAI spans follow the official <code>gen_ai.*</code> conventions.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn btn-quiet" onClick={() => void load()} disabled={loading || mode !== "live"}>{loading ? "Loading…" : "Refresh"}</button>
          <a className="btn btn-quiet" href={jaeger} target="_blank" rel="noreferrer">Open Jaeger</a>
        </div>
      </div>
      {mode !== "live" && <p className="glass mt-4 p-4 text-amber">Traces are read live from Jaeger; they are not part of the replay.</p>}
      <div role="radiogroup" aria-label="Filter by outcome" className="mt-6 flex flex-wrap gap-2">
        {FILTERS.map(([v, label]) => (
          <button key={v} type="button" role="radio" aria-checked={filter === v} onClick={() => setFilter(v)}
            className="min-h-9 rounded-full px-4 text-sm"
            style={filter === v ? { background: "var(--cyan)", color: "var(--space-0)", fontWeight: 600 } : { background: "var(--glass)", color: "var(--ink-soft)" }}>
            {label}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="glass mt-4 p-4 text-red">{error}</p>}
      <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(320px,440px)_1fr]">
        <ul className="glass max-h-[70vh] overflow-auto p-2" aria-label="Traces">
          {traces.length === 0 && !loading && <li className="p-3 text-sm text-ink-mute">No traces for this filter yet.</li>}
          {traces.map((t) => (
            <li key={t.trace_id}>
              <button type="button" onClick={() => setSelected(t)} aria-pressed={selected?.trace_id === t.trace_id}
                className="grid w-full grid-cols-[1fr_auto] gap-1 rounded-lg p-3 text-left text-sm hover:bg-[var(--glass)]"
                style={selected?.trace_id === t.trace_id ? { background: "var(--glass-strong)" } : undefined}>
                <span className="flex items-center gap-2 text-ink">
                  <span className={`status-dot s-${t.status === "ok" ? (t.cache === "miss" || t.cache === "bypass" ? "ok" : "cached") : t.status ? "error" : "unknown"}`} aria-hidden="true" />
                  {t.tenant ?? "?"} · {t.status ?? "in flight"} · {t.cache ?? "—"}
                </span>
                <span className="text-right font-tabular text-ink-soft">{fmtMs(t.duration_ms)}</span>
                <span className="text-xs text-ink-mute">{backendLabel(t.backend)} · {t.span_count} spans{t.hedged ? " · hedged" : ""}{t.failovers ? ` · ${t.failovers} failover` : ""}</span>
                <span className="text-right text-xs text-ink-mute">{fmtAgo(t.start)}</span>
              </button>
            </li>
          ))}
        </ul>
        <section className="glass p-5" aria-label="Trace detail">
          {selected ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="font-display text-xl font-semibold">{selected.root}</h2>
                <div className="flex gap-3 text-sm">
                  {selected.request_id && <Link className="text-cyan underline underline-offset-2" to={`/architecture?view=trace&request=${selected.request_id}`}>Replay on the graph</Link>}
                  <a className="text-cyan underline underline-offset-2" href={`${jaeger}/trace/${selected.trace_id}`} target="_blank" rel="noreferrer">Open in Jaeger</a>
                </div>
              </div>
              <p className="mt-1 text-xs text-ink-mute">trace {selected.trace_id}</p>
              <Waterfall trace={selected} />
            </>
          ) : (
            <p className="text-ink-mute">Select a trace to see its waterfall.</p>
          )}
        </section>
      </div>
    </div>
  );
}
