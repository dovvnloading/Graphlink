import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// globals: false in vitest.config.ts means RTL's automatic per-test cleanup
// (which detects a global afterEach) never registers - without this, DOM
// from one test leaks into the next and getByLabelText finds duplicates.
afterEach(() => {
  cleanup();
});

// jsdom implements no ResizeObserver at all (not even a no-op stub) - the
// pin-overlay island (Phase 5 increment 1) is the first component needing
// one, for its content-driven height negotiation. A minimal stub that never
// actually fires is enough for tests: none exercise real layout, so nothing
// needs the callback to run. Deliberately stays a true no-op globally - an
// earlier attempt at ADR-013 stage 13.2 made this fire synchronously off
// getBoundingClientRect, which broke @xyflow/react's OWN internal
// ResizeObserver usage (updateNodeInternals reaches for
// window.DOMMatrixReadOnly, which jsdom also doesn't implement, and crashed
// the moment the callback actually ran). A component whose rendering
// depends on a real measured size (see charts/ChartRenderer.test.tsx) mocks
// ResizeObserver locally in its own test file instead of changing this
// shared global.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

// jsdom implements no scrollIntoView at all (not even a no-op stub) - calling
// it throws "is not a function" in any component that does. A no-op default
// is enough for tests that don't care about scrolling; a test that DOES care
// (CommandPalette.test.tsx's scroll-into-view case) overrides it locally
// with its own vi.fn() to make assertions.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
