import { render } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";
import { useCanvasFontVars } from "./useCanvasFontVars";

/**
 * The regression this pins: the node font color's default is "" - "follow
 * the theme" - and the hook must translate that into the ABSENCE of
 * --gl-node-font-color, so styles.css's fallback chain
 * (var(--gl-node-font-color, var(--gl-surface-text-primary))) resolves to
 * the active palette's text token. The old default was a stored "#F0F0F0",
 * which rendered node text white-on-white the moment the light palette was
 * active. Writing "" instead of removing the property would be the same
 * bug in a new shape: an empty custom property is still a SET property,
 * and var() substitution then takes the empty value over the fallback.
 */

/** Owns its ref (useRef, so the hook sees the same RefObject shape the
 * real canvas wrapper passes) and reports the host element out through
 * `onHost` so assertions can reach the real DOM node. */
function Probe({ color, onHost }: { color: string; onHost: (el: HTMLDivElement) => void }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  useCanvasFontVars(hostRef, "Segoe UI", 9, color);
  return (
    <div
      ref={(el) => {
        hostRef.current = el;
        if (el) onHost(el);
      }}
    />
  );
}

describe("useCanvasFontVars", () => {
  it("writes an explicitly chosen color as-is", () => {
    let host: HTMLDivElement | null = null;
    render(<Probe color="#9EC1E8" onHost={(el) => (host = el)} />);
    expect(host!.style.getPropertyValue("--gl-node-font-color")).toBe("#9EC1E8");
  });

  it('removes the property entirely for "" so the theme token wins', () => {
    let host: HTMLDivElement | null = null;
    const view = render(<Probe color="#9EC1E8" onHost={(el) => (host = el)} />);
    view.rerender(<Probe color="" onHost={(el) => (host = el)} />);
    // Removed, not set-to-empty: an empty value would still suppress the
    // CSS fallback.
    expect(host!.style.getPropertyValue("--gl-node-font-color")).toBe("");
    expect([...host!.style]).not.toContain("--gl-node-font-color");
  });

  it("still writes family and size when the color is theme-following", () => {
    let host: HTMLDivElement | null = null;
    render(<Probe color="" onHost={(el) => (host = el)} />);
    expect(host!.style.getPropertyValue("--gl-node-font-family")).toBe("Segoe UI");
    expect(host!.style.getPropertyValue("--gl-node-font-size")).toBe("9pt");
  });
});
