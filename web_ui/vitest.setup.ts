import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// globals: false in vitest.config.ts means RTL's automatic per-test cleanup
// (which detects a global afterEach) never registers - without this, DOM
// from one test leaks into the next and getByLabelText finds duplicates.
afterEach(() => {
  cleanup();
});
