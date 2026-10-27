// The project's own docs, rendered from the gateway (/api/docs), so the site and the AEGIS
// assistant read the same source.
import { BlurWords } from "../components/fx";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { NavLink, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { api } from "../lib/api";
import { useLive } from "../lib/live";
import { TiltedGridHero } from "@/components/ui/tilted-grid-hero";

const REEL = ["architecture", "control-room", "chaos", "playground", "traces", "assistant", "home", "docs"].map((n) => ({ src: `/assets/screens/${n}-800.webp`, alt: "" }));

interface DocMeta { slug: string; title: string; path: string }

export default function Docs() {
  const { slug } = useParams();
  const mode = useLive((s) => s.mode);
  const [docs, setDocs] = useState<DocMeta[]>([]);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const current = slug ?? "README.md";

  useEffect(() => {
    if (mode !== "live") return;
    api<DocMeta[]>("/api/docs").then(setDocs).catch((e) => setError(e.message));
  }, [mode]);

  useEffect(() => {
    if (mode !== "live") return;
    setMarkdown(null);
    api<{ markdown: string }>(`/api/docs/${current}`).then((d) => setMarkdown(d.markdown)).catch((e) => setError(e.message));
  }, [current, mode]);

  return (
    <>
    <TiltedGridHero images={REEL} speed={4.5} tileHeight={34} axis={60} className="h-[420px] w-full">
      <div className="relative z-10 flex h-full flex-col items-center justify-start pt-10 text-center">
        <p className="text-shimmer font-display text-4xl font-semibold tracking-tight md:text-6xl">How AEGIS works</p>
        <p className="mt-3 max-w-[46ch] px-6 text-ink-soft">The architecture, the decisions, the API and the demo, read from the same documents the AEGIS assistant answers from.</p>
      </div>
    </TiltedGridHero>
    <div className="mx-auto grid max-w-[1300px] gap-10 px-[var(--gutter)] py-10 lg:grid-cols-[240px_1fr]">
      <nav aria-label="Documents" className="lg:sticky lg:top-24 lg:self-start">
        <h1 className="font-display text-4xl font-semibold tracking-tight"><BlurWords text="Docs" shimmer /></h1>
        <ul className="mt-5 space-y-1">
          {docs.map((d) => (
            <li key={d.slug}>
              <NavLink to={`/docs/${d.slug}`} className={() => `block rounded-lg px-3 py-2 text-[0.95rem] ${d.slug === current ? "text-ink" : "text-ink-mute hover:text-ink"}`}
                style={d.slug === current ? { background: "var(--glass)" } : undefined}>
                {d.title}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <article className="prose-aegis min-w-0">
        {mode !== "live" && <p className="text-amber">The docs are served by the gateway; start it to read them here, or open the docs/ folder.</p>}
        {error && <p role="alert" className="text-red">{error}</p>}
        {markdown === null && mode === "live" && !error && <p aria-busy="true">Loading…</p>}
        {markdown && <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>}
      </article>
    </div>
    </>
  );
}
