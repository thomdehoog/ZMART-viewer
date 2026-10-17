import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { rememberedTheme } from "./ThemeToggle.tsx";
// The named colours both themes are built from; every component's styles
// refer to these names rather than to hex values.
import "./theme.css";

// Put the remembered theme on before anything is drawn: reading it here --
// before React renders a single element -- means a page that was left light
// comes back light, with no dark flash on the way.
document.documentElement.dataset.theme = rememberedTheme();

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
