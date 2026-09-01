import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppPluginsState } from "../../lib/bridge-core/generated/app-plugins-state";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";

/**
 * The plugin picker popover (Qt-removal plan R2.5) - plugin-picker island's
 * SPA successor. Categories/plugins are static app-lifetime data from the
 * backend (backend/plugins.py); selecting a plugin fires the real
 * `executePlugin` intent, which surfaces an honest "lands in R3/R5"
 * notification instead of creating a node - node types don't exist yet.
 *
 * R5.1: every plugin's click also sends the canvas's currently-selected
 * node id (store.getSelectedNodeId()) as executePlugin's second argument -
 * plugin-node-creation wiring built once, here, rather than special-cased
 * per plugin.
 *
 * It now sends the canvas viewport's center alongside it. A plugin
 * registered with requires_parent=False is creatable with NOTHING selected,
 * and this is the position such a node spawns at - the missing piece that
 * previously made requires_parent=False unrepresentable and so forced every
 * plugin, System Prompt included, to demand a pre-existing node before it
 * could run at all.
 */

const initialState: AppPluginsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  categories: [],
  // ADR-014 stage 14.4: required by the "app-plugins" contract now, but
  // unused here - the grants list only matters to SettingsDialog.tsx's own
  // Plugins page (Settings > Plugins), not the picker itself.
  grants: [],
};

export function PluginPicker({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
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
    <Popover name="plugins" label="Plugins" className="plugin-picker-shell" anchored>
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
                <li key={plugin.name} role="option" aria-selected={false}>
                  <button
                    type="button"
                    className="plugin-picker-row"
                    onClick={() => {
                      const spawn = store.getSpawnPoint();
                      transport.fireIntent("app-plugins", "executePlugin", [
                        plugin.name,
                        store.getSelectedNodeId(),
                        spawn.x,
                        spawn.y,
                      ]);
                      // Close on select, matching the legacy popup's own
                      // post-execute dismiss - and so the resulting
                      // notification banner is seen unobstructed.
                      overlays.close();
                    }}
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
