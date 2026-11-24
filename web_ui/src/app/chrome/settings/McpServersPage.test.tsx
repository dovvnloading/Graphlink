import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { McpServerConfig } from "../../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../../lib/ws/transport";
import { OverlayProvider } from "../../overlays/overlays";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { McpServersPage, parseMcpArguments } from "./McpServersPage";

const remote: McpServerConfig = { id: "remote", name: "Project tools", transport: "streamable_http", url: "https://tools.example/mcp",
  command: "", args: [], scopes: ["read"], approval: "always", enabledTools: [], enabled: true, timeout: 30,
  envKeys: [], headerKeys: ["Authorization", "X-API-Key"] };

function mount(servers: McpServerConfig[] = []) {
  const fireIntent = vi.fn<WsTransport["fireIntent"]>();
  const request = vi.fn<WsTransport["request"]>().mockResolvedValue({ servers: [remote], tools: [{
    serverId: remote.id, serverName: remote.name, name: "search", title: "Search projects", description: "Read matching project records.",
    inputSchema: { type: "object" }, annotations: { readOnlyHint: true }, allowed: true,
  }], errors: [] });
  const transport = { fireIntent, request } as unknown as WsTransport;
  render(<OverlayProvider><McpServersPage state={{ mcpServers: servers }} transport={transport} /><ConfirmDialog /></OverlayProvider>);
  return { fireIntent, request, user: userEvent.setup() };
}

function choose(label: string, option: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.click(screen.getByRole("option", { name: option }));
}

describe("MCP server configuration", () => {
  it("preserves quoted paths, Windows backslashes, empty arguments, and JSON argv", () => {
    expect(parseMcpArguments('server --path "a folder" \'another path\' ""')).toEqual(["server", "--path", "a folder", "another path", ""]);
    expect(parseMcpArguments('server C:\\Users\\project')).toEqual(["server", "C:\\Users\\project"]);
    expect(parseMcpArguments('["C:\\\\Program Files\\\\tool", ""]')).toEqual(["C:\\Program Files\\tool", ""]);
    expect(() => parseMcpArguments('server "missing')).toThrow(/unclosed quote/);
    expect(() => parseMcpArguments('["server", 3]')).toThrow(/only strings/);
  });
  it("saves a remote URL with explicit bearer and custom headers, then clears the secret fields", async () => {
    const { user, fireIntent } = mount();
    fireEvent.change(screen.getByLabelText("Server name"), { target: { value: "Remote" } });
    choose("Connection type", "Remote server (Streamable HTTP)");
    fireEvent.change(screen.getByLabelText("MCP endpoint URL"), { target: { value: "https://tools.example/mcp" } });
    fireEvent.change(screen.getByLabelText("Bearer token (optional)"), { target: { value: "new-secret" } });
    fireEvent.change(screen.getByLabelText("Authentication headers (one Name=value per line)"), { target: { value: "X-Workspace=project=a" } });
    await user.click(screen.getByRole("button", { name: "Add server" }));
    expect(fireIntent).toHaveBeenCalledWith("app-settings", "setMcpServers", [[expect.objectContaining({
      name: "Remote", transport: "streamable_http", url: "https://tools.example/mcp", command: "", args: [],
      headers: { Authorization: "Bearer new-secret", "X-Workspace": "project=a" }, headerKeys: ["Authorization", "X-Workspace"],
    })]], undefined, true);
    expect(screen.queryByDisplayValue("new-secret")).toBeNull();
  });
  it("editing keeps saved headers write-only and omits secret values when unchanged", async () => {
    const { user, fireIntent } = mount([remote]);
    await user.click(screen.getByRole("button", { name: "Actions for Project tools" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    expect(screen.getByLabelText("Bearer token (optional)")).toHaveValue("");
    expect(screen.getByLabelText("Authentication headers (one Name=value per line)")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Server name"), { target: { value: "Renamed" } });
    await user.click(screen.getByRole("button", { name: "Save server" }));
    const sent = (fireIntent.mock.calls[0][2]?.[0] as McpServerConfig[])[0];
    expect(sent).toMatchObject({ id: remote.id, name: "Renamed", headerKeys: remote.headerKeys });
    expect(sent).not.toHaveProperty("headers");
    expect(sent).not.toHaveProperty("env");
  });
  it("explicit clear removes authentication without resending a fake masked token", async () => {
    const { user, fireIntent } = mount([remote]);
    await user.click(screen.getByRole("button", { name: "Actions for Project tools" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    await user.click(screen.getByRole("checkbox", { name: "Clear saved authentication headers" }));
    await user.click(screen.getByRole("button", { name: "Save server" }));
    expect(fireIntent.mock.calls[0][2]?.[0]).toEqual([expect.objectContaining({ headers: {}, headerKeys: [] })]);
  });
  it("discovers only on request and disabling the final tool cannot accidentally grant all tools", async () => {
    const { user, request, fireIntent } = mount([remote]);
    expect(request).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Test connection and discover tools" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("scene", "listMcpTools", ["remote"], 120_000));
    await user.click(await screen.findByRole("checkbox", { name: "Search projects" }));
    expect(fireIntent.mock.calls[0][2]?.[0]).toEqual([expect.objectContaining({ enabled: false, enabledTools: ["search"] })]);
  });
});
