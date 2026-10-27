// The Chaos Lab: break the real gateway on purpose and watch it cope. Every control calls
// POST /api/chaos (dev-only, gated by AEGIS_CHAOS_ENABLED); every action is logged, reversible and
// reverts by itself. Guided scenarios run server-side and narrate themselves as events.
import { BlurWords } from "../components/fx";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import Chart from "../components/Chart";
import MiniGraph from "../components/MiniGraph";
import RequestFeed from "../components/RequestFeed";
import { api } from "../lib/api";
import { fmtClock, fmtMs } from "../lib/format";
import { useLive } from "../lib/live";
import { EASE_UI, SPRING, useReducedMotion } from "../lib/motion";
import type { ArchNode } from "../lib/types";

interface Fault { latency_ms: number; error_rate: number; killed: boolean; kill_mode: string | null; expires: Record<string, number> }
interface ChaosState { enabled: boolean; auto_revert_seconds: number; faults: Record<string, Fault>; log: { ts: number; action: string; backend: string; value: unknown; mode?: string }[] }
interface Scenario { id: string; title: string; about: string; running: boolean }

function useChaos() {
  const [state, setState] = useState<ChaosState | null>(null);
  const mode = useLive((s) => s.mode);
  useEffect(() => {
    if (mode !== "live") return;
    let alive = true;
    const load = () => api<ChaosState>("/api/chaos").then((s) => alive && setState(s)).catch(() => undefined);
    void load();
    const t = window.setInterval(load, 1500);
    return () => { alive = false; window.clearInterval(t); };
  }, [mode]);
  return [state, setState] as const;
}

function Switch({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  const reduced = useReducedMotion();
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
      className="relative h-14 w-28 rounded-full p-1.5 transition-colors disabled:opacity-50"
      style={{ background: on ? "var(--red)" : "var(--space-1)", boxShadow: `inset 0 0 0 1px ${on ? "var(--red)" : "var(--glass-edge-hi)"}${on ? ", var(--glow-red)" : ""}` }}>
      <motion.span layout transition={reduced ? { duration: 0 } : SPRING} className="block h-11 w-11 rounded-full"
        style={{ background: "var(--ink)", marginLeft: on ? "auto" : 0, boxShadow: "0 4px 14px var(--shade)" }} />
      <span className="sr-only">{on ? "killed" : "running"}</span>
    </button>
  );
}

function Countdown({ at }: { at?: number }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  if (!at) return null;
  return <span className="font-tabular text-xs text-ink-mute">auto-reverts in {Math.max(0, Math.round(at - Date.now() / 1000))} s</span>;
}

function BackendPanel({ node, fault, apply, disabled }: { node: ArchNode; fault?: Fault; apply: (action: string, value: unknown) => Promise<void>; disabled: boolean }) {
  const name = node.id.slice(8);
  const [latency, setLatency] = useState(fault?.latency_ms ?? 0);
  const [errors, setErrors] = useState(Math.round((fault?.error_rate ?? 0) * 100));
  useEffect(() => setLatency(fault?.latency_ms ?? 0), [fault?.latency_ms]);
  useEffect(() => setErrors(Math.round((fault?.error_rate ?? 0) * 100)), [fault?.error_rate]);
  const breaker = node.breaker?.state ?? "closed";
  return (
    <section className="glass p-5" aria-labelledby={`p-${name}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 id={`p-${name}`} className="font-display text-xl font-semibold">{node.label}</h3>
          <p className="text-sm text-ink-mute">{String(node.config?.model ?? "")}</p>
          <p className="mt-2 flex items-center gap-2 text-sm">
            <span className={`status-dot s-${breaker === "open" ? "down" : breaker === "half_open" ? "degraded" : node.status}`} aria-hidden="true" />
            <span className="text-ink-soft">{node.status} · breaker {breaker.replace("_", "-")}{breaker === "open" && node.breaker ? ` (${Math.ceil(node.breaker.retry_after_s)} s)` : ""}</span>
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Switch on={Boolean(fault?.killed)} label={`Kill ${node.label}`} disabled={disabled} onChange={(v) => void apply("kill", v)} />
          <span className="text-xs text-ink-mute">{fault?.killed ? (fault.kill_mode === "sigstop" ? "frozen (SIGSTOP)" : "refusing connections") : "kill switch"}</span>
          <Countdown at={fault?.expires?.kill} />
        </div>
      </div>
      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        <label className="block">
          <span className="flex justify-between text-sm text-ink-soft"><span>Added latency</span><span className="font-tabular text-ink">{fmtMs(latency)}</span></span>
          <input type="range" min={0} max={6000} step={250} value={latency} disabled={disabled} onChange={(e) => setLatency(Number(e.target.value))}
            onPointerUp={() => void apply("latency", latency)} onKeyUp={() => void apply("latency", latency)} className="mt-2 w-full accent-[var(--amber)]" />
          <Countdown at={fault?.expires?.latency} />
        </label>
        <label className="block">
          <span className="flex justify-between text-sm text-ink-soft"><span>Injected error rate</span><span className="font-tabular text-ink">{errors}%</span></span>
          <input type="range" min={0} max={100} step={5} value={errors} disabled={disabled} onChange={(e) => setErrors(Number(e.target.value))}
            onPointerUp={() => void apply("error_rate", errors / 100)} onKeyUp={() => void apply("error_rate", errors / 100)} className="mt-2 w-full accent-[var(--red)]" />
          <Countdown at={fault?.expires?.error_rate} />
        </label>
      </div>
    </section>
  );
}

function Narration({ runId }: { runId: string | null }) {
  const recent = useLive((s) => s.recent);
  const reduced = useReducedMotion();
  const steps = recent.filter((e) => e.type === "scenario.step" && e.run_id === runId);
  if (!runId) return <p className="text-sm text-ink-mute">Pick a scenario. Each step is narrated here while the graph and charts show it happening.</p>;
  return (
    <ol className="space-y-2" aria-live="polite">
      <AnimatePresence initial={false}>
        {steps.map((e) => (
          <motion.li key={e.seq} initial={reduced ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, ease: EASE_UI }}
            className="flex gap-3 text-sm">
            <span className="w-16 shrink-0 font-tabular text-ink-mute">{fmtClock(e.ts)}</span>
            <span className={e.status === "failed" ? "text-red" : e.status === "done" ? "text-green" : "text-ink"}>{String(e.text)}</span>
          </motion.li>
        ))}
      </AnimatePresence>
    </ol>
  );
}

export default function ChaosLab() {
  const [chaos, setChaos] = useChaos();
  const arch = useLive((s) => s.architecture);
  const metrics = useLive((s) => s.metrics);
  const mode = useLive((s) => s.mode);
  const recent = useLive((s) => s.recent);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const backends = useMemo(() => arch?.nodes.filter((n) => n.kind === "backend" && n.status !== "disabled") ?? [], [arch]);
  const disabled = mode !== "live" || !chaos?.enabled;

  useEffect(() => {
    if (mode !== "live") return;
    api<Scenario[]>("/api/scenarios").then(setScenarios).catch(() => undefined);
  }, [mode]);

  // Follow the most recent scenario even if it was started elsewhere (e.g. a second tab).
  useEffect(() => {
    const last = [...recent].reverse().find((e) => e.type === "scenario.step");
    if (last && !runId) setRunId(String(last.run_id));
  }, [recent, runId]);

  const apply = async (backend: string, action: string, value: unknown) => {
    setError(null);
    try {
      const r = await api<{ state: ChaosState }>("/api/chaos", { method: "POST", json: { backend, action, value } });
      setChaos(r.state);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const start = async (id: string) => {
    setError(null);
    try {
      const r = await api<{ run_id: string }>(`/api/scenarios/${id}`, { method: "POST" });
      setRunId(r.run_id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const s = metrics?.series;
  const t = (s?.t ?? []) as number[];

  return (
    <div className="mx-auto max-w-[1600px] px-[var(--gutter)] py-8">
      <h1 className="font-display text-4xl font-semibold tracking-tight md:text-6xl"><BlurWords text="Chaos Lab" shimmer /></h1>
      <p className="mt-2 max-w-[72ch] text-ink-soft">Freeze a model, slow it down or make it fail, and watch hedging, failover and the circuit breakers respond. Every action is real, logged, reversible, and reverts by itself after {chaos?.auto_revert_seconds ?? 120} s.</p>
      {mode !== "live" && <p className="glass mt-4 p-4 text-amber">The Chaos Lab needs the live gateway. In replay mode the controls are off.</p>}
      {mode === "live" && chaos && !chaos.enabled && <p className="glass mt-4 p-4 text-amber">Chaos is disabled on this gateway. Start it with <code>AEGIS_CHAOS_ENABLED=1</code> (make dev does this).</p>}
      {error && <p role="alert" className="glass mt-4 p-4 text-red">{error}</p>}

      <div className="mt-8 grid gap-4 xl:grid-cols-[1.15fr_1fr]">
        <div className="flex flex-col gap-4">
          {backends.map((n) => (
            <BackendPanel key={n.id} node={n} fault={chaos?.faults[n.id.slice(8)]} disabled={disabled} apply={(a, v) => apply(n.id.slice(8), a, v)} />
          ))}
          <section aria-labelledby="scen" className="glass p-5">
            <h2 id="scen" className="font-display text-xl font-semibold">Guided scenarios</h2>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {scenarios.map((sc) => (
                <button key={sc.id} type="button" disabled={mode !== "live" || sc.running} onClick={() => void start(sc.id)}
                  className="rounded-[var(--radius-sm)] p-3 text-left transition-colors hover:bg-[var(--glass-strong)] disabled:opacity-50" style={{ background: "var(--glass)" }}>
                  <span className="block font-semibold text-ink">{sc.title}</span>
                  <span className="mt-1 block text-[0.82rem] leading-snug text-ink-mute">{sc.about}</span>
                </button>
              ))}
            </div>
          </section>
        </div>
        <div className="flex flex-col gap-4">
          <section aria-labelledby="narr" className="glass p-5">
            <div className="flex items-center justify-between">
              <h2 id="narr" className="font-display text-xl font-semibold">What is happening</h2>
              <Link to="/architecture" className="text-sm text-cyan underline underline-offset-2">Watch it on the graph</Link>
            </div>
            <div className="mt-3"><Narration runId={runId} /></div>
          </section>
          <MiniGraph />
          {s && (
            <>
              <Chart title="Time to first token, p95" t={t} height={160} series={[{ name: "TTFT p95", data: s.ttft_p95, color: "--cyan", area: true }]} format={(v) => fmtMs(v)} />
              <Chart title="Model calls by backend" t={t} height={160} series={(metrics?.backends ?? []).map((b, i) => ({ name: b, data: s[`served_${b}`] ?? [], color: i === 0 ? "--cyan" : "--violet", type: "bar" as const, stack: "x" }))} format={(v) => String(Math.round(v))} />
            </>
          )}
          <section aria-labelledby="log" className="glass p-5">
            <div className="flex items-center justify-between">
              <h2 id="log" className="font-display text-xl font-semibold">Action log</h2>
              <button type="button" className="btn btn-quiet min-h-9 px-3 text-sm" disabled={disabled} onClick={() => void api("/api/chaos/revert", { method: "POST" }).then((s2) => setChaos(s2 as ChaosState))}>Revert everything</button>
            </div>
            <ul className="mt-3 max-h-48 space-y-1 overflow-auto text-[0.82rem]">
              {(chaos?.log ?? []).length === 0 && <li className="text-ink-mute">No chaos yet.</li>}
              {(chaos?.log ?? []).map((l, i) => (
                <li key={i} className="flex gap-3 text-ink-soft">
                  <span className="w-16 shrink-0 font-tabular text-ink-mute">{fmtClock(l.ts)}</span>
                  <span>{l.action} · {l.backend}{l.value !== null && l.value !== undefined && typeof l.value !== "boolean" ? ` · ${String(l.value)}` : ""}{l.mode ? ` · ${l.mode}` : ""}</span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
      <div className="mt-4"><RequestFeed limit={10} /></div>
    </div>
  );
}
