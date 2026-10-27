// The dashboard: dense but calm. KPIs are type with a quiet sparkline; colour goes on data and on
// change (good or bad), not on chrome. Every value is from /api/metrics, /api/tenants or events.
import { BlurWords } from "../components/fx";
import { useEffect, useMemo, useState } from "react";
import Chart, { type Series } from "../components/Chart";
import HoverRobot, { useSystemMood } from "../components/HoverRobot";
import LiveNumber from "../components/LiveNumber";
import Mission from "../components/Mission";
import RequestFeed from "../components/RequestFeed";
import { fmtInt, fmtMs, fmtPct, fmtUsd } from "../lib/format";
import { fetchTenants, useLive } from "../lib/live";
import type { Metrics, Tenant } from "../lib/types";

function Sparkline({ data, color }: { data: (number | null)[]; color: string }) {
  const vals = data.map((v) => v ?? 0);
  const max = Math.max(1e-9, ...vals);
  const pts = vals.map((v, i) => `${(i / Math.max(1, vals.length - 1)) * 100},${28 - (v / max) * 26}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="mt-3 h-8 w-full" aria-hidden="true">
      <polyline points={`0,30 ${pts} 100,30`} style={{ fill: `var(${color})` }} opacity="0.14" stroke="none" />
      <polyline points={pts} fill="none" style={{ stroke: `var(${color})`, filter: `drop-shadow(0 0 4px var(${color}))` }} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ label, value, prev, format, spark, color, betterWhen, sub }: {
  label: string; value: number | null | undefined; prev: number | null | undefined; format: (n: number) => string;
  spark: (number | null)[]; color: string; betterWhen: "up" | "down"; sub?: string;
}) {
  let delta: { text: string; good: boolean } | null = null;
  if (value !== null && value !== undefined && prev !== null && prev !== undefined && prev !== 0) {
    const d = (value - prev) / Math.abs(prev);
    if (Math.abs(d) >= 0.01) delta = { text: `${d > 0 ? "+" : ""}${(d * 100).toFixed(0)}% vs previous minute`, good: betterWhen === "up" ? d > 0 : d < 0 };
  }
  return (
    <div className="glass glow-border p-5">
      <p className="text-sm text-ink-mute">{label}</p>
      <p className="mt-1 font-display text-4xl font-semibold tracking-tight">
        <LiveNumber value={value} format={format} className="gradient-num" />
      </p>
      <p className="mt-1 min-h-5 text-xs" style={{ color: delta ? (delta.good ? "var(--green)" : "var(--red)") : "var(--ink-faint)" }}>
        {delta ? delta.text : sub ?? "no change"}
      </p>
      <Sparkline data={spark} color={color} />
    </div>
  );
}

function BreakerTimeline({ metrics }: { metrics: Metrics }) {
  const start = metrics.series.t[0] ?? metrics.server_time - metrics.window_s;
  const end = metrics.server_time;
  const backends = Object.keys(metrics.breakers);
  const span = Math.max(1, end - start);
  return (
    <figure className="glass p-4">
      <figcaption className="font-semibold text-ink">Breaker state per backend</figcaption>
      <div className="mt-4 space-y-3">
        {backends.map((b) => {
          const hist = metrics.breaker_history.filter((h) => h.backend === b);
          // reconstruct segments: state before the window = last change before it, else closed
          const before = [...hist].reverse().find((h) => h.ts < start);
          let state = before?.state ?? "closed";
          let cursor = start;
          const segs: { from: number; to: number; state: string }[] = [];
          for (const h of hist.filter((x) => x.ts >= start)) {
            segs.push({ from: cursor, to: h.ts, state });
            cursor = h.ts;
            state = h.state;
          }
          segs.push({ from: cursor, to: end, state: metrics.breakers[b]?.state ?? state });
          return (
            <div key={b} className="grid grid-cols-[120px_1fr] items-center gap-3 text-sm">
              <span className="truncate text-ink-soft">{b}</span>
              <div className="relative h-4 overflow-hidden rounded" style={{ background: "var(--space-1)" }} role="img"
                aria-label={`${b}: ${segs.map((s) => `${s.state} for ${Math.round(s.to - s.from)} s`).join(", ")}`}>
                {segs.map((s, i) => (
                  <span key={i} className="absolute top-0 h-full" title={`${s.state} ${Math.round(s.to - s.from)} s`}
                    style={{ left: `${((s.from - start) / span) * 100}%`, width: `${Math.max(0.4, ((s.to - s.from) / span) * 100)}%`, background: s.state === "open" ? "var(--red)" : s.state === "half_open" ? "var(--amber)" : "var(--cyan-wash)" }} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-ink-mute">Last {Math.round(span / 60)} min · <span className="tone-broken">open</span>, <span className="tone-degraded">half-open</span>, faint cyan closed.</p>
    </figure>
  );
}

function Tenants() {
  const [rows, setRows] = useState<Tenant[]>([]);
  const mode = useLive((s) => s.mode);
  useEffect(() => {
    let alive = true;
    const load = () => fetchTenants().then((r) => alive && setRows(r)).catch(() => undefined);
    void load();
    const t = window.setInterval(load, 4000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [mode]);
  return (
    <section aria-labelledby="tenants" className="glass overflow-x-auto p-4">
      <h2 id="tenants" className="font-semibold text-ink">Tenants this month</h2>
      <table className="mt-3 w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="text-ink-mute">
            <th className="pb-2 font-medium">tenant</th>
            <th className="pb-2 text-right font-medium">requests</th>
            <th className="pb-2 text-right font-medium">tokens</th>
            <th className="pb-2 pl-6 font-medium">budget used (est. cloud equivalent)</th>
            <th className="pb-2 text-right font-medium">429s</th>
            <th className="pb-2 text-right font-medium">saved</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const used = r.spent_est_usd / r.budget.hard;
            const soft = r.budget.soft / r.budget.hard;
            const tone = r.spent_est_usd >= r.budget.hard ? "var(--red)" : r.spent_est_usd >= r.budget.soft ? "var(--amber)" : "var(--cyan)";
            return (
              <tr key={r.api_key} className="text-ink-soft">
                <td className="py-2 pr-3 text-ink">{r.tenant}</td>
                <td className="py-2 text-right font-tabular">{fmtInt(r.requests)}</td>
                <td className="py-2 text-right font-tabular">{fmtInt(r.tokens)}</td>
                <td className="py-2 pl-6">
                  <div className="flex items-center gap-3">
                    <div className="relative h-2 w-40 rounded-full" style={{ background: "var(--space-1)" }} role="meter" aria-valuemin={0} aria-valuemax={r.budget.hard} aria-valuenow={r.spent_est_usd} aria-label={`${r.tenant} budget`}>
                      <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(100, used * 100)}%`, background: tone }} />
                      <span className="absolute -top-1 h-4 w-0.5" style={{ left: `${soft * 100}%`, background: "var(--amber)" }} title="soft limit" />
                    </div>
                    <span className="font-tabular text-xs">{fmtUsd(r.spent_est_usd)} / {fmtUsd(r.budget.hard)}</span>
                  </div>
                </td>
                <td className="py-2 text-right font-tabular" style={{ color: r.rate_limited ? "var(--red)" : undefined }}>{fmtInt(r.rate_limited)}</td>
                <td className="py-2 text-right font-tabular">{fmtUsd(r.saved_est_usd)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-ink-mute">Amber tick: soft limit (warning header). Bar end: hard limit (requests blocked).</p>
    </section>
  );
}

export default function ControlRoom() {
  const metrics = useLive((s) => s.metrics);
  const mood = useSystemMood();
  const [key, setKey] = useState("aegis-demo-key");
  const s = metrics?.series;
  const t = (s?.t ?? []) as number[];
  const k = metrics?.kpis;

  const charts = useMemo(() => {
    if (!s || !metrics) return null;
    const stageColors = ["--cyan", "--cyan-deep", "--blue", "--violet", "--violet-deep", "--slate"];
    const stages: Series[] = metrics.stages.map((st, i) => ({ name: st.replace("_", " "), data: s[`stage_${st}`] ?? [], color: stageColors[i % stageColors.length] }));
    const cache: Series[] = [
      { name: "exact", data: s.cache_exact, color: "--violet", type: "bar", stack: "c" },
      { name: "semantic", data: s.cache_semantic, color: "--violet-deep", type: "bar", stack: "c" },
      { name: "miss", data: s.cache_miss, color: "--ink-faint", type: "bar", stack: "c" },
    ];
    const split: Series[] = metrics.backends.map((b, i) => ({ name: b, data: s[`served_${b}`] ?? [], color: i === 0 ? "--cyan" : i === 1 ? "--violet" : "--slate", type: "bar", stack: "b" }));
    const hedges: Series[] = [
      { name: "hedges fired", data: s.hedges_fired, color: "--cyan", type: "bar" },
      { name: "won by hedge", data: s.hedges_won_by_hedge, color: "--violet", type: "bar" },
      { name: "failovers", data: s.failovers, color: "--red", type: "bar" },
    ];
    return { stages, cache, split, hedges };
  }, [s, metrics]);

  const limit = metrics?.limits[key]?.tpm;
  const tpmNow = metrics?.tokens_per_min_by_key[key] ?? 0;

  return (
    <div className="mx-auto max-w-[1600px] px-[var(--gutter)] py-8">
      <div className="flex flex-wrap items-center justify-between gap-6">
        <div>
          <h1 className="font-display text-4xl font-semibold tracking-tight md:text-6xl"><BlurWords text="Control Room" shimmer /></h1>
          <p className="mt-2 text-ink-soft">The last five minutes of the gateway, in 5-second steps. Dollar values are estimated cloud equivalents; local inference has no bill.</p>
        </div>
        <div className="flex items-center gap-3">
          <p className="text-right text-sm text-ink-soft">
            AEGIS looks <strong className="text-ink">{mood === "happy" ? "content" : mood === "alarmed" ? "alarmed: a breaker is open" : mood === "concerned" ? "concerned: something is degraded" : "asleep"}</strong>
          </p>
          <HoverRobot interactive={false} className="w-28" sizes="112px" />
        </div>
      </div>

      <div className="mt-8"><Mission /></div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Requests per second (last minute)" value={k?.current.rps} prev={k?.previous.rps} format={(n) => n.toFixed(2)} spark={s?.rps ?? []} color="--cyan" betterWhen="up" />
        <Kpi label="Time to first token, p95" value={k?.current.ttft_p95_ms} prev={k?.previous.ttft_p95_ms} format={fmtMs} spark={s?.ttft_p95 ?? []} color="--blue" betterWhen="down" sub="no model calls this minute" />
        <Kpi label="Cache hit rate" value={k?.current.hit_rate} prev={k?.previous.hit_rate} format={(n) => fmtPct(n)} spark={s?.hit_rate ?? []} color="--violet" betterWhen="up"
          sub={k?.current.hit_rate !== null && k ? `exact ${fmtPct(k.current.hit_rate_exact)} · semantic ${fmtPct(k.current.hit_rate_semantic)}` : "no lookups this minute"} />
        <Kpi label="Saved, est. cloud equivalent" value={metrics?.totals.saved_est_usd} prev={null} format={fmtUsd} spark={s?.saved ?? []} color="--cyan-hi" betterWhen="up" sub="all time, by cache and coalescing" />
      </div>

      {charts && metrics && s ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          <Chart title="Latency by stage (average)" t={t} series={charts.stages} format={(v) => fmtMs(v)} />
          <Chart title="Cache results per 5 s" t={t} series={charts.cache} format={(v) => String(Math.round(v))} />
          <div>
            <Chart title={`Tokens per minute vs limit · ${metrics.limits[key]?.tenant ?? key}`} t={t} series={[{ name: "all keys", data: s.tokens_per_min, color: "--cyan", area: true }]} format={(v) => fmtInt(v)}
              markLine={limit ? { value: limit, label: `${fmtInt(limit)} tpm limit (${key})`, color: "--red" } : undefined} />
            <label className="mt-2 flex items-center gap-2 text-sm text-ink-mute">
              Limit shown for
              <select value={key} onChange={(e) => setKey(e.target.value)} className="min-h-9 rounded-[var(--radius-sm)] px-2 text-ink" style={{ background: "var(--space-1)" }}>
                {Object.entries(metrics.limits).map(([k2, v]) => <option key={k2} value={k2}>{v.tenant}</option>)}
              </select>
              <span className="font-tabular">now {fmtInt(tpmNow)} / {fmtInt(limit)} tpm</span>
            </label>
          </div>
          <BreakerTimeline metrics={metrics} />
          <Chart title="Hedging and failover" t={t} series={charts.hedges} format={(v) => String(Math.round(v))} />
          <Chart title="Traffic split by backend (model calls)" t={t} series={charts.split} format={(v) => String(Math.round(v))} />
        </div>
      ) : (
        <p className="mt-10 text-ink-mute">Waiting for /api/metrics…</p>
      )}

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Tenants />
        <RequestFeed />
      </div>
    </div>
  );
}
