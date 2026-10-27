// What each part of the gateway is, in a few plain words, with an icon, so the graph reads
// without the docs. Anything the API adds that is not listed here falls back to a generic icon.
import {
  Activity, Binary, BrainCircuit, Cpu, Database, Gauge, GitMerge, HardDrive, KeyRound, ListChecks,
  Monitor, Radio, Route, Server, ShieldCheck, Split, Wallet, Zap, type LucideIcon,
} from "lucide-react";

export const META: Record<string, { icon: LucideIcon; role: string }> = {
  client: { icon: Monitor, role: "apps that call the gateway" },
  auth: { icon: KeyRound, role: "checks the API key" },
  guardrails: { icon: ShieldCheck, role: "removes personal data, blocks injection" },
  ratelimit: { icon: Gauge, role: "tokens per minute, per key" },
  budget: { icon: Wallet, role: "monthly spend per tenant" },
  exact_cache: { icon: Database, role: "same question: instant replay" },
  semantic_cache: { icon: BrainCircuit, role: "similar question: instant replay" },
  embedder: { icon: Binary, role: "turns text into vectors" },
  coalescer: { icon: GitMerge, role: "identical requests share one call" },
  router: { icon: Route, role: "picks a healthy, free model" },
  hedger: { icon: Split, role: "races a slow model" },
  relay: { icon: Radio, role: "streams tokens back" },
  ledger_queue: { icon: ListChecks, role: "records usage off the hot path" },
  sqlite: { icon: HardDrive, role: "the usage ledger" },
  redis: { icon: Server, role: "cache, limits and budgets" },
  otel: { icon: Activity, role: "one trace per request" },
  chaos: { icon: Zap, role: "breaks things on purpose" },
};

export const BACKEND_META = { icon: Cpu, role: "a model server" };

export function metaFor(id: string) {
  return META[id] ?? BACKEND_META;
}

/** The six phases of a request, in order, for the journey strip and the legend. */
export const JOURNEY: { group: string; title: string; explains: string; stages: string[] }[] = [
  { group: "Ingress", title: "Check", explains: "Who is asking, and is the prompt safe?", stages: ["auth", "guardrails"] },
  { group: "Policy", title: "Allow", explains: "Within its token rate and budget?", stages: ["ratelimit", "budget"] },
  { group: "Memory", title: "Remember", explains: "Answered before, or being answered now?", stages: ["exact_cache", "semantic_cache", "coalescer"] },
  { group: "Reflexes", title: "Choose", explains: "Which model is healthy and free right now?", stages: ["router", "hedger"] },
  { group: "Backends", title: "Think", explains: "A local model writes the answer.", stages: [] },
  { group: "Delivery", title: "Deliver", explains: "Stream it back and record it.", stages: ["relay", "ledger_queue"] },
];
