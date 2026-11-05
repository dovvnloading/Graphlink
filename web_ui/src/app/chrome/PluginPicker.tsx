import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppPluginsState } from "../../lib/bridge-core/generated/app-plugins-state";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";
import { searchPlugins, type PluginSearchHit } from "./pluginPickerSearch";

/**
 * Backend-driven plugin picker. A local search spans all category entries,
 * while an empty query keeps the original category browsing experience.
 * All activation paths use the same backend executePlugin intent: searching
 * never bypasses plugin grants, node selection, or the viewport spawn point.
 */
const initialState: AppPluginsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  categories: [],
  grants: [],
};

export function PluginPicker({ transport, store }: { transport: WsTransport; store: SceneStore }) {
  const overlays = useOverlays();
  const isOpen = overlays.isOpen("plugins");
  const [state, setState] = useState<AppPluginsState>(initialState);
  const [activeCategoryName, setActiveCategoryName] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const rowRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const hintId = useId();

  useEffect(() => {
    return transport.subscribe("app-plugins", (payload) => {
      const validated = TOPIC_VALIDATORS["app-plugins"](payload);
      if (validated.ok) setState(validated.value as AppPluginsState);
      else console.error("[app-plugins] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  // The picker stays mounted when its Popover is closed. Reset only on a
  // fresh open, so a partially typed search is not erased during browsing.
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (wasOpen !== isOpen) {
    setWasOpen(isOpen);
    if (isOpen) setQuery("");
  }

  if (state.categories.length > 0 && !state.categories.some((c) => c.name === activeCategoryName)) {
    setActiveCategoryName(state.categories[0].name);
  }

  const activeCategory =
    state.categories.find((c) => c.name === activeCategoryName) ?? state.categories[0] ?? null;
  const isSearching = query.trim().length > 0;
  const visiblePlugins: PluginSearchHit[] = isSearching
    ? searchPlugins(state.categories, query)
    : (activeCategory?.plugins.map((plugin) => ({ categoryName: activeCategory.name, plugin })) ?? []);

  function executePlugin(name: string) {
    const spawn = store.getSpawnPoint();
    transport.fireIntent("app-plugins", "executePlugin", [
      name,
      store.getSelectedNodeId(),
      spawn.x,
      spawn.y,
    ]);
    overlays.close();
  }

  function focusRow(index: number) {
    rowRefs.current[index]?.focus();
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      focusRow(event.key === "ArrowDown" ? 0 : visiblePlugins.length - 1);
    } else if (event.key === "Enter" && isSearching && visiblePlugins.length > 0) {
      // The first result is ranked by name relevance; Enter from the search
      // field activates it without requiring a mouse or an extra Tab.
      event.preventDefault();
      executePlugin(visiblePlugins[0].plugin.name);
    }
  }

  function handleRowKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "ArrowDown" && index < visiblePlugins.length - 1) {
      event.preventDefault();
      focusRow(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (index === 0) searchRef.current?.focus();
      else focusRow(index - 1);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      focusRow(event.key === "Home" ? 0 : visiblePlugins.length - 1);
    }
    // Native button Enter/Space activation, Tab, and overlay Escape are
    // deliberately left to the browser and the shared overlay system.
  }

  return (
    <Popover name="plugins" label="Plugins" className="plugin-picker-shell" anchored>
      <div className="plugin-picker-search">
        <input
          ref={searchRef}
          type="search"
          className="plugin-picker-search-input"
          aria-label="Search plugins"
          aria-describedby={hintId}
          placeholder="Find a plugin or task…"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleSearchKeyDown}
        />
        {query.length > 0 && (
          <button
            type="button"
            className="plugin-picker-clear-btn"
            onClick={() => {
              setQuery("");
              searchRef.current?.focus();
            }}
          >
            Clear
          </button>
        )}
      </div>
      <p id={hintId} className="plugin-picker-hint">
        Search all categories. Arrow keys move through results; Enter adds a plugin.
      </p>
      <div className="plugin-picker-main">
        {!isSearching && (
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
        )}

        <div className="plugin-picker-content">
          {state.categories.length === 0 ? (
            <p className="plugin-picker-empty">No plugins are available.</p>
          ) : (
            <>
              <div className="plugin-picker-header">
                <p className="plugin-picker-title">{isSearching ? "Search results" : activeCategory?.name}</p>
                <p className="plugin-picker-meta" aria-live="polite">
                  {visiblePlugins.length} {isSearching ? "matching " : ""}
                  plugin{visiblePlugins.length !== 1 ? "s" : ""}
                </p>
              </div>
              {visiblePlugins.length === 0 ? (
                <p className="plugin-picker-empty">
                  {isSearching ? "No plugins match this search." : "No plugins in this category."}
                </p>
              ) : (
                <ul
                  className="plugin-picker-list"
                  aria-label={isSearching ? "Plugin search results" : (activeCategory?.name ?? "") + " plugins"}
                >
                  {visiblePlugins.map(({ plugin, categoryName }, index) => (
                    <li key={categoryName + ":" + plugin.name}>
                      <button
                        ref={(element) => { rowRefs.current[index] = element; }}
                        type="button"
                        className="plugin-picker-row"
                        onKeyDown={(event) => handleRowKeyDown(event, index)}
                        onClick={() => executePlugin(plugin.name)}
                      >
                        <span className="plugin-picker-row-copy">
                          <span className="plugin-picker-row-label">{plugin.name}</span>
                          <span className="plugin-picker-row-description">{plugin.description}</span>
                          {isSearching && (
                            <span className="plugin-picker-row-category">{categoryName}</span>
                          )}
                        </span>
                        <span className="plugin-picker-row-chevron" aria-hidden="true">
                          &rsaquo;
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </Popover>
  );
}
