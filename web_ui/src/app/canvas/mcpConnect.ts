/** MCP plugin payloads are versioned JSON inside the SDK's pluginState seam. */
export type McpJson = null | boolean | number | string | McpJson[] | { [key: string]: McpJson };
export type McpSchema = Record<string, unknown>;

export interface McpBinding {
  sourceId: string;
  output: "text" | "json" | "table";
  pointer: string;
}

export interface McpConfiguration {
  version: number;
  bindingId: string;
  serverId: string;
  serverName: string;
  toolName: string;
  description: string;
  inputSchema: McpSchema;
  outputSchema?: McpSchema;
  annotations: Record<string, unknown>;
  arguments: Record<string, McpJson>;
  bindings: Record<string, McpBinding>;
}

export interface McpResult {
  version: number;
  text: string;
  structuredContent: McpJson;
  content: Array<Record<string, unknown>>;
  isError: boolean;
  provenance: {
    requestId: string;
    startedAt: string;
    finishedAt: string;
    serverId: string;
    serverName: string;
    toolName: string;
    inputHash: string;
    configHash: string;
    resultHash: string;
    receiptHash: string;
    sources: Array<{ argument: string; sourceId: string; sourceKind: string; output: string; pointer: string; valueHash: string }>;
  };
}

export interface McpState {
  version: 1;
  nodeKey: string;
  config: McpConfiguration | null;
  latest: McpResult | null;
  history: McpResult[];
  error: string;
  status?: string;
}

export interface McpTool {
  serverId: string;
  serverName: string;
  name: string;
  title?: string;
  description: string;
  inputSchema: McpSchema;
  outputSchema?: McpSchema;
  annotations: Record<string, unknown>;
  allowed: boolean;
}

export interface McpCatalog {
  servers: Array<{ id: string; name: string; enabled: boolean; transport: string }>;
  tools: McpTool[];
  errors?: Array<{ serverId: string; message: string }>;
}

export interface McpConfigureArgs {
  serverId: string;
  toolName: string;
  arguments: Record<string, McpJson>;
  bindings: Record<string, McpBinding>;
}

export interface McpPreview {
  previewToken: string;
  serverName: string;
  toolName: string;
  arguments: Record<string, McpJson>;
  inputHash: string;
  requiresApproval: boolean;
  scopes?: string[];
  warnings?: string[];
}

export interface McpApproval {
  id: string;
  nodeId: string;
  serverName: string;
  toolName: string;
  arguments: Record<string, McpJson>;
  scopes: string[];
}

export interface McpParent {
  id: string;
  title: string;
  kind: string;
  outputs: McpBinding["output"][];
}

export interface McpAttempt {
  requestId: string;
  runId: string;
  status: string;
  phase: string;
  toolName: string;
  detail: string;
}

export interface McpToolStatus {
  configured: boolean;
  current: boolean;
  status: string;
  error: string;
}

export function parseMcpToolStatus(value: unknown): McpToolStatus {
  if (!isMcpObject(value) || typeof value.configured !== "boolean" || typeof value.current !== "boolean"
      || typeof value.status !== "string" || typeof value.error !== "string")
    throw new Error("Tool freshness could not be verified. Reconnect and reopen the node before using its saved result.");
  return value as unknown as McpToolStatus;
}

export function parseMcpAttempts(value: unknown): McpAttempt[] {
  if (!Array.isArray(value) || !value.every(item => isMcpObject(item)
      && ["requestId", "runId", "status", "phase", "toolName", "detail"].every(key => typeof item[key] === "string")))
    throw new Error("Tool request receipts could not be read. Reconnect and check Graph Runner.");
  return value as McpAttempt[];
}

export function isMcpObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isJson(value: unknown, depth = 0): value is McpJson {
  if (depth > 32) return false;
  if (value === null || typeof value === "string" || typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.length <= 10000 && value.every(item => isJson(item, depth + 1));
  return isMcpObject(value) && Object.keys(value).length <= 10000 && Object.values(value).every(item => isJson(item, depth + 1));
}

function validConfig(value: unknown): value is McpConfiguration {
  if (!isMcpObject(value) || value.version !== 1 || typeof value.bindingId !== "string"
      || typeof value.serverId !== "string" || typeof value.serverName !== "string"
      || typeof value.toolName !== "string" || typeof value.description !== "string"
      || !isMcpObject(value.inputSchema) || !isMcpObject(value.annotations)
      || !isMcpObject(value.arguments) || !isJson(value.arguments) || !isMcpObject(value.bindings)) return false;
  return Object.values(value.bindings).every(binding => isMcpObject(binding)
    && typeof binding.sourceId === "string" && ["text", "json", "table"].includes(String(binding.output))
    && typeof binding.pointer === "string");
}

function validResult(value: unknown): value is McpResult {
  if (!isMcpObject(value) || !Number.isInteger(value.version) || typeof value.text !== "string"
      || !isJson(value.structuredContent) || !Array.isArray(value.content)
      || !value.content.every(isMcpObject) || typeof value.isError !== "boolean"
      || !isMcpObject(value.provenance)) return false;
  const p = value.provenance;
  return ["requestId", "startedAt", "finishedAt", "serverId", "serverName", "toolName", "inputHash", "configHash", "resultHash", "receiptHash"]
    .every(key => typeof p[key] === "string") && Array.isArray(p.sources)
    && p.sources.every(source => isMcpObject(source)
      && ["argument", "sourceId", "sourceKind", "output", "pointer", "valueHash"].every(key => typeof source[key] === "string"));
}

export function readMcpState(raw: string | undefined): McpState | null {
  if (!raw) return null;
  try {
    const state: unknown = JSON.parse(raw);
    if (!isMcpObject(state) || state.version !== 1 || typeof state.nodeKey !== "string"
        || (state.config !== null && !validConfig(state.config))
        || (state.latest !== null && !validResult(state.latest)) || !Array.isArray(state.history)
        || !state.history.every(validResult) || typeof state.error !== "string") return null;
    return state as unknown as McpState;
  } catch { return null; }
}

export function parseMcpCatalog(value: unknown): McpCatalog {
  if (!isMcpObject(value) || !Array.isArray(value.servers) || !Array.isArray(value.tools)
      || !value.servers.every(server => isMcpObject(server) && typeof server.id === "string"
        && typeof server.name === "string" && typeof server.enabled === "boolean" && typeof server.transport === "string")
      || !value.tools.every(tool => isMcpObject(tool) && typeof tool.serverId === "string"
        && typeof tool.serverName === "string" && typeof tool.name === "string"
        && typeof tool.description === "string" && isMcpObject(tool.inputSchema)
        && isMcpObject(tool.annotations) && typeof tool.allowed === "boolean")
      || (value.errors !== undefined && (!Array.isArray(value.errors)
        || !value.errors.every(error => isMcpObject(error) && typeof error.serverId === "string" && typeof error.message === "string"))))
    throw new Error("The tool list could not be read. Check the server connection and discover tools again.");
  return value as unknown as McpCatalog;
}

export function parseMcpPreview(value: unknown): McpPreview {
  if (!isMcpObject(value) || typeof value.previewToken !== "string" || typeof value.serverName !== "string"
      || typeof value.toolName !== "string" || !isMcpObject(value.arguments) || !isJson(value.arguments)
      || typeof value.inputHash !== "string" || typeof value.requiresApproval !== "boolean"
      || (value.warnings !== undefined && (!Array.isArray(value.warnings) || !value.warnings.every(item => typeof item === "string"))))
    throw new Error("The tool preview could not be read. Preview the tool again before running.");
  return value as unknown as McpPreview;
}

export function parseMcpParents(value: unknown): McpParent[] {
  if (!Array.isArray(value) || !value.every(item => isMcpObject(item) && typeof item.id === "string"
      && typeof item.title === "string" && typeof item.kind === "string" && Array.isArray(item.outputs)
      && item.outputs.every(output => ["text", "json", "table"].includes(String(output)))))
    throw new Error("Connected inputs could not be read. Check the source connections and reopen this node.");
  return value as McpParent[];
}

export function parseMcpApprovals(value: unknown): McpApproval[] {
  if (!Array.isArray(value) || !value.every(item => isMcpObject(item) && typeof item.id === "string"
      && typeof item.nodeId === "string" && typeof item.serverName === "string" && typeof item.toolName === "string"
      && isMcpObject(item.arguments) && isJson(item.arguments) && Array.isArray(item.scopes)
      && item.scopes.every(scope => typeof scope === "string")))
    throw new Error("Tool approvals could not be read. Reopen Graph Runner to check the pending run.");
  return value as McpApproval[];
}

export function schemaFields(schema: McpSchema): Array<{ name: string; schema: McpSchema; required: boolean }> {
  const properties = isMcpObject(schema.properties) ? schema.properties : {};
  const required = Array.isArray(schema.required) ? schema.required : [];
  return Object.entries(properties).map(([name, value]) => ({ name, schema: isMcpObject(value) ? value : {}, required: required.includes(name) }));
}

export function schemaDefaults(schema: McpSchema): Record<string, McpJson> {
  return Object.fromEntries(schemaFields(schema).filter(field => field.schema.default !== undefined && isJson(field.schema.default))
    .map(field => [field.name, field.schema.default as McpJson]));
}

export function parseArguments(text: string): Record<string, McpJson> {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error("Arguments must be valid JSON. Check the highlighted values and punctuation."); }
  if (!isMcpObject(value) || !isJson(value)) throw new Error("Arguments must be a JSON object with finite numbers and at most 32 nested levels.");
  return value as Record<string, McpJson>;
}

export function resultTable(value: McpJson): { columns: string[]; rows: McpJson[][]; total: number } | null {
  let data: McpJson = value;
  if (isMcpObject(data) && Array.isArray(data.rows) && Array.isArray(data.columns)
      && data.columns.every(column => typeof column === "string") && data.rows.every(Array.isArray)) {
    return { columns: (data.columns as string[]).slice(0, 20), rows: (data.rows as McpJson[][]).slice(0, 100), total: data.rows.length };
  }
  if (isMcpObject(data)) {
    const candidate = data.rows ?? data.items ?? data.data;
    if (Array.isArray(candidate)) data = candidate as McpJson[];
  }
  if (!Array.isArray(data) || !data.length || !data.every(isMcpObject)) return null;
  const columns = [...new Set(data.flatMap(row => Object.keys(row as Record<string, McpJson>)))].slice(0, 20);
  return { columns, rows: data.slice(0, 100).map(row => columns.map(key => (row as Record<string, McpJson>)[key] ?? null)), total: data.length };
}

export function mcpTableColumns(value: McpJson): string[] {
  const rows = isMcpObject(value) ? value.rows ?? value.items ?? value.data : value;
  if (!Array.isArray(rows) || !rows.length || rows.length > 10000 || !isMcpObject(rows[0])) return [];
  const columns = Object.keys(rows[0]);
  if (!columns.length || columns.length > 128 || columns.some(key => !key || key.length > 160)
      || !rows.every(row => isMcpObject(row) && Object.keys(row).length === columns.length
        && columns.every(column => Object.hasOwn(row, column)) && Object.values(row).every(cell =>
          cell === null || typeof cell === "string" || typeof cell === "boolean" || typeof cell === "number" && Number.isFinite(cell)))) return [];
  return columns;
}

export function displayMcpValue(value: McpJson): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}
