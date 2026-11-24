import { describe, expect, it } from "vitest";
import type { PortSpec } from "../../lib/workflow-contract/generated/workflow";
import { draftFor, money, parseCsv, plainInputs, typedInputs } from "./workflowApi";

const ports: PortSpec[] = [
  { name: "draft", kind: "text", required: true },
  { name: "rows", kind: "table", required: true },
  { name: "options", kind: "json", required: false },
];

describe("workflow input drafts", () => {
  it("turns form drafts into typed run values and plain preset values", () => {
    const drafts = { draft: "Ship Friday", rows: 'team,hours\n"Ops, east",7\nSupport,', options: "" };
    expect(typedInputs(ports, drafts)).toEqual({
      draft: { kind: "text", text: "Ship Friday" },
      rows: { kind: "table", columns: ["team", "hours"], rows: [["Ops, east", 7], ["Support", null]] },
    });
    expect(plainInputs(ports, { ...drafts, options: '{"limit": 5}' })).toEqual({
      draft: "Ship Friday",
      rows: { columns: ["team", "hours"], rows: [["Ops, east", 7], ["Support", null]] },
      options: { limit: 5 },
    });
  });

  it("names the input and the fix when a value is missing or malformed", () => {
    expect(() => typedInputs(ports, { draft: "", rows: "a\n1" })).toThrow("Enter a value for “draft”.");
    expect(() => typedInputs(ports, { draft: "x", rows: "a\n1", options: "{oops" })).toThrow("“options” must be valid JSON.");
    expect(() => parseCsv("a,a\n1,2")).toThrow("Column names must be unique");
  });

  it("round-trips a table example back into an editable draft", () => {
    const value = { kind: "table", columns: ["name", "note"], rows: [["a", "x, y"], ["b", null]] } as const;
    expect(parseCsv(draftFor(ports[1], value))).toEqual({ columns: ["name", "note"], rows: [["a", "x, y"], ["b", null]] });
    expect(money(0)).toBe("$0.00");
    expect(money(1.5)).toBe("$1.50");
  });
});
