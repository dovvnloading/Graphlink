/**
 * ADR-003 stage 3.6: repo-hygiene ratchet for the ONE way the offline intent
 * queue can silently corrupt user data - marking a NON-IDEMPOTENT intent
 * `queueable: true`.
 *
 * fireIntent's 5th argument opts an intent into being held while the socket is
 * closed and replayed on reconnect. That is only safe when replaying is
 * indistinguishable from delivering once: a `set*` intent carries the value it
 * wants, so applying it twice lands on the same state. A `toggle*` intent does
 * NOT - the backend handler flips (`node.state.is_locked = not
 * node.state.is_locked`, backend/domain/groups.py), so a second application
 * undoes the first.
 *
 * That second application is reachable, not theoretical: fireIntent re-queues
 * an intent that was genuinely IN FLIGHT when the socket died (the
 * WsUnavailableError branch), and in that window the server may already have
 * received and applied it - only the reply was lost. The replay then flips the
 * value back and the user's action silently reverts. This was a real
 * misclassification caught during stage 3.6's own review of its call sites;
 * this guard is what stops it being reintroduced.
 *
 * KNOWN LIMITATION, stated rather than papered over (same posture as
 * wireTypeCastGuard.test.ts's own documented gap): this keys off the `toggle`
 * NAMING CONVENTION, not semantic analysis of the backend handler. An intent
 * that flips a value without saying "toggle" in its name is equally unsafe and
 * equally invisible here. The convention holds across every intent in the
 * codebase today (checked against backend/api/intents_*.py), so this is a
 * limitation of the tool's reach, not a live undetected violation - but a
 * reviewer adding a new flip-style intent must still think, and should extend
 * NON_IDEMPOTENT_PREFIXES below rather than assuming a green test means safe.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, globSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_SRC = join(HERE, "..", "..", "app");

const NON_IDEMPOTENT_PREFIXES = ["toggle"];

const INTENT_NAME_RE = /"[^"]*"\s*,\s*"([^"]+)"/;
/** The 5th positional arg. Trailing comma is prettier's, for multi-line calls. */
const QUEUEABLE_RE = /,\s*true\s*,?\s*$/;

/** REVIEW-FIX: this used to be a single lazy regex,
 * `/fireIntent\(([\s\S]{0,500}?)\)\s*;/g`, that captured up to the first `)`
 * followed by a literal `;`. That has two independent blind spots, both
 * confirmed against this repo's real files:
 *
 * 1. A comment that merely mentions `fireIntent(` with no real call behind
 *    it (e.g. sceneStore.ts's own Gitlink section doc, "transport.fireIntent
 *    ()/intent() are ...") still matches the literal text `fireIntent(`. The
 *    lazy capture then has nothing of its own to stop on and keeps growing
 *    until the NEXT `);` anywhere later in the file - which is a real call
 *    site's own closing paren - swallowing that whole real call into one
 *    garbage match and silently dropping it from the collected set.
 * 2. A `fireIntent(...)` call written as a JSX inline arrow-function body
 *    (`onChange={(id) => transport.fireIntent(...)}`) ends in `)}`, never
 *    `);`. The old regex could never terminate on that call's own closing
 *    paren at all, so it kept scanning past it looking for a `;` - which,
 *    combined with blind spot 1, is how 22 of SettingsDialog.tsx's 33 real
 *    call sites went uncollected.
 *
 * Fixed by not relying on a terminator character at all: walk forward from
 * each `fireIntent(` occurrence counting paren depth (skipping over string
 * literals, so a `)` inside a quoted arg can't look like the call's own
 * close) until depth returns to zero. That closing paren is unambiguously
 * the call's own, regardless of what follows it - `;`, `)}`, or nothing. A
 * bare mention like `fireIntent()` in a comment still matches the call
 * syntax, but now captures an empty body between its own open/close parens
 * instead of swallowing everything up to some unrelated later call, so
 * INTENT_NAME_RE correctly rejects it as not a real call site. */
const CALL_START_RE = /fireIntent\(/g;

function extractFireIntentArgLists(source: string): string[] {
  const argLists: string[] = [];
  for (const start of source.matchAll(CALL_START_RE)) {
    const bodyStart = start.index + start[0].length;
    let depth = 1;
    let quote: string | null = null;
    let i = bodyStart;
    for (; i < source.length && depth > 0; i++) {
      const ch = source[i];
      if (quote) {
        if (ch === "\\") i++;
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'" || ch === "`") quote = ch;
      else if (ch === "(") depth++;
      else if (ch === ")") depth--;
    }
    argLists.push(source.slice(bodyStart, i - 1));
  }
  return argLists;
}

interface CallSite {
  file: string;
  intent: string;
  queueable: boolean;
}

function collectFireIntentCallSites(): CallSite[] {
  const files = globSync("**/*.{ts,tsx}", { cwd: APP_SRC })
    .filter((f) => !f.endsWith(".test.ts") && !f.endsWith(".test.tsx"))
    .map((f) => join(APP_SRC, f));

  const sites: CallSite[] = [];
  for (const file of files) {
    const source = readFileSync(file, "utf8");
    for (const args of extractFireIntentArgLists(source)) {
      const name = INTENT_NAME_RE.exec(args);
      if (!name) continue;
      sites.push({ file, intent: name[1], queueable: QUEUEABLE_RE.test(args) });
    }
  }
  return sites;
}

describe("queueable intent classification (ADR-003 stage 3.6)", () => {
  // Guards the guard: a regex that silently matched nothing would make every
  // assertion below vacuously true, which is exactly the false-confidence
  // failure this stage's review was hunting for.
  it("finds the real fireIntent call sites at all", () => {
    const sites = collectFireIntentCallSites();
    expect(sites.length).toBeGreaterThan(100);
    expect(sites.some((s) => s.intent === "moveNodes" && s.queueable)).toBe(true);
    expect(sites.some((s) => s.intent === "sendMessage" && !s.queueable)).toBe(true);
  });

  it("never marks a non-idempotent toggle* intent queueable", () => {
    const offenders = collectFireIntentCallSites()
      .filter((s) => s.queueable)
      .filter((s) => NON_IDEMPOTENT_PREFIXES.some((p) => s.intent.startsWith(p)))
      .map((s) => `${s.intent} (${s.file})`);

    expect(offenders).toEqual([]);
  });

  // REVIEW-FIX regression coverage: the old lazy `fireIntent\(...\)\s*;`
  // regex latched onto sceneStore.ts's own Gitlink-section doc comment
  // ("transport.fireIntent()/intent() are ...", no real call behind it) and
  // grew its capture past the real loadGitlinkRepoTree call site's own
  // arguments until it hit the NEXT literal `);` in the file - which was
  // that same call's closing paren - swallowing the whole thing into one
  // garbage match. loadGitlinkRepoTree never appeared as its own collected
  // site, so it had zero coverage under the "never queueable toggle*"
  // assertion above (moot for it specifically, but the general hole is what
  // matters: any call site downstream of such a comment was equally
  // invisible). This asserts it is now found, on its own, exactly once.
  it("collects loadGitlinkRepoTree as its own call site, not swallowed by a nearby comment", () => {
    const sites = collectFireIntentCallSites().filter(
      (s) => s.intent === "loadGitlinkRepoTree",
    );
    expect(sites).toHaveLength(1);
    expect(sites[0].file.replace(/\\/g, "/")).toMatch(/canvas\/sceneStore\.ts$/);
    expect(sites[0].queueable).toBe(false);
  });

  // REVIEW-FIX regression coverage: every fireIntent call in
  // SettingsDialog.tsx is a JSX inline arrow-function body ending in `)}`,
  // never `);` - a shape the old regex's required trailing `;` could never
  // match at all, independent of the comment-latching issue above. That
  // silently dropped 22 of the file's 33 real call sites. This pins the
  // full count (verified by hand against the file: 33 `fireIntent(`
  // occurrences, all real calls, no bare mentions in comments) so a future
  // regression that drops any of them fails loudly instead of the guard's
  // other assertions just quietly evaluating fewer sites.
  it("collects every real fireIntent call site in SettingsDialog.tsx, including JSX arrow-body calls", () => {
    const sites = collectFireIntentCallSites().filter((s) =>
      s.file.replace(/\\/g, "/").endsWith("chrome/SettingsDialog.tsx"),
    );
    expect(sites).toHaveLength(33);
    // Spot-check a few of the specific call sites that were among the 22
    // previously hidden by the `)}` JSX-arrow-body gap.
    for (const intent of ["setTheme", "setLlamaCppNCtx", "pullOllamaModel", "setActiveSection"]) {
      expect(sites.some((s) => s.intent === intent)).toBe(true);
    }
  });
});
