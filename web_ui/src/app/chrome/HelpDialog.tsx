import { useState } from "react";
import { HELP_SECTIONS } from "./help-data/sections";
import { Dialog } from "../overlays/overlays";

/**
 * The Help dialog (Qt-removal plan R2.5) - help-web's SPA successor.
 *
 * Confirmed by recon to be 100% static content (9 sections/19 subsections/
 * 76 items, byte-exact in help-data/sections.ts) with no backend state at
 * all - Python never learned which section was open even in the legacy
 * island. No WS topic, no client store; this is a plain client component
 * whose only "state" (active section) is local UI selection, exactly as
 * before.
 */

const RAIL_INTRO =
  "Graphlink is a visual AI workspace. Start with the overview, then jump directly to the workflow, tool, or project area you need.";

export function HelpDialog() {
  const [activeSectionName, setActiveSectionName] = useState(HELP_SECTIONS[0].name);
  const activeSection =
    HELP_SECTIONS.find((section) => section.name === activeSectionName) ?? HELP_SECTIONS[0];

  return (
    <Dialog name="help" title="Help Center" className="help-dialog">
      <div className="help-shell">
        <nav className="help-rail" aria-label="Help sections">
          <p className="help-rail-intro">{RAIL_INTRO}</p>
          <div className="help-rail-buttons">
            {HELP_SECTIONS.map((section) => (
              <button
                key={section.name}
                type="button"
                className={"help-rail-button" + (section.name === activeSectionName ? " active" : "")}
                aria-current={section.name === activeSectionName ? "page" : undefined}
                onClick={() => setActiveSectionName(section.name)}
              >
                {section.name}
              </button>
            ))}
          </div>
        </nav>

        <div className="help-content" role="region" aria-label={activeSection.name}>
          <h1 className="help-content-title">{activeSection.name}</h1>
          <p className="help-content-description">{activeSection.description}</p>

          <div className="help-scroll-area">
            {activeSection.subsections.map((subsection) => (
              <section className="help-section-block" key={subsection.title}>
                <h2 className="help-section-title">{subsection.title}</h2>
                {subsection.items.map((item, index) => (
                  <div className="help-item-card" key={`${subsection.title}-${index}`}>
                    <p className="help-item-action">{item.action}</p>
                    <p className="help-item-description">{item.description}</p>
                  </div>
                ))}
              </section>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  );
}
