import { describe, expect, it } from "vitest";
import { createMcpDraft, draftArguments } from "./McpArgumentEditor";
import { parseArguments, parseMcpApprovals, parseMcpCatalog, readMcpState, resultTable, schemaDefaults } from "./mcpConnect";

describe("MCP configuration boundaries", () => {
  const schema = { type: "object", required: ["query", "count"], properties: {
    query: { type: "string" }, count: { type: "integer" }, enabled: { type: "boolean", default: false },
    threshold: { type: "number" }, mode: { type: "string", enum: ["brief", "full"] },
    filter: { type: "object" }, tags: { type: "array" },
  } };
  it("preserves exact numbers, false booleans, enums, and nested JSON in the form", () => {
    const args = { query: "weather", count: 2, enabled: false, threshold: 0.1234567890123,
      mode: "brief", filter: { category: "news" }, tags: ["a", "b"] };
    expect(draftArguments(schema, createMcpDraft(schema, args, {}))).toEqual(args);
    expect(schemaDefaults(schema)).toEqual({ enabled: false });
  });
  it("omits disabled optional fields and replaces bound fields without copying their old literals", () => {
    const binding = { sourceId: "source", output: "text" as const, pointer: "" };
    const draft = createMcpDraft(schema, { query: "old", count: 3, enabled: false }, { query: binding });
    draft.included.enabled = false;
    expect(draftArguments(schema, draft)).toEqual({ count: 3 });
  });
  it("rejects malformed numeric values and JSON instead of coercing them silently", () => {
    const draft = createMcpDraft(schema, { query: "q", count: 2 }, {});
    draft.fields.count = "2.25";
    expect(() => draftArguments(schema, draft)).toThrow(/integer/);
    expect(() => parseArguments("[]")).toThrow(/object/);
    expect(() => parseArguments('{"count":1e999}')).toThrow(/finite/);
    expect(() => parseArguments('{"bad":}')).toThrow(/valid JSON/);
  });
  it("rejects malformed payloads and future state versions", () => {
    expect(readMcpState("not JSON")).toBeNull();
    expect(readMcpState(JSON.stringify({ version: 2, nodeKey: "future", config: null, latest: null, history: [], error: "" }))).toBeNull();
    expect(() => parseMcpCatalog({ servers: [], tools: [{ name: "untrusted" }] })).toThrow(/tool list/);
    expect(() => parseMcpApprovals([{ id: "x", arguments: "not an object" }])).toThrow(/approvals/);
  });
  it("bounds the table preview while preserving the source result", () => {
    const values = Array.from({ length: 120 }, (_, i) => ({ item: i, available: false }));
    const table = resultTable({ items: values });
    expect(table?.columns).toEqual(["item", "available"]);
    expect(table?.rows).toHaveLength(100);
    expect(table?.total).toBe(120);
    expect(values).toHaveLength(120);
    expect(table?.rows[0]).toEqual([0, false]);
  });
});
