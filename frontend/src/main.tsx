import "@fontsource-variable/sora";
import "@fontsource-variable/oxanium";
import "@fontsource-variable/jetbrains-mono";
import "./styles/app.css";

import { StrictMode, lazy } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import Shell from "./components/Shell";
import Home from "./pages/Home";
import NotFound from "./pages/NotFound";
import { startLive } from "./lib/live";
import { onFirstInteraction } from "./lib/motion";

const PAGES = {
  architecture: () => import("./pages/Architecture"),
  controlRoom: () => import("./pages/ControlRoom"),
  chaos: () => import("./pages/ChaosLab"),
  playground: () => import("./pages/Playground"),
  traces: () => import("./pages/Traces"),
  docs: () => import("./pages/Docs"),
};
const Architecture = lazy(PAGES.architecture);
const ControlRoom = lazy(PAGES.controlRoom);
const ChaosLab = lazy(PAGES.chaos);
const Playground = lazy(PAGES.playground);
const Traces = lazy(PAGES.traces);
const Docs = lazy(PAGES.docs);

// Pages stay split out of the first load, then are fetched on the visitor's first interaction, so
// switching tabs never waits on a download and the first paint never waits on them.
onFirstInteraction(() => Object.values(PAGES).forEach((load) => void load()), 8000);

startLive();

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/architecture", element: <Architecture /> },
      { path: "/control-room", element: <ControlRoom /> },
      { path: "/chaos", element: <ChaosLab /> },
      { path: "/playground", element: <Playground /> },
      { path: "/traces", element: <Traces /> },
      { path: "/docs", element: <Docs /> },
      { path: "/docs/:slug", element: <Docs /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
