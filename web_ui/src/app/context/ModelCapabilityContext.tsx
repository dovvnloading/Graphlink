import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { ComposerStore, ModelCapabilityPreview } from "../chrome/composerStore";

// One lookup for the active route, shared by canvas and composer.
// Model changes immediately hide the previous result, even during an in-flight
// request; a late response cannot show a warning about the wrong model.
export const ModelCapabilityContext = createContext<ModelCapabilityPreview | null>(null);

/** Share the composer's model selection with Agent cards; no second store. */
export const ComposerModelStoreContext = createContext<ComposerStore | null>(null);
export function useComposerModelStore(): ComposerStore | null {
  return useContext(ComposerModelStoreContext);
}

export function useModelCapabilityPreview(): ModelCapabilityPreview | null {
  return useContext(ModelCapabilityContext);
}

export function ModelCapabilityProvider({ store, children }: {
  store: ComposerStore;
  children: ReactNode;
}) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const { provider, modelId } = composer.route;
  const [loaded, setLoaded] = useState<ModelCapabilityPreview | null>(null);

  useEffect(() => {
    if (!provider || !modelId) return;
    let cancelled = false;
    void store.loadModelCapabilityPreview(provider, modelId)
      .then((value) => { if (!cancelled) setLoaded(value); })
      .catch(() => { if (!cancelled) setLoaded(null); });
    return () => { cancelled = true; };
  }, [store, provider, modelId]);

  const current = loaded?.provider === provider && loaded.modelId === modelId ? loaded : null;
  return (
    <ComposerModelStoreContext.Provider value={store}>
      <ModelCapabilityContext.Provider value={current}>{children}</ModelCapabilityContext.Provider>
    </ComposerModelStoreContext.Provider>
  );
}
