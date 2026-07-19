import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

// The default browser-preview mock (bridgeTypes.ts's initialComposerState)
// deliberately carries an empty theme.cssVariables - real values normally
// come from the Python bridge, which jsdom has no equivalent of. This proves
// ComposerApp's theme-application effect actually runs end-to-end on real
// bridge-shaped state, not just that the extracted applyThemeCssVariables()
// function works in isolation (see theme.test.ts for that).
vi.mock("./bridgeTypes", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./bridgeTypes")>();
  return {
    ...actual,
    initialComposerState: {
      ...actual.initialComposerState,
      theme: {
        ...actual.initialComposerState.theme,
        cssVariables: {
          "--gl-composer-shell-background": "#1f1f1f",
          "--gl-composer-shell-border": "rgba(150, 150, 150, 0.34)",
        },
      },
    },
  };
});

const { default: ComposerApp } = await import("./ComposerApp");

describe("ComposerApp theme wiring", () => {
  afterEach(() => {
    cleanup();
    document.documentElement.removeAttribute("style");
  });

  it("applies the mock bridge's initial theme.cssVariables to documentElement on mount", () => {
    render(<ComposerApp />);

    expect(
      document.documentElement.style.getPropertyValue("--gl-composer-shell-background"),
    ).toBe("#1f1f1f");
    expect(document.documentElement.style.getPropertyValue("--gl-composer-shell-border")).toBe(
      "rgba(150, 150, 150, 0.34)",
    );
  });
});
