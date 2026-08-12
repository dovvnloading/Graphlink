/**
 * ADR-013 stage 13.2 / ADR-012 stage 12.2 interaction: charts must repaint
 * their colors when the user switches theme mid-session.
 *
 * These tests exist because of a real shipped bug found in a 2026-08-12 audit.
 * useChartTheme originally read the resolved `--gl-*` custom properties exactly
 * once, in a useEffect keyed on a ref whose identity never changes - on the
 * documented assumption that "nothing in this codebase mutates --gl-* at
 * runtime today (ADR-012's theme toggle is still proposed, not shipped)".
 * ADR-012 stage 12.2 shipped a genuine live toggle the SAME DAY, so that
 * assumption was false on arrival: an already-open chart kept last theme's
 * colors (wrong-contrast bars/lines/tooltips) until it happened to unmount.
 *
 * Both branches below have to be covered separately, because applyTheme has
 * three states and only two mechanisms can observe them:
 *   - explicit light/dark -> `data-theme` attribute on <html> (MutationObserver)
 *   - "system"            -> no attribute at all, palette follows the
 *                            prefers-color-scheme media query (matchMedia
 *                            listener - the DOM never changes)
 * A fix that watched only the attribute would silently fail every user on the
 * default "system" setting, so the media-query test is not redundant.
 */

import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef } from "react";
import { useChartTheme } from "./chartHooks";

/** Drives useChartTheme against a real mounted element and renders the one
 * token these tests assert on. */
function ThemeProbe() {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useChartTheme(ref);
  return (
    <div ref={ref}>
      <span data-testid="chart-text-color">{theme.text}</span>
    </div>
  );
}

/** Stubs getComputedStyle so `--gl-surface-text-primary` resolves to whatever
 * the "current theme" should be, the same way a real stylesheet swap would.
 * Returns a setter so a test can flip the value and then fire the event that
 * should trigger a re-read. */
function stubResolvedTheme(initialTextColor: string) {
  let textColor = initialTextColor;
  const original = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation(((element: Element) => {
    const real = original.call(window, element);
    return {
      ...real,
      getPropertyValue: (property: string) =>
        property === "--gl-surface-text-primary" ? textColor : real.getPropertyValue(property),
    } as CSSStyleDeclaration;
  }) as typeof window.getComputedStyle);
  return {
    setTextColor(next: string) {
      textColor = next;
    },
  };
}

/** Minimal matchMedia stub that records listeners so a test can fire a real
 * change event. jsdom has no matchMedia at all by default. */
function stubMatchMedia() {
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
  return { fireChange: () => listeners.forEach((listener) => listener()) };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

describe("useChartTheme keeps an open chart in sync with a live theme switch", () => {
  it("re-reads the palette when data-theme flips on <html> (explicit light/dark)", async () => {
    const theme = stubResolvedTheme("#F1F1F1"); // dark-theme text
    stubMatchMedia();

    render(<ThemeProbe />);
    expect(screen.getByTestId("chart-text-color").textContent).toBe("#F1F1F1");

    // What ADR-012's applyTheme() actually does on a Settings change.
    theme.setTextColor("#1A1A1A"); // light-theme text
    await act(async () => {
      document.documentElement.setAttribute("data-theme", "light");
      // MutationObserver callbacks are delivered as microtasks.
      await Promise.resolve();
    });

    expect(screen.getByTestId("chart-text-color").textContent).toBe("#1A1A1A");
  });

  it("re-reads the palette when the OS appearance changes under theme=system", async () => {
    const theme = stubResolvedTheme("#F1F1F1");
    const media = stubMatchMedia();

    render(<ThemeProbe />);
    expect(screen.getByTestId("chart-text-color").textContent).toBe("#F1F1F1");

    // "system" mode: no data-theme attribute is ever written, so ONLY the
    // media-query listener can catch this. An attribute-only fix fails here.
    theme.setTextColor("#1A1A1A");
    await act(async () => {
      media.fireChange();
    });

    expect(screen.getByTestId("chart-text-color").textContent).toBe("#1A1A1A");
  });

  it("does not churn state when an unrelated <html> attribute mutation resolves to the same colors", async () => {
    stubResolvedTheme("#F1F1F1");
    stubMatchMedia();

    render(<ThemeProbe />);
    const before = screen.getByTestId("chart-text-color").textContent;

    // Same resolved palette - the equality guard should keep the previous
    // theme object, so nothing re-renders. (Without the guard, readChartTheme's
    // freshly-built `categorical` array alone would make every compare unequal.)
    await act(async () => {
      document.documentElement.setAttribute("data-theme", "dark");
      await Promise.resolve();
    });

    expect(screen.getByTestId("chart-text-color").textContent).toBe(before);
  });
});
