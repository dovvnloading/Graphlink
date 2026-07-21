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
// needs the callback to run.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
