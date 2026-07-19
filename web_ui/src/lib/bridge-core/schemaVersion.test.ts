import { describe, expect, it } from "vitest";
import {
  READER_MIN_COMPATIBLE_SCHEMA_VERSION,
  READER_SCHEMA_VERSION,
  checkSchemaCompatibility,
} from "./schemaVersion";

describe("checkSchemaCompatibility", () => {
  it("accepts the version this build actually emits", () => {
    expect(
      checkSchemaCompatibility({
        schemaVersion: READER_SCHEMA_VERSION,
        minCompatibleSchemaVersion: READER_MIN_COMPATIBLE_SCHEMA_VERSION,
      }).compatible,
    ).toBe(true);
  });

  it("accepts a NEWER payload whose sender says older readers are still fine - this is what 'additive fields allowed' means", () => {
    const verdict = checkSchemaCompatibility({
      schemaVersion: READER_SCHEMA_VERSION + 5,
      minCompatibleSchemaVersion: READER_SCHEMA_VERSION,
    });

    expect(verdict.compatible).toBe(true);
  });

  it("REJECTS a newer payload whose sender declares a breaking floor above this reader", () => {
    const verdict = checkSchemaCompatibility({
      schemaVersion: READER_SCHEMA_VERSION + 5,
      minCompatibleSchemaVersion: READER_SCHEMA_VERSION + 3,
    });

    expect(verdict.compatible).toBe(false);
    if (!verdict.compatible) expect(verdict.reason).toMatch(/out of date/i);
  });

  it("rejects a payload older than this reader's stated minimum", () => {
    const verdict = checkSchemaCompatibility({
      schemaVersion: READER_MIN_COMPATIBLE_SCHEMA_VERSION - 1,
    });

    expect(verdict.compatible).toBe(false);
  });

  it("accepts a sender that declares no floor at all, rather than refusing it", () => {
    // A sender predating minCompatibleSchemaVersion is otherwise perfectly
    // readable; treating absence as a hard error would break it needlessly.
    const verdict = checkSchemaCompatibility({ schemaVersion: READER_SCHEMA_VERSION });

    expect(verdict.compatible).toBe(true);
  });

  it("rejects a payload with no schema version at all", () => {
    expect(checkSchemaCompatibility({ revision: 3 }).compatible).toBe(false);
  });

  it("rejects a non-numeric schema version", () => {
    expect(checkSchemaCompatibility({ schemaVersion: "1" }).compatible).toBe(false);
  });

  it("rejects non-objects without throwing", () => {
    for (const value of [null, undefined, 42, "x", []]) {
      expect(checkSchemaCompatibility(value).compatible).toBe(false);
    }
  });
});
