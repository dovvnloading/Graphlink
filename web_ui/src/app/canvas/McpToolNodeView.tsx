import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { Button } from "../ui/Button";
import { Disclosure } from "../ui/Disclosure";
import { EmptyState } from "../ui/EmptyState";
import { InlineAlert } from "../ui/InlineAlert";
import { MenuItem } from "../ui/Menu";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { useConfirm } from "../ui/ConfirmDialog";
import { formatCount } from "../ui/format";
import { useOverlays } from "../overlays/overlays";
import { CollapseToggleButton } from "./CollapseToggleButton";
import { McpApprovalPanel } from "./McpApprovalPanel";
import { createMcpDraft, draftArguments, McpArgumentEditor, type McpDraft } from "./McpArgumentEditor";
import { McpResultView } from "./McpResultView";
import { McpReconciliation } from "./McpReconciliation";
import { WsTimeoutError, WsUnavailableError } from "../../lib/ws/transport";
import { NodeHeading } from "./NodeHeading";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import type { MenuPosition } from "./menuPosition";
import type { SceneStore } from "./sceneStore";
import { mcpTableColumns, readMcpState, schemaDefaults, type McpCatalog, type McpParent, type McpPreview, type McpState, type McpTool } from "./mcpConnect";
import { useLodVisibility } from "./useLodVisibility";
import "./McpToolNodeView.css";

export interface McpToolNodeData extends Record<string, unknown> {
  title: string;
  raw: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  sourceRevision: string;
  store: SceneStore;
}
export type McpToolFlowNode = Node<McpToolNodeData, "mcp_tool">;

function configuredTool(state: McpState): McpTool | null {
  const config = state.config;
  return config ? { serverId: config.serverId, serverName: config.serverName, name: config.toolName,
    description: config.description, inputSchema: config.inputSchema, outputSchema: config.outputSchema,
    annotations: config.annotations, allowed: true } : null;
}

function McpToolEditor({ id, data, state }: { id: string; data: McpToolNodeData; state: McpState }) {
  const overlays = useOverlays();
  const savedTool = useMemo(() => configuredTool(state), [state]);
  const [catalog, setCatalog] = useState<McpCatalog>({ servers: [], tools: [] });
  const [serverId, setServerId] = useState(state.config?.serverId ?? "");
  const [selectedTool, setSelectedTool] = useState<McpTool | null>(savedTool);
  const [query, setQuery] = useState("");
  const [discoveredServer, setDiscoveredServer] = useState("");
  const [draft, setDraft] = useState<McpDraft>(() => createMcpDraft(state.config?.inputSchema ?? {},
    state.config?.arguments ?? {}, state.config?.bindings ?? {}));
  const [parents, setParents] = useState<McpParent[]>([]);
  const [busy, setBusy] = useState("");
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<(McpPreview & { sourceRevision: string }) | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [seenConfig, setSeenConfig] = useState(state.config?.bindingId ?? "");
  const [configConflict, setConfigConflict] = useState(false);
  const [freshness, setFreshness] = useState<{ key: string; current: boolean; error: string } | null>(null);
  const freshnessKey = JSON.stringify([data.sourceRevision, state.config?.bindingId, state.latest?.provenance.receiptHash, state.status]);
  if (seenConfig !== (state.config?.bindingId ?? "")) {
    setSeenConfig(state.config?.bindingId ?? "");
    setPreview(null);
    if (!dirty || busy === "Saving inputs…" || busy === "Preparing preview…") {
      setServerId(state.config?.serverId ?? ""); setSelectedTool(savedTool);
      setDraft(createMcpDraft(state.config?.inputSchema ?? {}, state.config?.arguments ?? {}, state.config?.bindings ?? {}));
      setDirty(false); setConfigConflict(false);
    } else setConfigConflict(true);
  }
  const operationId = useRef("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    void data.store.listMcpTools("").then(value => { if (current) setCatalog(value); })
      .catch(failure => { if (current) setError(failure instanceof Error ? failure.message : "Servers could not be loaded. Check MCP settings."); });
    return () => { current = false; };
  }, [data.store]);
  useEffect(() => {
    let current = true;
    void data.store.getMcpToolSources(id).then(value => { if (current) setParents(value); })
      .catch(failure => { if (current) setError(failure instanceof Error ? failure.message : "Connected inputs could not be loaded. Reconnect and check this node."); });
    return () => { current = false; };
  }, [data.store, id, data.sourceRevision]);
  useEffect(() => {
    let current = true;
    void data.store.getMcpToolStatus(id).then(value => {
      if (current) setFreshness({ key: freshnessKey, current: value.current, error: "" });
    }).catch(failure => {
      if (current) setFreshness({ key: freshnessKey, current: false,
        error: failure instanceof Error ? failure.message : "Saved tool freshness could not be verified. Reconnect and reopen the node." });
    });
    return () => { current = false; };
  }, [data.store, id, freshnessKey]);
  const running = !!data.pendingRequestId || busy === "Running tool…";
  const disabled = !!busy || running;
  const unresolved = uncertain || state.status === "reconciliation-required";
  const activePreview = preview?.sourceRevision === data.sourceRevision ? preview : null;
  function openSettings() {
    data.store.openModelTaskSettings("mcp servers");
    overlays.open("settings", "dialog");
  }
  function changeDraft(value: McpDraft) { setDraft(value); setDirty(true); setPreview(null); setNotice(""); }
  function selectTool(name: string) {
    const tool = catalog.tools.find(item => item.serverId === serverId && item.name === name);
    if (!tool) return;
    setSelectedTool(tool);
    setDraft(createMcpDraft(tool.inputSchema, schemaDefaults(tool.inputSchema), {}));
    setDirty(true); setPreview(null); setNotice(""); setError("");
  }
  async function perform(label: string, action: () => Promise<void>) {
    if (busy) return;
    setBusy(label); setError(""); setNotice("");
    try { await action(); }
    catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "The tool action could not finish. Check the server and try again."); }
    finally { if (mounted.current) setBusy(""); }
  }
  function discover() {
    void perform("Discovering tools…", async () => {
      const result = await data.store.listMcpTools(serverId);
      if (!mounted.current) return;
      setCatalog(result);
      const problem = result.errors?.find(item => item.serverId === serverId);
      if (problem) throw new Error(problem.message);
      setDiscoveredServer(serverId);
      if (selectedTool?.serverId === serverId) {
        const refreshed = result.tools.find(tool => tool.serverId === serverId && tool.name === selectedTool.name);
        if (refreshed) {
          try {
            setDraft(createMcpDraft(refreshed.inputSchema, draftArguments(selectedTool.inputSchema, draft), draft.bindings));
            setSelectedTool(refreshed);
          } catch { setNotice("Tools discovered. Finish editing the arguments before choosing the refreshed tool schema."); }
        }
      }
      setNotice(`${formatCount(result.tools.filter(item => item.serverId === serverId).length)} tools discovered.`);
    });
  }
  async function saveConfiguration() {
    if (!selectedTool) throw new Error("Choose a tool before saving its inputs.");
    await data.store.configureMcpTool(id, { serverId: selectedTool.serverId, toolName: selectedTool.name,
      arguments: draftArguments(selectedTool.inputSchema, draft), bindings: draft.bindings });
    if (mounted.current) { setDirty(false); setConfigConflict(false); }
  }
  function prepare() {
    void perform("Preparing preview…", async () => {
      await saveConfiguration();
      const next = await data.store.previewMcpTool(id);
      if (!mounted.current) return;
      operationId.current = crypto.randomUUID();
      setPreview({ ...next, sourceRevision: data.sourceRevision }); setUncertain(false);
    });
  }
  function run() {
    if (!activePreview || unresolved) return;
    const reviewed = activePreview;
    setPreview(null);
    void perform("Running tool…", async () => {
      try {
        const result = await data.store.runMcpTool(id, reviewed.previewToken, operationId.current);
        if (mounted.current) {
          const step = result.steps.find(item => item.nodeId === id);
          setUncertain(step?.status === "reconciliation-required");
          if (step?.status === "completed" || step?.status === "current")
            setNotice("Tool run finished. The saved result includes its provenance.");
          else setError(step?.detail || "The tool did not complete. Review its request receipt before trying again.");
        }
      } catch (failure) {
        if (mounted.current) setUncertain(failure instanceof WsTimeoutError || failure instanceof WsUnavailableError);
        throw failure;
      }
    });
  }
  const servers = catalog.servers;
  const serverOptions = servers.map(server => ({ value: server.id, label: server.name,
    description: server.enabled ? server.transport === "stdio" ? "Local command" : "Remote HTTP" : "Disabled in settings", disabled: !server.enabled }));
  if (serverId && !serverOptions.some(server => server.value === serverId)) serverOptions.push({
    value: serverId, label: selectedTool?.serverName ?? "Saved server", description: "Unavailable in settings", disabled: true,
  });
  const availableTools = catalog.tools.filter(tool => tool.serverId === serverId);
  const toolOptions = availableTools.filter(tool => !query ||
    `${tool.name} ${tool.title ?? ""} ${tool.description}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
    .map(tool => ({ value: tool.name, label: tool.title || tool.name, description: tool.description,
      disabled: !tool.allowed }));
  if (selectedTool && selectedTool.serverId === serverId && !toolOptions.some(tool => tool.value === selectedTool.name))
    toolOptions.unshift({ value: selectedTool.name, label: selectedTool.name, description: "Saved tool configuration", disabled: false });
  const selectedAllowed = !!selectedTool && (discoveredServer === serverId
    ? availableTools.some(tool => tool.name === selectedTool.name && tool.allowed) : selectedTool.allowed);
  return <>
    {configConflict && <InlineAlert variant="warning" title="Saved inputs changed"
      body={<>Your unsaved draft is still here. Reload the saved inputs, or save your draft to replace them.
        <Button size="sm" onClick={() => {
          setServerId(state.config?.serverId ?? ""); setSelectedTool(savedTool);
          setDraft(createMcpDraft(state.config?.inputSchema ?? {}, state.config?.arguments ?? {}, state.config?.bindings ?? {}));
          setDirty(false); setConfigConflict(false); setPreview(null);
        }}>Reload saved inputs</Button></>} />}
    <div className="mcp-section-heading"><span className="mcp-help">Connect tools to your graph</span>
      <Button size="sm" iconStart="settings" onClick={openSettings}>Servers</Button></div>
    {servers.length === 0 && !selectedTool ? <EmptyState icon="hub" title="Add an MCP server"
      body="Configure a local command or remote server, then discover its tools here." /> : <>
      <Select label="MCP server" value={serverId} disabled={disabled} options={serverOptions} placeholder="Choose a server…"
        onChange={value => { setServerId(value); setSelectedTool(null); setPreview(null); setDirty(true); setQuery(""); }} />
      <Button size="sm" iconStart="refresh" disabled={!serverId || disabled} onClick={discover}>Discover tools</Button>
      {(availableTools.length > 0 || selectedTool) && <>
        <TextField label="Search tools" type="search" prefixIcon="search" value={query} onValueChange={setQuery}
          placeholder="Search tool names and descriptions" disabled={disabled} />
        <Select label="Tool" value={selectedTool?.name ?? ""} disabled={disabled} options={toolOptions} onChange={selectTool}
          placeholder="Choose a tool…" />
      </>}
    </>}
    {selectedTool && <>
      <p className="mcp-tool-description">{selectedTool.description || selectedTool.name}</p>
      {selectedTool.annotations.readOnlyHint === true && <p className="mcp-help">Server declares this tool read-only. Permissions still apply.</p>}
      <McpArgumentEditor schema={selectedTool.inputSchema} draft={draft} parents={parents} disabled={disabled} onChange={changeDraft} />
      <Disclosure title="Tool schema"><pre className="mcp-json-output">{JSON.stringify(selectedTool.inputSchema, null, 2)}</pre></Disclosure>
      <div className="mcp-actions">
        <Button disabled={disabled || unresolved} onClick={() => { void perform("Saving inputs…", async () => {
          await saveConfiguration(); setPreview(null); setNotice("Inputs saved. This tool is ready for Graph Runner.");
        }); }}>Save inputs</Button>
        {!activePreview && <Button variant="primary" disabled={disabled || unresolved || !selectedAllowed} onClick={prepare}>Preview tool</Button>}
        {running && <Button iconStart="stop" onClick={() => {
          void data.store.cancelMcpTool(id).catch(failure => setError(failure instanceof Error ? failure.message : "Cancellation could not be requested. Check the connection."));
        }}>Cancel tool</Button>}
      </div>
    </>}
    {activePreview && <section className="mcp-preview" aria-label="Tool preview">
      <strong>{activePreview.serverName} · {activePreview.toolName}</strong>
      <p className="mcp-help">Review the resolved arguments. Running this tool can affect the connected service.</p>
      <pre className="mcp-json-output">{JSON.stringify(activePreview.arguments, null, 2)}</pre>
      {activePreview.scopes?.length ? <p className="mcp-help">Scopes: {activePreview.scopes.join(", ")}</p> : null}
      {activePreview.warnings?.map((warning, i) => <p className="mcp-help" key={i}>{warning}</p>)}
      <div className="mcp-actions"><Button onClick={() => setPreview(null)} disabled={disabled}>Dismiss preview</Button>
        <Button variant="primary" iconStart="play" onClick={run} disabled={disabled || unresolved}>Run tool</Button></div>
    </section>}
    {unresolved && <InlineAlert variant="warning" title="Check the external outcome"
      body="This request may already have run. Check the connected service and the Graph Runner receipt before creating another attempt." />}
    {unresolved && <McpReconciliation store={data.store} nodeId={id} onReconciled={() => { setUncertain(false); setError(""); setPreview(null); }} />}
    {(error || state.error) && <InlineAlert variant="error" title="Tool needs attention" body={error || state.error} />}
    {(busy || notice) && <p role="status" className="mcp-help">{busy || notice}</p>}
    <McpApprovalPanel store={data.store} active={running} nodeId={id} />
    {state.latest && <>
      {freshness?.key === freshnessKey && freshness.error && <p role="status" className="mcp-help">{freshness.error}</p>}
      <McpResultView latest={state.latest} history={state.history}
        stale={dirty || freshness?.key === freshnessKey && !freshness.current} nodeId={id} store={data.store} />
    </>}
  </>;
}

export const McpToolNodeView = memo(function McpToolNodeView({ data, id, selected }: NodeProps<McpToolFlowNode>) {
  const askConfirm = useConfirm();
  const overlays = useOverlays();
  const [menu, setMenu] = useState<MenuPosition | null>(null);
  const state = useMemo(() => readMcpState(data.raw), [data.raw]);
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  function toggle() { data.store.setChatCollapsed(id, !data.isCollapsed); }
  return <NodeShell kindClassName="mcp-tool-node" selected={!!selected} collapsed={collapsed}
    textOutput tablePort={state?.latest && resultHasTable(state) ? "source" : undefined}
    header={<><CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={toggle} />
      <NodeHeading>{data.title || "MCP tool"}</NodeHeading></>}
    bodyClassName="mcp-tool-body nodrag nopan nowheel" onMenuChange={setMenu}
    menu={menu && <NodeMenu position={menu} onClose={() => setMenu(null)} ariaLabel="MCP node actions">
      <MenuItem label={data.isCollapsed ? "Expand" : "Collapse"} icon={data.isCollapsed ? "maximize" : "minimize"}
        onSelect={() => { toggle(); setMenu(null); }} />
      <MenuItem label="Open graph runner" icon="play" onSelect={() => {
        data.store.requestGraphRunnerNodes([id]); overlays.open("graph-runner", "dialog"); setMenu(null);
      }} />
      <MenuItem label="Delete node" destructive onSelect={() => {
        setMenu(null);
        void askConfirm({ title: "Delete MCP node?", body: "Delete this node and its saved results? Completed actions in the connected service remain in effect.",
          confirmLabel: "Delete node", destructive: true }).then(accepted => { if (accepted) data.store.removeNodes([id]); });
      }} />
    </NodeMenu>}
  >{state ? <McpToolEditor id={id} data={data} state={state} key={state.nodeKey} />
    : <InlineAlert variant="error" title="MCP tool unavailable" body="This saved tool could not be read. Enable MCP Connect in plugin settings and reopen the graph." />}</NodeShell>;
}, (previous, next) => previous.id === next.id && previous.selected === next.selected
  && previous.data.title === next.data.title && previous.data.raw === next.data.raw
  && previous.data.isCollapsed === next.data.isCollapsed && previous.data.pendingRequestId === next.data.pendingRequestId
  && previous.data.sourceRevision === next.data.sourceRevision && previous.data.store === next.data.store);

function resultHasTable(state: McpState): boolean {
  return !!state.latest && !state.latest.isError && mcpTableColumns(state.latest.structuredContent).length > 0;
}
