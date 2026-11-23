import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// A cascade layer is ordered by its first appearance. base.css declares the
// order, so the app entry must load it before any component stylesheet;
// otherwise a lazily loaded chunk's @layer vendor is created last and beats
// every app layer (React Flow's panel positions overrode the canvas HUD).
describe("CSS cascade layer order", () => {
  const root = resolve(__dirname, "..");
  it("declares every layer once, in order, at the top of base.css", () => {
    const base = readFileSync(resolve(root, "lib/ui/base.css"), "utf8");
    expect(base.trimStart().startsWith("@layer vendor, base, kit, surfaces, nodes, overrides;")).toBe(true);
  });
  it("imports base.css before anything else in the app entry", () => {
    const main = readFileSync(resolve(root, "app/main.tsx"), "utf8");
    const firstImport = main.split(/\r?\n/).find((line) => line.startsWith("import "));
    expect(firstImport).toBe('import "../lib/ui/base.css";');
  });
});
