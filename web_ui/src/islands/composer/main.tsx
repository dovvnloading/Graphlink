import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ComposerApp from "./ComposerApp";
import "./styles.css";
// Separate file so styles.css stays byte-stable for its resolution-golden
// test; see bridge-error.css's own header for why.
import "./bridge-error.css";

// styles.css resolves every color through var(--gl-*). In the real app those
// values are injected into <head> by the Python host for whichever theme is
// active; `npm run dev` has no Python in the loop, so without this the composer
// renders unstyled in a browser. Gated on DEV (not merely lazy-loaded) so Vite
// statically eliminates it from the production bundle - shipping it would put a
// hardcoded dark-theme :root block after the host's injected one at equal
// specificity, silently overriding the real theme and masking a failed
// injection instead of letting it show.
if (import.meta.env.DEV) {
  await import("../../lib/tokens/gl-vars-dev.css");
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ComposerApp />
  </StrictMode>,
);
