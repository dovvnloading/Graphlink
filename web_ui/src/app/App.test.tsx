/**
 * ADR-003 stage 3.6: the connection badge's own text.
 *
 * This is the user-visible half of the stage's exit criterion ("visible paused
 * state") - and until this file existed, nothing in the suite covered the
 * badge at all, in any status. It was verified once by hand against a real
 * running app, which proves it worked that day but is not a regression guard.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { connectionBadgeLabel } from "./connectionBadge";
import { SkipToComposerLink } from "./App";
import { getAuthToken } from "../lib/auth/token";

describe("connectionBadgeLabel (UI-14 shell)", () => {
  it("shows the reconnecting label while an established connection is paused", () => {
    expect(connectionBadgeLabel("reconnecting")).toBe("Reconnecting…");
  });
  it("makes permanent disconnects distinct from first connection attempts", () => {
    expect(connectionBadgeLabel("closed")).toBe("Offline");
    expect(connectionBadgeLabel("connecting")).toBe("Connecting…");
  });
  it("emits no status indicator text while healthy", () => {
    expect(connectionBadgeLabel("open")).toBe("");
  });
});

/**
 * fix-security-pass finding key skip-link-clobbers-token-fragment.
 *
 * Regression guard for the bug: the skip link used to be a plain
 * `<a href="#composer-message-input">`, which performs a real same-document
 * fragment navigation on click (React does not intercept a bare anchor
 * click) - overwriting location.hash and, with it, any `#token=...` the
 * desktop shell had put there for lib/auth/token.ts to read. Rendered
 * standalone (not the whole <App/> shell, which needs a live WS
 * backend/stores) since SkipToComposerLink has no dependency on either.
 */
describe("SkipToComposerLink (fix-security-pass: skip-link-clobbers-token-fragment)", () => {
  afterEach(() => {
    window.location.hash = "";
  });

  function renderSkipLink() {
    render(
      <>
        <SkipToComposerLink />
        <textarea id="composer-message-input" />
      </>,
    );
  }

  it("moves focus to the composer input", () => {
    renderSkipLink();
    fireEvent.click(screen.getByText("Skip to message composer"));
    expect(document.activeElement).toBe(screen.getByRole("textbox"));
  });

  it("does not touch location.hash, so a pre-existing capability token survives activation", () => {
    // Stand-in for graphlink_desktop.py's `#token=<token>` launch URL.
    window.location.hash = "#token=abc123";
    renderSkipLink();
    fireEvent.click(screen.getByText("Skip to message composer"));
    expect(window.location.hash).toBe("#token=abc123");
    // The actual end-to-end symptom the finding described: every /api call
    // after one skip-link activation lost its token and got a silent 401.
    expect(getAuthToken()).toBe("abc123");
  });

  // jsdom implements no default action for anchor-element clicks (neither
  // fireEvent.click nor a native .click() moves focus or updates
  // location.hash for an <a href="#...">, confirmed by hand against this
  // exact jsdom version) - so the fragment-clobbering navigation itself
  // can't be reproduced here, and the test above passes unchanged even
  // against the old <a href="#composer-message-input"> shape. What DOES
  // discriminate the two shapes in this environment, and is what actually
  // rules the mechanism out: there is no href for the browser to navigate
  // to in the first place. Confirmed by hand-reverting to the <a> shape,
  // which fails this exact assertion (tagName "A", href attribute present).
  it("is a real <button>, not an anchor - nothing here can trigger a fragment navigation", () => {
    render(<SkipToComposerLink />);
    const el = screen.getByText("Skip to message composer");
    expect(el.tagName).toBe("BUTTON");
    expect(el).not.toHaveAttribute("href");
  });
});
