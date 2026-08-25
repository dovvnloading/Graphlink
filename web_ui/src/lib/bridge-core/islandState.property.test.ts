/**
 * ADR-022 stage 22.3: property-based tests for islandState.ts's
 * parse/reject shell (see that module's own header comment).
 */
import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { parseIslandState, type StateValidator } from "./islandState";

describe("parseIslandState (property-based)", () => {
  it("malformed JSON always yields a parse rejection, never ok:true", () => {
    fc.assert(
      fc.property(fc.string(), (raw) => {
        let isValidJson = true;
        try {
          JSON.parse(raw);
        } catch {
          isValidJson = false;
        }
        fc.pre(!isValidJson);

        const alwaysAccept: StateValidator<unknown> = (value) => ({ ok: true, value });
        const outcome = parseIslandState(raw, alwaysAccept);

        expect(outcome.ok).toBe(false);
        if (!outcome.ok) {
          expect(outcome.rejection.kind).toBe("parse");
        }
      }),
    );
  });

  it("never returns ok:true while also carrying a rejection field, and vice versa", () => {
    fc.assert(
      fc.property(fc.jsonValue(), fc.boolean(), (parsedShape, validatorAccepts) => {
        const raw = JSON.stringify(parsedShape);
        const validate: StateValidator<unknown> = () =>
          validatorAccepts ? { ok: true, value: parsedShape } : { ok: false, errors: ["nope"] };

        const outcome = parseIslandState(raw, validate);

        if (outcome.ok) {
          expect("rejection" in outcome).toBe(false);
        } else {
          expect("state" in outcome).toBe(false);
          expect(outcome.rejection).toBeDefined();
        }
      }),
    );
  });
});
