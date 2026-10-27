import { Link, useLocation } from "react-router-dom";

/** Any unknown address: say so plainly and offer the way back. */
export default function NotFound() {
  const { pathname } = useLocation();
  return (
    <section className="mx-auto grid min-h-[70vh] max-w-[720px] place-content-center px-[var(--gutter)] text-center">
      <p className="font-tabular text-ink-mute">404</p>
      <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight text-ink md:text-5xl">This page is off the map</h1>
      <p className="mt-4 text-ink-soft">
        Nothing lives at <code className="text-ink">{pathname}</code>. Every real route is in the navigation above.
      </p>
      <div className="mt-8 flex justify-center gap-3">
        <Link to="/" className="glass glow-border px-5 py-2.5 font-semibold text-ink">Back to the start</Link>
        <Link to="/architecture" className="px-5 py-2.5 text-ink-soft hover:text-ink">See the architecture</Link>
      </div>
    </section>
  );
}
