import { describe, expect, it } from "vitest";
import { formatBytes, formatCount, formatDuration, formatRelativeTime, formatTokens } from "./format";

describe("formatCount", () => {
  it.each([
    [0, "0"], [1, "1"], [999, "999"], [1000, "1,000"],
    [1200345, "1,200,345"], [-1234567, "-1,234,567"],
    [999.5, "1,000"], [-1.6, "-2"], [Number.NaN, "0"],
  ] as const)("formats %s as %s", (value, expected) => {
    expect(formatCount(value)).toBe(expected);
  });
});

describe("formatDuration", () => {
  it.each([
    [0, "0 s"], [0.4, "<1 s"], [-0.4, "-<1 s"], [0.999, "<1 s"],
    [1, "1 s"], [59, "59 s"], [59.6, "1 min"],
    [60, "1 min"], [61, "1 min 1 s"], [763, "12 min 43 s"],
    [3600, "1 h"], [3725, "1 h 2 min"], [7200, "2 h"],
    [-763, "-12 min 43 s"], [-3725, "-1 h 2 min"],
    [Number.POSITIVE_INFINITY, "0 s"],
  ] as const)("formats %s seconds as %s", (value, expected) => {
    expect(formatDuration(value)).toBe(expected);
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"], [1, "1 B"], [1023, "1,023 B"],
    [1024, "1.0 KB"], [1536, "1.5 KB"],
    [1024 ** 2, "1.0 MB"], [2.5 * 1024 ** 3, "2.5 GB"],
    [1024 ** 4, "1.0 TB"], [1024 ** 5, "1.0 PB"],
    [-1024, "-1.0 KB"], [-1, "-1 B"],
    [1024 ** 2 - 1, "1.0 MB"], [Number.NaN, "0 B"],
  ] as const)("formats %s bytes as %s", (value, expected) => {
    expect(formatBytes(value)).toBe(expected);
  });
});

describe("formatTokens", () => {
  it.each([
    [0, "0"], [999, "999"], [1000, "1k"], [42700, "42.7k"],
    [80000, "80k"], [123456, "123.5k"], [999999, "1m"],
    [1000000, "1m"], [1500000, "1.5m"], [1000000000, "1b"],
    [-42700, "-42.7k"], [-999, "-999"], [999.5, "1k"],
    [Number.NEGATIVE_INFINITY, "0"],
  ] as const)("formats %s tokens as %s", (value, expected) => {
    expect(formatTokens(value)).toBe(expected);
  });
});

describe("formatRelativeTime", () => {
  const now = new Date("2026-11-22T12:00:00.000Z");
  const offset = (seconds: number) => new Date(now.getTime() + seconds * 1000);

  it.each([
    [0, "just now"], [-1, "just now"], [59, "just now"], [59.99, "just now"],
    [60, "in 1 min"], [-60, "1 min ago"], [-120, "2 min ago"],
    [-2 * 3600, "2 h ago"], [5 * 60, "in 5 min"],
    [90, "in 2 min"], [3600, "in 1 h"], [86400, "in 1 d"],
    [-7 * 86400, "1 wk ago"], [30 * 86400, "in 1 mo"],
    [-365 * 86400, "1 y ago"],
  ] as const)("formats an offset of %s seconds", (seconds, expected) => {
    expect(formatRelativeTime(offset(seconds), now)).toBe(expected);
  });

  it("accepts numeric timestamps, dates before epoch and invalid inputs", () => {
    expect(formatRelativeTime(-3600000, 0)).toBe("1 h ago");
    expect(formatRelativeTime(Number.NaN, 0)).toBe("just now");
    expect(formatRelativeTime(now, Number.NaN)).toBe("just now");
  });
});
