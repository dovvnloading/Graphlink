/**
 * ADR-022 stage 22.3: property-based tests for schemaVersion.ts's
 * negotiation rules (see that module's own header comment).
 */
import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { checkSchemaCompatibility, READER_MIN_COMPATIBLE_SCHEMA_VERSION } from "./schemaVersion";

describe("checkSchemaCompatibility (property-based)", () => {
  it("rejects any payload that is not a non-null object", () => {
    fc.assert(
      fc.property(
        fc.oneof(fc.string(), fc.integer(), fc.boolean(), fc.constant(null), fc.constant(undefined)),
        (payload) => {
          expect(checkSchemaCompatibility(payload).compatible).toBe(false);
        },
      ),
    );
  });

  it("rejects any schemaVersion below the reader's minimum, regardless of other fields", () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true, max: READER_MIN_COMPATIBLE_SCHEMA_VERSION - 0.001 }),
        fc.dictionary(fc.string(), fc.anything()),
        (belowMinVersion, extraFields) => {
          const payload = { ...extraFields, schemaVersion: belowMinVersion };
          expect(checkSchemaCompatibility(payload).compatible).toBe(false);
        },
      ),
    );
  });

  it("accepts any schemaVersion at or above the minimum when no sender floor is declared", () => {
    fc.assert(
      fc.property(fc.double({ noNaN: true, noDefaultInfinity: true, min: READER_MIN_COMPATIBLE_SCHEMA_VERSION }), (version) => {
        expect(checkSchemaCompatibility({ schemaVersion: version }).compatible).toBe(true);
      }),
    );
  });
});
