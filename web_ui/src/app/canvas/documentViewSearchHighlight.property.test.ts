/**
 * ADR-022 stage 22.3: property-based test for
 * documentViewSearchHighlight.ts's regex-escaping helper (see that
 * module's own header comment).
 */
import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { escapeSearchRegExp } from "./documentViewSearchHighlight";

describe("escapeSearchRegExp (property-based)", () => {
  it("a RegExp compiled from the escaped output always matches the original string literally", () => {
    fc.assert(
      fc.property(fc.string(), (value) => {
        const escaped = escapeSearchRegExp(value);
        const regex = new RegExp(escaped);
        expect(regex.test(value)).toBe(true);
      }),
    );
  });
});
