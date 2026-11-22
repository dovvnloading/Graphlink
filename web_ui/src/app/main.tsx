import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "../lib/ui/base.css";
// The single-SPA app has no Qt host injecting :root { --gl-* } at build time
// (that was _inline_bundle()'s job, deleted in the Qt-removal cutover) - the
// token values ship with the app unconditionally, imported here
// unconditionally too (never behind import.meta.env.DEV - there is no other
// injection path for production to fall back on). ADR-012 stage 12.1 added
// a real light palette alongside dark, switched via [data-theme] + a
// prefers-color-scheme default - see gl-vars-dev.css's own header for the
// cascade. Stage 12.2 wires the data-theme attribute itself, from a real
// Settings field.
import "../lib/tokens/gl-vars-dev.css";
import "./styles.css";
import "./canvas/workspace.css";

// The opt-in gallery stays in a separate chunk; ordinary launches keep the
// original <StrictMode><App /></StrictMode> tree and do not fetch it.
const Gallery = lazy(() => import("./ui/gallery/Gallery"));
const startsInGallery = window.location.hash === "#gallery";
const root = createRoot(document.getElementById("root")!);

if (startsInGallery) {
  root.render(
    <StrictMode>
      <Suspense fallback={<p role="status">Loading gallery…</p>}>
        <Gallery />
      </Suspense>
    </StrictMode>,
  );
} else {
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

// Switch routes with a reload, not an in-place remount. Unrelated hashes,
// including desktop #token=... and the composer skip link, must not reload.
window.addEventListener("hashchange", () => {
  if ((window.location.hash === "#gallery") !== startsInGallery) {
    window.location.reload();
  }
});
