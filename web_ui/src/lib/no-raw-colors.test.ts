/**
 * Repo-hygiene guard for section 3.4's rule: "Raw hex in island CSS is
 * banned." Scoped to src/islands/** so it automatically covers every future
 * island, not just composer - nobody has to remember to extend this file
 * when island #2 is added.
 *
 * EXCLUDED, and why:
 * - src/lib/tokens/*.css (gl-theme.css, gl-vars-dev.css) are GENERATED and
 *   contain real color values by design - that is their entire purpose.
 *   Each already has its own staleness pytest guarding its content; this
 *   file only cares about hand-authored island CSS.
 * - web_ui/src/islands/composer/index.html's
 *   `<meta name="theme-color" content="#1a1a1a">` is a deliberate, narrow
 *   exception, not an oversight: it is a browser/OS chrome hint (the tab/
 *   task-switcher accent color), read before any CSS or JS runs, so it
 *   cannot be a var(--gl-*) reference by construction - nothing has resolved
 *   any custom property at that point. Scoping the ban to the two places a
 *   literal could actually be replaced by a token (CSS declaration values
 *   and TSX inline styles) rather than to "any hex string in island source"
 *   keeps the ban meaningful instead of chasing an unfixable false positive.
 *
 * Anchored on DECLARATION VALUES, not raw file text: `#face` and `#dad` are
 * valid CSS ID selectors as well as valid hex colors, so a scan of raw text
 * would flag `#face { ... }` as a color literal. Scanning only the text
 * between a `:` and the declaration's terminating `;`/`}` avoids that class
 * of false positive without needing a real CSS parser.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { globSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_SRC = join(HERE, "..");

const COLOR_LITERAL_RE = /#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g;
// Matches the text between a `:` and the declaration's terminating `;` or
// `}`, so pseudo-selector colons (:hover, :not(:disabled)) and selector-only
// hex-looking IDs never enter the scanned text at all - only real property
// values do.
const DECLARATION_VALUE_RE = /:\s*([^;{}]+)[;}]/g;

function findColorLiteralsInCssDeclarationValues(css: string): string[] {
  const found: string[] = [];
  for (const match of css.matchAll(DECLARATION_VALUE_RE)) {
    const value = match[1];
    found.push(...(value.match(COLOR_LITERAL_RE) ?? []));
  }
  return found;
}

function findColorLiteralsInInlineStyles(source: string): string[] {
  const found: string[] = [];
  // A JSX inline style block: style={{ ... }}. Non-greedy up to the matching
  // `}}` - island code today never nests an object literal inside a style
  // prop, so this simple bound is sufficient without a real JS parser.
  for (const block of source.matchAll(/style=\{\{([\s\S]*?)\}\}/g)) {
    found.push(...(block[1].match(COLOR_LITERAL_RE) ?? []));
  }
  return found;
}

describe("no raw color literals in island CSS", () => {
  const cssFiles = globSync("islands/**/*.css", { cwd: REPO_SRC });

  it("found at least one island CSS file to scan (sanity check the glob itself)", () => {
    expect(cssFiles.length).toBeGreaterThan(0);
  });

  it.each(cssFiles)("%s has zero hardcoded hex/rgba literals", (relPath) => {
    const css = readFileSync(join(REPO_SRC, relPath), "utf-8");
    const literals = findColorLiteralsInCssDeclarationValues(css);

    expect(literals).toEqual([]);
  });
});

describe("no raw color literals in island TSX inline styles", () => {
  const sourceFiles = globSync("islands/**/*.{ts,tsx}", { cwd: REPO_SRC }).filter(
    (path) => !path.includes(".test."),
  );

  it("found at least one island source file to scan (sanity check the glob itself)", () => {
    expect(sourceFiles.length).toBeGreaterThan(0);
  });

  it.each(sourceFiles)("%s has zero hardcoded colors in style={{...}} blocks", (relPath) => {
    const source = readFileSync(join(REPO_SRC, relPath), "utf-8");
    const literals = findColorLiteralsInInlineStyles(source);

    expect(literals).toEqual([]);
  });
});

describe("the scanner itself catches a real regression", () => {
  it("flags a hex color in a declaration value", () => {
    expect(findColorLiteralsInCssDeclarationValues(".foo { color: #1f1f1f; }")).toEqual([
      "#1f1f1f",
    ]);
  });

  it("flags every color in a multi-value declaration (e.g. a two-color box-shadow)", () => {
    const css =
      ".foo { box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2), inset 0 1px rgba(255, 255, 255, 0.035); }";
    expect(findColorLiteralsInCssDeclarationValues(css)).toEqual([
      "rgba(0, 0, 0, 0.2)",
      "rgba(255, 255, 255, 0.035)",
    ]);
  });

  it("does NOT flag a hex-looking ID selector", () => {
    expect(findColorLiteralsInCssDeclarationValues("#face { color: var(--gl-x); }")).toEqual([]);
  });

  it("does NOT flag pseudo-class colons in a compound selector", () => {
    const css = ".attach-button:hover:not(:disabled) { color: var(--gl-x); }";
    expect(findColorLiteralsInCssDeclarationValues(css)).toEqual([]);
  });

  it("flags a hex color inside a JSX inline style block", () => {
    expect(findColorLiteralsInInlineStyles('<div style={{ color: "#1f1f1f" }} />')).toEqual([
      "#1f1f1f",
    ]);
  });

  it("does NOT flag an SVG path's `d` attribute, which is not a style block", () => {
    expect(findColorLiteralsInInlineStyles('<path d="M12 5.5 6.4 11.1" />')).toEqual([]);
  });
});
