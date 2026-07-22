import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppPluginsState } from "../../lib/bridge-core/generated/app-plugins-state";
import { Popover } from "../overlays/overlays";

/**
 * The plugin picker popover (Qt-removal plan R2.5) - plugin-picker island's
 * SPA successor. Categories/plugins are static app-lifetime data from the
 * backend (backend/plugins.py); selecting a plugin fires the real
 * `executePlugin` intent, which surfaces an honest "lands in R3/R5"
 * notification instead of creating a node - node types don't exist yet.
 */

const initialState: AppPluginsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  categories: [],
};

export function PluginPicker({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<AppPluginsState>(initialState);
  const [activeCategoryName, setActiveCategoryName] = useState<string | null>(null);

  useEffect(() => {
    return transport.subscribe("app-plugins", (payload) => {
      const validated = TOPIC_VALIDATORS["app-plugins"](payload);
      if (validated.ok) setState(validated.value as AppPluginsState);
      else console.error("[app-plugins] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  // Categories are static app-lifetime data (this only really applies once,
  // on the first snapshot) - reset-during-render, same as CommandPalette's
  // wasOpen pattern, rather than an effect.
  if (state.categories.length > 0 && !state.categories.some((c) => c.name === activeCategoryName)) {
    setActiveCategoryName(state.categories[0].name);
  }

  const activeCategory =
    state.categories.find((c) => c.name === activeCategoryName) ?? state.categories[0] ?? null;

  return (
    <Popover name="plugins" className="plugin-picker-shell">
      <div className="plugin-picker-rail">
        <p className="plugin-picker-rail-label">Categories</p>
        <div className="plugin-picker-rail-buttons">
          {state.categories.map((category) => (
            <button
              key={category.name}
              type="button"
              className={"plugin-picker-category-btn" + (category.name === activeCategory?.name ? " active" : "")}
              aria-pressed={category.name === activeCategory?.name}
              onClick={() => setActiveCategoryName(category.name)}
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
                    onClick={() => transport.intent("app-plugins", "executePlugin", [plugin.name])}
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
    </Popover>
  );
}
