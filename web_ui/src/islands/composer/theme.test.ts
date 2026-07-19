import { afterEach, describe, expect, it } from "vitest";
import { applyThemeCssVariables } from "./theme";

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
