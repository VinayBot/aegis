export const fmtInt = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : Math.round(n).toLocaleString("en-US");

export const fmtMs = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : n >= 1000 ? `${(n / 1000).toFixed(2)} s` : `${Math.round(n)} ms`;

export const fmtPct = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined ? "—" : `${(n * 100).toFixed(digits)}%`;

/** Dollar amounts here are always an est. cloud equivalent; callers add the label. */
export const fmtUsd = (n: number | null | undefined) => {
  if (n === null || n === undefined) return "—";
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(5)}`;
  return `$${n.toFixed(n < 1 ? 4 : 2)}`;
};

export const fmtAgo = (ts: number) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
};

export const fmtClock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false });

export const backendLabel = (name: string | null | undefined) =>
  !name ? "—" : name.startsWith("cache (") ? name : name.replace("backend:", "");
