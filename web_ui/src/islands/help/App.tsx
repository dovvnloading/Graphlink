import { useEffect, useRef, useState } from "react";
import { HelpBridge, BridgeRejection, createHelpBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";
import { HELP_SECTIONS } from "./data/sections";

const RAIL_INTRO =
  "Graphlink is a visual AI workspace. Start with the overview, then jump directly to the workflow, tool, or project area you need.";

function App() {
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<HelpBridge | null>(null);
  // Which section is showing is pure client-side state - Python never needs
  // to know (see graphlink_help_bridge.py's module docstring), so there is
  // no bridge round-trip for this at all, unlike the settings island's
  // outwardly-similar rail.
  const [activeSectionName, setActiveSectionName] = useState(HELP_SECTIONS[0].name);

  useEffect(() => {
    const bridge = createHelpBridge(() => {}, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        bridgeRef.current?.close();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Help is unavailable"
        rejection={rejection}
        className="help-shell bridge-error"
      />
    );
  }

  const activeSection = HELP_SECTIONS.find((section) => section.name === activeSectionName) ?? HELP_SECTIONS[0];

  return (
    <div className="help-shell">
      <nav className="help-rail" aria-label="Help sections">
        <p className="help-rail-eyebrow">Help Center</p>
        <p className="help-rail-intro">{RAIL_INTRO}</p>
        <div className="help-rail-buttons">
          {HELP_SECTIONS.map((section) => (
            <button
              key={section.name}
              type="button"
              className="help-rail-button"
              aria-current={section.name === activeSectionName ? "page" : undefined}
              onClick={() => setActiveSectionName(section.name)}
            >
              {section.name}
            </button>
          ))}
        </div>
      </nav>

      <div className="help-content" role="region" aria-label={activeSection.name}>
        <div className="help-content-header">
          <div className="help-content-heading">
            <h1 className="help-content-title">{activeSection.name}</h1>
            <p className="help-content-description">{activeSection.description}</p>
          </div>
          <button type="button" className="help-close-btn" onClick={() => bridgeRef.current?.close()}>
            Close
          </button>
        </div>

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
  );
}

export default App;
