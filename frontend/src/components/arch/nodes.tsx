import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Cpu } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import { fmtMs } from "../../lib/format";
import type { ArchNode } from "../../lib/types";
import { metaFor } from "./meta";
import { registry } from "./packets";

const SIDES = [
  [Position.Top, "t"],
  [Position.Right, "r"],
  [Position.Bottom, "b"],
  [Position.Left, "l"],
] as const;

function Handles() {
  return (
    <>
      {SIDES.map(([pos, k]) => (
        <span key={k}>
          <Handle type="source" position={pos} id={`s-${k}`} className="!h-1.5 !w-1.5 !border-0 !opacity-0" />
          <Handle type="target" position={pos} id={`t-${k}`} className="!h-1.5 !w-1.5 !border-0 !opacity-0" />
        </span>
      ))}
    </>
  );
}

/** Registers this node so packets.ts can light it up without a React render. */
function useNodeHandle(id: string) {
  const ref = useRef<HTMLDivElement>(null);
  const badge = useRef<HTMLSpanElement>(null);
  useEffect(
    () =>
      registry.node(id, {
        set(attr, value) {
          const el = ref.current;
          if (!el) return;
          if (attr === "badge") {
            if (badge.current) {
              badge.current.textContent = value ?? "";
              badge.current.dataset.on = value ? "true" : "false";
            }
            return;
          }
          if (value === null) delete el.dataset[attr];
          else el.dataset[attr] = value;
        },
      }),
    [id],
  );
  return { ref, badge };
}

const STATUS_TEXT: Record<string, string> = { up: "healthy", down: "down", degraded: "degraded", disabled: "disabled", unknown: "unknown", starting: "starting" };

export interface StageData extends Record<string, unknown> {
  node: ArchNode;
  selected?: boolean;
  dimmed?: boolean;
  selfcheckMs?: number;
}

function StageNodeImpl({ id, data }: NodeProps) {
  const { node, dimmed, selfcheckMs } = data as StageData;
  const { ref, badge } = useNodeHandle(id);
  const p95 = node.stats?.p95_ms ?? null;
  const { icon: Icon, role } = metaFor(node.id);
  return (
    <div ref={ref} className="arch-node glass" data-kind={node.kind} data-status={node.status} style={{ opacity: dimmed ? 0.22 : 1 }}>
      <Handles />
      <div className="flex items-start gap-2.5">
        <span className={`planet planet-${node.status}`} aria-hidden="true"><Icon size={14} strokeWidth={2.2} /></span>
        <div className="min-w-0">
          <div className="truncate font-semibold leading-tight text-ink">{node.label}</div>
          <div className="truncate text-[0.72rem] leading-snug text-ink-mute">{role}</div>
        </div>
      </div>
      <div className="mt-1.5 text-[0.74rem] text-ink-soft font-tabular">
        {node.kind === "store" || node.kind === "side" || node.kind === "client"
          ? STATUS_TEXT[node.status] ?? node.status
          : `p95 ${fmtMs(p95)} · ${node.stats?.count ?? 0} runs`}
      </div>
      {selfcheckMs !== undefined && <span className="arch-check font-tabular">{fmtMs(selfcheckMs)}</span>}
      <span ref={badge} className="arch-badge" data-on="false" aria-hidden="true" />
    </div>
  );
}

function BreakerRing({ retry, total }: { retry: number; total: number }) {
  const [left, setLeft] = useState(retry);
  useEffect(() => {
    setLeft(retry);
    const started = performance.now();
    const t = window.setInterval(() => setLeft(Math.max(0, retry - (performance.now() - started) / 1000)), 250);
    return () => window.clearInterval(t);
  }, [retry]);
  const r = 15;
  const c = 2 * Math.PI * r;
  return (
    <span className="relative inline-grid h-10 w-10 place-items-center" role="timer" aria-label={`breaker reopens in ${Math.ceil(left)} seconds`}>
      <svg viewBox="0 0 40 40" className="absolute inset-0 -rotate-90" aria-hidden="true">
        <circle cx="20" cy="20" r={r} fill="none" strokeWidth="3" style={{ stroke: "var(--red-wash)" }} />
        <circle cx="20" cy="20" r={r} fill="none" strokeWidth="3" strokeLinecap="round" style={{ stroke: "var(--red)" }} strokeDasharray={c} strokeDashoffset={c * (1 - left / Math.max(1, total))} />
      </svg>
      <span className="text-[0.72rem] font-semibold text-red font-tabular">{Math.ceil(left)}s</span>
    </span>
  );
}

function BackendNodeImpl({ id, data }: NodeProps) {
  const { node, dimmed, selfcheckMs } = data as StageData;
  const { ref, badge } = useNodeHandle(id);
  const breaker = node.breaker?.state ?? "closed";
  const slots = node.slots;
  const status = breaker === "open" ? "down" : breaker === "half_open" ? "degraded" : node.status;
  return (
    <div ref={ref} className="arch-node arch-backend glass" data-kind="backend" data-status={status} data-breaker={breaker} style={{ opacity: dimmed ? 0.22 : 1 }}>
      <Handles />
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2.5">
            <span className={`planet planet-${status}`} aria-hidden="true"><Cpu size={14} strokeWidth={2.2} /></span>
            <div className="min-w-0">
              <div className="font-semibold leading-tight text-ink">{node.label}</div>
              <div className="max-w-[150px] truncate text-[0.72rem] text-ink-mute">{String(node.config?.model ?? "")}</div>
            </div>
          </div>
        </div>
        {breaker === "open" && node.breaker && <BreakerRing retry={node.breaker.retry_after_s} total={node.breaker.open_seconds} />}
      </div>
      <div className="mt-2 flex items-center gap-3 text-[0.78rem] text-ink-soft font-tabular">
        <span title="slots in use">
          {slots ? Array.from({ length: slots.capacity }, (_, i) => (
            <span key={i} className="mr-0.5 inline-block h-2 w-3 rounded-sm" style={{ background: i < slots.in_flight ? "var(--cyan)" : "var(--line)" }} />
          )) : null}
        </span>
        <span>TTFT p95 {fmtMs(node.stats?.ttft_p95_ms ?? null)}</span>
      </div>
      <div className="mt-1 text-[0.74rem] font-tabular" style={{ color: breaker === "closed" ? "var(--ink-mute)" : breaker === "open" ? "var(--red)" : "var(--amber)" }}>
        breaker {breaker.replace("_", "-")}{breaker === "half_open" ? ": next request probes" : ""}
      </div>
      {selfcheckMs !== undefined && <span className="arch-check font-tabular">{fmtMs(selfcheckMs)}</span>}
      <span ref={badge} className="arch-badge" data-on="false" aria-hidden="true" />
    </div>
  );
}

export interface GroupData extends Record<string, unknown> {
  label: string;
  status: string;
  members: ArchNode[];
}

function GroupNodeImpl({ id, data }: NodeProps) {
  const { label, status, members } = data as GroupData;
  const { ref, badge } = useNodeHandle(id);
  return (
    <div ref={ref} className="arch-node arch-group glass" data-status={status}>
      <Handles />
      <div className="flex items-center gap-2">
        <span className={`status-dot s-${status}`} aria-hidden="true" />
        <span className="font-display text-lg font-semibold text-ink">{label}</span>
      </div>
      <div className="mt-1 text-[0.78rem] leading-snug text-ink-mute">{members.map((m) => m.label).join(" · ") || "—"}</div>
      <span ref={badge} className="arch-badge" data-on="false" aria-hidden="true" />
    </div>
  );
}

/** Re-render a node only when something it shows changed (the topology is re-polled every
 *  1.5 s and arrives as new objects). */
function sameView(a: NodeProps, b: NodeProps): boolean {
  const pick = (p: NodeProps) => {
    const d = p.data as StageData & GroupData;
    const n = d.node;
    return JSON.stringify([d.dimmed, d.selfcheckMs, d.status, n?.status, n?.stats, n?.breaker?.state, Math.round(n?.breaker?.retry_after_s ?? 0), n?.slots, d.members?.map((m) => m.status)]);
  };
  return a.id === b.id && pick(a) === pick(b);
}

export const StageNode = memo(StageNodeImpl, sameView);
export const BackendNode = memo(BackendNodeImpl, sameView);
export const GroupNode = memo(GroupNodeImpl, sameView);
export const nodeTypes = { stage: StageNode, backend: BackendNode, group: GroupNode };
