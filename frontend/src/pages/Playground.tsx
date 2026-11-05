// Send requests by hand and see exactly what the gateway did with each one.
import { BlurWords } from "../components/fx";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, streamChat, wsChat, type StreamResult } from "../lib/api";
import { backendLabel, fmtMs, fmtUsd } from "../lib/format";
import { useLive } from "../lib/live";
import { EASE_UI, useReducedMotion } from "../lib/motion";

const KEYS = [
  ["prahari-demo-key", "demo"],
  ["veritas-key", "veritas"],
  ["nexus-key", "nexus"],
  ["prahari-flood-key", "flood-test (1,500 tpm)"],
  ["prahari-budget-key", "budget-test (tiny budget)"],
] as const;

/** Deterministic paraphrases, so "rephrase and resend" exercises the semantic cache honestly
 *  (no model call to rephrase, and the same input always gives the same rephrasing). */
export function rephrase(prompt: string, n: number): string {
  const variants = [
    (p: string) => p.toLowerCase().replace(/[?.!]+$/, ""),
    (p: string) => p.replace(/^what is\b/i, "What's").replace(/^how do\b/i, "How do you"),
    (p: string) => `${p.replace(/[?.!]+$/, "")}, please?`,
    (p: string) => p.replace(/\bthe\b/gi, "").replace(/\s+/g, " ").trim(),
  ];
  return variants[n % variants.length](prompt);
}

interface Run {
  id: number;
  prompt: string;
  text: string;
  result: StreamResult | null;
  started: number;
  headers: Record<string, string>;
  key: string;
}

/** Thumbs up/down on an answer: stored per tenant, metered, and a Langfuse score on its trace. */
function Rate({ run }: { run: Run }) {
  const [sent, setSent] = useState<"up" | "down" | null>(null);
  const requestId = run.headers["x-prahari-request-id"];
  if (!run.result || run.result.status !== 200 || !requestId) return null;
  const rate = async (rating: "up" | "down") => {
    await api("/v1/feedback", { method: "POST", json: { request_id: requestId, rating }, headers: { authorization: `Bearer ${run.key}` } }).catch(() => undefined);
    setSent(rating);
  };
  return (
    <div className="mt-2 flex items-center gap-2 text-sm text-ink-soft">
      <span>Was this answer good?</span>
      <button type="button" className="btn btn-quiet min-h-9 px-3" aria-pressed={sent === "up"} aria-label="Good answer" onClick={() => void rate("up")}>👍</button>
      <button type="button" className="btn btn-quiet min-h-9 px-3" aria-pressed={sent === "down"} aria-label="Bad answer" onClick={() => void rate("down")}>👎</button>
      {sent && <span className="text-xs text-ink-mute">Recorded on this request's trace.</span>}
    </div>
  );
}

function TraceChip({ run }: { run: Run }) {
  const s = run.result?.summary as Record<string, unknown> | null | undefined;
  const h = run.headers;
  if (!run.result) return <p className="text-sm text-ink-mute">streaming…</p>;
  const stopped = (run.result.error as { code?: string } | null)?.code === "cancelled";
  if (stopped) {
    return (
      <p className="text-sm text-amber">
        Stopped mid-answer over {h["x-transport"] === "websocket" ? "the WebSocket" : "SSE"}: the gateway cancelled the model call and freed its slot.
      </p>
    );
  }
  if (run.result.status !== 200) {
    const err = run.result.error as { message?: string; code?: string } | null;
    return (
      <p className="text-sm text-red">
        HTTP {run.result.status} · {err?.code ?? ""} {err?.message ?? ""}{h["retry-after"] ? ` · Retry-After ${h["retry-after"]} s` : ""}
      </p>
    );
  }
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-ink-soft">
      <span>cache: <strong className={h["x-cache"] === "miss" || h["x-cache"] === "bypass" ? "text-ink" : "text-violet"}>{h["x-cache"]}</strong>{h["x-prahari-similarity"] ? ` (${Number(h["x-prahari-similarity"]).toFixed(3)})` : ""}</span>
      <span>backend: <strong className="text-ink">{backendLabel(h["x-prahari-backend"])}</strong></span>
      {h["x-transport"] === "websocket" && <span>over <strong className="text-ink">WebSocket</strong></span>}
      {s?.hedged ? <span className="text-amber">hedged</span> : null}
      {Number(s?.failovers) > 0 && <span className="text-amber">{String(s?.failovers)} failover(s)</span>}
      <span>{Number(s?.tokens_in ?? 0) + Number(s?.tokens_out ?? 0)} tokens{s?.usage_estimated ? " (estimated ±30%)" : ""}</span>
      <span>{fmtMs(Number(s?.latency_ms))}</span>
      {s?.ttft_ms ? <span>TTFT {fmtMs(Number(s.ttft_ms))}</span> : null}
      <span>est. saved {fmtUsd(Number(s?.saved_est_usd ?? 0))}</span>
      {h["x-prahari-budget-warning"] && <span className="text-amber">budget warning</span>}
    </p>
  );
}

function MiniTrace({ run }: { run: Run }) {
  const s = run.result?.summary as { stages?: { stage: string; ms: number; outcome: string }[]; request_id?: string; trace_id?: string } | null | undefined;
  const reduced = useReducedMotion();
  const stages = s?.stages ?? [];
  const max = Math.max(1, ...stages.map((x) => x.ms));
  return (
    <div>
      <ol className="space-y-1.5">
        {stages.map((st, i) => (
          <motion.li key={st.stage + i} initial={reduced ? false : { opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i, 10) * 0.03, duration: 0.2, ease: EASE_UI }}
            className="grid grid-cols-[110px_1fr_64px] items-center gap-2 text-[0.8rem]">
            <span className="text-ink-soft">{st.stage.replace("_", " ")}</span>
            <span className="h-1.5 rounded-full" style={{ width: `${Math.max(3, (st.ms / max) * 100)}%`, background: st.outcome === "hit" ? "var(--violet)" : st.outcome === "rejected" || st.outcome === "blocked" ? "var(--red)" : st.outcome === "ok" || st.outcome === "leader" || st.outcome === "queued" ? "var(--cyan)" : "var(--amber)" }} />
            <span className="text-right font-tabular text-ink-mute">{fmtMs(st.ms)}</span>
          </motion.li>
        ))}
      </ol>
      {s?.request_id && (
        <p className="mt-3 flex flex-wrap gap-3 text-sm">
          <Link className="text-cyan underline underline-offset-2" to={`/architecture?view=detailed&request=${s.request_id}`}>Watch this request's journey</Link>
          <a className="text-cyan underline underline-offset-2" href={`http://127.0.0.1:16686/trace/${s.trace_id}`} target="_blank" rel="noreferrer">Open in Jaeger</a>
        </p>
      )}
    </div>
  );
}

export default function Playground() {
  const mode = useLive((s) => s.mode);
  const [models, setModels] = useState<string[]>(["prahari-chat"]);
  const [model, setModel] = useState("prahari-chat");
  const [prompt, setPrompt] = useState("What is the refund policy for annual plans?");
  const [temperature, setTemperature] = useState(0);
  const [key, setKey] = useState<string>(KEYS[0][0]);
  const [noCache, setNoCache] = useState(false);
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false);
  const [rephrases, setRephrases] = useState(0);
  const [batch, setBatch] = useState<{ label: string; results: { status: number; cache: string }[] } | null>(null);
  const [transport, setTransport] = useState<"sse" | "ws">("sse");
  const stopper = useRef<AbortController | null>(null);

  useEffect(() => {
    if (mode !== "live") return;
    api<{ data: { id: string }[] }>("/v1/models").then((r) => setModels(r.data.map((m) => m.id))).catch(() => undefined);
  }, [mode]);

  const one = async (p: string, id: number) => {
    setRuns((r) => [{ id, prompt: p, text: "", result: null, started: Date.now(), headers: {}, key }, ...r].slice(0, 6));
    stopper.current = new AbortController();
    const chat = transport === "ws" ? wsChat : streamChat;
    const res = await chat({ model, temperature, max_tokens: 160, messages: [{ role: "user", content: p }] }, {
      key, noCache, signal: stopper.current.signal,
      onHeaders: (h) => setRuns((r) => r.map((x) => (x.id === id ? { ...x, headers: h } : x))),
      onToken: (tok) => setRuns((r) => r.map((x) => (x.id === id ? { ...x, text: x.text + tok } : x))),
    });
    setRuns((r) => r.map((x) => (x.id === id ? { ...x, result: res } : x)));
    return res;
  };

  const send = async (p = prompt) => {
    setBusy(true);
    setBatch(null);
    try { await one(p, Date.now()); } finally { setBusy(false); }
  };
  const sendTen = async () => {
    setBusy(true);
    setBatch(null);
    try {
      // Fired by the gateway, not from here: a browser opens at most six streams per host, so the
      // last four would queue behind the first answer instead of joining it.
      const { results } = await api<{ results: { status: number; x_cache: string | null }[] }>("/api/playground/duplicate", {
        method: "POST",
        json: { n: 10, prompt, model, key, temperature, no_cache: noCache },
      });
      setBatch({ label: "10 identical requests at once", results: results.map((r) => ({ status: r.status, cache: r.x_cache ?? "" })) });
    } finally { setBusy(false); }
  };
  const resendRephrased = async () => {
    const p = rephrase(prompt, rephrases);
    setRephrases((n) => n + 1);
    await send(p);
  };
  const disabled = busy || mode !== "live";

  return (
    <div className="mx-auto max-w-[1440px] px-[var(--gutter)] py-8">
      <h1 className="font-display text-4xl font-semibold tracking-tight md:text-6xl"><BlurWords text="Playground" shimmer /></h1>
      <p className="mt-2 max-w-[70ch] text-ink-soft">Send a request through the real gateway and see which stages ran, whether the cache answered, which backend replied, and what it would have cost on a cloud model.</p>
      {mode !== "live" && <p className="glass mt-4 p-4 text-amber">The Playground sends real requests, so it needs the live gateway.</p>}
      <div className="mt-8 grid gap-6 lg:grid-cols-[380px_1fr]">
        <form className="glass flex flex-col gap-4 p-5" onSubmit={(e) => { e.preventDefault(); void send(); }}>
          <label className="block text-sm text-ink-soft">
            Prompt
            <textarea name="prompt" autoComplete="off" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={5} className="mt-1.5 w-full rounded-[var(--radius-sm)] p-3 text-[0.95rem] text-ink" style={{ background: "var(--space-1)" }} />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm text-ink-soft">
              Model
              <select value={model} onChange={(e) => setModel(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-[var(--radius-sm)] px-2 text-ink" style={{ background: "var(--space-1)" }}>
                {models.map((m) => <option key={m}>{m}</option>)}
              </select>
            </label>
            <label className="block text-sm text-ink-soft">
              Tenant key
              <select value={key} onChange={(e) => setKey(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-[var(--radius-sm)] px-2 text-ink" style={{ background: "var(--space-1)" }}>
                {KEYS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select>
            </label>
          </div>
          <label className="block text-sm text-ink-soft">
            <span className="flex justify-between"><span>Temperature</span><span className="font-tabular text-ink">{temperature.toFixed(1)}</span></span>
            <input type="range" min={0} max={1.5} step={0.1} value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} className="mt-2 w-full accent-[var(--cyan)]" />
            <span className="text-xs text-ink-mute">Part of the cache key: a different temperature is a different entry.</span>
          </label>
          <fieldset className="text-sm text-ink-soft">
            <legend>Transport</legend>
            <div className="mt-1.5 flex gap-4">
              <label className="flex items-center gap-2"><input type="radio" name="transport" checked={transport === "sse"} onChange={() => setTransport("sse")} className="accent-[var(--cyan)]" />Server-Sent Events</label>
              <label className="flex items-center gap-2"><input type="radio" name="transport" checked={transport === "ws"} onChange={() => setTransport("ws")} className="accent-[var(--cyan)]" />WebSocket</label>
            </div>
            <span className="text-xs text-ink-mute">Same pipeline either way. Stop cancels the model call mid-answer.</span>
          </fieldset>
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input type="checkbox" checked={noCache} onChange={(e) => setNoCache(e.target.checked)} className="accent-[var(--cyan)]" />
            Bypass cache (Cache-Control: no-cache)
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="btn btn-primary" disabled={disabled}>Send</button>
            {busy && <button type="button" className="btn btn-quiet" onClick={() => stopper.current?.abort()}>Stop</button>}
            <button type="button" className="btn btn-quiet" disabled={disabled} onClick={() => void sendTen()}>Send this 10× at once</button>
            <button type="button" className="btn btn-quiet" disabled={disabled} onClick={() => void resendRephrased()}>Rephrase and resend</button>
          </div>
          <p className="text-xs text-ink-mute">Next rephrasing: "{rephrase(prompt, rephrases)}"</p>
        </form>

        <div className="flex flex-col gap-4">
          {batch && (
            <section className="glass p-5" aria-label="Batch result">
              <h2 className="font-semibold text-ink">{batch.label}</h2>
              <p className="mt-1 text-sm text-ink-soft">
                {batch.results.filter((r) => r.cache === "coalesced").length} coalesced ·{" "}
                {batch.results.filter((r) => r.cache === "miss" || r.cache === "bypass").length} upstream call(s) ·{" "}
                {batch.results.filter((r) => r.cache === "hit" || r.cache === "semantic").length} cache hit(s) ·{" "}
                {batch.results.filter((r) => r.status !== 200).length} error(s)
              </p>
              <div className="mt-3 flex flex-wrap gap-1.5" aria-hidden="true">
                {batch.results.map((r, i) => (
                  <span key={i} className="rounded-md px-2 py-1 text-xs font-semibold" style={{ background: r.status !== 200 ? "var(--red-wash)" : r.cache === "coalesced" ? "var(--violet-wash)" : "var(--cyan-wash)", color: r.status !== 200 ? "var(--red)" : r.cache === "coalesced" ? "var(--violet)" : "var(--cyan)" }}>
                    {r.status !== 200 ? r.status : r.cache}
                  </span>
                ))}
              </div>
            </section>
          )}
          {runs.length === 0 && !batch && <p className="glass p-5 text-ink-mute">Responses appear here, with a live trace of every stage.</p>}
          {runs.map((run) => (
            <article key={run.id} className="glass grid gap-5 p-5 md:grid-cols-[1fr_300px]">
              <div>
                <p className="text-sm text-ink-mute">"{run.prompt}"</p>
                <p className="mt-2 whitespace-pre-wrap leading-relaxed text-ink">{run.text || (run.result ? "" : "…")}</p>
                <div className="mt-3"><TraceChip run={run} /></div>
                <Rate run={run} />
              </div>
              <MiniTrace run={run} />
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
