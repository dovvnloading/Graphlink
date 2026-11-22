/**
 * UI-02: freeze the ad-hoc UI patterns that predate src/app/ui/.
 *
 * Every positive count is pinned by file, not by a repo-wide total. A new
 * occurrence (including one in a new file) fails; a decrease outside the
 * deliberately flexible styles.css maxima asks the author to lower the pin.
 * This keeps a migration from one legacy file from subsidising regressions in
 * another file. Refer to docs/DESIGN_SYSTEM.md for replacement conventions.
 *
 * The scanners work on comment-stripped source and only inspect JSX tags,
 * string/template text and JSX text for TSX-specific rules. They deliberately
 * avoid a broad text grep, which would see TS spread syntax and regexes as UI.
 */
import { describe, expect, it } from "vitest";
import { globSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { R1_ALLOWLIST, UI_RATCHET_INVENTORY, type RatchetRule } from "./ui-ratchet-inventory";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const DOC = "docs/DESIGN_SYSTEM.md";
const TSX_RULES: RatchetRule[] = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R13"];
const CSS_RULES: RatchetRule[] = ["R9", "R10", "R11", "R14"];

// An emoji is a Unicode code point, not a UTF-16 code unit. Use /u, and keep
// the HTML entities literal so escaping the visual symbol does not evade R1.
const ICON_RE = /[×✓★☆⌕↻⟳📁📄📌♫◈▸▾▼▲◂▶◀▣▧⌄⌃⇄⧉✎⚠⚙●○↗↑↓‹›]|&(?:times|rsaquo|lsaquo);|&#9662;/gu;
const ELLIPSIS_RE = /\.\.\./g;

function blank(source: string): string {
  return source.replace(/[^\r\n]/g, " ");
}

function afterQuote(source: string, start: number): number {
  const quote = source[start];
  let i = start + 1;
  while (i < source.length) {
    if (source[i] === "\\") { i += 2; continue; }
    if (quote === "`" && source[i] === "$" && source[i + 1] === "{") {
      i = afterExpression(source, i + 2);
      continue;
    }
    if (source[i] === quote) return i + 1;
    i++;
  }
  return i;
}

// Skip a complete template interpolation, including nested objects, strings,
// regexes, comments and other templates; those expressions are scanned below.
function afterExpression(source: string, start: number): number {
  let depth = 1;
  for (let i = start; i < source.length;) {
    if (source[i] === "'" || source[i] === '"' || source[i] === "`") {
      i = afterQuote(source, i);
    } else if (source[i] === "/" && source[i + 1] === "/") {
      const end = source.indexOf("\n", i + 2);
      i = end < 0 ? source.length : end;
    } else if (source[i] === "/" && source[i + 1] === "*") {
      const close = source.indexOf("*/", i + 2);
      i = close < 0 ? source.length : close + 2;
    } else if (source[i] === "/" && startsRegex(source, i)) {
      i = afterRegex(source, i);
    } else if (source[i] === "{") { depth++; i++; }
    else if (source[i] === "}") {
      if (--depth === 0) return i + 1;
      i++;
    } else { i++; }
  }
  return source.length;
}

function startsRegex(source: string, index: number): boolean {
  const before = source.slice(Math.max(0, index - 48), index).trimEnd();
  return (before.length > 0 && "=(:,!?[{;|&~".includes(before[before.length - 1])) ||
    /\b(?:return|throw|case|yield)\s*$/.test(before);
}

function afterRegex(source: string, start: number): number {
  let inClass = false;
  for (let i = start + 1; i < source.length; i++) {
    if (source[i] === "\\") { i++; continue; }
    if (source[i] === "[") inClass = true;
    else if (source[i] === "]") inClass = false;
    else if (source[i] === "/" && !inClass) {
      i++;
      while (/[a-z]/i.test(source[i] ?? "")) i++;
      return i;
    }
    if (source[i] === "\n") break;
  }
  return start + 1;
}

function stripTsComments(source: string): string {
  const parts: string[] = [];
  let from = 0;
  for (let i = 0; i < source.length;) {
    if (source[i] === "'" || source[i] === '"' || source[i] === "`") {
      i = afterQuote(source, i);
    } else if (source[i] === "/" && source[i + 1] === "/") {
      let end = source.indexOf("\n", i + 2);
      if (end < 0) end = source.length;
      parts.push(source.slice(from, i), blank(source.slice(i, end)));
      i = end;
      from = end;
    } else if (source[i] === "/" && source[i + 1] === "*") {
      const close = source.indexOf("*/", i + 2);
      const end = close < 0 ? source.length : close + 2;
      parts.push(source.slice(from, i), blank(source.slice(i, end)));
      i = end;
      from = end;
    } else if (source[i] === "/" && startsRegex(source, i)) {
      i = afterRegex(source, i);
    } else {
      i++;
    }
  }
  parts.push(source.slice(from));
  return parts.join("");
}

function stripCssComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, blank);
}

// Keep template literal fragments separate. Joining across an interpolation
// could invent an ellipsis that does not exist in any single literal fragment.
function templateFragments(value: string): { segments: string[]; expressions: string[] } {
  const segments: string[] = [];
  const expressions: string[] = [];
  let from = 0;
  for (let i = 0; i < value.length;) {
    if (value[i] === "\\") { i += 2; continue; }
    if (value[i] === "$" && value[i + 1] === "{") {
      segments.push(value.slice(from, i));
      const end = afterExpression(value, i + 2);
      expressions.push(value.slice(i + 2, end - 1));
      i = end;
      from = end;
    } else { i++; }
  }
  segments.push(value.slice(from));
  return { segments, expressions };
}

function inspectTsx(source: string): { clean: string; code: string; tags: JsxTag[]; literalTexts: string[] } {
  const clean = stripTsComments(source);
  const codeParts: string[] = [];
  const literals: string[] = [];
  const nestedExpressions: string[] = [];
  let from = 0;
  for (let i = 0; i < clean.length;) {
    if (clean[i] === "'" || clean[i] === '"' || clean[i] === "`") {
      // A possessive apostrophe in unquoted JSX text is not a JS literal.
      if (clean[i] === "'" && /[\p{L}\p{N}]/u.test(clean[i - 1] ?? "") &&
          /[\p{L}\p{N}]/u.test(clean[i + 1] ?? "")) { i++; continue; }
      const end = afterQuote(clean, i);
      const raw = clean.slice(i + 1, end - 1);
      if (clean[i] === "`") {
        const { segments, expressions } = templateFragments(raw);
        literals.push(...segments);
        nestedExpressions.push(...expressions);
      } else { literals.push(raw); }
      codeParts.push(clean.slice(from, i), blank(clean.slice(i, end)));
      i = end;
      from = end;
    } else if (clean[i] === "/" && startsRegex(clean, i)) {
      const end = afterRegex(clean, i);
      if (end > i + 1) {
        codeParts.push(clean.slice(from, i), blank(clean.slice(i, end)));
        i = end;
        from = end;
      } else { i++; }
    } else { i++; }
  }
  codeParts.push(clean.slice(from));
  const code = codeParts.join("");
  // Only tag-closing '>' may begin JSX text; comparison operators must not
  // make surrounding JS (including spread/rest) look like rendered text.
  const tags = jsxTags(clean, code);
  const tagEnds = new Set(tags.map((tag) => tag.end));
  for (const match of code.matchAll(/<\/[A-Za-z][\w.:-]*\s*>|<\/?>/g)) {
    tagEnds.add(match.index + match[0].length - 1);
  }
  const jsxTexts = [...code.matchAll(/(?:>|\})([^<>{}]+)(?=[<{])/g)]
    .filter((match) => match[0].startsWith(">")
      ? tagEnds.has(match.index)
      : code.lastIndexOf(">", match.index) > code.lastIndexOf(";", match.index))
    .map((match) => match[1]);
  const expressions = nestedExpressions.flatMap((expression) => inspectTsx(expression).literalTexts);
  return { clean, code, tags, literalTexts: [...literals, ...expressions, ...jsxTexts] };
}

type JsxTag = { name: string; attributes: Map<string, string>; end: number };

function jsxTags(clean: string, code: string): JsxTag[] {
  const tags: JsxTag[] = [];
  for (let i = 0; i < code.length; i++) {
    if (code[i] !== "<" || !/[A-Za-z]/.test(code[i + 1] ?? "")) continue;
    const name = /^[A-Za-z][\w.:-]*/.exec(code.slice(i + 1))?.[0];
    if (!name) continue;
    let j = i + 1 + name.length;
    let depth = 0;
    for (; j < clean.length; j++) {
      if (clean[j] === "'" || clean[j] === '"' || clean[j] === "`") { j = afterQuote(clean, j) - 1; continue; }
      if (clean[j] === "{") depth++;
      else if (clean[j] === "}") depth--;
      else if (clean[j] === ">" && depth === 0) break;
    }
    if (j === clean.length) continue;
    const contents = clean.slice(i + 1 + name.length, j);
    const attributes = new Map<string, string>();
    for (let k = 0; k < contents.length;) {
      if (/\s|\//.test(contents[k])) { k++; continue; }
      if (contents[k] === "{") {
        let depth = 1;
        k++;
        while (k < contents.length && depth > 0) {
          if (contents[k] === "'" || contents[k] === '"' || contents[k] === "`") { k = afterQuote(contents, k); continue; }
          if (contents[k] === "{") depth++;
          if (contents[k] === "}") depth--;
          k++;
        }
        continue;
      }
      const attr = /^[\w:-]+/.exec(contents.slice(k))?.[0];
      if (!attr) { k++; continue; }
      k += attr.length;
      while (/\s/.test(contents[k] ?? "")) k++;
      if (contents[k] !== "=") { attributes.set(attr, ""); continue; }
      k++;
      while (/\s/.test(contents[k] ?? "")) k++;
      if (contents[k] === "'" || contents[k] === '"') {
        const end = afterQuote(contents, k);
        attributes.set(attr, contents.slice(k + 1, end - 1));
        k = end;
      } else if (contents[k] === "{") {
        const start = k;
        let depth = 1;
        k++;
        while (k < contents.length && depth > 0) {
          if (contents[k] === "'" || contents[k] === '"' || contents[k] === "`") { k = afterQuote(contents, k); continue; }
          if (contents[k] === "{") depth++;
          else if (contents[k] === "}") depth--;
          k++;
        }
        const expression = contents.slice(start + 1, k - 1).trim();
        const literal = /^(?:"([^"\\]*)"|'([^'\\]*)')$/.exec(expression);
        attributes.set(attr, literal ? (literal[1] ?? literal[2]) : "{dynamic}");
      } else {
        const value = /^[^\s/>]*/.exec(contents.slice(k))?.[0] ?? "";
        attributes.set(attr, value);
        k += Math.max(1, value.length);
      }
    }
    tags.push({ name, attributes, end: j });
  }
  return tags;
}

function count(text: string, pattern: RegExp): number {
  return [...text.matchAll(pattern)].length;
}

function nativeDialogCalls(code: string): number {
  let total = 0;
  for (const match of code.matchAll(/\b(?:confirm|alert|prompt)\s*\(/g)) {
    const before = code.slice(0, match.index).trimEnd();
    const memberCall = /(?:\?\.|\.)\s*$/.test(before);
    if (!memberCall || /\bwindow\s*(?:\?\.|\.)\s*$/.test(before)) total++;
  }
  return total;
}

function tsxCounts(source: string, file: string): Partial<Record<RatchetRule, number>> {
  const parsed = inspectTsx(source);
  const tags = parsed.tags;
  const texts = parsed.literalTexts;
  const rawIcons = texts.reduce((sum, text) => sum + count(text, ICON_RE), 0);
  const exemptHarness = file === "app/canvas/HarnessNodeView.tsx"
    ? allowableGlyphCount(source, file, "×") : 0;
  return {
    R1: rawIcons - exemptHarness,
    R2: /^(?:app\/canvas\/charts\/|app\/canvas\/connections\/)/.test(file) ||
      ["app/canvas/ScientificResultViews.tsx", "app/canvas/OrthogonalEdge.tsx"].includes(file)
      ? 0 : tags.filter((tag) => tag.name === "svg").length,
    R3: nativeDialogCalls(parsed.code),
    R4: tags.filter((tag) => tag.name === "select").length,
    R5: tags.filter((tag) => tag.name === "details").length,
    R6: tags.filter((tag) => tag.name === "input" && ["checkbox", "radio", "range"].includes(tag.attributes.get("type") ?? "")).length,
    R7: tags.filter((tag) => ["menuitem", "menuitemcheckbox", "menuitemradio"].includes(tag.attributes.get("role") ?? "")).length,
    R8: tags.filter((tag) => tag.name !== "iframe" && tag.attributes.has("title")).length,
    R13: texts.reduce((sum, text) => sum + count(text, ELLIPSIS_RE), 0),
  };
}

type CssDeclaration = { name: string; value: string; scope: string };

function cssDeclarations(source: string): CssDeclaration[] {
  const css = stripCssComments(source);
  const result: CssDeclaration[] = [];
  const stack: string[] = [];
  let start = 0;
  function record(end: number) {
    const segment = css.slice(start, end).trim();
    const match = /^([\w-]+)\s*:\s*([\s\S]*)$/.exec(segment);
    if (match) result.push({ name: match[1].toLowerCase(), value: match[2].trim(), scope: stack.join(" ") });
  }
  for (let i = 0; i < css.length; i++) {
    if (css[i] === "{") { stack.push(css.slice(start, i).trim()); start = i + 1; }
    else if (css[i] === ";") { record(i); start = i + 1; }
    else if (css[i] === "}") { record(i); stack.pop(); start = i + 1; }
  }
  return result;
}

function isVar(value: string): boolean {
  return /^var\(\s*--[\w-]+(?:\s*,[\s\S]*)?\)$/.test(value);
}

function allowedRadius(value: string): boolean {
  const rest = value.replace(/var\([^)]*\)/g, "").trim();
  return !rest || rest.split(/\s+/).every((token) => ["0", "inherit", "50%"].includes(token));
}

function removeVarAndCalc(value: string): string {
  const chars = value.split("");
  for (let i = 0; i < value.length; i++) {
    const fn = /^(?:var|calc)\(/.exec(value.slice(i));
    if (!fn) continue;
    let depth = 0;
    let end = i;
    for (; end < value.length; end++) {
      if (value[end] === "(") depth++;
      else if (value[end] === ")" && --depth === 0) { end++; break; }
    }
    for (let j = i; j < end; j++) chars[j] = " ";
    i = end - 1;
  }
  return chars.join("");
}

function cssCounts(source: string): Partial<Record<RatchetRule, number>> {
  const totals = { R9: 0, R10: 0, R11: 0, R14: 0 };
  for (const { name, value, scope } of cssDeclarations(source)) {
    if (name === "font-size" && !isVar(value) &&
        !(/(?:\.node-md\b|\.document-view-md\b|\.katex\b)/i.test(scope) && /^-?\d*\.?\d+em$/.test(value))) totals.R9++;
    if (name === "border-radius" && !allowedRadius(value)) totals.R10++;
    if (name === "z-index" && !isVar(value) && !/^(?:0|1|2|-1|-2)$/.test(value)) totals.R11++;
    if (/^(?:padding|margin|inset)(?:-(?:top|right|bottom|left|block(?:-start|-end)?|inline(?:-start|-end)?))?$|^(?:gap|row-gap|column-gap)$/.test(name)) {
      const raw = removeVarAndCalc(value);
      for (const match of raw.matchAll(/(?<![\w.-])(-?\d*\.?\d+)px\b/g)) {
        if (match[1] !== "1") totals.R14++;
      }
    }
  }
  return totals;
}

function ruleCounts(source: string, file: string): Partial<Record<RatchetRule, number>> {
  if (file.endsWith(".css")) return {
    ...cssCounts(source),
    ...(file === "app/styles.css" ? { R12: source.split(/\r?\n/).length } : {}),
  };
  return tsxCounts(source, file);
}

// These are intentional exceptions to R1, *not* general exemptions for their
// files. They have their own exact counts and written justifications in the
// inventory; adding another occurrence still trips one of the two guards.
function allowableGlyphCount(source: string, file: string, needle: string): number {
  const clean = stripTsComments(source);
  if (file === "app/canvas/scientificWorkbench.ts" && needle === "×") {
    return count(clean, /["'] × 10["']/g);
  }
  if (file === "app/canvas/HarnessNodeView.tsx" && needle === "×") {
    return count(clean, /`[^`]*\bcompacted\s+\$\{data\.harnessCompactions\}×`/g);
  }
  if (file === "app/canvas/ChatVersionCompareDialog.tsx") {
    if (needle === "−") return count(clean, /<span\s+aria-hidden="true">−\s*<\/span>/g);
    if (needle === "+") return count(clean, /<span\s+aria-hidden="true">\+\s*<\/span>/g);
  }
  return 0;
}

const inScope = (file: string) => !file.includes(".test.") &&
  !file.split("/").includes("generated") && !file.startsWith("app/ui/");
const normalize = (file: string) => file.replaceAll("\\", "/");

const tsxFiles = globSync("**/*.tsx", { cwd: SRC }).map(normalize).filter(inScope).sort();
const cssFiles = globSync("app/**/*.css", { cwd: SRC }).map(normalize).filter(inScope).sort();
const sourceFiles = [...tsxFiles, ...cssFiles].sort();
const pinned = new Map<string, number>(UI_RATCHET_INVENTORY.map(({ file, rule, count: value }) => [`${file}|${rule}`, value] as const));

describe("UI-02 per-file ratchets", () => {
  it("discovers the TSX and CSS source trees", () => {
    expect(tsxFiles.length).toBeGreaterThan(0);
    expect(cssFiles.length).toBeGreaterThan(0);
  });

  it("keeps nonzero, unique inventory entries sorted by file then rule", () => {
    const keys = UI_RATCHET_INVENTORY.map(({ file, rule, count: value }) => {
      expect(value, `${file} ${rule} in ${DOC}: omit zero pins`).toBeGreaterThan(0);
      expect(sourceFiles, `${file} ${rule} in ${DOC}: file not in scan scope`).toContain(file);
      return `${file}|${rule.slice(1).padStart(2, "0")}`;
    });
    expect(keys).toEqual([...new Set(keys)].sort());
  });

  it.each(sourceFiles)("%s has no new or unpinned UI patterns", (file) => {
    const actual = ruleCounts(readFileSync(join(SRC, file), "utf8"), file);
    const rules = file.endsWith(".css") ? [...CSS_RULES, ...(file === "app/styles.css" ? ["R12" as RatchetRule] : [])] : TSX_RULES;
    for (const rule of rules) {
      const value = actual[rule] ?? 0;
      const pin = pinned.get(`${file}|${rule}`) ?? 0;
      const context = `${file} ${rule}: see ${DOC}.`;
      if (file === "app/styles.css") {
        expect(value, `${context} styles.css exceeds its frozen maximum ${pin}.`).toBeLessThanOrEqual(pin);
      } else {
        expect(value, value < pin
          ? `${context} Found ${value}, pinned ${pin}; lower this file's inventory pin.`
          : `${context} Found ${value}, pinned ${pin}; replace the new raw UI pattern.`).toBe(pin);
      }
    }
  });

  it("does not pin files or rules outside their applicable scope", () => {
    for (const { file, rule } of UI_RATCHET_INVENTORY) {
      expect(file.endsWith(".css") ? CSS_RULES.includes(rule) || (file === "app/styles.css" && rule === "R12") : TSX_RULES.includes(rule), `${file} ${rule} ${DOC}`).toBe(true);
    }
  });

  it("freezes each documented R1 exception separately", () => {
    const allowedPaths = new Set(["app/canvas/scientificWorkbench.ts", "app/canvas/HarnessNodeView.tsx", "app/canvas/ChatVersionCompareDialog.tsx"]);
    for (const { file, needle, count: pin, reason } of R1_ALLOWLIST) {
      expect(allowedPaths.has(file), `${file} R1 allowlist ${DOC}`).toBe(true);
      expect(reason.length, `${file} R1 requires a written reason in ${DOC}`).toBeGreaterThan(10);
      const actual = allowableGlyphCount(readFileSync(join(SRC, file), "utf8"), file, needle);
      expect(actual, `${file} R1 allowlist (${needle}): ${DOC}; ${actual < pin ? "lower the allowlist pin" : "new exception is not allowed"}.`).toBe(pin);
    }
    expect(new Set(R1_ALLOWLIST.map((x) => `${x.file}|${x.needle}`)).size).toBe(R1_ALLOWLIST.length);
  });
});

describe("UI-02 detector self-tests (positive and known false-positive shapes)", () => {
  it("R1 finds glyphs and entities in JSX text and JS strings, not in regex literals/comments", () => {
    expect(tsxCounts('const rx = /[×✓]/; // ×\n<span>✓ &times; {"📁"}</span>', "app/sample.tsx").R1).toBe(3);
  });
  it("R2 recognizes real SVGs but not markup inside strings or the chart exception", () => {
    expect(tsxCounts('const example = "<svg>"; <svg><path /></svg>', "app/sample.tsx").R2).toBe(1);
    expect(tsxCounts("<svg />", "app/canvas/charts/Chart.tsx").R2).toBe(0);
  });
  it("R3 distinguishes actual confirm/alert/prompt calls from commented or quoted text", () => {
    expect(tsxCounts('window.confirm("ok"); alert("x"); prompt("y"); // confirm()', "app/sample.tsx").R3).toBe(3);
  });
  it("R4–R7 inspect native JSX markup rather than comments", () => {
    const src = '<select /><details /><input type="checkbox" /><input type="radio" /><input type="range" /><div role="menuitem" />\n/* <input type="checkbox" /> */';
    const counts = tsxCounts(src, "app/sample.tsx");
    expect([counts.R4, counts.R5, counts.R6, counts.R7]).toEqual([1, 1, 3, 1]);
  });
  it("R8 ignores iframe titles and object-type prop names", () => {
    const src = 'type Props = { title: string }; const p: Props = { title: "x" }; <iframe title="Video" /><div title="Legacy tooltip" />';
    expect(tsxCounts(src, "app/sample.tsx").R8).toBe(1);
  });
  it("R9–R11 allow tokens, markdown em, standard radius and z-index values", () => {
    const css = '.foo { font-size: var(--gl-small); border-radius: 50%; z-index: -1; }\n.node-md p {font-size: 0.9em} .katex span { font-size: 1.1em }\n.foo {font-size: 13px; border-radius: 8px; z-index: 7}';
    expect(cssCounts(css)).toMatchObject({ R9: 1, R10: 1, R11: 1 });
  });
  it("R14 ignores calc geometry, variables, zero and 1px hairlines, but finds raw spacing", () => {
    const css = '.a { padding: calc(100% - 2px) var(--gl-space); margin: 0 1px; } /* gap: 10px; */ .b { gap: 4px; padding-top: 6px; }';
    expect(cssCounts(css).R14).toBe(2);
  });
  it("R13 reads only literal ellipses, not object spread or rest parameters", () => {
    const src = 'const a = {...x}; function fn(...args: string[]) {} const r = /[.]{3}/; <p>Wait...</p>; const s = "Loading..."';
    expect(tsxCounts(src, "app/sample.tsx").R13).toBe(2);
    expect(tsxCounts("<p>{value}Wait...</p>", "app/sample.tsx").R13).toBe(1);
  });
  it("R1 exception scanner requires its exact justified context", () => {
    expect(allowableGlyphCount('return " × 10";', "app/canvas/scientificWorkbench.ts", "×")).toBe(1);
    expect(allowableGlyphCount('const s = "×";', "app/canvas/scientificWorkbench.ts", "×")).toBe(0);
    expect(tsxCounts('<button>×</button> `compacted ${data.harnessCompactions}×`', "app/canvas/HarnessNodeView.tsx").R1).toBe(1);
  });
  it("R12 counts styles.css lines and applies to no other stylesheet", () => {
    expect(ruleCounts(".a {}\n.b {}", "app/styles.css").R12).toBe(2);
    expect(ruleCounts(".a {}", "app/other.css").R12).toBeUndefined();
  });
  it("counts only JSX text, not expressions following comparison operators", () => {
    const code = 'const a = value > "✓" && value < limit; const b = value > [...items].length && value < limit;';
    expect(tsxCounts(code, "app/sample.tsx").R1).toBe(1);
    expect(tsxCounts(code, "app/sample.tsx").R13).toBe(0);
  });
  it("scans nested template expression strings without joining adjacent fragments", () => {
    const code = 'const a = `value ${"Loading..."} tail`; const b = `outer ${`inner ${"✓"}`} tail`;';
    expect(tsxCounts(code, "app/sample.tsx").R13).toBe(1);
    expect(tsxCounts(code, "app/sample.tsx").R1).toBe(1);
    expect(tsxCounts('const x = `one.${value}..`', "app/sample.tsx").R13).toBe(0);
  });
  it("accepts every CSS var() token, but not a raw 0px spacing value", () => {
    expect(cssCounts(".x { font-size: var(--font-size); z-index: var(--layer); margin: 0px; }"))
      .toMatchObject({ R9: 0, R11: 0, R14: 1 });
  });
  it("handles multiline JSX attributes and excludes non-window member dialogs", () => {
    const jsx = '<input\n type=\n"checkbox" title=\n"Custom" />\nwindow . alert("ok"); dialogs . alert("not native");';
    expect(tsxCounts(jsx, "app/sample.tsx").R6).toBe(1);
    expect(tsxCounts(jsx, "app/sample.tsx").R8).toBe(1);
    expect(tsxCounts(jsx, "app/sample.tsx").R3).toBe(1);
  });

});
