import { afterEach, describe, expect, it } from "vitest";
import { authHeaders, getAuthToken, withAuthToken } from "./token";

/**
 * ADR-004 stage 4.1. These tests set window.location.hash directly rather
 * than mocking the module, because "reads the real fragment the desktop
 * shell wrote" is the actual contract under test - a mocked getAuthToken
 * would pass even if the parsing were wrong.
 */

function setFragment(fragment: string): void {
  window.location.hash = fragment;
}

afterEach(() => {
  setFragment("");
});

describe("getAuthToken", () => {
  it("reads the token graphlink_desktop.py puts in the URL fragment", () => {
    setFragment("#token=abc123");
    expect(getAuthToken()).toBe("abc123");
  });

  it("returns null when there is no fragment at all", () => {
    // The normal case for vitest and for the vite-dev workflow - every
    // helper here must degrade to a no-op rather than throwing.
    expect(getAuthToken()).toBeNull();
  });

  it("returns null when the fragment carries other keys but no token", () => {
    setFragment("#something=else");
    expect(getAuthToken()).toBeNull();
  });

  it("finds the token alongside other fragment keys", () => {
    setFragment("#other=1&token=abc123");
    expect(getAuthToken()).toBe("abc123");
  });

  it("treats an empty token value as absent", () => {
    setFragment("#token=");
    expect(getAuthToken()).toBeNull();
  });
});

describe("withAuthToken", () => {
  it("appends with ? when the url has no query string", () => {
    setFragment("#token=abc123");
    expect(withAuthToken("/api/assets/xyz")).toBe("/api/assets/xyz?token=abc123");
  });

  it("appends with & when the url already has a query string", () => {
    // The chart asset URL case: /api/assets/{id}?v={version} must keep its
    // cache-buster and gain the token, not lose one to the other.
    setFragment("#token=abc123");
    expect(withAuthToken("/api/assets/xyz?v=7")).toBe("/api/assets/xyz?v=7&token=abc123");
  });

  it("returns the url untouched when there is no token", () => {
    // Why every pre-existing URL-builder test still passes unchanged: with
    // no fragment, this is the identity function.
    expect(withAuthToken("/api/assets/xyz?v=7")).toBe("/api/assets/xyz?v=7");
  });

  it("percent-encodes the token so an exotic value cannot break the query string", () => {
    setFragment("#token=" + encodeURIComponent("a&b=c"));
    expect(withAuthToken("/api/assets/xyz")).toBe("/api/assets/xyz?token=a%26b%3Dc");
  });
});

describe("authHeaders", () => {
  it("builds a Bearer header when a token is present", () => {
    setFragment("#token=abc123");
    expect(authHeaders()).toEqual({ Authorization: "Bearer abc123" });
  });

  it("is an empty object when there is no token, so it can always be spread", () => {
    expect(authHeaders()).toEqual({});
  });
});
