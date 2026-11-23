import { useLayoutEffect, useState, type ComponentType } from "react";
import { applyTheme } from "../../applyTheme";
import "./gallery.css";

export type GallerySection = {
  title: string;
  order: number;
  Component: ComponentType<{ theme: "dark" | "light" }>;
};

// New visual baselines are registered by adding a file, never by changing
// this shell. The eager glob belongs to the gallery's lazy-loaded chunk.
const modules = import.meta.glob<{ default: GallerySection }>("./sections/*.tsx", { eager: true });
const discoveredSections = Object.entries(modules)
  .map(([path, module]) => ({ path, ...module.default }))
  .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title) || a.path.localeCompare(b.path));

function initialTheme(): "dark" | "light" {
  const explicit = document.documentElement.getAttribute("data-theme");
  if (explicit === "light" || explicit === "dark") return explicit;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export default function Gallery({ sections = discoveredSections }: { sections?: GallerySection[] }) {
  const [theme, setTheme] = useState<"dark" | "light">(initialTheme);

  // The main app applies the same attribute via applyTheme(app-settings.theme).
  // Layout timing lets token cards read the new computed values after a switch.
  useLayoutEffect(() => applyTheme(theme), [theme]);

  return (
    <main className="gl-gallery-page">
      <header className="gl-gallery-header">
        <div>
          <span className="gl-gallery-kicker">Graphlink / visual baselines</span>
          <h1>UI gallery</h1>
          <p>A live inventory of the current design tokens and control surfaces.</p>
        </div>
        <div className="gl-gallery-theme" role="group" aria-label="Gallery theme">
          <span>Theme</span>
          {(["dark", "light"] as const).map((choice) => (
            <button
              key={choice}
              type="button"
              className={`gl-gallery-theme-button${theme === choice ? " is-active" : ""}`}
              aria-pressed={theme === choice}
              onClick={() => { applyTheme(choice); setTheme(choice); }}
            >
              {choice === "dark" ? "Dark" : "Light"}
            </button>
          ))}
        </div>
      </header>
      <div className="gl-gallery-sections">
        {sections.map(({ title, Component }) => (
          <section key={title} className="gl-gallery-section" aria-label={title}>
            <div className="gl-gallery-section-heading">
              <h2>{title}</h2>
            </div>
            <Component theme={theme} />
          </section>
        ))}
      </div>
    </main>
  );
}
