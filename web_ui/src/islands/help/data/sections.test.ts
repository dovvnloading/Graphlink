import { describe, expect, it } from "vitest";
import { HELP_SECTIONS } from "./sections";

// sections.ts was mechanically generated from the legacy HelpDialog.
// SECTION_DEFS (see that file's own header) and verified byte-exact against
// it at generation time - this test guards against a FUTURE hand-edit
// silently dropping content, not against the original transcription.
describe("HELP_SECTIONS", () => {
  it("has exactly 9 sections, 19 subsections, and 76 items", () => {
    expect(HELP_SECTIONS).toHaveLength(9);

    const subsectionCount = HELP_SECTIONS.reduce((sum, section) => sum + section.subsections.length, 0);
    expect(subsectionCount).toBe(19);

    const itemCount = HELP_SECTIONS.reduce(
      (sum, section) => sum + section.subsections.reduce((subSum, sub) => subSum + sub.items.length, 0),
      0,
    );
    expect(itemCount).toBe(76);
  });

  it("every section/subsection/item has non-empty text fields", () => {
    for (const section of HELP_SECTIONS) {
      expect(section.name.length).toBeGreaterThan(0);
      expect(section.description.length).toBeGreaterThan(0);
      for (const subsection of section.subsections) {
        expect(subsection.title.length).toBeGreaterThan(0);
        for (const item of subsection.items) {
          expect(item.action.length).toBeGreaterThan(0);
          expect(item.description.length).toBeGreaterThan(0);
        }
      }
    }
  });

  it("starts with Overview, matching the legacy default section", () => {
    expect(HELP_SECTIONS[0].name).toBe("Overview");
  });
});
