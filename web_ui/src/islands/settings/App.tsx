import { useEffect, useRef, useState } from "react";
import { SECTION_NAMES, SettingsState, initialSettingsState } from "./bridgeTypes";
import { BridgeRejection, SettingsBridge, createSettingsBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

// Phase 3 increment 2: the rail + client-side navigation shell only. Each
// section renders a placeholder - its real fields land in its own later
// increment (see the Phase 3 checklist item's recorded scope note in
// doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md).
function App() {
  const [state, setState] = useState<SettingsState>(initialSettingsState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<SettingsBridge | null>(null);

  useEffect(() => {
    const bridge = createSettingsBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Settings unavailable"
        rejection={rejection}
        className="settings-shell bridge-error"
      />
    );
  }

  return (
    <div className="settings-shell">
      <nav className="settings-rail" aria-label="Settings sections">
        {SECTION_NAMES.map((section) => (
          <button
            key={section}
            type="button"
            className="settings-rail-button"
            aria-current={section === state.activeSection ? "page" : undefined}
            onClick={() => bridgeRef.current?.setActiveSection(section)}
          >
            {section}
          </button>
        ))}
      </nav>
      <div className="settings-page" role="region" aria-label={state.activeSection}>
        {state.activeSection}
      </div>
    </div>
  );
}

export default App;
