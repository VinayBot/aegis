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

const Architecture = lazy(() => import("./pages/Architecture"));
const ControlRoom = lazy(() => import("./pages/ControlRoom"));
const ChaosLab = lazy(() => import("./pages/ChaosLab"));
const Playground = lazy(() => import("./pages/Playground"));
const Traces = lazy(() => import("./pages/Traces"));
const Docs = lazy(() => import("./pages/Docs"));

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
