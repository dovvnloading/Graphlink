/**
 * Repo-hygiene guard for section 3.4's rule: raw hex in hand-authored UI CSS
 * is banned - every color must come from a var(--gl-*) design token.
 *
 * R7.6a repointed this from src/islands/** to src/app/**: the 19 islands were
 * deleted with the Qt hosts that embedded them, and the SPA under src/app is
 * now the only hand-authored UI in the repo. The glob stays directory-scoped
 * rather than file-listed for the same reason as before - it covers every
 * future component automatically, with nobody needing to remember this file.
 *
 * EXCLUDED, and why:
 * - src/lib/tokens/*.css (gl-theme.css, gl-vars-dev.css) are GENERATED and
 *   contain real color values by design - that is their entire purpose.
 *   Each already has its own staleness pytest guarding its content; this
 *   file only cares about hand-authored island CSS.
 * - a document's
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
 *
 * Two further false-positive shapes, found by adversarial review with real
 * repro cases and fixed here rather than left latent: a CSS comment
 * mentioning a hex value in prose (e.g. `/* old value: #1f1f1f *\/`) would
 * otherwise be flagged, since comments are never stripped before scanning;
 * and `url(path#fragment)` where the fragment happens to be all hex digits
 * (e.g. `url(sprite.svg#abc123)`) would be misread as a color, since a URL
 * fragment identifier and a hex color are textually indistinguishable to a
 * regex. Comments are stripped from the whole file before declaration
 * extraction; url(...) contents are stripped from each declaration's value
 * before color matching, since a url() argument is never itself a color.
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
const CSS_COMMENT_RE = /\/\*[\s\S]*?\*\//g;
const URL_RE = /url\([^)]*\)/gi;

function findColorLiteralsInCssDeclarationValues(css: string): string[] {
  const found: string[] = [];
  const withoutComments = css.replace(CSS_COMMENT_RE, "");
  for (const match of withoutComments.matchAll(DECLARATION_VALUE_RE)) {
    const value = match[1].replace(URL_RE, "");
    found.push(...(value.match(COLOR_LITERAL_RE) ?? []));
  }
  return found;
}

// KNOWN LIMITATION, not fixed here: this only sees an inline OBJECT LITERAL
// (style={{ color: "#1f1f1f" }}). style={someVariable} referencing a hex
// string defined elsewhere in the file - or imported from another module -
// is invisible to a regex-based scanner; catching that would need real
// static analysis (tracing a variable's value across an arbitrary module
// graph), which is disproportionate to a repo-hygiene check. No island TSX
// uses a `style=` prop of any shape today (grepped directly - zero matches),
// so this is a latent gap, not a live one - flagged here so it isn't
// rediscovered as a surprise the first time someone reaches for `style=`.
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

// R7.6a: the SPA came under this guard for the first time when the glob moved
// off the deleted islands tree, and it immediately surfaced 8 literals that
// had never been checked. They are PINNED here rather than silently excluded,
// deliberately mirroring qt_burndown.json's own idiom: the count may only go
// down, and any NEW literal fails the build.
//
// They are not swapped for tokens in THIS increment because none is a
// like-for-like substitution - doing it correctly changes rendered output and
// belongs in a design pass, not an island deletion:
//   - `0 8px 28px rgba(0,0,0,0.45)` and `0 12px 40px rgba(0,0,0,0.55)` match no
//     existing token: --gl-shadow-3 is `0 8px 28px rgba(0,0,0,0.55)`, so it
//     shares geometry with one and alpha with the other but equals neither.
//   - `rgba(0,0,0,0.43)` is a scrim with no token of any kind.
//   - `var(--gl-accent, #6ea8fe)`'s fallback is load-bearing: --gl-accent is
//     not defined in the generated token files, so the literal is what
//     actually renders. Dropping it would remove the color entirely.
const PINNED_APP_CSS_LITERALS: Record<string, string[]> = {
  "app\\styles.css": [
    "rgba(0, 0, 0, 0.45)",
    "rgba(0, 0, 0, 0.45)",
    "rgba(0, 0, 0, 0.43)",
    "rgba(0, 0, 0, 0.55)",
    // R8a: .composer-dock and .composer-stream-preview's box-shadow - the
    // composer island reuses the SAME 0 8px 28px rgba(0,0,0,0.45) value
    // already pinned above for .overlay-popover, not a new distinct raw
    // color, so it's added to the ratchet rather than tokenized alone.
    "rgba(0, 0, 0, 0.45)",
    "rgba(0, 0, 0, 0.45)",
    "rgba(0, 0, 0, 0.55)",
    "rgba(0, 0, 0, 0.45)",
    "#6ea8fe",
    "rgba(0, 0, 0, 0.45)",
  ],
};

describe("no raw color literals in app CSS", () => {
  const cssFiles = globSync("app/**/*.css", { cwd: REPO_SRC });

  it("found at least one app CSS file to scan (sanity check the glob itself)", () => {
    expect(cssFiles.length).toBeGreaterThan(0);
  });

  it.each(cssFiles)("%s has no unpinned hardcoded hex/rgba literals", (relPath) => {
    const css = readFileSync(join(REPO_SRC, relPath), "utf-8");
    const literals = findColorLiteralsInCssDeclarationValues(css);

    expect(literals).toEqual(PINNED_APP_CSS_LITERALS[relPath] ?? []);
  });
});

describe("no raw color literals in island TSX inline styles", () => {
  const sourceFiles = globSync("app/**/*.{ts,tsx}", { cwd: REPO_SRC }).filter(
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

  it("does NOT flag a hex value mentioned inside a CSS comment", () => {
    const css = "/* old value: #1f1f1f, replaced by a token */\n.foo { color: var(--gl-x); }";
    expect(findColorLiteralsInCssDeclarationValues(css)).toEqual([]);
  });

  it("does NOT flag an all-hex-digit url() fragment identifier", () => {
    const css = ".foo { background: url(sprite.svg#abc123); }";
    expect(findColorLiteralsInCssDeclarationValues(css)).toEqual([]);
  });

  it("still flags a real color declared alongside a url() in the same rule", () => {
    const css = ".foo { background: url(sprite.svg#abc123); color: #1f1f1f; }";
    expect(findColorLiteralsInCssDeclarationValues(css)).toEqual(["#1f1f1f"]);
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
