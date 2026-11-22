import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
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

// The visual-review gallery is opt-in. React.lazy doesn't fetch its chunk on
// ordinary app boots, including desktop launches carrying #token=... .
const Gallery = lazy(() => import("./ui/gallery/Gallery"));

function Root() {
  const [showGallery, setShowGallery] = useState(() => window.location.hash === "#gallery");
  useEffect(() => {
    const onHashChange = () => setShowGallery(window.location.hash === "#gallery");
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return showGallery ? (
    <Suspense fallback={<p role="status">Loading gallery…</p>}>
      <Gallery />
    </Suspense>
  ) : <App />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
