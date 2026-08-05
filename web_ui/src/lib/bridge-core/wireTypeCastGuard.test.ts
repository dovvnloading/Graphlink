/**
 * ADR-003 stage 3.3 (C9): repo-hygiene guard for the pattern this stage
 * eliminated - a local TypeScript interface widening a GENERATED wire type
 * via intersection (`type XWithY = SceneNodeRow & YFields`) so a component
 * could read a real field the generated type didn't (yet) declare, cast in
 * with `n as XWithY`. That was never a type-safety net; it was TypeScript
 * being told to trust a field really exists on the wire, on the component
 * author's word alone - exactly backwards from what the generated type +
 * runtime validator (contracts/codegen.py's whole reason for existing) are
 * for. The real fix, applied in this stage, is declaring the field on the
 * SOURCE dataclass so codegen emits it for real; this test is the ratchet
 * that keeps a future field gap from being "fixed" the cast way again.
 *
 * Scoped to intersecting a GENERATED type specifically (derived dynamically
 * from every `export interface`/`export type` in bridge-core/generated/,
 * not a hardcoded list - a future new generated topic is covered
 * automatically) - an ordinary `A & B` intersecting two hand-authored types
 * is unrelated to this problem and must not be flagged. Generic exports
 * (e.g. `ValidationResult<T>`, shared boilerplate every generated file
 * re-exports identically) are excluded from the collected name set - a real
 * wire ROW type is never generic, and including one would risk flagging
 * completely unrelated code that merely shares a substring with a
 * generic helper's name (see the false-positive regression test below).
 *
 * KNOWN LIMITATION, not fixed here (same "flagged so it isn't rediscovered
 * as a surprise" posture as no-raw-colors.test.ts's own documented gap):
 * this is regex/text matching, not real TypeScript type resolution, so it
 * can be evaded by one extra level of local aliasing - `type Row =
 * SceneNodeRow; type Wide = Row & { extra: string };` never puts the
 * literal substring "SceneNodeRow" next to `&`, so it passes undetected
 * even though `Wide` is structurally the exact same widened-wire-type shape
 * this guard exists to catch. Closing that gap for real needs actual TS
 * type-checker integration (ts-morph or the compiler API), which is
 * disproportionate machinery for a repo-hygiene ratchet - no such alias
 * pattern exists anywhere in the codebase today (grepped), so this is a
 * latent gap in the TOOL's robustness, not a live, currently-undetected
 * violation.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { globSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_SRC = join(HERE, "..", "..");
const GENERATED_DIR = join(HERE, "generated");

// Captures an optional generic parameter list (`<T>`) after the name, so
// generic exports can be filtered out below - a real wire ROW/ENTITY type is
// never generic; only shared boilerplate helpers are (e.g. ValidationResult).
const EXPORTED_TYPE_NAME_RE = /^export (?:interface|type) (\w+)(<[^>]*>)?/gm;

function generatedWireTypeNames(): string[] {
  const names = new Set<string>();
  for (const file of globSync("*.ts", { cwd: GENERATED_DIR })) {
    const source = readFileSync(join(GENERATED_DIR, file), "utf-8");
    for (const match of source.matchAll(EXPORTED_TYPE_NAME_RE)) {
      const [, name, genericParams] = match;
      if (genericParams) continue; // e.g. ValidationResult<T> - not a row type
      names.add(name);
    }
  }
  return [...names];
}

function findWireTypeWideningCasts(source: string, wireTypeNames: string[]): string[] {
  const found: string[] = [];
  for (const name of wireTypeNames) {
    // A type intersection widening the generated type, EITHER operand
    // order - `SceneNodeRow & X` and `X & SceneNodeRow` are semantically
    // identical in TypeScript, so both must be caught; an earlier version
    // of this scanner only matched the name-then-`&` order and silently
    // passed the reversed one. Word-boundaried on both sides so e.g.
    // "SceneNodeRow" doesn't also match a longer identifier that happens to
    // contain it as a substring.
    const intersectionRe = new RegExp(`(?:\\b${name}\\s*&|&\\s*${name}\\b)`, "g");
    for (const match of source.matchAll(intersectionRe)) found.push(match[0].trim());
    // `as unknown as SceneNodeRow` (or straight to a local alias built from
    // one) - the other half of the ADR's own wording for this anti-pattern.
    const unsafeCastRe = new RegExp(`as unknown as \\w*${name}\\w*`, "g");
    for (const match of source.matchAll(unsafeCastRe)) found.push(match[0].trim());
  }
  return found;
}

describe("no local interface/cast widens a generated wire type (ADR-003 C9 ratchet)", () => {
  const wireTypeNames = generatedWireTypeNames();

  it("found at least one generated wire type name (sanity check the scanner itself)", () => {
    expect(wireTypeNames.length).toBeGreaterThan(0);
    expect(wireTypeNames).toContain("SceneNodeRow");
  });

  it("excludes generic exports like ValidationResult<T> from the collected name set", () => {
    // Review-fix: ValidationResult<T> is real, shared boilerplate re-exported
    // identically by every generated file (confirmed: scene-state.ts and
    // app-settings-state.ts both declare it) - if it were included, any
    // future, entirely unrelated `as unknown as SomeValidationResultThing`
    // anywhere in the app would trip this guard for no real reason.
    expect(wireTypeNames).not.toContain("ValidationResult");
  });

  // Scans BOTH app/** (components) and lib/** (transport/store/api-contract
  // code that also imports generated wire types, e.g. lib/api-contract/
  // topics.ts) - review-fix: the original version only scanned app/**,
  // leaving lib/** as a real, unmonitored blind spot for the exact same
  // anti-pattern. generated/ itself is excluded (it's what defines the
  // names being searched for, not code that could misuse them), as is any
  // .test. file (test-only fixtures are covered by a dedicated hygiene pass
  // in SceneCanvas.test.tsx itself, not this production-code ratchet).
  const sourceFiles = globSync("{app,lib}/**/*.{ts,tsx}", { cwd: REPO_SRC }).filter(
    // globSync always returns POSIX-style ("/") relative paths regardless of
    // platform - a join()-built prefix would use "\" on Windows and never
    // match, silently defeating this exclusion there.
    (path) => !path.includes(".test.") && !path.startsWith("lib/bridge-core/generated/"),
  );

  it("found at least one source file to scan (sanity check the glob itself)", () => {
    expect(sourceFiles.length).toBeGreaterThan(0);
  });

  it.each(sourceFiles)("%s does not widen a generated wire type", (relPath) => {
    const source = readFileSync(join(REPO_SRC, relPath), "utf-8");
    expect(findWireTypeWideningCasts(source, wireTypeNames)).toEqual([]);
  });
});

describe("the scanner itself catches a real regression", () => {
  const wireTypeNames = ["SceneNodeRow"];

  it("flags a local interface intersection widening the wire type (wire type on the left)", () => {
    const source = 'type SceneNodeRowWithChart = SceneNodeRow & { chartType: string };\nconst chart = n as SceneNodeRowWithChart;';
    expect(findWireTypeWideningCasts(source, wireTypeNames)).toEqual(["SceneNodeRow &"]);
  });

  it("flags a local interface intersection widening the wire type (wire type on the right)", () => {
    // Review-fix: TypeScript intersections are commutative - `X & SceneNodeRow`
    // is the identical anti-pattern with the operands swapped, and the
    // original regex (name immediately BEFORE `&` only) silently missed it.
    const source = 'type Wide = { chartType: string } & SceneNodeRow;\nconst chart = n as Wide;';
    expect(findWireTypeWideningCasts(source, wireTypeNames)).toEqual(["& SceneNodeRow"]);
  });

  it("flags a direct `as unknown as` escape hatch onto the wire type", () => {
    const source = "const n = raw as unknown as SceneNodeRow;";
    expect(findWireTypeWideningCasts(source, wireTypeNames)).toEqual(["as unknown as SceneNodeRow"]);
  });

  it("does NOT flag an intersection of two UNRELATED, non-generated types", () => {
    const source = "type Combined = Foo & Bar;";
    expect(findWireTypeWideningCasts(source, wireTypeNames)).toEqual([]);
  });

  it("does NOT flag plain, unwidened use of the generated type", () => {
    const source = "function f(n: SceneNodeRow) { return n.title; }";
    expect(findWireTypeWideningCasts(source, wireTypeNames)).toEqual([]);
  });
});

describe("generatedWireTypeNames() itself", () => {
  it("does not collect a generic export's bare name (regression pin for the ValidationResult false-positive gap)", () => {
    // Direct unit test of the regex/filter logic, independent of the real
    // generated/ directory's current contents - proves the exclusion holds
    // even if a future generated file's exact type set changes.
    const source = [
      "export interface RealRowType {",
      "  id: string;",
      "}",
      "export type ValidationResult<T> = { ok: true; value: T } | { ok: false; errors: string[] };",
    ].join("\n");
    const names = new Set<string>();
    for (const match of source.matchAll(EXPORTED_TYPE_NAME_RE)) {
      const [, name, genericParams] = match;
      if (genericParams) continue;
      names.add(name);
    }
    expect([...names]).toEqual(["RealRowType"]);
  });
});
