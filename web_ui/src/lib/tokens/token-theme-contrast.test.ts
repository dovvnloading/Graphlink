/**
 * ADR-012 stage 12.1: gl-vars-dev.css's own drift/contrast guard - the
 * frontend-toolchain replacement for the deleted Qt-era Python drift test
 * (test_gl_vars_dev_css.py, which checked the file against a Python
 * generator that no longer exists). There is no other consumer left for
 * this file to drift AGAINST, so "drift" here means something different
 * and more useful than before: the file's own INTERNAL consistency across
 * its three cascade layers, and real accessibility math against the values
 * actually shipped.
 *
 * Three things checked, matching the three real ways this file could
 * silently break without any of the other CSS/TS tests catching it:
 *
 * 1. TOKEN-NAME PARITY: every --gl-* name defined in the dark block is also
 *    defined in the light block, and vice versa. Without this, a token
 *    added to one theme and forgotten in the other doesn't error - it just
 *    silently falls through the cascade to the OTHER theme's inherited
 *    value (a light-mode component painted with a stray dark-mode color),
 *    which is exactly the kind of bug that only shows up visually, in one
 *    theme, and nowhere in a type system.
 * 2. LIGHT-BLOCK IDENTITY: the light palette is intentionally duplicated
 *    across two blocks (the prefers-color-scheme media query, for a user
 *    who hasn't explicitly chosen, and the [data-theme="light"] attribute
 *    selector, for one who has - see this file's own header for why plain
 *    CSS can't express "either selector, one value list" without a
 *    preprocessor). Duplication this exact is a real drift risk on its own
 *    - this test is the thing that makes editing only one of the two blocks
 *    a hard failure instead of a silent light/dark-preference-dependent bug.
 * 3. WCAG AA CONTRAST: real relative-luminance contrast ratios (not raw RGB
 *    distance, which is not what the eye or the spec measures) for the
 *    surface/text pairs that carry real body copy, in BOTH themes. The
 *    light palette is a systematic per-token derivation from dark (see the
 *    CSS file's own header) - this is what actually PROVES the derivation
 *    stayed legible rather than just plausible-looking.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS_PATH = join(HERE, "gl-vars-dev.css");
// Comments stripped before any selector search: this file's own header
// comment quotes the cascade's selector syntax as documentation prose
// (":root { }", ":root:not(...)", etc.) - a naive indexOf against the raw
// text would match those quotes instead of the real rules below them.
const CSS_COMMENT_RE = /\/\*[\s\S]*?\*\//g;
const css = readFileSync(CSS_PATH, "utf-8").replace(CSS_COMMENT_RE, "");

const TOKEN_RE = /(--gl-[a-z0-9-]+):\s*([^;]+);/g;

function parseTokens(blockText: string): Map<string, string> {
  const tokens = new Map<string, string>();
  for (const match of blockText.matchAll(TOKEN_RE)) {
    tokens.set(match[1], match[2].trim());
  }
  return tokens;
}

// Extracts the FIRST top-level brace-balanced { ... } body following the
// given selector text - simple brace counting is sufficient here (no
// nested {} inside a token declaration), and is what lets this pull the
// media-query's NESTED :root:not(...) block out distinctly from the outer
// @media wrapper.
function extractBlock(source: string, selectorText: string): string {
  const start = source.indexOf(selectorText);
  if (start === -1) {
    throw new Error(`selector not found: ${selectorText}`);
  }
  const braceStart = source.indexOf("{", start);
  let depth = 0;
  let i = braceStart;
  for (; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) break;
    }
  }
  return source.slice(braceStart + 1, i);
}

const darkBlock = extractBlock(css, ":root {");
const mediaOuterBlock = extractBlock(css, "@media (prefers-color-scheme: light)");
const mediaLightBlock = extractBlock(mediaOuterBlock, ':root:not([data-theme="dark"]) {');
const explicitLightBlock = extractBlock(css, ':root[data-theme="light"] {');

const darkTokens = parseTokens(darkBlock);
const mediaLightTokens = parseTokens(mediaLightBlock);
const explicitLightTokens = parseTokens(explicitLightBlock);

describe("gl-vars-dev.css structure sanity", () => {
  it("found a non-trivial number of tokens in each block (sanity-checks the extraction itself)", () => {
    expect(darkTokens.size).toBeGreaterThan(100);
    expect(mediaLightTokens.size).toBeGreaterThan(100);
    expect(explicitLightTokens.size).toBeGreaterThan(100);
  });
});

describe("token-name parity between dark and light", () => {
  it("every dark token has a light counterpart (prefers-color-scheme block)", () => {
    const missing = [...darkTokens.keys()].filter((name) => !mediaLightTokens.has(name));
    expect(missing).toEqual([]);
  });

  it("every light token (prefers-color-scheme block) has a dark counterpart", () => {
    const missing = [...mediaLightTokens.keys()].filter((name) => !darkTokens.has(name));
    expect(missing).toEqual([]);
  });

  it("every dark token has a light counterpart ([data-theme=light] block)", () => {
    const missing = [...darkTokens.keys()].filter((name) => !explicitLightTokens.has(name));
    expect(missing).toEqual([]);
  });
});

describe("the two light blocks stay identical", () => {
  it("prefers-color-scheme and [data-theme=light] define the exact same token set with the exact same values", () => {
    expect(Object.fromEntries(explicitLightTokens)).toEqual(Object.fromEntries(mediaLightTokens));
  });
});

// -- WCAG contrast --------------------------------------------------------

function relativeLuminance(hex: string): number {
  const clean = hex.replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16) / 255;
  const g = parseInt(clean.slice(2, 4), 16) / 255;
  const b = parseInt(clean.slice(4, 6), 16) / 255;
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrastRatio(hexA: string, hexB: string): number {
  const lA = relativeLuminance(hexA);
  const lB = relativeLuminance(hexB);
  const lighter = Math.max(lA, lB);
  const darker = Math.min(lA, lB);
  return (lighter + 0.05) / (darker + 0.05);
}

const WCAG_AA_TEXT = 4.5;
const WCAG_AA_LARGE_TEXT = 3.0;

// The surface/text pairs that carry real body copy - not every token
// (borders/shadows/dividers are deliberately low-contrast by design in
// BOTH themes, see gl-vars-dev.css's own derivation notes; testing those
// against a text-contrast bar would be testing the wrong thing).
//
// text-muted is held to AA-LARGE (3:1), not full AA-normal-text (4.5:1) -
// not a carve-out invented for this test, but the pre-existing dark
// palette's own actual ratio: #1E1E1E/#767676 is 3.67:1, already below
// 4.5:1 before this stage touched anything, consistent with "muted" being
// a deliberately de-emphasized role (captions/hints, not body copy). The
// light derivation (#E1E1E1/#717171, 3.73:1) was hand-corrected beyond the
// mechanical inversion specifically to match this same bar, not to invent
// a new one.
const TEXT_SURFACE_PAIRS: Array<[surface: string, text: string, minRatio: number]> = [
  ["--gl-surface-window", "--gl-surface-text-bright", WCAG_AA_TEXT],
  ["--gl-surface-window", "--gl-surface-text-strong", WCAG_AA_TEXT],
  ["--gl-surface-window", "--gl-surface-text-primary", WCAG_AA_TEXT],
  ["--gl-surface-window", "--gl-surface-text-soft", WCAG_AA_TEXT],
  ["--gl-surface-window", "--gl-surface-text-secondary", WCAG_AA_TEXT],
  ["--gl-surface-window", "--gl-surface-text-label", WCAG_AA_TEXT],
  ["--gl-surface-window", "--gl-surface-text-muted", WCAG_AA_LARGE_TEXT],
  ["--gl-surface-node-body", "--gl-surface-text-primary", WCAG_AA_TEXT],
  ["--gl-surface-inset-deep", "--gl-surface-text-primary", WCAG_AA_TEXT],
  ["--gl-surface-field", "--gl-composer-input-text", WCAG_AA_TEXT],
];

describe.each([
  ["dark", darkTokens],
  ["light", explicitLightTokens],
] as const)("%s theme: text/surface contrast meets its WCAG bar", (themeName, tokens) => {
  it.each(TEXT_SURFACE_PAIRS)("%s / %s (>= %s:1)", (surfaceVar, textVar, minRatio) => {
    const surfaceHex = tokens.get(surfaceVar);
    const textHex = tokens.get(textVar);
    expect(surfaceHex, `${surfaceVar} missing in ${themeName}`).toBeDefined();
    expect(textHex, `${textVar} missing in ${themeName}`).toBeDefined();

    const ratio = contrastRatio(surfaceHex!, textHex!);
    expect(
      ratio,
      `${themeName} ${surfaceVar}(${surfaceHex}) / ${textVar}(${textHex}) = ${ratio.toFixed(2)}:1, needs >= ${minRatio}:1`,
    ).toBeGreaterThanOrEqual(minRatio);
  });
});

describe("the contrast checker itself catches a real regression", () => {
  it("flags a low-contrast pair", () => {
    // near-identical grays - should fail AA badly
    expect(contrastRatio("#808080", "#888888")).toBeLessThan(WCAG_AA_TEXT);
  });

  it("passes a genuinely high-contrast pair", () => {
    expect(contrastRatio("#ffffff", "#000000")).toBeGreaterThanOrEqual(WCAG_AA_TEXT);
  });
});
