// ADR-019 stage 19.2: the bundle-size CI counting gate.
//
// Fails the build when the built JS grows past a ratchet ceiling. This is a
// REGRESSION ratchet pinned ~5% above measured reality, NOT the ADR-019
// budget itself (initial chunk <= 500 KiB, rest lazy-loaded) - this gate's
// job is to make sure the number only ever moves DOWN: nobody can add half a
// megabyte of dependencies without CI saying so.
//
// ADR-011 stage 11.6 landed the code-splitting this file's own prior comment
// was waiting on (React.lazy for SettingsDialog/ChatLibraryDialog/HelpDialog
// + manualChunks pulling katex/highlight.js - NodeMarkdown.tsx's own two
// heaviest deps - out of the main chunk) and re-anchored both ceilings below
// to the real post-split numbers (six chunks now, largest 775,553 bytes on
// 2026-08-08, down from one 1,255,741-byte chunk). That real number is
// STILL above the ADR-019 500 KiB (512_000-byte) budget for the initial
// chunk - React + React Flow + every eagerly-rendered node view (memoized in
// 11.1, virtualized in 11.2, but not themselves code-split) account for the
// rest, and splitting those further is out of this stage's scope. Ratchet
// against reality, not the aspiration: tighten LARGEST_CHUNK_CEILING_BYTES
// again, as its own deliberate, commented amendment (ADR-019 §4), whenever a
// later stage shrinks the main chunk further - never loosen it to paper over
// a regression.
//
// Runs after `npm run build` in the `check` chain (see package.json), so CI's
// existing frontend-checks job enforces it with no workflow changes.

import { readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ASSETS_DIR = join(HERE, "..", "dist", "app", "assets");

// Deliberate, commented amendment - 2026-08-12 (ADR-019 §4).
//
// The comment above describes post-11.6 reality (775,553 bytes) and claims
// "~5% headroom". That stopped being true without anyone noticing: by
// 2026-08-12 the real largest chunk had crept to 814,677 bytes, leaving 323
// bytes - 0.04% - under the old 815,000 ceiling. A repo-wide audit flagged
// that the ratchet had quietly stopped being able to catch anything, and the
// very next change (two small bug fixes: the ConversationNodeView streaming
// throttle and the chart live-theme listener, ~600 bytes combined) tripped it
// - a false alarm on an unrelated PR, exactly the failure mode predicted.
//
// Raised to 840,000: ~3% real headroom against today's 815,280 bytes, enough
// to absorb ordinary dependency-patch drift again while still blowing up on a
// genuine regression (a heavyweight dep landing outside the katex/highlight.js
// manualChunks, or an accidental double-bundle).
//
// This is a raise, and raises deserve suspicion - so, stated plainly: the
// underlying ADR-019 budget for the initial chunk is 500 KiB (512,000 bytes),
// and at 815,280 we are ~59% OVER it. This ceiling is a regression ratchet,
// not the budget, and moving it does not move the budget. Closing the real gap
// needs code-splitting the eagerly-rendered node views (memoized in 11.1,
// virtualized in 11.2, but never themselves split) - work no stage currently
// owns. Tighten this back down the moment that lands; never loosen it again to
// paper over a regression.
const LARGEST_CHUNK_CEILING_BYTES = 840_000;
// Post-11.6 reality: six chunks (main + katex + highlight.js + the three
// lazy dialogs) total 1,288,075 bytes - essentially unchanged from the
// pre-split single-chunk total, as expected: splitting redistributes code
// across chunks, it doesn't shrink it. ~5% headroom over that measured
// total, same rationale as the per-chunk ceiling above; still comfortably
// looser than the per-chunk ceiling so a later stage adding one more small
// lazy chunk doesn't trip this on its own.
//
// ADR-020 stage 20.2 (real, deliberate growth, not absorbed silently): the
// lazy-loaded ChatLibraryDialog chunk grew with the workspace switcher, tag
// filter chips, favorite/archive icon buttons, and inline tag editing this
// stage adds - measured total is now 1,356,069 bytes, which already exceeds
// the prior ceiling on its own. Re-anchored to that new reality with the
// same ~5% headroom the original ceiling used (1,356,069 * 1.05 ≈
// 1,423,872, rounded down to a clean number) - this only ever moves the
// ceiling down again in a later stage that shrinks the total, per this
// file's own ratchet discipline above.
const TOTAL_JS_CEILING_BYTES = 1_423_000;

let entries;
try {
  entries = readdirSync(ASSETS_DIR);
} catch {
  console.error(
    `check-bundle-size: ${ASSETS_DIR} does not exist - run \`npm run build\` first ` +
      "(the `check` script does this; this gate must never pass vacuously on a missing build).",
  );
  process.exit(1);
}

const chunks = entries
  .filter((name) => name.endsWith(".js"))
  .map((name) => ({ name, bytes: statSync(join(ASSETS_DIR, name)).size }))
  .sort((a, b) => b.bytes - a.bytes);

if (chunks.length === 0) {
  // Guards the guard: an empty assets dir means the build layout changed out
  // from under this script - that must fail loud, not pass with zero chunks.
  console.error(
    `check-bundle-size: no .js chunks found in ${ASSETS_DIR} - the build output ` +
      "layout changed; update this script's ASSETS_DIR to match.",
  );
  process.exit(1);
}

const totalBytes = chunks.reduce((sum, c) => sum + c.bytes, 0);
const failures = [];

if (chunks[0].bytes > LARGEST_CHUNK_CEILING_BYTES) {
  failures.push(
    `largest chunk ${chunks[0].name} is ${chunks[0].bytes.toLocaleString()} bytes ` +
      `(ceiling: ${LARGEST_CHUNK_CEILING_BYTES.toLocaleString()})`,
  );
}
if (totalBytes > TOTAL_JS_CEILING_BYTES) {
  failures.push(
    `total JS is ${totalBytes.toLocaleString()} bytes across ${chunks.length} chunk(s) ` +
      `(ceiling: ${TOTAL_JS_CEILING_BYTES.toLocaleString()})`,
  );
}

if (failures.length > 0) {
  console.error(
    "check-bundle-size FAILED (ADR-019 stage 19.2 bundle ratchet):\n" +
      failures.map((f) => `  - ${f}`).join("\n") +
      "\n\nIf this growth is genuinely intended, raise the ceiling in " +
      "web_ui/scripts/check-bundle-size.mjs as a deliberate, commented amendment " +
      "(ADR-019 §4) - never to quietly absorb an accidental dependency.",
  );
  process.exit(1);
}

console.log(
  `check-bundle-size OK: largest chunk ${chunks[0].bytes.toLocaleString()} bytes ` +
    `(ceiling ${LARGEST_CHUNK_CEILING_BYTES.toLocaleString()}), ` +
    `total ${totalBytes.toLocaleString()} bytes (ceiling ${TOTAL_JS_CEILING_BYTES.toLocaleString()}).`,
);
