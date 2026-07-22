import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "../lib/ui/base.css";
// The single-SPA app has no Qt host injecting :root { --gl-* } at build time
// (that was _inline_bundle()'s job) - the generated token values ship with
// the app unconditionally. Live theme switching becomes a backend topic in
// R2; dark is the only theme the old app ever defaulted to.
import "../lib/tokens/gl-vars-dev.css";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
