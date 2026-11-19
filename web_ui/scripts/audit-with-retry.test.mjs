// Guard the guard: audit-with-retry.mjs decides whether a merge is blocked,
// so the decision itself gets tested rather than trusted. Same posture as the
// backend's own AST-scan gates.
//
// The process/retry shell around classifyAuditRun is deliberately NOT tested
// here: stubbing `npm` cross-platform is more machinery than it is worth, and
// the shell is a thin loop over the three verdicts below. What matters - and
// what the real npm audit conflates - is which of those three a given run is.

import { describe, expect, it } from "vitest";

import { classifyAuditRun, severeEntries, unacceptedSevereNames } from "./audit-with-retry.mjs";

const REAL_FINDING = JSON.stringify({
  metadata: { vulnerabilities: { info: 0, low: 0, moderate: 1, high: 2, critical: 1 } },
  vulnerabilities: {
    lodash: { severity: "high" },
    minimist: { severity: "critical" },
    qs: { severity: "moderate" },
  },
});

describe("classifyAuditRun", () => {
  it("treats a zero exit as clean", () => {
    expect(classifyAuditRun({ status: 0, stdout: "{}" })).toBe("clean");
  });

  it("treats a non-zero exit WITH a real report as a finding", () => {
    expect(classifyAuditRun({ status: 1, stdout: REAL_FINDING })).toBe("finding");
  });

  it("treats a non-zero exit with unparseable output as unavailable", () => {
    // The 503 shape: npm printed a human error, not JSON.
    expect(classifyAuditRun({ status: 1, stdout: "npm error audit endpoint returned an error" }))
      .toBe("unavailable");
  });

  it("treats a non-zero exit with an error OBJECT as unavailable, not a finding", () => {
    // The subtle one. npm can emit valid JSON that is still not a report -
    // checking merely "did it parse" would classify a registry outage as a
    // security finding and block every merge during one.
    expect(classifyAuditRun({ status: 1, stdout: '{"error":{"code":"E503"}}' })).toBe("unavailable");
  });

  it("treats a non-zero exit with no output at all as unavailable", () => {
    // The network-timeout shape: npm died before writing anything.
    expect(classifyAuditRun({ status: 1, stdout: "" })).toBe("unavailable");
    expect(classifyAuditRun({ status: null, stdout: undefined })).toBe("unavailable");
  });

  it("does not mistake an empty-but-present counts block for an outage", () => {
    // A clean-at-this-level report still carries counts; if npm ever exits
    // non-zero with one, that is a finding to surface, not a network problem.
    expect(classifyAuditRun({ status: 1, stdout: '{"metadata":{"vulnerabilities":{}}}' }))
      .toBe("finding");
  });
});

describe("severeEntries", () => {
  it("lists only high and critical entries", () => {
    const lines = severeEntries(JSON.parse(REAL_FINDING));
    expect(lines).toHaveLength(2);
    expect(lines.join("\n")).toContain("lodash");
    expect(lines.join("\n")).toContain("minimist");
    expect(lines.join("\n")).not.toContain("qs");
  });

  it("survives a report with no vulnerabilities block", () => {
    expect(severeEntries({})).toEqual([]);
    expect(severeEntries(null)).toEqual([]);
  });
});

describe("unacceptedSevereNames", () => {
  const PICO = "https://github.com/advisories/GHSA-fc5q-39xc-vjpf";
  const RULES = [{ url: PICO, package: "picomatch", expires: "2026-12-18", reason: "no fix" }];
  // The real 2026-11-18 shape: one advisory on picomatch, surfacing through
  // every dev-tool package that depends on it.
  const PICO_CHAIN = {
    vulnerabilities: {
      picomatch: { severity: "high", via: [{ name: "picomatch", url: PICO, severity: "high" }] },
      fdir: { severity: "high", via: ["picomatch"] },
      tinyglobby: { severity: "high", via: ["fdir", "picomatch"] },
      vite: { severity: "high", via: ["fdir", "picomatch", "tinyglobby"] },
      qs: { severity: "moderate", via: [{ name: "qs", url: "https://x/qs", severity: "moderate" }] },
    },
  };

  it("excuses a chain that traces only to an accepted, unexpired advisory", () => {
    expect(unacceptedSevereNames(PICO_CHAIN, RULES, "2026-11-18")).toEqual([]);
  });

  it("stops excusing it after the expiry date", () => {
    expect(unacceptedSevereNames(PICO_CHAIN, RULES, "2026-12-19").sort())
      .toEqual(["fdir", "picomatch", "tinyglobby", "vite"]);
  });

  it("still fails a package that ALSO has an unaccepted advisory", () => {
    const report = structuredClone(PICO_CHAIN);
    report.vulnerabilities.vite.via.push({ name: "vite", url: "https://x/vite-new", severity: "high" });
    expect(unacceptedSevereNames(report, RULES, "2026-11-18")).toEqual(["vite"]);
  });

  it("never excuses an unrelated high or critical advisory", () => {
    const report = {
      vulnerabilities: {
        lodash: { severity: "critical", via: [{ name: "lodash", url: "https://x/l", severity: "critical" }] },
      },
    };
    expect(unacceptedSevereNames(report, RULES, "2026-11-18")).toEqual(["lodash"]);
  });

  it("does not let the accepted URL excuse a different package", () => {
    const report = {
      vulnerabilities: { other: { severity: "high", via: [{ name: "other", url: PICO, severity: "high" }] } },
    };
    expect(unacceptedSevereNames(report, RULES, "2026-11-18")).toEqual(["other"]);
  });

  it("fails an entry whose via chain names a package missing from the report", () => {
    const report = { vulnerabilities: { vite: { severity: "high", via: ["ghost"] } } };
    expect(unacceptedSevereNames(report, RULES, "2026-11-18")).toEqual(["vite"]);
  });
});
