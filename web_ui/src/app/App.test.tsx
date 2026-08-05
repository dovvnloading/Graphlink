/**
 * ADR-003 stage 3.6: the connection badge's own text.
 *
 * This is the user-visible half of the stage's exit criterion ("visible paused
 * state") - and until this file existed, nothing in the suite covered the
 * badge at all, in any status. It was verified once by hand against a real
 * running app, which proves it worked that day but is not a regression guard.
 */
import { describe, expect, it } from "vitest";
import { connectionBadgeLabel } from "./connectionBadge";

describe("connectionBadgeLabel (ADR-003 stage 3.6)", () => {
  it("says a session is paused while reconnecting, not just the bare status", () => {
    // The exact wording matters: the ADR's decision text names this string as
    // what the badge "gains", and it is the app's only answer to "why didn't
    // my click do anything" while intents are being queued or refused.
    expect(connectionBadgeLabel("reconnecting")).toBe("reconnecting — actions paused");
  });

  it("distinguishes the first-ever connect from a reconnect", () => {
    // "connecting" is the very first attempt - nothing was ever working, so
    // there is no in-progress session to report as paused. Collapsing these
    // two into one label is exactly the pre-3.6 behaviour this stage changed.
    expect(connectionBadgeLabel("connecting")).toBe("connecting");
    expect(connectionBadgeLabel("connecting")).not.toBe(connectionBadgeLabel("reconnecting"));
  });

  it("keeps the established labels for the pre-existing statuses", () => {
    expect(connectionBadgeLabel("open")).toBe("connected");
    expect(connectionBadgeLabel("closed")).toBe("closed");
  });
});
