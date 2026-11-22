import { useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import type { SceneStore } from "./sceneStore";
import { readScienceState, type ScienceState } from "./scientificWorkbench";

interface SourceNode { id: string; title: string; pluginId: string; state: ScienceState }

export function ScienceTableSource({ targetId, packTitle, store, renderImporter }: {
  targetId: string; packTitle: string; store: SceneStore; renderImporter: (node: SourceNode) => ReactNode;
}) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const [chosen, setChosen] = useState("");
  const [created, setCreated] = useState<{ id: string; revision: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const nodes = useMemo(() => scene.nodes.flatMap((row) => {
    if (row.id === targetId || !row.kind.startsWith("science_") || !row.pluginState?.science) return [];
    const state = readScienceState(row.pluginState.science);
    if (!state || state.error || row.kind !== `science_${state.packId}.workbench`) return [];
    return [{ id: row.id, title: row.title, pluginId: "science_" + state.packId, state }];
  }), [scene.nodes, targetId]);
  const upstream = new Set(scene.edges.filter((edge) => edge.target === targetId).map((edge) => edge.source));
  const createdNode = nodes.find((node) => node.id === created?.id);
  const selected = nodes.find((node) => node.id === chosen) ?? createdNode ??
    nodes.find((node) => upstream.has(node.id)) ?? nodes.find((node) => node.state.table || node.state.latest?.tableAsset);
  const connected = !!selected && upstream.has(selected.id);
  const canCreate = !created || (!createdNode && scene.revision > created.revision);

  async function connect(create: boolean) {
    if (pending.current || (!create && !selected)) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const source = create ? await store.createScientificTableSource(packTitle, targetId) : selected?.id;
      if (!source) return;
      if (create) {
        setCreated({ id: source, revision: scene.revision });
        setChosen(source);
      }
      await store.connectScientificSource(source, targetId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Check the graph and reconnect the source.");
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return <section aria-label="Connect a source table" className="science-table-source">
    <strong>Connect a source table</strong>
    {nodes.length > 0 && <label>Source workbench<select value={selected?.id ?? ""} disabled={busy} onChange={(e) => {
      setChosen(e.target.value); setError("");
    }}>
      {!selected && <option value="">Choose a source workbench</option>}
      {nodes.map((n) => <option key={n.id} value={n.id}>{n.title} ({n.id}) · {
        n.state.table?.sourceName ?? (n.state.latest?.tableAsset ? "Generated result table" : "No table yet")
      }</option>)}
    </select></label>}
    <div className="science-actions">
      {selected && !connected && <button type="button" disabled={busy} onClick={() => void connect(false)}>Connect selected source</button>}
      {canCreate && <button type="button" disabled={busy} onClick={() => void connect(true)}>Create source and import</button>}
    </div>
    {busy && <p role="status">Connecting source…</p>}
    {error && <p role="alert">{error}</p>}
    {selected && connected && !selected.state.table && !selected.state.latest?.tableAsset &&
      <div><p>Import into {selected.title} ({selected.id}) to make its columns available here.</p>{renderImporter(selected)}</div>}
    {selected && connected && (selected.state.table || selected.state.latest?.tableAsset) &&
      <p role="status">Connected. Choose its table in the input below.</p>}
  </section>;
}
