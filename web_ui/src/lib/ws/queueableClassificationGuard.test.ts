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

/** Captures one whole fireIntent(...) statement, up to the `)` that is
 * followed by `;`. Tolerates the multi-line formatting prettier gives the
 * longer call sites (moveNodes) and nested parens inside the args array
 * (`positions.map((p) => [...])`), neither of which puts `);` inside itself. */
const FIRE_INTENT_RE = /fireIntent\(([\s\S]{0,500}?)\)\s*;/g;
const INTENT_NAME_RE = /"[^"]*"\s*,\s*"([^"]+)"/;
/** The 5th positional arg. Trailing comma is prettier's, for multi-line calls. */
const QUEUEABLE_RE = /,\s*true\s*,?\s*$/;

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
    for (const match of source.matchAll(FIRE_INTENT_RE)) {
      const args = match[1];
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
});
