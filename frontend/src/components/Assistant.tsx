// AEGIS, the project assistant: on every page, bottom-right. Answers are grounded in the docs and
// live state, cite their sources, and each one is a real request through the gateway with its
// trace chip underneath. "/" or Ctrl/Cmd+K focuses it; Escape closes it.
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { HOVER } from "../config/robot";
import { backendLabel, fmtMs, fmtUsd } from "../lib/format";
import { useLive } from "../lib/live";
import { SPRING, useReducedMotion } from "../lib/motion";
import { useUi } from "../lib/ui";
import AssistantBot from "./AssistantBot";
import RobotFace, { type Mood } from "./RobotFace";

interface Source { n: number | string; source: string; heading: string; score: number | null }
interface Message {
  role: "user" | "assistant";
  text: string;
  sources?: Source[];
  summary?: Record<string, unknown>;
  headers?: Record<string, string>;
  error?: string;
  grounded?: boolean;
}

const STARTERS = ["How does failover work?", "Show me the cache in action", "What happens when the primary dies?", "Is the primary healthy right now?"];

const xs = HOVER.screen.map((p) => p[0]);
const ys = HOVER.screen.map((p) => p[1]);
const FACE_BOX = `${Math.min(...xs) - 6} ${Math.min(...ys) - 6} ${Math.max(...xs) - Math.min(...xs) + 12} ${Math.max(...ys) - Math.min(...ys) + 12}`;

function Face({ mood }: { mood: Mood }) {
  return (
    <svg viewBox={FACE_BOX} className="h-11 w-12" aria-hidden="true">
      <RobotFace mood={mood} glow={1.2} />
    </svg>
  );
}

async function ask(question: string, history: { role: string; content: string }[], onItem: (item: Record<string, unknown>) => void) {
  const res = await fetch("/api/assistant/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, history }),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i).replace(/^data:\s*/, "");
      buf = buf.slice(i + 2);
      if (line) onItem(JSON.parse(line));
    }
  }
}

function Chip({ m }: { m: Message }) {
  const s = m.summary;
  if (!s) return null;
  const cache = String(s.cache);
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.72rem] text-ink-mute">
      <span className="rounded-full px-2 py-0.5" style={{ background: cache === "miss" || cache === "bypass" ? "var(--cyan-wash)" : "var(--violet-wash)", color: cache === "miss" || cache === "bypass" ? "var(--cyan)" : "var(--violet)" }}>
        cache: {cache === "semantic" ? "semantic hit" : cache === "exact" ? "exact hit" : cache}
      </span>
      <span>backend: {backendLabel(String(s.backend))}</span>
      <span>{Number(s.tokens_in) + Number(s.tokens_out)} tokens</span>
      <span>{fmtMs(Number(s.latency_ms))}</span>
      <span>est. saved {fmtUsd(Number(s.saved_est_usd))}</span>
      <Link to={`/architecture?view=detailed&request=${String(s.request_id)}`} className="text-cyan underline underline-offset-2">watch this answer's journey</Link>
    </div>
  );
}

export default function Assistant() {
  const open = useUi((s) => s.assistantOpen);
  const pending = useUi((s) => s.assistantPrompt);
  const openAssistant = useUi((s) => s.openAssistant);
  const close = useUi((s) => s.closeAssistant);
  const mode = useLive((s) => s.mode);
  const reduced = useReducedMotion();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [state, setState] = useState<"idle" | "thinking" | "streaming" | "done" | "error">("idle");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const mood: Mood = state === "thinking" ? "thinking" : state === "error" ? "concerned" : state === "done" ? "surprised" : "happy";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && /input|textarea|select/i.test(e.target.tagName);
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        openAssistant();
        window.setTimeout(() => inputRef.current?.focus(), 50);
      } else if (e.key === "Escape" && useUi.getState().assistantOpen) {
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openAssistant, close]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: reduced ? "auto" : "smooth" });
  }, [messages, reduced]);

  const send = async (question: string) => {
    const q = question.trim();
    if (!q || state === "thinking" || state === "streaming") return;
    setInput("");
    const history = messages.filter((m) => !m.error).slice(-4).map((m) => ({ role: m.role, content: m.text }));
    setMessages((ms) => [...ms, { role: "user", text: q }, { role: "assistant", text: "" }]);
    setState("thinking");
    const patch = (fn: (m: Message) => Message) => setMessages((ms) => [...ms.slice(0, -1), fn(ms[ms.length - 1])]);
    try {
      await ask(q, history, (item) => {
        if (item.type === "sources") patch((m) => ({ ...m, sources: item.sources as Source[], grounded: Boolean(item.grounded) }));
        else if (item.type === "meta") patch((m) => ({ ...m, headers: item.headers as Record<string, string> }));
        else if (item.type === "token") {
          setState("streaming");
          patch((m) => ({ ...m, text: m.text + String(item.text) }));
        } else if (item.type === "summary") patch((m) => ({ ...m, summary: item.summary as Record<string, unknown> }));
        else if (item.type === "error") {
          const err = item.error as { message?: string } | undefined;
          patch((m) => ({ ...m, error: err?.message ?? `HTTP ${String(item.status)}` }));
          setState("error");
        }
      });
      setState((s) => (s === "error" ? s : "done"));
      window.setTimeout(() => setState((s) => (s === "done" ? "idle" : s)), 1400);
    } catch (e) {
      patch((m) => ({ ...m, error: (e as Error).message }));
      setState("error");
    }
  };

  useEffect(() => {
    if (open && pending) {
      useUi.setState({ assistantPrompt: null });
      void send(pending);
    }
    if (open) window.setTimeout(() => inputRef.current?.focus(), 80);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pending]);

  return (
    <>
    <AssistantBot open={open} onOpen={(q) => openAssistant(q)} />
    <div className="fixed bottom-5 right-5 z-50">
      <AnimatePresence>
        {open && (
          <motion.section
            key="panel"
            role="dialog"
            aria-label="AEGIS assistant"
            initial={reduced ? false : { opacity: 0, scale: 0.4, y: 40, originX: 1, originY: 1 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={reduced ? undefined : { opacity: 0, scale: 0.5, y: 30 }}
            transition={SPRING}
            className="glass glass-strong flex h-[min(640px,calc(100vh-110px))] w-[min(420px,calc(100vw-40px))] flex-col overflow-hidden"
          >
            <header className="flex items-center gap-3 px-4 py-3" style={{ background: "var(--space-1)" }}>
              <motion.span animate={{ filter: state === "streaming" ? "brightness(1.35)" : "brightness(1)" }}><Face mood={mood} /></motion.span>
              <div className="flex-1">
                <p className="font-display text-lg font-semibold leading-none">AEGIS</p>
                <p className="mt-1 text-xs text-ink-mute">{state === "thinking" ? "Reading the docs…" : state === "streaming" ? "Answering through the gateway…" : "Answers from this project's docs and live state"}</p>
              </div>
              <button type="button" onClick={close} className="btn btn-quiet min-h-9 px-3 text-sm" aria-label="Close assistant">Close</button>
            </header>
            <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 py-4" aria-live="polite">
              {messages.length === 0 && (
                <div>
                  <p className="text-sm text-ink-soft">Ask how any part of the gateway works, or what it is doing right now.</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {STARTERS.map((s) => (
                      <button key={s} type="button" onClick={() => void send(s)} disabled={mode !== "live"}
                        className="rounded-full px-3 py-1.5 text-left text-[0.82rem] text-ink hover:bg-[var(--glass-strong)] disabled:opacity-50" style={{ background: "var(--glass)" }}>
                        {s}
                      </button>
                    ))}
                  </div>
                  {mode !== "live" && <p className="mt-3 text-sm text-amber">AEGIS answers through the live gateway, which is offline.</p>}
                </div>
              )}
              {messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "ml-8 rounded-2xl px-3 py-2 text-[0.92rem] text-ink" : "text-[0.92rem] leading-relaxed text-ink-soft"} style={m.role === "user" ? { background: "var(--cyan-wash)" } : undefined}>
                  {m.role === "assistant" && !m.text && !m.error ? <span className="text-ink-mute">…</span> : <p className="whitespace-pre-wrap">{m.text}</p>}
                  {m.error && <p className="mt-1 text-red">{m.error}</p>}
                  {m.role === "assistant" && m.sources && m.sources.length > 0 && m.text && (
                    <ol className="mt-2 space-y-0.5 text-[0.75rem] text-ink-mute">
                      {m.sources.map((s) => (
                        <li key={String(s.n)}>[{s.n}] {s.source} › {s.heading}</li>
                      ))}
                    </ol>
                  )}
                  {m.role === "assistant" && <Chip m={m} />}
                </div>
              ))}
            </div>
            <form className="flex items-end gap-2 p-3" style={{ background: "var(--space-1)" }} onSubmit={(e) => { e.preventDefault(); void send(input); }}>
              <label htmlFor="aegis-input" className="sr-only">Ask AEGIS</label>
              <textarea id="aegis-input" name="question" autoComplete="off" ref={inputRef} value={input} rows={1} placeholder="Ask about the gateway…"
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(input); } }}
                className="max-h-28 min-h-11 flex-1 resize-none rounded-[var(--radius-sm)] px-3 py-2.5 text-[0.92rem] text-ink placeholder:text-ink-mute" style={{ background: "var(--space-0)" }} />
              <button type="submit" className="btn btn-primary" disabled={!input.trim() || mode !== "live" || state === "thinking" || state === "streaming"}>Ask</button>
            </form>
          </motion.section>
        )}
      </AnimatePresence>
    </div>
    </>
  );
}
