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

export const DEMO_KEY = "aegis-demo-key";

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
      "x-aegis-summary": "1",
      "x-aegis-client": "playground",
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
    const { value, done } = await reader.read();
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
      if (event === "aegis") summary = obj;
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
