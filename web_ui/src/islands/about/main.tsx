import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "../../lib/ui/base.css";
import "./styles.css";

// See composer/main.tsx for the full rationale: styles.css resolves colors
// through var(--gl-*), which only exist in the real app via the Python
// host's build-time :root injection - npm run dev has no Python in the
// loop, so this supplies the same variables for browser preview.
if (import.meta.env.DEV) {
  await import("../../lib/tokens/gl-vars-dev.css");
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
