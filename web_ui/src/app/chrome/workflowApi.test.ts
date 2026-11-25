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
    expect(typedInputs([ports[1]], { rows: draftFor(ports[1], value) }).rows).toEqual(value);
    expect(money(0)).toBe("$0.00");
    expect(money(1.5)).toBe("$1.50");
  });

  it("preserves identifiers, whitespace, quoted empties and blank rows", () => {
    expect(parseCsv('id,description\n00123,"  padded  "\n,\n9007199254740993,"123"\n"",  ')).toEqual({
      columns: ["id", "description"],
      rows: [["00123", "  padded  "], [null, null], ["9007199254740993", "123"], ["", "  "]],
    });
    expect(parseCsv("a\n\n")).toEqual({ columns: ["a"], rows: [[null]] });
    expect(parseCsv("a,b,c\n-1.25,2e3,1e-999").rows).toEqual([[-1.25, 2000, "1e-999"]]);
  });

  it("supports CRLF, embedded CR and escaped quotes", () => {
    expect(parseCsv('a,b\r\n"x\ry","a""b"\r\n')).toEqual({
      columns: ["a", "b"], rows: [["x\ry", 'a"b']],
    });
  });

  it.each([
    ["a,b\n7,ok,extra", "CSV row 2, column 3"],
    ["a,b\n7", "CSV row 2, column 2"],
    ['a,b\n7,"oops', "Unterminated quoted field"],
    ['a\nab"c', "quote may only start"],
    ['a\n"x"y', "after the closing quote"],
  ])("rejects malformed CSV %j rather than changing its shape", (csv, error) => {
    expect(() => parseCsv(csv)).toThrow(error);
    const drafts = { draft: "x", rows: csv };
    expect(() => typedInputs(ports, drafts)).toThrow(error);
    expect(() => plainInputs(ports, drafts)).toThrow(error);
  });

  it("round-trips typed table drafts through both run and preset requests", () => {
    const value = {
      kind: "table",
      columns: ["id", "note", "flag", "empty", "missing", "number"],
      rows: [["00123", "a\rb", true, "", null, 7], ["123", "  padded  ", false, "", null, 1.25]],
    } as const;
    const drafts = { draft: "x", rows: draftFor(ports[1], value) };
    expect(typedInputs(ports, drafts).rows).toEqual(value);
    expect(plainInputs(ports, drafts).rows).toEqual({ columns: value.columns, rows: value.rows });
  });

  it.each([
    ['{"columns":["a"],"rows":[[1,2]]}', "row 1"],
    ['{"columns":["a"],"rows":[[{}]]}', "column 1"],
    ['{"columns":["a"],"rows":[[9007199254740993]]}', "safe numeric range"],
    ['{"columns":["a"],"rows":[[1e999]]}', "JSON scalar"],
  ])("rejects invalid typed table JSON %j", (json, error) => {
    expect(() => typedInputs(ports, { draft: "x", rows: json })).toThrow(error);
  });
});
