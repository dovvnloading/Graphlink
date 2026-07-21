import { useEffect, useRef, useState } from "react";
import { PluginPickerState, initialPluginPickerState } from "./bridgeTypes";
import { BridgeRejection, PluginPickerBridge, createPluginPickerBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<PluginPickerState>(initialPluginPickerState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [currentCategoryName, setCurrentCategoryName] = useState<string | null>(null);
  const bridgeRef = useRef<PluginPickerBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bridge = createPluginPickerBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  // Mirrors PluginFlyoutPanel._build_category_buttons()'s own fallback: keep
  // whichever category is already selected as long as it still exists,
  // otherwise fall back to the first category. Computed during render
  // (the same "reset local state when a fresh X begins" pattern
  // composer-picker's own openToken reset uses), not in an effect - Categories
  // are static app-lifetime data, so this only ever really applies once, on
  // the first - and only - state publish.
  if (state.categories.length > 0 && !state.categories.some((category) => category.name === currentCategoryName)) {
    setCurrentCategoryName(state.categories[0].name);
  }

  // Content-driven height negotiation, matching every prior picker host's
  // own ResizeObserver-based reporting.
  useEffect(() => {
    const element = shellRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height;
      if (height) bridgeRef.current?.resize(Math.ceil(height));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Escape closes from anywhere in the surface, matching the legacy native
  // popup's own Qt.WindowType.Popup dismiss-on-focus-loss behavior for the
  // keyboard case - outside-click-close itself is handled natively
  // (WebIslandHost's dismiss_on_outside_focus).
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") bridgeRef.current?.close();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="The plugin picker is unavailable"
        rejection={rejection}
        className="plugin-picker-shell bridge-error"
      />
    );
  }

  const activeCategory =
    state.categories.find((category) => category.name === currentCategoryName) ?? state.categories[0] ?? null;

  return (
    <div className="plugin-picker-shell" ref={shellRef}>
      <div className="plugin-picker-rail">
        <p className="plugin-picker-rail-label">Categories</p>
        <div className="plugin-picker-rail-buttons">
          {state.categories.map((category) => (
            <button
              key={category.name}
              type="button"
              className={
                "plugin-picker-category-btn" + (category.name === activeCategory?.name ? " active" : "")
              }
              aria-pressed={category.name === activeCategory?.name}
              onClick={() => setCurrentCategoryName(category.name)}
            >
              {category.name}
            </button>
          ))}
        </div>
      </div>

      <div className="plugin-picker-content">
        {activeCategory ? (
          <>
            <div className="plugin-picker-header">
              <p className="plugin-picker-title">{activeCategory.name}</p>
              <p className="plugin-picker-meta">
                {activeCategory.plugins.length} plugin{activeCategory.plugins.length !== 1 ? "s" : ""}
              </p>
            </div>
            <ul className="plugin-picker-list" role="listbox" aria-label={`${activeCategory.name} plugins`}>
              {activeCategory.plugins.map((plugin) => (
                <li key={plugin.name} role="option">
                  <button
                    type="button"
                    className="plugin-picker-row"
                    onClick={() => bridgeRef.current?.executePlugin(plugin.name)}
                  >
                    <span className="plugin-picker-row-copy">
                      <span className="plugin-picker-row-label">{plugin.name}</span>
                      <span className="plugin-picker-row-description">{plugin.description}</span>
                    </span>
                    <span className="plugin-picker-row-chevron" aria-hidden="true">
                      &rsaquo;
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="plugin-picker-empty">No plugins are available.</p>
        )}
      </div>
    </div>
  );
}

export default App;
