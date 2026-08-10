import { afterEach, describe, expect, it } from "vitest";
import { applyTheme } from "./applyTheme";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

describe("applyTheme (ADR-012 stage 12.2)", () => {
  it("stamps data-theme for an explicit light choice", () => {
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("stamps data-theme for an explicit dark choice", () => {
    applyTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("removes data-theme for system, handing off to prefers-color-scheme", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    applyTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("treats any unrecognized value the same as system rather than stamping garbage", () => {
    document.documentElement.setAttribute("data-theme", "light");
    applyTheme("not-a-real-theme");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});
