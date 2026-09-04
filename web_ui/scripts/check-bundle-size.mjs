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
//
// Deliberate, commented amendment - 2026-08-19 (ADR-019 section 4), ADR-021
// stage 21.3.
//
// Two separate things are recorded here, because conflating them is how a
// ratchet dies quietly:
//
// 1. Drift, again, unattributed. The amendment above measured 815,280 bytes
//    on 2026-08-12. The pre-21.3 main chunk measures 838,707 - ~23 KB of
//    growth landed across the intervening week without anyone re-anchoring
//    this ceiling, leaving 1,293 bytes (0.15%) of headroom. That is the same
//    erosion the 08-12 amendment was itself written about, recurring within
//    a week, and it is the reason the very next feature tripped the gate.
// 2. Real, intended growth. ADR-021 stage 21.3 adds the plan-step editor to
//    PlanNodeView (the checklist edit ADR-008 decided on and shipped
//    without): a draft-then-commit editing mode with per-step title inputs,
//    reorder/remove controls, and its validation. Measured cost: +2,640
//    bytes, taking the main chunk to 841,347.
//
// Raised to 866,000: ~2.9% real headroom against today's 841,347, the same
// posture the 08-12 amendment chose. The underlying ADR-019 budget for the
// initial chunk is still 500 KiB (512,000 bytes) and we are now ~64% over
// it; this ceiling remains a regression ratchet, not the budget, and moving
// it still does not move the budget. Closing the real gap still needs the
// eagerly-rendered node views code-split - work no stage owns, and which
// this stage does not pretend to do.
//
// Deliberate, commented amendment - 2026-09-03 (ADR-019 section 4),
// Review Lens feature.
//
// Real, intended growth, no new dependency: the Review Lens node adds one
// more eagerly-rendered node view (CodeReviewNodeView: Setup/Walkthrough/
// Findings tabs, verdict banner, tiered findings, diff viewer, Q&A) plus
// its SceneCanvas/sceneStore wiring and card CSS. Measured cost: largest
// chunk 866,000-ceiling -> 890,541 bytes (+24,541, ~2.8%); total JS
// 1,423,000-ceiling -> 1,445,539 bytes (+22,539, ~1.6%). No chunk grew
// from a dependency (imports are the existing shared card components -
// NodeShell/NodeMenu/NodeMarkdown/CollapseToggleButton - plus React).
//
// CORRECTION - 2026-09-04 (QA audit).
//
// The amendment immediately above measures its own delta from the previous
// CEILING (866,000) instead of the previous MEASUREMENT (841,347, recorded
// by the 08-19 amendment). Review Lens did not cost +24,541 bytes; against
// the last number this file actually recorded it cost +49,194 (+5.8%),
// roughly double. The ceiling itself is not wrong - 917,000 is ~3% over the
// real 890,541, the posture every amendment here has chosen - but the
// arithmetic explaining it is, and a ratchet whose audit trail is wrong
// cannot be reasoned about later.
//
// The gap is drift, and it went unattributed exactly as before: `git log`
// shows 39 commits touching web_ui/src between the 08-19 amendment and
// Review Lens, and NONE of them touched this file. Both prior amendments
// were written about this same recurrence ("Drift, again, unattributed...
// conflating them is how a ratchet dies quietly"), which makes this the
// third time, and the first two were only caught after the gate had already
// stopped being able to catch anything - 323 bytes of headroom in August,
// 1,293 in September.
//
// So the numbers below are re-anchored to measured reality, and the script
// now prints its own headroom and raises a CI annotation when that headroom
// gets thin (see HEADROOM_WARN_RATIO). A warning, deliberately not a
// failure: drift is nobody's individual fault, and failing an unrelated PR
// for it is the false alarm the 08-12 amendment was itself written about.
// Somebody has to see the erosion while there is still room to act on it.
//
// The underlying ADR-019 budget for the initial chunk is still 500 KiB
// (512,000 bytes) and 890,541 is ~74% over it. This ceiling remains a
// regression ratchet, not the budget. Closing the real gap still needs the
// 17 eagerly-rendered node views in SceneCanvas.tsx code-split - work no
// stage owns.
// Deliberate, commented amendment - 2026-09-04 (ADR-019 section 4).
//
// The first time either number in this file has moved DOWN, and the reason
// every prior amendment gave for not moving it: the eagerly-rendered node
// views are now code-split. SceneCanvas.tsx registered all 17 *NodeView
// components through static imports, so every one landed in the initial
// chunk whether or not the open canvas held a single node of that kind.
// They now load one chunk per kind, on first render of that kind - see
// web_ui/src/app/canvas/lazyNodeViews.tsx.
//
// Measured: largest chunk 890,541 -> 780,470 bytes (-110,071, -12.4%).
//
// Total JS went the other way, 1,445,539 -> 1,456,011 (+10,472, +0.7%),
// which is expected and is not a regression: splitting redistributes code
// and adds a small per-chunk overhead. The budget ADR-019 actually sets is
// on the INITIAL chunk - what the app must parse before it can paint - and
// that is the number that fell.
//
// Both ceilings are re-anchored to ~3% over measured reality, the posture
// every amendment here has used. Stated plainly, as the raises were: the
// ADR-019 budget for the initial chunk is 500 KiB (512,000 bytes), and at
// 780,470 the initial chunk is still ~52% over it - down from ~74%. React
// and React Flow are the bulk of what remains, and no further split is
// planned; this closes the gap that was attributed to the node views, not
// the whole gap.
const LARGEST_CHUNK_CEILING_BYTES = 804_000;
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
// 2026-09-04: re-anchored to 1,456,011 with the same ~3% headroom, for the
// per-chunk overhead the node-view split added. Recorded as a raise, small
// as it is - the amendment above is what pays for it.
const TOTAL_JS_CEILING_BYTES = 1_500_000;

// Below this much headroom, say so. Both prior amendments record the same
// story: the ceiling was set with ~3-5% room, ordinary week-to-week growth
// ate it silently, and the erosion was only noticed once the gate had 0.04%
// (August) and 0.15% (September) left - at which point the next unrelated
// change tripped it and looked like the culprit. 1.5% would have surfaced
// both while there was still room to re-anchor deliberately.
const HEADROOM_WARN_RATIO = 0.015;

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

// Headroom is printed, not just the raw sizes: the number that actually
// predicts a false alarm is how much room is left, and it was never on
// screen. A CI annotation (rather than a buried log line) is what gets it in
// front of someone while re-anchoring is still a deliberate choice.
const headroom = (bytes, ceiling) => (ceiling - bytes) / ceiling;
const asPercent = (ratio) => `${(ratio * 100).toFixed(1)}%`;
const chunkHeadroom = headroom(chunks[0].bytes, LARGEST_CHUNK_CEILING_BYTES);
const totalHeadroom = headroom(totalBytes, TOTAL_JS_CEILING_BYTES);

for (const [label, bytes, ceiling, room] of [
  ["largest chunk", chunks[0].bytes, LARGEST_CHUNK_CEILING_BYTES, chunkHeadroom],
  ["total JS", totalBytes, TOTAL_JS_CEILING_BYTES, totalHeadroom],
]) {
  if (room >= HEADROOM_WARN_RATIO) continue;
  const message =
    `check-bundle-size: ${label} is ${bytes.toLocaleString()} bytes against a ` +
    `${ceiling.toLocaleString()} ceiling - only ${asPercent(room)} headroom left. ` +
    "Growth has been accumulating without the ceiling being re-anchored. Re-anchor it " +
    "as a deliberate, commented amendment (ADR-019 §4) measured from the LAST RECORDED " +
    "MEASUREMENT, not from the current ceiling, before an unrelated change trips it.";
  // GitHub renders ::warning:: as an annotation on the run; elsewhere it is
  // just a line, which is why the prefix is conditional rather than always on.
  console.log(process.env.GITHUB_ACTIONS ? `::warning::${message}` : `WARNING: ${message}`);
}

console.log(
  `check-bundle-size OK: largest chunk ${chunks[0].bytes.toLocaleString()} bytes ` +
    `(ceiling ${LARGEST_CHUNK_CEILING_BYTES.toLocaleString()}, ${asPercent(chunkHeadroom)} headroom), ` +
    `total ${totalBytes.toLocaleString()} bytes ` +
    `(ceiling ${TOTAL_JS_CEILING_BYTES.toLocaleString()}, ${asPercent(totalHeadroom)} headroom).`,
);
