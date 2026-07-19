import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ComposerApp from "./ComposerApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ComposerApp />
  </StrictMode>,
);
