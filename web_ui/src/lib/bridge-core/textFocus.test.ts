import { describe, expect, it } from "vitest";
import { isTextEditable } from "./textFocus";

describe("isTextEditable", () => {
  it("textarea is text-editable", () => {
    expect(isTextEditable(document.createElement("textarea"))).toBe(true);
  });

  it("input type=text is text-editable", () => {
    const el = document.createElement("input");
    el.type = "text";
    expect(isTextEditable(el)).toBe(true);
  });

  it("select counts as text-editable (arrow keys/typeahead must reach it, not the canvas)", () => {
    // Found by adversarial review: an open <select> dropdown is keyboard-
    // driven (arrow keys, typeahead) exactly like a text field is, from the
    // canvas's perspective - without this, WASD/arrow-key canvas pan would
    // steal keys from a future island's <select> the same way the original
    // checklist bug let it steal keys from a composer textarea.
    expect(isTextEditable(document.createElement("select"))).toBe(true);
  });

  it("exclusion-list input types not in the explicit non-text set still count as text-editable (e.g. date)", () => {
    // The classifier is an EXCLUSION list on purpose - date/month/week/time
    // inputs are genuinely keyboard-editable and must not need updating this
    // file every time a new HTML input type is added.
    const el = document.createElement("input");
    el.type = "date";
    expect(isTextEditable(el)).toBe(true);
  });

  it.each(["button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit"])(
    "input type=%s is NOT text-editable",
    (type) => {
      const el = document.createElement("input");
      el.type = type;
      expect(isTextEditable(el)).toBe(false);
    },
  );

  it("contenteditable element is text-editable", () => {
    // jsdom does not implement the isContentEditable getter (it's undefined,
    // not computed from the contenteditable attribute) - real Chromium (the
    // actual runtime here, via QtWebEngine) computes it correctly, so this
    // stubs the property the way jsdom itself would if it implemented the
    // spec, rather than testing a jsdom gap instead of the real contract.
    const el = document.createElement("div");
    Object.defineProperty(el, "isContentEditable", { value: true, configurable: true });
    expect(isTextEditable(el)).toBe(true);
  });

  it("a plain non-editable element is not text-editable", () => {
    expect(isTextEditable(document.createElement("div"))).toBe(false);
  });

  it("null is not text-editable", () => {
    expect(isTextEditable(null)).toBe(false);
  });
});
