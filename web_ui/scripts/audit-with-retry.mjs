// The npm vulnerability scan, with the registry's own availability separated
// from the thing being scanned.
//
// `npm audit --audit-level=high` conflates two completely different outcomes
// into exit code 1: "this dependency set has a high-severity advisory" and
// "registry.npmjs.org did not answer". Only the first is a reason to block a
// merge. The second took down two consecutive, Python-only pull requests in
// one afternoon (2026-09-04: a 503, then a network timeout, each after
// hanging ~5 minutes), and in the checks UI it is indistinguishable from a
// real security failure - so the reflex becomes "just re-run it", which is
// exactly the reflex that eventually gets applied to a genuine finding too.
//
// The two ARE distinguishable through --json: on a real finding npm exits
// non-zero AND writes a report carrying metadata.vulnerabilities; on an
// endpoint error it exits non-zero having written no usable report (often
// still valid JSON - an {error: ...} object - which is why classifyAuditRun
// looks for the counts block specifically, not merely for parseable output).
//
// So: a real advisory fails on the first attempt, unchanged. A registry
// error is retried three times, backing off, and if the endpoint is still
// unreachable the run continues with a CI annotation saying plainly that the
// scan did NOT run. That last part is the deliberate trade - a registry
// outage is not evidence of a clean dependency set, and passing silently
// would be worse than passing loudly.
//
// Run from web_ui/ by the frontend-checks job. Node rather than a shell blob
// with jq: that job already has node, and it matches check-bundle-size.mjs's
// own shape. classifyAuditRun is exported and unit-tested
// (audit-with-retry.test.mjs) because it is the part that decides whether a
// merge is blocked, and a guard nobody tested is how this gate got its
// reputation in the first place.

import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const ATTEMPTS = 3;
export const AUDIT_LEVEL = "high";

/**
 * Decide what one `npm audit --json` run actually told us.
 *
 * @returns {"clean"|"finding"|"unavailable"}
 *   clean       - npm exited 0: nothing at or above AUDIT_LEVEL.
 *   finding     - npm exited non-zero AND produced a real report: block.
 *   unavailable - npm exited non-zero with no usable report: the registry
 *                 did not answer, which says nothing about the dependencies.
 */
export function classifyAuditRun({ status, stdout }) {
  if (status === 0) return "clean";
  let parsed;
  try {
    parsed = JSON.parse(stdout || "");
  } catch {
    return "unavailable";
  }
  const counts = parsed && parsed.metadata && parsed.metadata.vulnerabilities;
  return counts && typeof counts === "object" ? "finding" : "unavailable";
}

/** The high/critical entries a "finding" report names, for the failure log. */
export function severeEntries(report) {
  return Object.entries((report && report.vulnerabilities) || {})
    .filter(([, entry]) => entry && (entry.severity === "high" || entry.severity === "critical"))
    .map(([name, entry]) => `${entry.severity}\t${name}`);
}

// -- everything below is the CLI shell around the two functions above -------

// True only when this file was invoked directly, so importing it from the
// test does not kick off a real audit.
const isMain =
  !!process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;

function runAudit() {
  // shell:true on Windows because npm is npm.cmd there, and Node refuses to
  // spawn .cmd without a shell. CI is ubuntu, where the direct spawn works -
  // this is purely so the script is runnable on a maintainer's machine.
  return spawnSync("npm", ["audit", `--audit-level=${AUDIT_LEVEL}`, "--json"], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    shell: process.platform === "win32",
  });
}

const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

function main() {
  const isCi = !!process.env.GITHUB_ACTIONS;

  for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
    const result = runAudit();
    const verdict = classifyAuditRun(result);

    if (verdict === "clean") {
      console.log(`npm audit: no ${AUDIT_LEVEL} or critical advisories.`);
      return 0;
    }

    if (verdict === "finding") {
      // A real finding fails immediately - retrying a genuine advisory would
      // only delay it.
      const report = JSON.parse(result.stdout);
      console.error(
        `npm audit FAILED: ${AUDIT_LEVEL}+ advisories in the dependency set.\n` +
          `  counts: ${JSON.stringify(report.metadata.vulnerabilities)}`,
      );
      for (const line of severeEntries(report)) console.error(`  - ${line}`);
      return 1;
    }

    console.log(
      `npm audit: the registry returned no usable report (attempt ${attempt}/${ATTEMPTS}).` +
        (result.stderr ? `\n${result.stderr.trim().split("\n").slice(-3).join("\n")}` : ""),
    );
    if (attempt < ATTEMPTS) sleep(attempt * 10_000);
  }

  const message =
    `npm audit could not reach the registry after ${ATTEMPTS} attempts - the vulnerability ` +
    "scan did NOT run for this build. This is a registry availability problem, not a clean " +
    "result; re-run the job once the registry is healthy.";
  console.log(isCi ? `::warning::${message}` : `WARNING: ${message}`);
  return 0;
}

if (isMain) process.exit(main());
