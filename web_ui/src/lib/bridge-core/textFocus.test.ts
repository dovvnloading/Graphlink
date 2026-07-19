import { describe, expect, it, vi } from "vitest";
import { installTextFocusReporting, isTextEditable } from "./textFocus";

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

describe("installTextFocusReporting", () => {
  function makeIslandHost() {
    return { reportTextFocus: vi.fn() };
  }

  it("is a no-op when objects.islandHost is absent", () => {
    expect(() => installTextFocusReporting({})).not.toThrow();
  });

  it("is a no-op when islandHost.reportTextFocus is not a function", () => {
    expect(() => installTextFocusReporting({ islandHost: {} })).not.toThrow();
  });

  it("reports true when a text field gains focus", async () => {
    const islandHost = makeIslandHost();
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    installTextFocusReporting({ islandHost });

    textarea.focus();
    await Promise.resolve();

    expect(islandHost.reportTextFocus).toHaveBeenCalledExactlyOnceWith(true);
    textarea.remove();
  });

  it("reports false (after the deferred microtask) when a text field loses focus to nothing", async () => {
    const islandHost = makeIslandHost();
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    installTextFocusReporting({ islandHost });

    textarea.focus();
    await Promise.resolve();
    islandHost.reportTextFocus.mockClear();

    textarea.blur();
    await Promise.resolve();

    expect(islandHost.reportTextFocus).toHaveBeenCalledExactlyOnceWith(false);
    textarea.remove();
  });

  it("does not blip false when focus moves directly between two text fields", async () => {
    const islandHost = makeIslandHost();
    const a = document.createElement("textarea");
    const b = document.createElement("textarea");
    document.body.append(a, b);
    installTextFocusReporting({ islandHost });

    a.focus();
    await Promise.resolve();
    islandHost.reportTextFocus.mockClear();

    // Real tab-between-fields: focusout(a) fires (deferred check scheduled),
    // then focusin(b) fires synchronously and reports true immediately -
    // moving activeElement to b before the deferred check ever runs.
    b.focus();
    await Promise.resolve();

    // Only ever reported (or re-affirmed) true - never a spurious false.
    expect(islandHost.reportTextFocus).not.toHaveBeenCalledWith(false);
    a.remove();
    b.remove();
  });

  it("dedupes: does not re-report the same value on repeated focus of the same kind of element", async () => {
    const islandHost = makeIslandHost();
    const a = document.createElement("textarea");
    const b = document.createElement("textarea");
    document.body.append(a, b);
    installTextFocusReporting({ islandHost });

    a.focus();
    await Promise.resolve();
    islandHost.reportTextFocus.mockClear();

    b.focus(); // still text-editable -> true again, but no transition
    await Promise.resolve();

    expect(islandHost.reportTextFocus).not.toHaveBeenCalled();
    a.remove();
    b.remove();
  });

  it("forces false on pagehide even while a text field is focused", async () => {
    const islandHost = makeIslandHost();
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    installTextFocusReporting({ islandHost });

    textarea.focus();
    await Promise.resolve();
    islandHost.reportTextFocus.mockClear();

    window.dispatchEvent(new Event("pagehide"));

    expect(islandHost.reportTextFocus).toHaveBeenCalledExactlyOnceWith(false);
    textarea.remove();
  });

  it("forces false on visibilitychange when the document becomes hidden", async () => {
    const islandHost = makeIslandHost();
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    installTextFocusReporting({ islandHost });

    textarea.focus();
    await Promise.resolve();
    islandHost.reportTextFocus.mockClear();

    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));

    expect(islandHost.reportTextFocus).toHaveBeenCalledExactlyOnceWith(false);
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    textarea.remove();
  });
});
