// Thin fetch helpers for the control plane. Every number on the site comes through here or the
// WebSocket; in replay mode the same shapes come from the recorded fixture instead.

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(path, {
    ...rest,
    headers: { ...(json !== undefined ? { "content-type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const message = body?.error?.message ?? body?.detail ?? `HTTP ${res.status}`;
    throw new ApiError(res.status, String(message), body);
  }
  return body as T;
}

export const DEMO_KEY = "prahari-demo-key";

export interface StreamResult {
  status: number;
  headers: Record<string, string>;
  text: string;
  summary: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
}

/** POST /v1/chat/completions with SSE, calling onToken as tokens arrive. */
export async function streamChat(
  body: Record<string, unknown>,
  opts: { key?: string; noCache?: boolean; onToken?: (t: string) => void; onHeaders?: (h: Record<string, string>, status: number) => void; signal?: AbortSignal },
): Promise<StreamResult> {
  const res = await fetch("/v1/chat/completions", {
    method: "POST",
    signal: opts.signal,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${opts.key ?? DEMO_KEY}`,
      "x-prahari-summary": "1",
      "x-prahari-client": "playground",
      ...(opts.noCache ? { "cache-control": "no-cache" } : {}),
    },
    body: JSON.stringify({ ...body, stream: true }),
  });
  const headers: Record<string, string> = {};
  res.headers.forEach((v, k) => {
    if (k.startsWith("x-") || k === "retry-after") headers[k] = v;
  });
  opts.onHeaders?.(headers, res.status);
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({}));
    return { status: res.status, headers, text: "", summary: null, error: err.error ?? err };
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let text = "";
  let summary: Record<string, unknown> | null = null;
  let error: Record<string, unknown> | null = null;
  for (;;) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (e) {
      // Stop: the fetch was aborted; the gateway sees the client leave and cancels the model call
      if ((e as Error).name === "AbortError") error = { code: "cancelled", message: "Stopped by you" };
      else throw e;
      break;
    }
    const { value, done } = chunk;
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event: string | null = null;
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data || data === "[DONE]") continue;
      const obj = JSON.parse(data);
      if (event === "prahari") summary = obj;
      else if (event === "error") error = obj.error ?? obj;
      else {
        for (const c of obj.choices ?? []) {
          const t = c.delta?.content;
          if (t) {
            text += t;
            opts.onToken?.(t);
          }
        }
      }
    }
  }
  return { status: res.status, headers, text, summary, error };
}

/** The same chat over the full-duplex WebSocket (/v1/ws/chat). Aborting the signal sends a
 *  `cancel` frame: the gateway cancels the upstream model call and answers `cancelled`. */
export function wsChat(
  body: Record<string, unknown>,
  opts: { key?: string; noCache?: boolean; onToken?: (t: string) => void; onHeaders?: (h: Record<string, string>, status: number) => void; signal?: AbortSignal },
): Promise<StreamResult> {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/v1/ws/chat?key=${encodeURIComponent(opts.key ?? DEMO_KEY)}`;
  return new Promise((resolve) => {
    const ws = new WebSocket(url);
    const out: StreamResult = { status: 0, headers: {}, text: "", summary: null, error: null };
    let settled = false;
    const finish = () => { if (settled) return; settled = true; ws.close(); resolve(out); };
    opts.signal?.addEventListener("abort", () => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "cancel", id: "pg" }));
    });
    ws.onopen = () => ws.send(JSON.stringify({ type: "chat", id: "pg", no_cache: opts.noCache, body: { ...body, stream: true } }));
    ws.onerror = () => { out.error = { code: "websocket_error", message: "WebSocket failed" }; finish(); };
    // the server closed without a final frame (a restart, a crash): never leave the caller waiting
    ws.onclose = () => {
      if (!settled) out.error = out.error ?? { code: "websocket_closed", message: "The connection closed before the answer finished" };
      finish();
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(String(ev.data));
      if (msg.type === "start") {
        out.status = 200;
        out.headers = { ...(msg.headers ?? {}), "x-transport": "websocket" };
        opts.onHeaders?.(out.headers, 200);
      } else if (msg.type === "chunk") {
        for (const c of msg.data?.choices ?? []) {
          const t = c.delta?.content;
          if (t) { out.text += t; opts.onToken?.(t); }
        }
      } else if (msg.type === "summary") {
        out.summary = msg.data;
      } else if (msg.type === "error") {
        out.status = msg.status ?? 500;
        out.error = msg.error ?? msg;
        if (!out.headers["x-prahari-request-id"]) opts.onHeaders?.(out.headers, out.status);
        finish();
      } else if (msg.type === "cancelled") {
        out.error = { code: "cancelled", message: "Stopped by you: the model call was cancelled" };
        finish();
      } else if (msg.type === "done") {
        finish();
      }
    };
  });
}
