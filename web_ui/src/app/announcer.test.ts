import { describe, expect, it } from "vitest";
import { announce, getAnnouncement, subscribeAnnouncer } from "./announcer";

describe("announcer (ADR-012 stage 12.3)", () => {
  it("starts empty", () => {
    // Vitest gives each test FILE its own fresh module registry, so this
    // holds as long as it's the first test to touch the module in THIS
    // file - every later test in this file mutates the same module-level
    // state on purpose (see this module's own doc: it's a real always-
    // mounted region, not something with a reset hook).
    expect(getAnnouncement()).toBe("");
  });

  it("notifies subscribers with the announced text", () => {
    const received: string[] = [];
    const unsubscribe = subscribeAnnouncer(() => received.push(getAnnouncement()));
    announce("Assistant is responding");
    expect(received).toEqual(["Assistant is responding"]);
    unsubscribe();
  });

  it("unsubscribe stops further notifications", () => {
    const received: string[] = [];
    const unsubscribe = subscribeAnnouncer(() => received.push(getAnnouncement()));
    unsubscribe();
    announce("Response complete");
    expect(received).toEqual([]);
  });

  it("supports multiple independent subscribers", () => {
    const a: string[] = [];
    const b: string[] = [];
    const offA = subscribeAnnouncer(() => a.push(getAnnouncement()));
    const offB = subscribeAnnouncer(() => b.push(getAnnouncement()));
    announce("Run started");
    expect(a).toHaveLength(1);
    expect(b).toHaveLength(1);
    offA();
    offB();
  });

  it("two consecutive identical announcements still produce two distinct DOM texts", () => {
    // The whole reason for the sequence-parity marker: a screen reader only
    // re-announces an aria-live region when its TEXT CONTENT actually
    // changes - two calls with the exact same message must not collapse
    // into a DOM no-op on the second one.
    announce("Run completed");
    const first = getAnnouncement();
    announce("Run completed");
    const second = getAnnouncement();
    expect(second).not.toBe(first);
    // Both still read as the same message to a human/screen-reader, i.e.
    // the marker itself is invisible/unpronounced content, not visible text.
    expect(first.replace(/[^\x20-\x7E]/g, "")).toBe("Run completed");
    expect(second.replace(/[^\x20-\x7E]/g, "")).toBe("Run completed");
  });
});
