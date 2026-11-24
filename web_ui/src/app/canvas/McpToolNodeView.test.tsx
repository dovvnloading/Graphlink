import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OverlayProvider } from "../overlays/overlays";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { WsRequestError, WsTimeoutError, type WsTransport } from "../../lib/ws/transport";
import { SceneStore } from "./sceneStore";
import { McpToolNodeView, type McpToolFlowNode } from "./McpToolNodeView";
import { McpApprovalPanel } from "./McpApprovalPanel";
import type { McpResult, McpState, McpTool } from "./mcpConnect";

const lodCalls = { count: 0 };
vi.mock("./useLodVisibility", async (importOriginal) => {
  const original = await importOriginal<typeof import("./useLodVisibility")>();
  return { ...original, useLodVisibility: (...args: Parameters<typeof original.useLodVisibility>) => {
    lodCalls.count++; return original.useLodVisibility(...args);
  } };
});

const tool: McpTool = { serverId: "srv", serverName: "Research", name: "search", description: "Search a connected source.",
  inputSchema: { type: "object", required: ["query"], properties: { query: { type: "string" }, limit: { type: "integer" },
    mode: { type: "string", enum: ["brief", "full"] } } }, annotations: {}, allowed: true };
const state: McpState = { version: 1, nodeKey: "test-node", config: { version: 1, bindingId: "binding", serverId: "srv",
  serverName: "Research", toolName: "search", description: tool.description, inputSchema: tool.inputSchema,
  annotations: {}, arguments: { query: "current topic", limit: 2, mode: "brief" }, bindings: {} }, latest: null, history: [], error: "" };
const result: McpResult = { version: 1, text: "Found two entries.", structuredContent: { items: [{ name: "First", score: 2 }, { name: "Second", score: 3 }] },
  content: [{ type: "text", text: "Found two entries." }], isError: false, provenance: { requestId: "request-1",
    startedAt: "2026-11-23T10:00:00Z", finishedAt: "2026-11-23T10:00:02Z", serverId: "srv", serverName: "Research",
    toolName: "search", inputHash: "input-hash", configHash: "config-hash", resultHash: "result-hash", receiptHash: "receipt-hash", sources: [] } };

function setup(initial: McpState = state, handler?: (intent: string, args: unknown[]) => unknown) {
  const request = vi.fn<WsTransport["request"]>(async (_topic, intent, args = []) => {
    const override = handler?.(intent, args);
    if (override !== undefined) return override;
    if (intent === "listMcpTools") return { servers: [{ id: "srv", name: "Research", enabled: true, transport: "stdio" }], tools: args[0] ? [tool] : [], errors: [] };
    if (intent === "getMcpToolSources") return [{ id: "source", title: "Upstream note", kind: "note", outputs: ["text", "json"] }];
    if (intent === "getMcpToolStatus") return { configured: true, current: true, status: "idle", error: "" };
    if (intent === "previewMcpTool") return { previewToken: "reviewed", serverName: "Research", toolName: "search", arguments: { query: "resolved source" }, inputHash: "hash", requiresApproval: true, scopes: [] };
    if (intent === "getMcpToolApprovals") return [];
    if (intent === "getMcpToolAttempts") return [];
    if (intent === "runMcpTool") return { runId: "run", status: "completed", steps: [{ nodeId: "target", status: "completed" }] };
    return { ok: true };
  });
  const fireIntent = vi.fn<WsTransport["fireIntent"]>();
  const store = new SceneStore({ request, fireIntent } as unknown as WsTransport);
  const data: McpToolFlowNode["data"] = { title: "MCP tool", raw: JSON.stringify(initial), isCollapsed: false,
    pendingRequestId: null, sourceRevision: "sources", store };
  function draw(current: McpState, sourceRevision = "sources", position = 0) {
    const props = { id: "target", selected: false, positionAbsoluteX: position,
      data: { ...data, raw: JSON.stringify(current), sourceRevision } } as NodeProps<McpToolFlowNode>;
    return <OverlayProvider><ReactFlowProvider><McpToolNodeView {...props} /><ConfirmDialog /></ReactFlowProvider></OverlayProvider>;
  }
  const view = render(draw(initial));
  return { request, fireIntent, store, view, user: userEvent.setup(),
    refresh: (current: McpState, sourceRevision?: string, position?: number) => view.rerender(draw(current, sourceRevision, position)) };
}

function choose(label: string, option: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: option }));
}

describe("MCP Connect tool node", () => {
  it("never connects to a server or runs a tool on mount, then previews resolved arguments before explicit execution", async () => {
    const { request, user } = setup();
    await screen.findByRole("textbox", { name: "query" });
    expect(request.mock.calls.some(([, intent, args]) => intent === "listMcpTools" && !!args?.[0])).toBe(false);
    expect(request.mock.calls.some(([, intent]) => intent === "runMcpTool")).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "query" }), { target: { value: "updated topic" } });
    await user.click(screen.getByRole("button", { name: "Preview tool" }));
    const preview = await screen.findByRole("region", { name: "Tool preview" });
    expect(preview).toHaveTextContent("resolved source");
    expect(request).toHaveBeenCalledWith("scene", "configureMcpTool", ["target", {
      serverId: "srv", toolName: "search", arguments: { query: "updated topic", limit: 2, mode: "brief" }, bindings: {},
    }], 120_000);
    expect(request.mock.calls.some(([, intent]) => intent === "runMcpTool")).toBe(false);
    await user.click(within(preview).getByRole("button", { name: "Run tool" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("scene", "runMcpTool", ["target", "reviewed", expect.any(String)], 600_000));
    expect(request.mock.calls.filter(([, intent]) => intent === "runMcpTool")).toHaveLength(1);
  });
  it("binds a source and JSON Pointer instead of persisting a copied literal", async () => {
    const { request, user } = setup();
    await waitFor(() => expect(request).toHaveBeenCalledWith("scene", "getMcpToolSources", ["target"]));
    choose("Input for query", "Upstream note");
    choose("Output for query", "Structured JSON");
    fireEvent.change(screen.getByRole("textbox", { name: "JSON Pointer for query" }), { target: { value: "/items/0/title" } });
    await user.click(screen.getByRole("button", { name: "Save inputs" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("scene", "configureMcpTool", ["target", {
      serverId: "srv", toolName: "search", arguments: { limit: 2, mode: "brief" },
      bindings: { query: { sourceId: "source", output: "json", pointer: "/items/0/title" } },
    }], 120_000));
  });
  it("rejects malformed inputs locally and invalidates a preview when edited", async () => {
    const { request, user } = setup();
    fireEvent.change(screen.getByRole("textbox", { name: "limit" }), { target: { value: "3.2" } });
    await user.click(screen.getByRole("button", { name: "Preview tool" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("finite integer");
    expect(request.mock.calls.some(([, intent]) => intent === "configureMcpTool")).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "limit" }), { target: { value: "3" } });
    await user.click(screen.getByRole("button", { name: "Preview tool" }));
    await screen.findByRole("region", { name: "Tool preview" });
    fireEvent.change(screen.getByRole("textbox", { name: "query" }), { target: { value: "new" } });
    expect(screen.queryByRole("region", { name: "Tool preview" })).toBeNull();
  });
  it("keeps timeout outcomes fenced, displays the request receipt, and requires an acknowledgement to reconcile", async () => {
    const { request, user } = setup(state, intent => {
      if (intent === "runMcpTool") throw new WsTimeoutError("The connection timed out.");
      if (intent === "getMcpToolAttempts") return [{ requestId: "uncertain-request", runId: "run", status: "reconciliation-required",
        phase: "reconciliation-required", toolName: "search", detail: "The remote outcome is unknown." }];
    });
    await user.click(screen.getByRole("button", { name: "Preview tool" }));
    await user.click(await screen.findByRole("button", { name: "Run tool" }));
    expect(await screen.findByText("uncertain-request")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview tool" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "I checked the outcome" }));
    const confirmation = await screen.findByRole("dialog", { name: "Record the checked outcome?" });
    expect(confirmation).toHaveTextContent("does not undo remote changes");
    expect(request.mock.calls.some(([, intent]) => intent === "reconcileMcpTool")).toBe(false);
    await user.click(within(confirmation).getByRole("button", { name: "I checked the outcome" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("scene", "reconcileMcpTool", ["target", "uncertain-request"]));
    expect(request.mock.calls.filter(([, intent]) => intent === "runMcpTool")).toHaveLength(1);
  });
  it("allows a new preview after a correlated validation rejection", async () => {
    const { user } = setup(state, intent => { if (intent === "runMcpTool") throw new WsRequestError("The input changed. Preview again."); });
    await user.click(screen.getByRole("button", { name: "Preview tool" }));
    await user.click(await screen.findByRole("button", { name: "Run tool" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Preview again");
    expect(screen.getByRole("button", { name: "Preview tool" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "External request reconciliation" })).toBeNull();
  });
  it("shows structured rows and immutable provenance, with keyboard-accessible result tabs", async () => {
    const { user } = setup({ ...state, latest: result });
    expect(screen.getByRole("table", { name: "Tool output" })).toHaveTextContent("Second");
    const tableTab = screen.getByRole("tab", { name: "Table" });
    tableTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Provenance" })).toHaveFocus();
    expect(screen.getByText("request-1")).toBeInTheDocument();
    expect(screen.getByText("input-hash")).toBeInTheDocument();
    expect(result.provenance.receiptHash).toBe("receipt-hash");
  });
  it("reloads changed saved configurations without overwriting an unsaved draft", async () => {
    const { refresh, user } = setup();
    await waitFor(() => expect(screen.getByRole("textbox", { name: "query" })).toHaveValue("current topic"));
    const changed: McpState = { ...state, config: { ...state.config!, bindingId: "external", arguments: { query: "external edit" } } };
    refresh(changed);
    expect(screen.getByRole("textbox", { name: "query" })).toHaveValue("external edit");
    fireEvent.change(screen.getByRole("textbox", { name: "query" }), { target: { value: "my draft" } });
    refresh({ ...changed, config: { ...changed.config!, bindingId: "undo", arguments: { query: "restored" } } });
    expect(screen.getByRole("textbox", { name: "query" })).toHaveValue("my draft");
    expect(screen.getByText("Saved inputs changed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reload saved inputs" }));
    expect(screen.getByRole("textbox", { name: "query" })).toHaveValue("restored");
  });
  it("checks saved output freshness on open and source changes, and never redraws content for position alone", async () => {
    let current = true;
    const saved = { ...state, latest: result };
    const { refresh, request } = setup(saved, intent => intent === "getMcpToolStatus"
      ? { configured: true, current, status: "completed", error: "" } : undefined);
    await waitFor(() => expect(request).toHaveBeenCalledWith("scene", "getMcpToolStatus", ["target"]));
    await act(async () => {});
    const count = lodCalls.count;
    refresh(saved, "sources", 200);
    expect(lodCalls.count).toBe(count);
    current = false;
    refresh(saved, "changed-source", 200);
    expect(await screen.findByText(/Inputs or configuration have changed/)).toBeInTheDocument();
    expect(request.mock.calls.filter(([, intent]) => intent === "getMcpToolStatus")).toHaveLength(2);
  });
  it("renders resolved Graph Runner tool approvals and sends only the explicit decision", async () => {
    const approval = { id: "approval", nodeId: "target", serverName: "Research", toolName: "search", arguments: { query: "resolved" }, scopes: ["read"] };
    const request = vi.fn<WsTransport["request"]>(async (_topic, intent) => intent === "getMcpToolApprovals" ? [approval] : { ok: true });
    const store = new SceneStore({ request } as unknown as WsTransport);
    const user = userEvent.setup();
    const view = render(<McpApprovalPanel store={store} active />);
    expect(await screen.findByRole("region", { name: "Approve search" })).toHaveTextContent("resolved");
    await user.click(screen.getByRole("button", { name: "Deny tool" }));
    expect(request).toHaveBeenCalledWith("scene", "approveMcpTool", ["approval", false]);
    view.unmount();
    await act(async () => {});
  });
});
