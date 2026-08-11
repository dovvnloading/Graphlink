import { describe, expect, it } from "vitest";
import { fuzzyFilterAndSort, fuzzyScore } from "./quickSwitcherFuzzy";

describe("fuzzyScore", () => {
  it("matches every query character in order, case-insensitively", () => {
    expect(fuzzyScore("gql", "Global Query Log")).not.toBeNull();
    expect(fuzzyScore("GQL", "global query log")).not.toBeNull();
  });

  it("returns null when a query character is missing entirely", () => {
    expect(fuzzyScore("xyz", "Global Query Log")).toBeNull();
  });

  it("returns null when the query characters appear out of order", () => {
    // "g" only occurs before "query" here - "qg" has no valid in-order
    // subsequence match, unlike against "Global Query Log" (which has a
    // SECOND "g", after "query", still leaving "qg" satisfiable there).
    expect(fuzzyScore("qg", "Log Query")).toBeNull();
  });

  it("scores an empty query as 0 (matches everything, ties)", () => {
    expect(fuzzyScore("", "anything")).toBe(0);
  });

  it("ranks a tighter (more contiguous) match ahead of a looser one", () => {
    const tight = fuzzyScore("cat", "concatenate");
    const loose = fuzzyScore("cat", "circus at twilight");
    expect(tight).not.toBeNull();
    expect(loose).not.toBeNull();
    expect(tight as number).toBeLessThan(loose as number);
  });

  it("ranks a prefix match ahead of a non-prefix match regardless of span", () => {
    const prefix = fuzzyScore("api", "API research notes for the quarter");
    const nonPrefix = fuzzyScore("api", "Graph API");
    expect(prefix).not.toBeNull();
    expect(nonPrefix).not.toBeNull();
    expect(prefix as number).toBeLessThan(nonPrefix as number);
  });
});

describe("fuzzyFilterAndSort", () => {
  const rows = [
    { title: "Onboarding Notes" },
    { title: "Q3 Planning" },
    { title: "API Research" },
    { title: "Quarterly API Review" },
  ];

  it("filters out non-matching items", () => {
    const result = fuzzyFilterAndSort("api", rows, (r) => r.title);
    expect(result.map((r) => r.title)).toEqual(["API Research", "Quarterly API Review"]);
  });

  it("ranks the prefix match first", () => {
    const result = fuzzyFilterAndSort("api", rows, (r) => r.title);
    expect(result[0].title).toBe("API Research");
  });

  it("preserves input order (a stable sort) when the query is blank", () => {
    const result = fuzzyFilterAndSort("", rows, (r) => r.title);
    expect(result.map((r) => r.title)).toEqual(rows.map((r) => r.title));
  });

  it("preserves input order when the query is only whitespace", () => {
    const result = fuzzyFilterAndSort("   ", rows, (r) => r.title);
    expect(result.map((r) => r.title)).toEqual(rows.map((r) => r.title));
  });
});
