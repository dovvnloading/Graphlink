import { describe, expect, it } from "vitest";
import { compileFields, hydrateRow, wireDefaults, type WireFields } from "./wireCheck";

// The generated validators (./generated/*.ts) are field tables compiled by
// this runtime. It replaced unrolled per-field code that had been checked in
// for every topic, and was proven against it input-for-input (52,000 fuzzed
// payloads across all 13 topics, identical results and messages) before that
// code was deleted - these tests pin the behaviour that proof established.

const ROW: WireFields = [["label", "s", 0]];
const SHAPE: WireFields = [
  ["name", "s", 0],
  ["on", "b", 0],
  ["count", "n", 0],
  ["mode", { e: ["a", "b"] }, 0],
  ["tags", { a: "s" }, 0],
  ["stats", { d: "s" }, 0],
  ["rows", { a: { o: ROW } }, 0],
  ["nested", { o: ROW }, 1],
  ["note", "s", 1],
];

function run(fields: WireFields, value: unknown): string[] {
  const errors: string[] = [];
  compileFields(fields)(value, "$", errors);
  return errors;
}

const VALID = { name: "n", on: true, count: 1, mode: "a", tags: ["x"], stats: { k: "v" }, rows: [{ label: "r" }] };

describe("compileFields", () => {
  it("accepts a valid payload, with or without its optional fields", () => {
    expect(run(SHAPE, VALID)).toEqual([]);
    expect(run(SHAPE, { ...VALID, nested: { label: "x" }, note: "y" })).toEqual([]);
  });

  it("reports each wrong scalar type at the field's path", () => {
    expect(run(SHAPE, { ...VALID, name: 1, on: "yes", count: "2" })).toEqual([
      "$.name: expected string",
      "$.on: expected boolean",
      "$.count: expected number",
    ]);
  });

  it("treats a missing and a null required field alike, and skips a missing or null optional one", () => {
    const { name: _name, ...noName } = VALID;
    expect(run(SHAPE, noName)).toEqual(["$.name: missing required field"]);
    expect(run(SHAPE, { ...VALID, name: null })).toEqual(["$.name: missing required field"]);
    expect(run(SHAPE, { ...VALID, note: null, nested: null })).toEqual([]);
  });

  it("names the allowed values when a literal is off the list", () => {
    expect(run(SHAPE, { ...VALID, mode: "c" })).toEqual(['$.mode: "c" is not one of [a, b]']);
  });

  it("checks arrays, records and nested rows at indexed paths", () => {
    expect(
      run(SHAPE, { ...VALID, tags: ["x", 2], stats: { "a.b": 3 }, rows: [{ label: "ok" }, { label: 5 }, 7] }),
    ).toEqual([
      "$.tags[1]: expected string",
      '$.stats["a.b"]: expected string',
      "$.rows[1].label: expected string",
      "$.rows[2]: expected object",
    ]);
    expect(run(SHAPE, { ...VALID, tags: "x", stats: [], rows: {} })).toEqual([
      "$.tags: expected array",
      "$.stats: expected object",
      "$.rows: expected array",
    ]);
  });

  it("rejects a non-object outright and tolerates keys it does not know", () => {
    expect(run(SHAPE, [])).toEqual(["$: expected object"]);
    expect(run(SHAPE, { ...VALID, fromANewerSender: 1 })).toEqual([]);
  });
});

describe("sparse rows", () => {
  const SPARSE: WireFields = [
    ["id", "s", 0],
    ["title", "s", 0, ""],
    ["items", { a: "s" }, 0, []],
    ["meta", { d: "s" }, 0, {}],
    ["owner", "s", 1, null],
  ];
  const DEFAULTS = wireDefaults(SPARSE);

  it("restores absent fields, in table order, keeping unknown keys after them", () => {
    const row = hydrateRow(SPARSE, DEFAULTS, { extra: 1, id: "r1" }) as Record<string, unknown>;
    expect(row).toEqual({ id: "r1", title: "", items: [], meta: {}, owner: null, extra: 1 });
    expect(Object.keys(row)).toEqual(["id", "title", "items", "meta", "owner", "extra"]);
  });

  it("returns a row that is already whole as the same object", () => {
    const whole = { id: "r1", title: "t", items: [], meta: {}, owner: null };
    expect(hydrateRow(SPARSE, DEFAULTS, whole)).toBe(whole);
  });

  it("does not treat an explicit null as absent", () => {
    const row = hydrateRow(SPARSE, DEFAULTS, { id: "r1", title: null }) as Record<string, unknown>;
    expect(row.title).toBeNull();
    expect(run(SPARSE, row)).toEqual(["$.title: missing required field"]);
  });

  it("gives every restored row its own containers", () => {
    const a = hydrateRow(SPARSE, DEFAULTS, { id: "a" }) as { items: unknown[] };
    const b = hydrateRow(SPARSE, DEFAULTS, { id: "b" }) as { items: unknown[] };
    expect(a.items).not.toBe(b.items);
  });

  it("re-validating a row object that already passed costs nothing, but a failing row is always re-checked", () => {
    const check = compileFields(SPARSE);
    const good = { id: "r", title: "", items: [], meta: {}, owner: null };
    const errors: string[] = [];
    check(good, "$", errors);
    expect(errors).toEqual([]);
    // Stored rows are replaced, never edited - the memo trusts that. This
    // edit exists only to prove the second call really was skipped.
    (good as Record<string, unknown>).title = 5;
    check(good, "$", errors);
    expect(errors).toEqual([]);

    const bad = { id: "r", title: 5, items: [], meta: {}, owner: null };
    check(bad, "$", errors);
    check(bad, "$", errors);
    expect(errors).toEqual(["$.title: expected string", "$.title: expected string"]);
  });

  it("only sparse rows are memoized - any other shape is checked every time", () => {
    const check = compileFields(ROW);
    const row: Record<string, unknown> = { label: "x" };
    const errors: string[] = [];
    check(row, "$", errors);
    row.label = 5;
    check(row, "$", errors);
    expect(errors).toEqual(["$.label: expected string"]);
  });
});
