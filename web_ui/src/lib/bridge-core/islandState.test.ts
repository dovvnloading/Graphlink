import { describe, expect, it } from "vitest";
import { parseIslandState, type StateValidator } from "./islandState";
import { READER_SCHEMA_VERSION } from "./schemaVersion";

/**
 * Exercises parseIslandState() with a throwaway validator, deliberately NOT
 * composer's - this is what actually proves the extraction from
 * composer/bridge.ts achieved genericity, rather than just moving the same
 * composer-only code to a different file. A second real island can plug its
 * own generated validator into this exact function.
 */
interface Widget {
  id: string;
  count: number;
}

const validateWidget: StateValidator<Widget> = (value) => {
  if (typeof value !== "object" || value === null) {
    return { ok: false, errors: ["$: expected object"] };
  }
  const v = value as Record<string, unknown>;
  const errors: string[] = [];
  if (typeof v.id !== "string") errors.push("$.id: expected string");
  if (typeof v.count !== "number") errors.push("$.count: expected number");
  return errors.length ? { ok: false, errors } : { ok: true, value: v as unknown as Widget };
};

const validPayload = () =>
  JSON.stringify({ schemaVersion: READER_SCHEMA_VERSION, id: "w1", count: 3 });

describe("parseIslandState (generic, island-agnostic)", () => {
  it("parses and validates a good payload with a custom validator", () => {
    const outcome = parseIslandState(validPayload(), validateWidget);

    expect(outcome.ok).toBe(true);
    if (outcome.ok) {
      expect(outcome.state).toEqual({
        schemaVersion: READER_SCHEMA_VERSION,
        id: "w1",
        count: 3,
      });
    }
  });

  it("rejects unparseable JSON with kind 'parse'", () => {
    const outcome = parseIslandState("not json", validateWidget);

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.rejection.kind).toBe("parse");
  });

  it("rejects an incompatible schema version with kind 'version', before ever calling the validator", () => {
    let validatorCalled = false;
    const spy: StateValidator<Widget> = (value) => {
      validatorCalled = true;
      return validateWidget(value);
    };

    const outcome = parseIslandState(
      JSON.stringify({ schemaVersion: 999, minCompatibleSchemaVersion: 999 }),
      spy,
    );

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.rejection.kind).toBe("version");
    expect(validatorCalled).toBe(false);
  });

  it("rejects a payload that fails the custom validator with kind 'shape' and its exact errors", () => {
    const outcome = parseIslandState(
      JSON.stringify({ schemaVersion: READER_SCHEMA_VERSION, id: 5, count: "nope" }),
      validateWidget,
    );

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.rejection.kind).toBe("shape");
      expect(outcome.rejection.details).toEqual(["$.id: expected string", "$.count: expected number"]);
    }
  });

  it("bounds the reported detail count to 8 even when the validator returns more", () => {
    const manyErrors: StateValidator<Widget> = () => ({
      ok: false,
      errors: Array.from({ length: 20 }, (_, i) => `error ${i}`),
    });

    const outcome = parseIslandState(validPayload(), manyErrors);

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.rejection.details.length).toBe(8);
  });
});
