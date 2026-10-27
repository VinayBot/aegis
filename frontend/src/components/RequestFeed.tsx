import { motion } from "motion/react";
import { Link } from "react-router-dom";
import { backendLabel, fmtClock, fmtMs } from "../lib/format";
import { useLive } from "../lib/live";
import { useReducedMotion } from "../lib/motion";

export function badgeFor(e: Record<string, unknown>): { label: string; tone: string } {
  if (e.status !== "ok") {
    if (e.http_status === 429) return { label: "429 rate limited", tone: "s-error" };
    if (e.http_status === 402) return { label: "402 budget", tone: "s-error" };
    if (e.status === "blocked") return { label: "blocked", tone: "s-error" };
    return { label: String(e.status), tone: "s-error" };
  }
  if (e.cache === "exact") return { label: "exact hit", tone: "s-cached" };
  if (e.cache === "semantic") return { label: "semantic hit", tone: "s-cached" };
  if (e.cache === "coalesced") return { label: "coalesced", tone: "s-cached" };
  if (e.hedged) return { label: "hedged", tone: "s-degraded" };
  if (Number(e.failovers) > 0) return { label: "failed over", tone: "s-degraded" };
  return { label: "model", tone: "s-ok" };
}

export default function RequestFeed({ limit = 14 }: { limit?: number }) {
  const recent = useLive((s) => s.recent);
  const reduced = useReducedMotion();
  const ends = recent.filter((e) => e.type === "request.end").slice(-limit).reverse();
  return (
    <section aria-labelledby="rfeed" className="glass p-4">
      <h2 id="rfeed" className="font-semibold text-ink">Live request feed</h2>
      <table className="mt-3 w-full text-left text-[0.82rem]">
        <thead>
          <tr className="text-ink-mute">
            <th className="pb-2 font-medium">time</th>
            <th className="pb-2 font-medium">tenant</th>
            <th className="pb-2 font-medium">result</th>
            <th className="pb-2 font-medium">backend</th>
            <th className="pb-2 text-right font-medium">latency</th>
          </tr>
        </thead>
        <tbody>
          {ends.length === 0 && (
            <tr><td colSpan={5} className="py-3 text-ink-mute">No requests yet. Try the Playground.</td></tr>
          )}
          {ends.map((e, i) => {
            const b = badgeFor(e);
            return (
              <motion.tr key={e.seq} initial={reduced || i > 9 ? false : { opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, delay: Math.min(i, 10) * 0.03 }} className="text-ink-soft">
                <td className="py-1.5 pr-2 font-tabular text-ink-mute">{fmtClock(e.ts)}</td>
                <td className="py-1.5 pr-2">{String(e.tenant)}</td>
                <td className="py-1.5 pr-2">
                  <Link to={`/architecture?view=detailed&request=${e.request_id}`} className="inline-flex items-center gap-1.5 hover:text-ink" title="Replay this request on the architecture graph">
                    <span className={`status-dot ${b.tone}`} aria-hidden="true" />{b.label}
                  </Link>
                </td>
                <td className="py-1.5 pr-2">{backendLabel(e.backend as string)}</td>
                <td className="py-1.5 text-right font-tabular">{fmtMs(Number(e.latency_ms))}</td>
              </motion.tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
