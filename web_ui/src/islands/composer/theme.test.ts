import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { applyThemeCssVariables, useAppliedThemeCssVariables } from "./theme";

describe("applyThemeCssVariables", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("style");
  });

  it("sets every entry as a custom property on the target element", () => {
    const target = document.createElement("div");

    applyThemeCssVariables(target, {
      "--gl-composer-shell-background": "#1f1f1f",
      "--gl-composer-shell-border": "rgba(150, 150, 150, 0.34)",
    });

    expect(target.style.getPropertyValue("--gl-composer-shell-background")).toBe("#1f1f1f");
    expect(target.style.getPropertyValue("--gl-composer-shell-border")).toBe(
      "rgba(150, 150, 150, 0.34)",
    );
  });

  it("is a no-op for an empty map and does not throw", () => {
    const target = document.createElement("div");

    expect(() => applyThemeCssVariables(target, {})).not.toThrow();
    expect(target.getAttribute("style")).toBeNull();
  });

  it("overwrites a previously-set value on a live theme switch", () => {
    const target = document.createElement("div");

    applyThemeCssVariables(target, { "--gl-composer-shell-background": "#1f1f1f" });
    applyThemeCssVariables(target, { "--gl-composer-shell-background": "#222222" });

    expect(target.style.getPropertyValue("--gl-composer-shell-background")).toBe("#222222");
  });

  it("preserves rgba() precision verbatim - the QColor alpha trap this exists to avoid", () => {
    const target = document.createElement("div");

    applyThemeCssVariables(target, {
      "--gl-composer-attach-button-background": "rgba(255, 255, 255, 0.018)",
    });

    expect(target.style.getPropertyValue("--gl-composer-attach-button-background")).toBe(
      "rgba(255, 255, 255, 0.018)",
    );
  });

  it("works against the real document.documentElement, the actual call target", () => {
    applyThemeCssVariables(document.documentElement, {
      "--gl-composer-send-button-background": "#414141",
    });

    expect(
      getComputedStyle(document.documentElement).getPropertyValue(
        "--gl-composer-send-button-background",
      ),
    ).toBe("#414141");
  });
});

describe("useAppliedThemeCssVariables", () => {
  afterEach(() => {
    document.documentElement.removeAttribute("style");
    vi.restoreAllMocks();
  });

  it("applies the initial cssVariables on mount", () => {
    const target = document.createElement("div");

    renderHook(({ vars }) => useAppliedThemeCssVariables(target, vars), {
      initialProps: { vars: { "--gl-composer-shell-background": "#1f1f1f" } },
    });

    expect(target.style.getPropertyValue("--gl-composer-shell-background")).toBe("#1f1f1f");
  });

  it("re-applies when the content genuinely changes across a re-render", () => {
    const target = document.createElement("div");
    const { rerender } = renderHook(({ vars }) => useAppliedThemeCssVariables(target, vars), {
      initialProps: { vars: { "--gl-composer-shell-background": "#1f1f1f" } },
    });

    rerender({ vars: { "--gl-composer-shell-background": "#222222" } });

    expect(target.style.getPropertyValue("--gl-composer-shell-background")).toBe("#222222");
  });

  it(
    "does NOT re-run setProperty on a re-render that supplies a new object " +
      "with IDENTICAL content - the exact shape every keystroke produces, " +
      "since bridge.ts's parseState() does a fresh JSON.parse() on every " +
      "snapshot regardless of whether the theme actually changed",
    () => {
      const target = document.createElement("div");
      const setPropertySpy = vi.spyOn(target.style, "setProperty");
      const firstVars = { "--gl-composer-shell-background": "#1f1f1f" };
      const identicalButNewObject = { "--gl-composer-shell-background": "#1f1f1f" };
      expect(firstVars).not.toBe(identicalButNewObject); // sanity: genuinely different references

      const { rerender } = renderHook(({ vars }) => useAppliedThemeCssVariables(target, vars), {
        initialProps: { vars: firstVars },
      });
      const callsAfterMount = setPropertySpy.mock.calls.length;
      expect(callsAfterMount).toBeGreaterThan(0);

      rerender({ vars: identicalButNewObject });

      expect(setPropertySpy.mock.calls.length).toBe(callsAfterMount);
    },
  );

  it("does re-run when a key's value changes even though the key set is identical", () => {
    const target = document.createElement("div");
    const setPropertySpy = vi.spyOn(target.style, "setProperty");

    const { rerender } = renderHook(({ vars }) => useAppliedThemeCssVariables(target, vars), {
      initialProps: { vars: { "--gl-composer-shell-background": "#1f1f1f" } },
    });
    const callsAfterMount = setPropertySpy.mock.calls.length;

    rerender({ vars: { "--gl-composer-shell-background": "#222222" } });

    expect(setPropertySpy.mock.calls.length).toBeGreaterThan(callsAfterMount);
    expect(target.style.getPropertyValue("--gl-composer-shell-background")).toBe("#222222");
  });
});
