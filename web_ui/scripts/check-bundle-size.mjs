// ADR-019 stage 19.2: the bundle-size CI counting gate.
//
// Fails the build when the built JS grows past a ratchet ceiling. This is a
// REGRESSION ratchet pinned ~5% above today's measured reality (one chunk,
// 1,255,741 bytes on 2026-08-06), NOT the ADR-019 budget itself - the budget
// (initial chunk <= 500 KiB, rest lazy-loaded) is ADR-011's code-splitting
// work. Until that lands, this gate's job is to make sure the number only
// ever moves DOWN: nobody can add half a megabyte of dependencies without CI
// saying so.
//
// When ADR-011's splitting lands, tighten LARGEST_CHUNK_CEILING_BYTES to the
// real 500 KiB budget (512_000) as part of that stage - per ADR-019 §4,
// changing a ceiling is a deliberate, commented amendment, never a quiet edit
// to make a red build green.
//
// Runs after `npm run build` in the `check` chain (see package.json), so CI's
// existing frontend-checks job enforces it with no workflow changes.

import { readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ASSETS_DIR = join(HERE, "..", "dist", "app", "assets");

// Today's reality: a single 1,255,741-byte chunk. ~5% headroom absorbs
// ordinary dependency-patch drift; a real regression (a new heavyweight
// dependency, an accidental double-bundle) blows straight through it.
const LARGEST_CHUNK_CEILING_BYTES = 1_320_000;
// Slightly looser than the per-chunk ceiling so future code-splitting can add
// small lazy chunks without tripping the TOTAL, while still catching the
// "total payload quietly ballooned" class the per-chunk ceiling alone would
// miss once there is more than one chunk.
const TOTAL_JS_CEILING_BYTES = 1_400_000;

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
