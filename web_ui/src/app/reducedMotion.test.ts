import { afterEach, describe, expect, it, vi } from "vitest";
import { motionDuration, prefersReducedMotion } from "./reducedMotion";

describe("reducedMotion (ADR-012 stage 12.4)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prefersReducedMotion() reads window.matchMedia's own answer", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    expect(prefersReducedMotion()).toBe(true);

    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: false } as MediaQueryList);
    expect(prefersReducedMotion()).toBe(false);
  });

  it("prefersReducedMotion() queries the correct media feature", () => {
    const spy = vi.spyOn(window, "matchMedia").mockReturnValue({ matches: false } as MediaQueryList);
    prefersReducedMotion();
    expect(spy).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
  });

  it("motionDuration() passes the full duration through when motion is fine", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: false } as MediaQueryList);
    expect(motionDuration(200)).toBe(200);
  });

  it("motionDuration() collapses to 0 when the user has asked for less motion", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    expect(motionDuration(200)).toBe(0);
    expect(motionDuration(300)).toBe(0);
  });
});
